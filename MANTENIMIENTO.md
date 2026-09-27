# Guia de mantenimiento (para SOC / CSIRT)

Esta guia esta pensada para quien mantiene la herramienta **sin saber Python**.
Casi todo lo que vas a necesitar cambiar se hace editando el archivo `.env`, sin
tocar el codigo.

Recuerda la regla base del proyecto: **esta herramienta es SOLO LECTURA**. Nunca
modifica el buzon de Microsoft 365. No borra, no marca leido, no responde.


## 1. Lo primero que debes saber

- La **configuracion** vive en el archivo `.env` (no en el codigo).
- El **codigo** son los archivos `.py`. La mayoria NO hay que tocarlos.
- La **evidencia** descargada queda en `downloads/pending/`.
- El **estado** (que ya se proceso) vive en `data/phishing_collector.db` (SQLite).
- Los **registros** de lo que hizo el programa estan en `logs/`.


## 2. "Quiero hacer X" -> "Hago esto"

Todo lo de esta tabla se cambia en el archivo `.env`. Despues de editar `.env`,
la proxima ejecucion ya toma el nuevo valor (no hay que reiniciar nada).

| Quiero...                                   | Cambio esto en `.env`         | Ejemplo                         |
|---------------------------------------------|-------------------------------|---------------------------------|
| Cambiar el buzon que se revisa              | `MAILBOX`                     | `MAILBOX=phishing@empresa.cl`   |
| Cambiar las credenciales de Microsoft       | `TENANT_ID` `CLIENT_ID` `CLIENT_SECRET` | (los da el equipo de Azure) |
| Revisar mas horas en la primera corrida     | `FIRST_RUN_HOURS`             | `FIRST_RUN_HOURS=48`            |
| Dar mas margen para no perder correos       | `LOOKBACK_MINUTES`            | `LOOKBACK_MINUTES=15`           |
| Permitir adjuntos mas grandes               | `MAX_ATTACHMENT_SIZE_MB`      | `MAX_ATTACHMENT_SIZE_MB=200`    |
| Cambiar donde se guardan las muestras       | `DOWNLOAD_DIR`                | `DOWNLOAD_DIR=downloads`        |
| Ver mas detalle en los logs                 | `LOG_LEVEL`                   | `LOG_LEVEL=DEBUG`               |

> **Nota:** `KEEP_ORIGINAL_MSG` es solo informativo. El `.msg` original es
> evidencia y **nunca** se borra, aunque lo pongas en `false`.

Para cambiar **cada cuanto corre** el programa NO se toca el `.env`: se edita la
linea de `cron` (ver seccion 4).


## 3. Comandos del dia a dia

Siempre con el entorno virtual activado:

```
source .venv/bin/activate
```

| Comando                   | Que hace                                                       |
|---------------------------|----------------------------------------------------------------|
| `python main.py --dry-run`| Prueba sin efectos: muestra que descargaria, sin descargar nada|
| `python main.py`          | Ejecucion normal (descarga y prepara las muestras)             |
| `python main.py --status` | Muestra el estado (cuantas muestras, errores, etc.)            |
| `python main.py --history`| Muestra los ultimos registros                                  |
| `python main.py --debug`  | Igual que normal, pero con mas detalle tecnico en pantalla     |

Si algo falla, **primero** ejecuta con `--dry-run` y luego con `--debug` para ver
mas detalle. El `--dry-run` es seguro: no descarga ni cambia nada.


## 4. Cambiar cada cuanto se ejecuta (cron)

La periodicidad la maneja `cron`, no el programa. Para editar:

```
crontab -e
```

Linea de ejemplo (cada 5 minutos, con `flock` para que no se solapen corridas):

```
*/5 * * * * /usr/bin/flock -n /tmp/phishing-msg-collector.lock /opt/phishing-msg-collector/ejecutar.sh >> /opt/phishing-msg-collector/logs/cron.log 2>&1
```

Para que corra cada 10 minutos, cambia `*/5` por `*/10`.


## 5. Errores comunes (troubleshooting)

| Sintoma en el log      | Causa probable                        | Que hacer                                            |
|------------------------|---------------------------------------|------------------------------------------------------|
| `401` (no autorizado)  | Credenciales malas o vencidas         | Revisar `TENANT_ID/CLIENT_ID/CLIENT_SECRET` en `.env`|
| `403` (prohibido)      | Falta el permiso `Mail.Read` o consent| Pedir al equipo de Azure el permiso y Admin Consent  |
| `404` (no encontrado)  | El `MAILBOX` esta mal o no existe     | Revisar `MAILBOX` en `.env`                          |
| `429` (throttling)     | Microsoft esta limitando             | Nada: el programa espera y reintenta solo            |
| `database is locked`   | Dos ejecuciones a la vez              | Verificar que `flock` este en la linea de cron       |
| Error de conversion EML| Un `.msg` raro no se pudo convertir  | Normal: el `.msg` original SI se guarda igual        |


## 6. Respaldos

Respaldar periodicamente estas dos carpetas:

- `data/`      -> la base SQLite (que ya se proceso).
- `downloads/` -> la evidencia descargada.

Para restaurar, basta con volver a poner esas carpetas en su lugar.


## 7. Zonas del codigo: verde vs roja

Si algun dia hay que tocar codigo, esta es la clasificacion honesta:

### Zona verde (mas facil de leer / cambiar con cuidado)

- `.env` y `.env.example` -> configuracion (no es codigo Python).
- `main.py`               -> textos que se muestran, opciones de la linea de comandos.
- `MANTENIMIENTO.md` / `README.md` -> documentacion.

### Zona roja (NO tocar sin saber Python)

- `graph_client.py` -> habla con Microsoft. Si se rompe, o deja de funcionar o
  (peor) podria dejar de ser solo lectura. **No tocar.**
- `sample.py`       -> guarda la evidencia y calcula hashes. Tiene un bloque
  marcado como **ZONA AVANZADA** (compatibilidad con ciertos `.msg`). No tocar.
- `utils.py`        -> tiene un bloque **ZONA AVANZADA** que evita que se escriban
  secretos en los logs. Quitarlo podria filtrar credenciales. No tocar.
- `database.py`     -> guarda el estado en SQLite. No tocar.
- `collector.py`    -> coordina todo el flujo. No tocar.

Regla simple: si el archivo tiene un comentario que dice **ZONA AVANZADA - NO
MODIFICAR**, no lo modifiques. Si necesitas un cambio ahi, pide ayuda a alguien
con Python.


## 8. Si algo se rompe y no sabes que hacer

1. Ejecuta `python main.py --dry-run --debug` y copia lo que sale.
2. Revisa `logs/phishing_collector.log` (el error suele estar al final).
3. La evidencia ya descargada en `downloads/` **no se pierde** por un error.
4. Ante la duda, la herramienta esta disenada para **fallar de forma segura**:
   prefiere no hacer nada antes que modificar el buzon o borrar evidencia.
