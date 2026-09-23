"""
utils.py
--------
Utilidades comunes del proyecto:

  - Configuracion de logging en espanol (a archivo y consola).
  - Un filtro de logging que evita imprimir datos sensibles (tokens, secretos,
    cabeceras de autorizacion) aunque por error se intente loggearlos.
  - Ayudas de tiempo en UTC con formato ISO (terminado en Z).

Reglas de seguridad relevantes (ver .kiro/steering/security.md):
  - Nunca se loggean secretos, access tokens ni cabeceras Authorization.
"""

import logging
import os
import re
from datetime import datetime, timezone

# Formato de fecha/hora ISO en UTC que entiende Microsoft Graph.
FORMATO_ISO = "%Y-%m-%dT%H:%M:%SZ"


# ---------------------------------------------------------------------------
#  Tiempo (UTC)
# ---------------------------------------------------------------------------

def ahora_utc():
    """Devuelve la fecha y hora actual como datetime con zona UTC."""
    return datetime.now(timezone.utc)


def a_iso(dt):
    """Convierte un datetime a texto ISO en UTC (terminado en Z)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(FORMATO_ISO)


def desde_iso(texto):
    """
    Convierte un texto ISO (con o sin Z) a datetime UTC.
    Devuelve None si el texto esta vacio o no es valido.
    """
    if not texto:
        return None
    texto = texto.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
#  Logging seguro (nunca imprime secretos)
# ---------------------------------------------------------------------------

# Patrones que, si aparecen en un mensaje de log, se enmascaran por precaucion.
_PATRONES_SENSIBLES = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(access_token\s*[\"':=]\s*)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(client_secret\s*[\"':=]\s*)\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
]


class FiltroSecretos(logging.Filter):
    """
    Filtro de logging que enmascara posibles secretos en los mensajes.

    Es una defensa en profundidad: el codigo ya evita loggear secretos, pero
    si alguno se colara, aqui se reemplaza por '***'.
    """

    def filter(self, record):
        try:
            mensaje = record.getMessage()
        except Exception:
            return True
        enmascarado = mensaje
        for patron in _PATRONES_SENSIBLES:
            enmascarado = patron.sub(r"\1***", enmascarado)
        if enmascarado != mensaje:
            # Reemplazar el mensaje por la version enmascarada.
            record.msg = enmascarado
            record.args = ()
        return True


def configurar_logging(log_dir, log_level="INFO", debug=False):
    """
    Configura el logging en espanol hacia consola y archivo.

    - log_dir: carpeta donde se guarda phishing_collector.log.
    - log_level: nivel base (INFO por defecto).
    - debug: si es True, sube el nivel a DEBUG.

    Devuelve el logger raiz configurado.
    """
    os.makedirs(log_dir, exist_ok=True)
    ruta_log = os.path.join(log_dir, "phishing_collector.log")

    nivel = logging.DEBUG if debug else getattr(logging, log_level.upper(), logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(nivel)

    # Evitar handlers duplicados si se llama mas de una vez.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formato = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    filtro = FiltroSecretos()

    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    consola.addFilter(filtro)
    logger.addHandler(consola)

    archivo = logging.FileHandler(ruta_log, encoding="utf-8")
    archivo.setFormatter(formato)
    archivo.addFilter(filtro)
    logger.addHandler(archivo)

    return logger
