# phishing-msg-collector

Herramienta que **recolecta y preserva** correos de phishing reportados al buzon
`phishing@itau.cl`, para que despues otro componente (un agente de IA) los
analice. Se ejecuta en **Linux** y se conecta a **Microsoft Graph en modo SOLO
LECTURA**.

---

## 1. Que hace

- Se conecta al buzon en modo **solo lectura** (permiso `Mail.Read`).
- Busca los correos dentro de una ventana de tiempo.
- Detecta los adjuntos `.msg` (el correo sospechoso reportado).
- **Descarga y preserva el `.msg` original** como evidencia.
- Genera un `.eml` derivado cuando es posible.
- Calcula hashes SHA256 y crea `metadata.json`, `hashes.txt` y un marcador `READY`.
- Lleva un registro en SQLite para no repetir trabajo.

## 2. Que NO hace

- **No decide** si un correo es malicioso (eso lo hara el Analyzer despues).
- **No modifica** el buzon (no marca leido, no mueve, no borra, no responde).
- **No ejecuta** ni abre el contenido de los adjuntos.
- No depende de Outlook ni de Windows.

> Importante: las muestras pueden contener contenido malicioso. Se preservan y se
> analizan, pero **nunca se ejecutan**.

## 3. Arquitectura (resumen)

```
Microsoft 365 -> Graph (READ ONLY) -> Collector -> SQLite + Filesystem
                                                     |
                                            downloads/pending/SAMPLE_ID/
                                              .msg  .eml  metadata.json
                                              hashes.txt  READY
                                                     |
                                        (futuro) OpenCode / AgentCore
```

El **Collector** (esta herramienta) recolecta y prepara. El **Analyzer** (futuro)
analiza. Estan separados: el Analyzer solo lee carpetas `READY`, sin tocar Graph.

## 4. Requisitos

- Linux con **Python 3.9+**.
- Una aplicacion registrada en Microsoft Entra ID con permiso `Mail.Read`.

## 5. Instalacion

### 5.1 Crear el entorno virtual (.venv)

```
python3 -m venv .venv
source .venv/bin/activate
```

### 5.2 Instalar dependencias

```
pip install -r requirements.txt
```

## 6. Configurar Microsoft Graph (una sola vez)

Un administrador de Microsoft 365 debe:

1. Entrar a https://portal.azure.com
2. **Microsoft Entra ID** -> **App registrations** -> **New registration**.
3. Copiar **Application (client) ID** y **Directory (tenant) ID**.
4. **Certificates & secrets** -> **New client secret** -> copiar el **Value**
   (solo se ve una vez).
5. **API permissions** -> **Add a permission** -> **Microsoft Graph** ->
   **Application permissions** -> agregar **`Mail.Read`**.
   - Usar `Mail.Read` (solo lectura). **No** usar `Mail.ReadWrite`.
6. **Grant admin consent** para aprobar el permiso.

## 7. Configurar el archivo .env

```
cp .env.example .env
nano .env
chmod 600 .env
```

Rellena `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET` y `MAILBOX`. Los demas valores
tienen valores por defecto razonables (ver comentarios en `.env.example`).

## 8. Primera ejecucion

La primera vez (sin historial) se revisan las ultimas `FIRST_RUN_HOURS` horas
(por defecto 24).

### 8.1 Prueba sin efectos (recomendado primero)

```
python main.py --dry-run
```

Autentica, consulta y muestra que descargaria, **sin** descargar ni modificar nada.

### 8.2 Ejecucion normal

```
python main.py
```

Los resultados quedan en `downloads/pending/` (una carpeta por muestra).

## 9. Otros modos (CLI)

```
python main.py                 # ventana automatica (desde la ultima revision)
python main.py --today         # correos de hoy
python main.py --hours 24      # ultimas 24 horas
python main.py --date 2026-09-22   # un dia especifico
python main.py --dry-run       # prueba sin efectos
python main.py --force         # reprocesa aunque ya este en la base
python main.py --status        # estado (desde SQLite)
python main.py --history       # ultimos registros
python main.py --debug         # mas detalle tecnico
python main.py --help          # ayuda
```

## 10. Automatizar con cron (cada 5 minutos)

Se incluye `ejecutar.sh`, que activa el `.venv` y ejecuta la herramienta con
`umask 027` (permisos seguros).

```
chmod +x ejecutar.sh
crontab -e
```

Agregar (ajustar la ruta del proyecto):

```
*/5 * * * * /usr/bin/flock -n /tmp/phishing-msg-collector.lock /opt/phishing-msg-collector/ejecutar.sh >> /opt/phishing-msg-collector/logs/cron.log 2>&1
```

- `*/5 * * * *`: cada 5 minutos.
- `flock`: evita que dos ejecuciones corran a la vez.

La herramienta usa `last_successful_check` + `LOOKBACK_MINUTES` para no perder
correos si una corrida falla o se atrasa.

## 11. Directorios y archivos

```
data/phishing_collector.db                     Base SQLite (estado y deduplicacion)
downloads/pending/<Asunto>__<SAMPLE_ID>/        Una carpeta por muestra
  <Asunto>.msg                                  Evidencia original (no se modifica)
  <Asunto>.eml                                  Derivado (si se pudo convertir)
  metadata.json                                 Metadatos de la muestra (sin secretos)
  hashes.txt                                    SHA256 del MSG y del EML
  READY                                         Marca que la muestra esta lista para analisis
logs/phishing_collector.log                     Log de la aplicacion
logs/cron.log                                   Salida de cron
```

- La **carpeta** de cada muestra se llama `<Asunto>__<SAMPLE_ID>`: el asunto la
  hace legible y el `SAMPLE_ID` (formato `YYYYMMDD_HHMMSS_HASHCORTO`) garantiza
  que sea unica, aunque dos correos tengan el mismo asunto.
- Los **archivos** usan el **Asunto** del correo sospechoso como nombre (sanitizado).
- En **Windows** se soportan rutas largas automaticamente (prefijo `\\?\`), asi
  que los nombres largos de carpeta/archivo no fallan por el limite de 260
  caracteres. En Linux no aplica.

## 12. Ver estado

```
python main.py --status
python main.py --history
```

## 13. Errores comunes

- **401 (no autorizado):** credenciales incorrectas. Revisa
  `TENANT_ID/CLIENT_ID/CLIENT_SECRET`.
- **403 (prohibido):** falta el permiso `Mail.Read` o el Admin Consent.
- **404 (no encontrado):** `MAILBOX` incorrecto.
- **429 (throttling):** demasiadas solicitudes; la herramienta espera y reintenta.
- **SQLite `database is locked`:** normalmente por concurrencia; `flock` + WAL lo
  mitigan.
- **Error de conversion MSG->EML:** se conserva el `.msg` y se marca el error; se
  puede reintentar sin volver a descargar.

## 14. Backup

Respaldar periodicamente `data/` (base SQLite) y `downloads/` (evidencia). Con
WAL, copiar juntos los archivos `.db`, `.db-wal` y `.db-shm`.

## 15. Seguridad

- Solo lectura contra Microsoft Graph (permiso `Mail.Read`).
- El contenido del correo se trata como **evidencia no confiable**; nunca se
  ejecuta.
- Secretos solo en `.env` (nunca en logs ni en `metadata.json`).
- Permisos de archivos restringidos (`umask 027`).
- Ver detalle en `.kiro/steering/security.md`.

## 16. Futuro: analisis con IA

Cuando exista el Analyzer (OpenCode / Amazon Bedrock AgentCore), recibira solo la
ruta de una muestra `READY` (por ejemplo
`downloads/pending/Convenio Banco Itau...__20260922_160501_9fd472ab/`) y
producira su analisis. No
necesitara acceso a Microsoft Graph ni credenciales. El prompt base esta en
`prompts/phishing_analysis.md`.
