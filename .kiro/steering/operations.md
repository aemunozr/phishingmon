---
inclusion: always
---

# Operaciones

Guia operacional para ejecutar y mantener el Collector en Linux. Pensada para
personas de SOC / CSIRT con conocimientos basicos de Linux.

## Entorno (Linux + .venv)

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# editar .env con las credenciales (permisos recomendados: chmod 600 .env)
```

## Ejecucion manual

```
python main.py --dry-run     # prueba sin efectos: muestra que descargaria
python main.py               # ejecucion normal
python main.py --status      # estado desde SQLite
python main.py --history     # ultimos registros
```

## cron + flock

Se ejecuta cada 5 minutos. `flock` evita ejecuciones concurrentes (si una corrida
tarda mas de 5 minutos, la siguiente no se solapa).

Hacer ejecutable el lanzador:

```
chmod +x ejecutar.sh
```

Linea de cron (`crontab -e`):

```
*/5 * * * * /usr/bin/flock -n /tmp/phishing-msg-collector.lock /opt/phishing-msg-collector/ejecutar.sh >> /opt/phishing-msg-collector/logs/cron.log 2>&1
```

No implementar scheduler en Python: la periodicidad la maneja cron.

## Ventana de consulta (no perder correos)

- No se consulta solo "ahora - 5 minutos" (podria perder mensajes).
- Se usa `last_successful_check` guardado en SQLite, menos `LOOKBACK_MINUTES`
  de margen. Ejemplo: ultima exitosa 16:00, ejecucion 16:05, se consulta desde 15:50.
- Los mensajes repetidos se filtran por SQLite (dedup por message_id+attachment_id).
- Si Graph falla completamente, **no** se actualiza `last_successful_check`
  (asi la proxima corrida reintenta ese rango y no se pierden correos).

## Primera ejecucion

Si no existe `last_successful_check`, se usa `FIRST_RUN_HOURS` (por defecto 24):
la primera corrida revisa las ultimas 24 horas.

## SQLite

- Base en `data/phishing_collector.db`.
- Modo WAL (`PRAGMA journal_mode=WAL`) y `timeout=30` en la conexion.
- Ademas se usa `flock` a nivel de proceso para serializar ejecuciones.

## Logs

- `logs/phishing_collector.log`: log de la aplicacion (en espanol).
- `logs/cron.log`: salida de cron.
- Nunca se loggean secretos, tokens ni cabeceras de autorizacion.
- Nivel configurable con `LOG_LEVEL` (INFO por defecto; `--debug` sube el detalle).

## Backups y recuperacion

- Respaldar periodicamente: `data/` (base SQLite) y `downloads/` (evidencia).
- Con WAL, respaldar preferentemente sin ejecuciones concurrentes o usando la API
  de backup de SQLite; como minimo copiar `.db`, `.db-wal` y `.db-shm` juntos.
- Recuperacion: restaurar `data/` y `downloads/`. El estado en SQLite permite
  retomar reintentos de descargas/conversiones pendientes o con error.

## Reinicio del servidor

- No hay demonio propio: cron reanuda la ejecucion tras el reinicio.
- Al volver, la primera corrida usa `last_successful_check` y no pierde el rango.

## Troubleshooting

- **HTTP 401**: token invalido o credenciales incorrectas. Revisar
  `TENANT_ID/CLIENT_ID/CLIENT_SECRET`.
- **HTTP 403**: falta permiso `Mail.Read` o Admin Consent. Revisar el registro
  de la app en Entra ID.
- **HTTP 404**: `MAILBOX` incorrecto o buzon inexistente.
- **HTTP 429**: throttling; la app respeta `Retry-After` y reintenta.
- **Errores SQLite** (`database is locked`): normalmente por concurrencia;
  `flock` + WAL + `timeout` lo mitigan. Verificar que no haya procesos colgados.
- **Error de conversion MSG->EML**: se conserva el MSG y se marca
  `conversion_status=ERROR`; se puede reintentar sin volver a descargar.

## Modo dry-run

`--dry-run` autentica, consulta mensajes, detecta adjuntos y consulta SQLite,
mostrando que descargaria. No descarga MSG, no crea EML, no modifica SQLite como
procesado y no modifica Graph.
