"""
config.py
---------
Unica fuente de configuracion del proyecto.

Lee el archivo .env (con python-dotenv) y expone una clase Config con todos los
valores ya validados y con el tipo correcto. Ningun otro modulo debe leer
variables de entorno directamente: asi, migrar a AWS/AgentCore en el futuro
solo implica cambiar la forma de cargar la configuracion aqui.

Seguridad (ver .kiro/steering/security.md):
  - Los secretos solo viven en memoria; nunca se imprimen.
  - __repr__ oculta el CLIENT_SECRET.
"""

import os
import sys

from dotenv import load_dotenv


def _error_y_salir(mensaje):
    """Muestra un error de configuracion entendible y detiene el programa."""
    print("")
    print("=" * 60)
    print("  ERROR DE CONFIGURACION")
    print("=" * 60)
    print(mensaje)
    print("")
    print("Revisa tu archivo .env (puedes copiar .env.example como base).")
    print("")
    sys.exit(1)


def _a_bool(valor, por_defecto=False):
    """Convierte un texto del .env a booleano de forma tolerante."""
    if valor is None:
        return por_defecto
    return valor.strip().lower() in ("1", "true", "si", "si.", "yes", "y", "verdadero")


def _a_int(valor, nombre, por_defecto):
    """Convierte un texto a entero; si falla, detiene con mensaje claro."""
    if valor is None or valor.strip() == "":
        return por_defecto
    try:
        return int(valor.strip())
    except ValueError:
        _error_y_salir(f"{nombre} debe ser un numero entero. Valor recibido: {valor!r}")


class Config:
    """Configuracion validada del Collector."""

    def __init__(self):
        load_dotenv()

        # --- Microsoft Graph (obligatorios) ---------------------------------
        self.tenant_id = os.getenv("TENANT_ID", "").strip()
        self.client_id = os.getenv("CLIENT_ID", "").strip()
        self.client_secret = os.getenv("CLIENT_SECRET", "").strip()
        self.mailbox = os.getenv("MAILBOX", "").strip()

        # --- Consulta --------------------------------------------------------
        self.first_run_hours = _a_int(os.getenv("FIRST_RUN_HOURS"), "FIRST_RUN_HOURS", 24)
        self.lookback_minutes = _a_int(os.getenv("LOOKBACK_MINUTES"), "LOOKBACK_MINUTES", 10)

        # --- Descarga --------------------------------------------------------
        self.download_dir = os.getenv("DOWNLOAD_DIR", "downloads").strip() or "downloads"
        self.max_attachment_size_mb = _a_int(
            os.getenv("MAX_ATTACHMENT_SIZE_MB"), "MAX_ATTACHMENT_SIZE_MB", 100
        )
        self.max_filename_length = _a_int(
            os.getenv("MAX_FILENAME_LENGTH"), "MAX_FILENAME_LENGTH", 150
        )

        # --- Conversion ------------------------------------------------------
        # KEEP_ORIGINAL_MSG es solo INFORMATIVO: el .msg original es evidencia y
        # NUNCA se borra (ver steering security.md seccion 10, cadena de
        # custodia). Aunque se ponga en false, el original se conserva igual.
        # Se mantiene la variable para dejar explicita la politica.
        self.keep_original_msg = _a_bool(os.getenv("KEEP_ORIGINAL_MSG"), True)
        self.create_eml = _a_bool(os.getenv("CREATE_EML"), True)

        # --- Base de datos ---------------------------------------------------
        self.database_path = os.getenv(
            "DATABASE_PATH", "data/phishing_collector.db"
        ).strip() or "data/phishing_collector.db"

        # --- Log -------------------------------------------------------------
        self.log_level = os.getenv("LOG_LEVEL", "INFO").strip() or "INFO"
        self.log_dir = os.getenv("LOG_DIR", "logs").strip() or "logs"

        self._validar()

    def _validar(self):
        """Comprueba que los datos obligatorios esten presentes y coherentes."""
        faltantes = []
        if not self.tenant_id:
            faltantes.append("TENANT_ID")
        if not self.client_id:
            faltantes.append("CLIENT_ID")
        if not self.client_secret:
            faltantes.append("CLIENT_SECRET")
        if not self.mailbox:
            faltantes.append("MAILBOX")
        if faltantes:
            _error_y_salir(
                "Faltan datos obligatorios en el .env: " + ", ".join(faltantes)
            )

        if self.max_attachment_size_mb <= 0:
            _error_y_salir("MAX_ATTACHMENT_SIZE_MB debe ser mayor que 0.")
        if self.max_filename_length <= 0:
            _error_y_salir("MAX_FILENAME_LENGTH debe ser mayor que 0.")
        if self.lookback_minutes < 0:
            _error_y_salir("LOOKBACK_MINUTES no puede ser negativo.")
        if self.first_run_hours <= 0:
            _error_y_salir("FIRST_RUN_HOURS debe ser mayor que 0.")

    @property
    def max_attachment_size_bytes(self):
        """Tamano maximo de adjunto en bytes."""
        return self.max_attachment_size_mb * 1024 * 1024

    def __repr__(self):
        # Nunca exponer el secreto.
        return (
            "Config(mailbox={!r}, download_dir={!r}, database_path={!r}, "
            "first_run_hours={}, lookback_minutes={}, client_secret=***)".format(
                self.mailbox, self.download_dir, self.database_path,
                self.first_run_hours, self.lookback_minutes,
            )
        )
