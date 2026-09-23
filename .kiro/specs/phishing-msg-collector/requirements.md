# Requerimientos - phishing-msg-collector

Requerimientos formales, numerados y verificables. Cada uno incluye un criterio
de aceptacion (como se comprueba que esta cumplido). La seguridad tiene prioridad
absoluta (ver steering `security`).

## Glosario

- **Collector**: esta aplicacion. Recolecta y prepara evidencia. No analiza.
- **Analyzer**: componente futuro (OpenCode / AgentCore) que analiza muestras.
- **Muestra (sample)**: carpeta con la evidencia de un adjunto (.msg, .eml,
  metadata.json, hashes.txt, READY).
- **Correo contenedor**: el correo recibido en el buzon de phishing.
- **Correo sospechoso**: el correo original reportado, incrustado como adjunto.

---

## Autenticacion y acceso a Graph

### REQ-001 - Autenticacion Client Credentials
El sistema DEBE autenticarse contra Microsoft Graph usando OAuth 2.0 Client
Credentials con `msal.ConfidentialClientApplication` y scope
`https://graph.microsoft.com/.default`.
**Aceptacion:** con credenciales validas se obtiene un access token; sin login
interactivo, browser ni device code.

### REQ-002 - Lectura del buzon configurado
El sistema DEBE leer el buzon indicado en `MAILBOX` (por defecto
`phishing@itau.cl`).
**Aceptacion:** las consultas se dirigen a `/users/{MAILBOX}/...`.

### REQ-003 - Graph estrictamente READ ONLY
El sistema DEBE usar exclusivamente operaciones `GET` contra el buzon y NO DEBE
ejecutar POST/PATCH/PUT/DELETE contra el buzon.
**Aceptacion:** revision de codigo: el cliente Graph solo implementa GET; no hay
llamadas de escritura. El buzon queda identico antes y despues.

### REQ-004 - Ventana temporal
El sistema DEBE consultar solo los mensajes dentro de una ventana temporal
definida.
**Aceptacion:** la consulta incluye filtro por `receivedDateTime`.

### REQ-005 - Paginacion
El sistema DEBE seguir la paginacion mediante `@odata.nextLink` hasta agotar los
resultados.
**Aceptacion:** con mas de una pagina de resultados, se recuperan todos.

### REQ-006 - Reduccion de campos
El sistema DEBE usar `$select` para pedir solo los campos necesarios.
**Aceptacion:** la query incluye `$select` con la lista minima de campos.

---

## Deteccion de adjuntos

### REQ-007 - Deteccion de attachments
El sistema DEBE detectar los adjuntos de cada correo.
**Aceptacion:** se listan los attachments via Graph.

### REQ-008 - Deteccion .msg case-insensitive
El sistema DEBE detectar archivos `.msg` sin distinguir mayusculas/minusculas.
**Aceptacion:** `.MSG`, `.Msg`, `.msg` se detectan igual.

### REQ-009 - Soporte fileAttachment
El sistema DEBE soportar adjuntos `#microsoft.graph.fileAttachment`.
**Aceptacion:** se descarga su contenido binario.

### REQ-010 - Deteccion itemAttachment
El sistema DEBE detectar adjuntos `#microsoft.graph.itemAttachment`.
**Aceptacion:** se identifican y se procesan segun REQ-011.

### REQ-011 - MIME de itemAttachment como .eml
Cuando Graph entregue el contenido MIME mediante `/$value`, el sistema DEBE
guardarlo como `.eml`.
**Aceptacion:** el itemAttachment se guarda con extension `.eml`.

### REQ-012 - No falsificar formatos
El sistema NO DEBE cambiar solo la extension para simular un formato.
**Aceptacion:** un MIME nunca se guarda como `.msg`; un `.msg` no se renombra a
`.eml` sin conversion real.

---

## Preservacion de evidencia y hashes

### REQ-013 - Preservar MSG original
El sistema DEBE preservar el `.msg` original sin modificarlo.
**Aceptacion:** tras guardarlo, el archivo no se reescribe ni altera.

### REQ-014 - SHA256 del MSG
El sistema DEBE calcular el SHA256 del `.msg`.
**Aceptacion:** el hash aparece en SQLite, metadata.json y hashes.txt.

### REQ-015 - EML derivado
El sistema DEBE generar un `.eml` derivado cuando sea tecnicamente posible.
**Aceptacion:** si la conversion es posible, existe el `.eml`.

### REQ-016 - SHA256 del EML
El sistema DEBE calcular el SHA256 del `.eml` cuando exista.
**Aceptacion:** el hash aparece en SQLite, metadata.json y hashes.txt.

---

## Persistencia y deduplicacion

### REQ-017 - Persistencia en SQLite
El sistema DEBE usar SQLite (`sqlite3`, sin ORM) para la persistencia.
**Aceptacion:** existe `data/phishing_collector.db` con las tablas requeridas.

### REQ-018 - Deduplicacion por message_id + attachment_id
El sistema DEBE deduplicar por la combinacion `message_id + attachment_id`.
**Aceptacion:** un mismo adjunto no se procesa dos veces.

### REQ-019 - Restriccion UNIQUE
La tabla `processed_attachments` DEBE tener `UNIQUE(message_id, attachment_id)`.
**Aceptacion:** insertar un duplicado viola la restriccion.

### REQ-020 - SHA256 repetido con trazabilidad
El sistema DEBE detectar SHA256 repetidos sin perder la trazabilidad del nuevo
reporte (no se elimina el nuevo registro).
**Aceptacion:** un segundo reporte con el mismo hash queda registrado como
"muestra ya observada" y se conserva su fila.

### REQ-021 - Estado por muestra
El sistema DEBE mantener el estado de descarga, conversion, analisis futuro,
errores y reintentos.
**Aceptacion:** existen columnas `download_status`, `conversion_status`,
`analysis_status`, `error_message`, `retry_count`.

---

## Ventana de consulta y tolerancia a fallos

### REQ-022 - last_successful_check
El sistema DEBE guardar `last_successful_check` en `app_state`.
**Aceptacion:** tras una corrida exitosa se actualiza el valor.

### REQ-023 - Lookback configurable
El sistema DEBE aplicar un `LOOKBACK_MINUTES` configurable al calcular la ventana.
**Aceptacion:** la consulta arranca en `last_successful_check - LOOKBACK_MINUTES`.

### REQ-024 - No perder correos ante fallo de cron
El sistema NO DEBE perder correos cuando una ejecucion de cron falle.
**Aceptacion:** si una corrida no actualiza `last_successful_check`, la siguiente
cubre el rango pendiente.

### REQ-025 - No avanzar el checkpoint si Graph falla del todo
El sistema NO DEBE actualizar `last_successful_check` si Graph falla por completo.
**Aceptacion:** ante fallo total de autenticacion/consulta, el valor no cambia.

### REQ-026 - Reintentar errores parciales
El sistema DEBE reintentar los errores parciales en corridas posteriores.
**Aceptacion:** una muestra con `download_status=ERROR` se reintenta luego.

---

## Ejecucion Linux / cron

### REQ-027 - Cron cada 5 minutos
El sistema DEBE poder ejecutarse con cron cada 5 minutos.
**Aceptacion:** existe `ejecutar.sh` y la linea de cron documentada.

### REQ-028 - flock
El sistema DEBE poder usar `flock` para el control de concurrencia.
**Aceptacion:** la linea de cron usa `flock` con un lockfile.

### REQ-029 - Sin ejecuciones concurrentes
El sistema NO DEBE permitir dos ejecuciones simultaneas.
**Aceptacion:** con `flock -n`, una segunda corrida no arranca si otra esta activa.

### REQ-030 - No depender de Outlook
El sistema NO DEBE depender de Outlook.
**Aceptacion:** la conversion usa `extract-msg`, no COM/win32.

### REQ-031 - No depender de Windows
El sistema NO DEBE depender de Windows.
**Aceptacion:** no se usan APIs exclusivas de Windows.

### REQ-032 - Funcionar en Linux
El sistema DEBE funcionar completamente en Linux.
**Aceptacion:** instalacion y ejecucion documentadas para Linux.

---

## Artefactos de la muestra

### REQ-033 - Conservar evidencia original
El sistema DEBE conservar la evidencia original.
**Aceptacion:** el `.msg` no se borra ante errores posteriores.

### REQ-034 - metadata.json
El sistema DEBE crear `metadata.json` por muestra.
**Aceptacion:** el archivo existe y respeta el esquema definido (sin secretos).

### REQ-035 - hashes.txt
El sistema DEBE crear `hashes.txt` por muestra.
**Aceptacion:** contiene SHA256 del MSG y del EML (si existe).

### REQ-036 - Marcador READY condicionado
El sistema DEBE crear el archivo `READY` solo cuando la muestra este preparada
(descarga ok, hash calculado, metadata creada, SQLite actualizado, conversion
finalizada o registrada como error/no aplica).
**Aceptacion:** no existe `READY` si falta algun paso.

### REQ-037 - El Analyzer solo procesa READY
El diseno DEBE establecer que el Analyzer solo analiza muestras con `READY`.
**Aceptacion:** documentado en steering y prompt; el `READY` es la senal.

---

## Nombres de archivos (seguridad)

### REQ-038 - Subject como nombre visible
El `.msg` y el `.eml` DEBEN usar el Subject del correo sospechoso como nombre
visible.
**Aceptacion:** el nombre base coincide con el Subject sanitizado.

### REQ-039 - Mismo nombre base MSG/EML
El `.msg` y el `.eml` DEBEN compartir exactamente el mismo nombre base.
**Aceptacion:** difieren solo en la extension.

### REQ-040 - Sanitizar Subject
El sistema DEBE sanitizar el Subject (reemplazar `/ \ : * ? " < > |` por `_`;
eliminar NULL bytes, control chars, saltos de linea y tabs).
**Aceptacion:** un Subject con esos caracteres produce un nombre seguro.

### REQ-041 - Impedir path traversal
El sistema DEBE impedir path traversal en los nombres/rutas.
**Aceptacion:** un Subject con `../` no escapa del directorio base; la ruta final
se valida dentro del directorio de descargas.

### REQ-042 - Limitar largo del filename
El sistema DEBE limitar el largo del nombre a `MAX_FILENAME_LENGTH`.
**Aceptacion:** un Subject muy largo se trunca; MSG y EML usan el mismo truncado.

### REQ-043 - Sin asunto
Si no existe Subject, el sistema DEBE usar `sin_asunto`.
**Aceptacion:** un correo sin subject genera archivos `sin_asunto.*`.

### REQ-044 - Carpeta unica por muestra
El sistema DEBE usar una carpeta unica por muestra (`downloads/pending/SAMPLE_ID/`).
**Aceptacion:** cada muestra vive en su propia carpeta.

### REQ-045 - sample_id tecnico en carpeta, nombre humano en archivos
El sistema DEBE usar el `sample_id` tecnico como nombre de carpeta y el Subject
como nombre humano de los archivos.
**Aceptacion:** carpeta = sample_id; archivos = Subject sanitizado.

### REQ-046 - Filename no es dedup
El sistema NO DEBE usar el filename como mecanismo de deduplicacion.
**Aceptacion:** dos muestras con el mismo Subject coexisten en carpetas distintas.

---

## HTTP y resiliencia

### REQ-047 - Timeout HTTP
Toda request HTTP DEBE tener timeout explicito.
**Aceptacion:** no hay requests sin timeout.

### REQ-048 - Manejo de 429
El sistema DEBE manejar HTTP 429 (throttling).
**Aceptacion:** ante 429 no falla abruptamente; reintenta.

### REQ-049 - Respetar Retry-After
Ante 429, el sistema DEBE respetar la cabecera `Retry-After`.
**Aceptacion:** la espera usa el valor de `Retry-After`.

### REQ-050 - Retry para 5xx
El sistema DEBE reintentar ante 500/502/503/504.
**Aceptacion:** esos codigos disparan reintento acotado.

### REQ-051 - Maximo de retries
El sistema DEBE aplicar un maximo de reintentos.
**Aceptacion:** superado el maximo, se registra error y se detiene el reintento.

### REQ-052 - Sin loops infinitos
El sistema NO DEBE crear loops infinitos de reintento.
**Aceptacion:** todo bucle de reintento termina.

---

## Logging y experiencia de usuario

### REQ-053 - Logging en espanol
El logging DEBE estar en espanol.
**Aceptacion:** los mensajes de log estan en espanol.

### REQ-054 - Errores entendibles
Los errores DEBEN ser entendibles para un usuario no tecnico.
**Aceptacion:** mensajes claros, sin jerga innecesaria.

### REQ-055 - Sin tracebacks por defecto
El sistema NO DEBE mostrar tracebacks en operacion normal.
**Aceptacion:** sin `--debug`, no se imprimen tracebacks al usuario.

### REQ-056 - Modo --debug
El sistema DEBE ofrecer `--debug` con detalle tecnico.
**Aceptacion:** con `--debug` se ve mas detalle (incluidos tracebacks).

### REQ-057 - Modo --dry-run
El sistema DEBE ofrecer `--dry-run` sin efectos (ver seccion dry-run del brief).
**Aceptacion:** no descarga, no crea EML, no marca procesado, no toca Graph.

### REQ-058 - Modo --status
El sistema DEBE ofrecer `--status` (estado desde SQLite).
**Aceptacion:** muestra ultima revision, totales por estado, etc.

### REQ-059 - Modo --history
El sistema DEBE ofrecer `--history` (ultimos registros).
**Aceptacion:** lista fecha, asunto y estado de los ultimos.

### REQ-060 - Modo --today
El sistema DEBE ofrecer `--today` (revisar el dia actual).
**Aceptacion:** la ventana cubre el dia actual.

### REQ-061 - Modo --hours N
El sistema DEBE ofrecer `--hours N` (ultimas N horas).
**Aceptacion:** la ventana cubre N horas hacia atras.

### REQ-062 - Modo --date YYYY-MM-DD
El sistema DEBE ofrecer `--date` (un dia especifico).
**Aceptacion:** la ventana cubre esa fecha.

### REQ-063 - Modo --force
El sistema DEBE ofrecer `--force` (reprocesar aunque exista en SQLite).
**Aceptacion:** con `--force`, un adjunto ya visto se vuelve a procesar.

---

## Configuracion y mantenibilidad

### REQ-064 - Configuracion en .env
Toda la configuracion operacional DEBE vivir en `.env`.
**Aceptacion:** no hay valores operacionales hardcodeados.

### REQ-065 - Documentacion en espanol
Toda la documentacion DEBE estar en espanol.
**Aceptacion:** steering, specs, README y comentarios importantes en espanol.

### REQ-066 - Operar sin editar Python
La operacion habitual NO DEBE requerir editar codigo Python.
**Aceptacion:** cambiar ventana, buzon, limites, etc. se hace via `.env`/CLI.

---

## Separacion y futuro

### REQ-067 - Preparado para OpenCode
La arquitectura DEBE permitir integrar OpenCode consumiendo solo `sample_path`.
**Aceptacion:** una muestra READY es autosuficiente (no requiere Graph).

### REQ-068 - Preparado para AgentCore
La arquitectura DEBE permitir migrar a AgentCore sin reescribir la logica central.
**Aceptacion:** logica desacoplada de cron/CLI/SQLite/Graph/rutas absolutas.

### REQ-069 - Separacion Collector / Analyzer
El sistema DEBE separar Collector y Analyzer.
**Aceptacion:** el Collector no analiza; deja `analysis_status=PENDIENTE`.

### REQ-070 - Analyzer sin Graph
El Analyzer futuro NO DEBE necesitar Microsoft Graph ni credenciales.
**Aceptacion:** el contrato del Analyzer solo recibe rutas/JSON de la muestra.
