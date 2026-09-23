# Diseno - phishing-msg-collector

Este documento describe la arquitectura y el diseno de los modulos. Cumple los
requerimientos de `requirements.md` y las reglas de los steering (en especial
`security` y `agentcore`). La seguridad tiene prioridad absoluta.

## 1. Vision general

```
Microsoft 365
    | (phishing@itau.cl)
    v
Microsoft Graph API (READ ONLY, GET, Mail.Read)
    v
graph_client.py  ---- config.py (config centralizada)
    v
collector.py  ----->  database.py (SQLite: estado + dedup)
    |
    v
sample.py  ->  downloads/pending/SAMPLE_ID/
                 ├── <Subject>.msg      (evidencia primaria, inmutable)
                 ├── <Subject>.eml      (derivado, si es posible)
                 ├── metadata.json
                 ├── hashes.txt
                 └── READY
    v
(futuro) OpenCode / AgentCore  ->  analysis.json / report.md / iocs.json
```

El Collector termina cada muestra con `analysis_status=PENDIENTE` y el marcador
`READY`. El Analyzer (futuro) consume solo carpetas `READY`, sin acceder a Graph.

## 2. Flujo principal (por ejecucion)

1. `main.py` carga configuracion (`config.py`) e interpreta CLI.
2. Se determina la ventana temporal:
   - Modos CLI (`--today`, `--hours`, `--date`) fijan la ventana explicitamente.
   - Por defecto: `[last_successful_check - LOOKBACK_MINUTES, ahora]`.
   - Si no hay `last_successful_check`: `[ahora - FIRST_RUN_HOURS, ahora]`.
3. `graph_client.obtener_token()` autentica (MSAL client credentials).
4. `graph_client.buscar_mensajes()` lista correos de la ventana (con `$select`,
   filtro `receivedDateTime`, paginacion `@odata.nextLink`).
5. Por cada correo: `graph_client.obtener_adjuntos()`.
6. Por cada adjunto candidato (`.msg` o `itemAttachment`):
   - Dedup: `database.attachment_existe(message_id, attachment_id)`
     (salvo `--force`).
   - `--dry-run`: solo se informa que se descargaria; no hay efectos.
   - Descarga segura (`.part` -> validaciones -> `.msg`/`.eml`).
   - Calculo de SHA256; deteccion de SHA256 repetido (trazabilidad).
   - Conversion MSG->EML si aplica.
   - Escritura de `metadata.json`, `hashes.txt`.
   - Registro/actualizacion en SQLite.
   - Creacion de `READY` cuando todo esta consistente.
7. Si la corrida fue exitosa a nivel Graph, se actualiza
   `last_successful_check`. Si Graph fallo por completo, NO se actualiza.
8. `main.py` muestra un resumen.

## 3. Modulos

### 3.1 config.py
- Carga `.env` con `python-dotenv` y expone una estructura `Config` con todos los
  valores tipados y validados. Unica fuente de configuracion (facilita migracion
  a AWS/AgentCore).
- Valida obligatorios: `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, `MAILBOX`.
- Provee: `FIRST_RUN_HOURS`, `LOOKBACK_MINUTES`, `DOWNLOAD_DIR`,
  `MAX_ATTACHMENT_SIZE_MB`, `MAX_FILENAME_LENGTH`, `KEEP_ORIGINAL_MSG`,
  `CREATE_EML`, `DATABASE_PATH`, `LOG_LEVEL`, `LOG_DIR`.
- No expone secretos en logs ni en `__repr__`.

### 3.2 graph_client.py (READ ONLY)
Toda la comunicacion con Graph. Sin logica SQLite. Funciones:
- `obtener_token()`: MSAL `ConfidentialClientApplication`, scope `.default`.
- `buscar_mensajes(desde, hasta)`: GET a
  `/users/{mailbox}/mailFolders/inbox/messages` con `$filter` por
  `receivedDateTime`, `$select` minimo, `$orderby`, `$top`, paginacion.
- `obtener_adjuntos(message_id)`: GET de attachments.
- `descargar_file_attachment(message_id, attachment)`: bytes del fileAttachment.
- `descargar_item_attachment(message_id, attachment_id)`: MIME via `/$value`.
- Helper interno `_get(...)` es el UNICO metodo de red: HTTPS con `verify` por
  defecto (TLS ON), `timeout` siempre, manejo de 401/403/404/429/5xx, respeto de
  `Retry-After`, reintentos acotados con backoff, sin loops infinitos.
- Nunca loggea Authorization/token/secret.

### 3.3 database.py (SQLite, sin ORM)
Toda la interaccion SQLite. Conexion con `timeout=30` y `PRAGMA journal_mode=WAL`.
Consultas siempre parametrizadas. Funciones:
- `inicializar_base()`, `attachment_existe()`, `obtener_attachment()`,
  `registrar_attachment()`, `actualizar_descarga()`, `actualizar_conversion()`,
  `registrar_error()`, `buscar_sha256()`, `obtener_ultima_revision()`,
  `actualizar_ultima_revision()`, `obtener_status()`, `obtener_historial()`.

Esquema (resumen; ver requirements y brief para detalle):
- `processed_attachments(... UNIQUE(message_id, attachment_id))`.
- `app_state(key TEXT PRIMARY KEY, value TEXT)` con `last_successful_check`.
- Indices: `idx_sha256_msg`, `idx_received_datetime`.

### 3.4 sample.py (evidencia)
Operaciones de filesystem sobre la muestra. No conoce credenciales Graph.
- `sanitizar_nombre(subject, max_len)`: aplica reglas de seguridad (reemplazos,
  quita control chars/NULL, prohibe `../`, trunca; vacio -> `sin_asunto`).
- `crear_directorio_muestra(sample_id)`: crea `downloads/pending/SAMPLE_ID/` y
  valida que la ruta quede dentro del directorio base (anti path traversal).
- `guardar_msg(bytes, ruta)`: escribe `.part`, valida tamano (> 0 y <= limite),
  rename atomico a `.msg`. `guardar_eml(...)` analogo para MIME.
- `calcular_sha256(ruta)`.
- `convertir_msg_a_eml(ruta_msg, ruta_eml)`: usa `extract-msg` + `email`
  (EmailMessage). Extrae datos, no ejecuta contenido. Si falla, conserva MSG y
  senala error.
- `crear_metadata(...)`, `crear_hashes_txt(...)`, `crear_ready(...)`.

### 3.5 collector.py (orquestacion)
Une Graph -> SQLite -> sample. Implementa dedup, lookback, reintentos y la
politica de checkpoint. Sin logica de IA. Devuelve un resumen (contadores) a
`main.py`.

### 3.6 main.py (CLI)
`argparse` con: (default), `--today`, `--hours N`, `--date YYYY-MM-DD`,
`--dry-run`, `--force`, `--status`, `--history`, `--debug`, `--help`. Solo carga
config, resuelve la ventana/modo, invoca collector y muestra resumen. Sin logica
de negocio grande.

### 3.7 utils.py
Helpers: configuracion de logging en espanol (archivo + consola), utilidades de
tiempo UTC (formato ISO `...Z`), y un filtro de logging que evita imprimir
cabeceras/valores sensibles.

## 4. Nombres y sample_id
- `sample_id = YYYYMMDD_HHMMSS_HASHCORTO` (ej. `20260922_160501_9fd472ab`).
- Carpeta = `sample_id`. Archivos = Subject sanitizado (mismo nombre base MSG/EML).
- Subject preferido: (1) del correo sospechoso dentro del `.msg`; (2) del
  itemAttachment; (3) del correo contenedor; (4) `sin_asunto`.

## 5. Estados
- `download_status`: `PENDIENTE | DESCARGADO | ERROR`.
- `conversion_status`: `PENDIENTE | CONVERTIDO | NO_APLICA | ERROR`.
- `analysis_status`: `PENDIENTE | EN_ANALISIS | ANALIZADO | ERROR`
  (el Collector deja `PENDIENTE`).

Reintentos: si falla la descarga -> `download_status=ERROR`, `retry_count++`,
`last_attempt_at`, `error_message`; se reintenta luego. Si el MSG ya esta
`DESCARGADO` y falla la conversion -> se reintenta solo la conversion (no se
vuelve a descargar).

## 6. Tratamiento por tipo de adjunto

### fileAttachment con nombre `.msg`
1. descargar bytes; 2. `.part`; 3. validar tamano; 4. cerrar; 5. rename a `.msg`;
6. SHA256; 7. preservar original; 8. intentar EML; 9. registrar SQLite.

### itemAttachment (mensaje)
Usar `/$value` para obtener MIME y guardarlo como `.eml`. No guardarlo como `.msg`.

## 7. Deduplicacion e integridad
- Dedup por `UNIQUE(message_id, attachment_id)`.
- SHA256 repetido: no se elimina el nuevo reporte; se marca "muestra ya observada"
  conservando su fila (trazabilidad del nuevo mensaje).
- Nunca se usa el estado del buzon para deduplicar.

## 8. Seguridad aplicada en el diseno (resumen)
- Graph solo GET; sin helpers de escritura.
- Contenido del correo tratado como UNTRUSTED: sanitizacion de nombres, SQL
  parametrizado, sin shell/eval.
- TLS ON y timeouts en todas las requests; reintentos acotados.
- Permisos fs 750/640 (via `umask 027` en `ejecutar.sh`).
- Secretos solo en `.env`; nunca en logs/metadata; Analyzer sin credenciales.
- Anti path traversal validando ruta dentro del directorio base.
- Limites de tamano; 0 bytes = error; `.part` + rename atomico.
- MSG original inmutable; fallar de forma segura conservando evidencia.

## 9. Contrato del Analyzer (futuro, no implementar)
`analizar_muestra(entrada) -> resultado` con entrada/salida JSON (ver steering
`agentcore`). Prompt base en `prompts/phishing_analysis.md`.
