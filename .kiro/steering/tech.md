---
inclusion: always
---

# Tecnologia y stack

## Principio rector

La **seguridad es la prioridad absoluta e innegociable**, por encima de la
simplicidad. La simplicidad y la mantenibilidad son objetivos importantes
(la herramienta la mantienen personas de SOC / CSIRT que pueden no saber
Python), pero **nunca** se sacrifica una buena practica de seguridad para
simplificar.

Regla practica: si la opcion mas simple es menos segura, se elige la opcion
segura y se documenta claramente el motivo en espanol para que sea mantenible.
Solo entre dos opciones **igualmente seguras** se elige la mas simple.

Orden de prioridades del proyecto:

```
SEGURIDAD -> READ ONLY -> SIMPLICIDAD -> MANTENIBILIDAD -> LEGIBILIDAD -> FIABILIDAD
```

Las buenas practicas de seguridad concretas (validacion de entrada no confiable,
permisos de filesystem, verificacion TLS, manejo de secretos, limites de recursos,
etc.) se detallan en el steering `security` y son de cumplimiento obligatorio
aunque agreguen algo de codigo o complejidad.

## Stack actual

- **Python 3** (probado con versiones recientes).
- **Linux** como sistema operativo de ejecucion.
- **Microsoft Graph API v1.0** (solo GET, permiso `Mail.Read`).
- **MSAL** (`msal.ConfidentialClientApplication`) para OAuth 2.0 Client Credentials.
- **requests** para las llamadas HTTP a Graph.
- **python-dotenv** para leer configuracion desde `.env`.
- **SQLite** mediante el modulo estandar `sqlite3` (NO se agrega a requirements).
- **extract-msg** para convertir `.msg` a `.eml` en Linux (sin Outlook).
- **email** (libreria estandar de Python) para construir/serializar el `.eml`.
- **cron** para la programacion periodica.
- **flock** para evitar ejecuciones concurrentes.

## Dependencias (requirements.txt)

Debe permanecer pequeno:

```
msal
requests
python-dotenv
extract-msg
```

`sqlite3` es parte de la libreria estandar de Python: **no** se agrega a
`requirements.txt`.

## Lo que NO se usa (evitar sobreingenieria)

No usar (salvo necesidad real y documentada): Docker, Kubernetes, FastAPI,
Flask, Django, Redis, Celery, PostgreSQL, MySQL, SQLAlchemy, ningun ORM,
microservicios ni colas complejas. Tampoco Kafka, SQS, Lambda, EventBridge,
ECS, DynamoDB, API Gateway, event sourcing, CQRS ni arquitecturas hexagonales.

Para SQLite se usa `import sqlite3` directamente, sin ORM.

## Entorno virtual

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Autenticacion (resumen)

- OAuth 2.0 **Client Credentials** con `msal.ConfidentialClientApplication`.
- Scope: `https://graph.microsoft.com/.default`.
- Permiso de aplicacion **`Mail.Read`** con Admin Consent. Nunca `Mail.ReadWrite`
  si `Mail.Read` es suficiente.
- Credenciales solo desde variables de entorno (`.env`), nunca hardcodeadas.
- Nunca usar username/password, login interactivo, browser login ni Device Code.

## Comandos utiles

Verificar sintaxis de un archivo:

```
python -m py_compile main.py
```

Ejecucion de prueba sin efectos:

```
python main.py --dry-run
```
