# Tareas - phishing-msg-collector

Plan de implementacion en fases pequenas y ordenadas. Cada tarea indica cuando se
considera completa. Se implementa en orden. La seguridad es prioridad absoluta.

Referencias: `requirements.md` (REQ-xxx) y `design.md`.

---

## Fase 1 - Estructura y configuracion
- [ ] 1.1 Crear estructura de carpetas: `data/`, `downloads/pending/`, `logs/`,
  `prompts/`.
- [ ] 1.2 Crear `.env.example` documentado en espanol, `.gitignore`,
  `requirements.txt`.
- [ ] 1.3 Implementar `config.py`: carga y valida `.env`; expone todos los
  parametros; no expone secretos.
- **Completa cuando:** `config.py` carga un `.env` valido, falla claro si faltan
  obligatorios, y `requirements.txt` contiene solo `msal/requests/python-dotenv/
  extract-msg`. (REQ-064, REQ-065, REQ-066)

## Fase 2 - SQLite
- [ ] 2.1 Implementar `database.py`: `inicializar_base()` crea
  `processed_attachments`, `app_state`, indices y activa WAL.
- [ ] 2.2 Funciones de estado y dedup (parametrizadas).
- **Completa cuando:** se crea `data/phishing_collector.db` con las tablas, la
  restriccion `UNIQUE(message_id, attachment_id)` y los indices; todas las
  consultas son parametrizadas. (REQ-017, REQ-018, REQ-019, REQ-021, REQ-022)

## Fase 3 - Autenticacion Graph
- [ ] 3.1 Implementar `obtener_token()` con MSAL client credentials.
- [ ] 3.2 Helper `_get()` con TLS ON, timeout, manejo 401/403/404/429/5xx,
  `Retry-After`, reintentos acotados, sin loops infinitos, sin loggear secretos.
- **Completa cuando:** con credenciales validas se obtiene token; el helper
  maneja los codigos indicados. (REQ-001, REQ-003, REQ-047..REQ-052)

## Fase 4 - Consulta de mensajes
- [ ] 4.1 `buscar_mensajes(desde, hasta)` con `$filter` por `receivedDateTime`,
  `$select`, `$orderby`, `$top` y paginacion `@odata.nextLink`.
- **Completa cuando:** devuelve todos los mensajes de la ventana, paginando.
  (REQ-002, REQ-004, REQ-005, REQ-006)

## Fase 5 - Adjuntos
- [ ] 5.1 `obtener_adjuntos(message_id)`.
- [ ] 5.2 Deteccion `.msg` case-insensitive y de `itemAttachment`.
- **Completa cuando:** se listan adjuntos y se clasifican fileAttachment `.msg`
  e itemAttachment. (REQ-007, REQ-008, REQ-009, REQ-010)

## Fase 6 - Descarga segura del MSG
- [ ] 6.1 `sample.guardar_msg()`: `.part` -> validar tamano (>0 y <= limite) ->
  rename atomico a `.msg`.
- [ ] 6.2 Validacion de directorio base (anti path traversal).
- **Completa cuando:** un MSG se descarga a `.part` y solo pasa a `.msg` si es
  valido; 0 bytes o exceso de tamano = error. (REQ-013, REQ-041, REQ-047,
  seccion limites)

## Fase 7 - EML derivado
- [ ] 7.1 `convertir_msg_a_eml()` con `extract-msg` + `email`.
- [ ] 7.2 itemAttachment: guardar MIME `/$value` como `.eml`.
- [ ] 7.3 Si falla la conversion: conservar MSG, `conversion_status=ERROR`.
- **Completa cuando:** un `.msg` valido produce `.eml`; un itemAttachment produce
  `.eml` desde MIME; un fallo no borra evidencia. (REQ-011, REQ-012, REQ-015)

## Fase 8 - Deduplicacion
- [ ] 8.1 `attachment_existe()` por `message_id+attachment_id`.
- [ ] 8.2 SHA256 repetido: registrar "muestra ya observada" sin perder trazas.
- **Completa cuando:** un adjunto ya visto no se reprocesa (salvo `--force`); un
  hash repetido se registra sin borrar el nuevo. (REQ-018, REQ-020, REQ-046)

## Fase 9 - metadata / hashes / READY
- [ ] 9.1 `calcular_sha256()` (MSG y EML).
- [ ] 9.2 `crear_metadata()` (esquema del brief, sin secretos).
- [ ] 9.3 `crear_hashes_txt()`.
- [ ] 9.4 `crear_ready()` solo cuando todo esta consistente.
- **Completa cuando:** cada muestra valida tiene MSG, (EML si aplica),
  metadata.json, hashes.txt y READY; nunca READY incompleto. (REQ-014, REQ-016,
  REQ-034, REQ-035, REQ-036, REQ-033)

## Fase 10 - CLI
- [ ] 10.1 `argparse` con default/`--today`/`--hours`/`--date`/`--dry-run`/
  `--force`/`--status`/`--history`/`--debug`/`--help`.
- [ ] 10.2 `--dry-run` sin efectos; `--status` y `--history` desde SQLite.
- **Completa cuando:** todos los modos funcionan segun el brief; `--dry-run` no
  produce efectos. (REQ-057..REQ-063, REQ-058, REQ-059)

## Fase 11 - cron / flock
- [ ] 11.1 Crear `ejecutar.sh` (`umask 027`, activar venv, `python main.py`).
- [ ] 11.2 Documentar `chmod +x` y la linea de cron con `flock`.
- **Completa cuando:** existe `ejecutar.sh` ejecutable y la linea de cron con
  `flock` documentada. (REQ-027, REQ-028, REQ-029)

## Fase 12 - Logging y errores
- [ ] 12.1 Logging en espanol a `logs/phishing_collector.log` y consola.
- [ ] 12.2 Sin secretos en logs; sin tracebacks salvo `--debug`.
- [ ] 12.3 Politica de checkpoint: actualizar `last_successful_check` solo si
  Graph respondio; reintentos de errores parciales.
- **Completa cuando:** los logs cumplen las reglas; el checkpoint respeta
  REQ-024, REQ-025, REQ-026. (REQ-053, REQ-054, REQ-055, REQ-056)

## Fase 13 - Documentacion
- [ ] 13.1 `README.md` en espanol para no-programadores (instalacion, .venv,
  .env, Graph/Mail.Read/Admin Consent, dry-run, cron, status, troubleshooting).
- [ ] 13.2 `prompts/phishing_analysis.md` con encabezado anti prompt-injection.
- **Completa cuando:** README cubre los 33 puntos del brief y el prompt declara
  la evidencia como no confiable. (REQ-065, seccion 59 y 61 del brief)

## Fase 14 - Pruebas
- [ ] 14.1 Tests de: sanitizacion de filename, path traversal, deteccion `.msg`,
  hashes, creacion de base, `UNIQUE`, dedup, metadata, estado READY, reintentos.
- [ ] 14.2 Mocking simple de Graph (sin buzon real).
- **Completa cuando:** las pruebas pasan sin depender del buzon real.
  (seccion 67 del brief)

## Fase 15 - Contrato Analyzer / AgentCore
- [ ] 15.1 Documentar el contrato `analizar_muestra(entrada) -> resultado` y el
  formato JSON de entrada/salida (en steering `agentcore` y/o prompt).
- [ ] 15.2 Verificar desacople: la logica no depende de cron/CLI/SQLite/Graph/
  rutas absolutas.
- **Completa cuando:** una muestra READY es autosuficiente para el Analyzer y el
  contrato esta documentado. (REQ-067, REQ-068, REQ-069, REQ-070)

---

## Definicion de "hecho" global
- Todos los REQ cubiertos y verificables.
- Instalable en Linux: `venv` -> `pip install -r requirements.txt` -> `.env`.
- `python main.py --dry-run` funciona sin efectos.
- `python main.py` recolecta y deja muestras READY.
- cron cada 5 min con flock documentado.
- Documentacion completa en espanol.
- Seguridad verificada (checklist del steering `security` y seccion 71 del brief).
