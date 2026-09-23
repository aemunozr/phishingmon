"""
database.py
-----------
Toda la interaccion con SQLite. Sin ORM.

Guarda el estado de cada adjunto procesado y el checkpoint de la ultima revision
exitosa. Sirve para deduplicar (no reprocesar lo mismo) y para reintentar errores.

Seguridad (ver .kiro/steering/security.md):
  - TODAS las consultas usan parametros (nunca se concatenan datos del correo).
  - Se usa modo WAL y timeout para tolerar concurrencia; ademas cron usa flock.

Es 100% local: no toca el buzon de Microsoft 365.
"""

import os
import sqlite3

from utils import a_iso, ahora_utc

# Estados posibles (ver design.md seccion 5).
DL_PENDIENTE = "PENDIENTE"
DL_DESCARGADO = "DESCARGADO"
DL_ERROR = "ERROR"

CONV_PENDIENTE = "PENDIENTE"
CONV_CONVERTIDO = "CONVERTIDO"
CONV_NO_APLICA = "NO_APLICA"
CONV_ERROR = "ERROR"

AN_PENDIENTE = "PENDIENTE"

# Clave del checkpoint en app_state.
CLAVE_ULTIMA_REVISION = "last_successful_check"


class Database:
    """Acceso a la base SQLite del Collector."""

    def __init__(self, ruta):
        self.ruta = ruta
        # Crear la carpeta contenedora si no existe (por ejemplo data/).
        carpeta = os.path.dirname(os.path.abspath(ruta))
        os.makedirs(carpeta, exist_ok=True)

        # timeout=30 para esperar si otra conexion tiene un lock momentaneo.
        self.conexion = sqlite3.connect(ruta, timeout=30)
        self.conexion.row_factory = sqlite3.Row
        # WAL mejora la concurrencia lectura/escritura.
        self.conexion.execute("PRAGMA journal_mode=WAL;")
        self.conexion.execute("PRAGMA foreign_keys=ON;")

    # ------------------------------------------------------------------ setup

    def inicializar_base(self):
        """Crea tablas e indices si no existen."""
        self.conexion.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                message_id TEXT NOT NULL,
                attachment_id TEXT NOT NULL,

                internet_message_id TEXT,

                received_datetime TEXT,

                sender TEXT,
                container_subject TEXT,
                suspicious_subject TEXT,

                original_attachment_name TEXT,

                sample_id TEXT,
                sample_path TEXT,

                local_msg_path TEXT,
                local_eml_path TEXT,

                sha256_msg TEXT,
                sha256_eml TEXT,

                graph_attachment_type TEXT,

                download_status TEXT NOT NULL,
                conversion_status TEXT,
                analysis_status TEXT,

                first_seen_at TEXT NOT NULL,
                processed_at TEXT,
                last_attempt_at TEXT,

                retry_count INTEGER DEFAULT 0,

                error_message TEXT,

                UNIQUE(message_id, attachment_id)
            )
            """
        )
        self.conexion.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self.conexion.execute(
            "CREATE INDEX IF NOT EXISTS idx_sha256_msg "
            "ON processed_attachments(sha256_msg)"
        )
        self.conexion.execute(
            "CREATE INDEX IF NOT EXISTS idx_received_datetime "
            "ON processed_attachments(received_datetime)"
        )
        self.conexion.commit()

    # ------------------------------------------------------------- dedup / get

    def attachment_existe(self, message_id, attachment_id):
        """Devuelve True si ya existe una fila para ese adjunto."""
        fila = self.conexion.execute(
            "SELECT 1 FROM processed_attachments "
            "WHERE message_id = ? AND attachment_id = ?",
            (message_id, attachment_id),
        ).fetchone()
        return fila is not None

    def obtener_attachment(self, message_id, attachment_id):
        """Devuelve la fila del adjunto (o None)."""
        return self.conexion.execute(
            "SELECT * FROM processed_attachments "
            "WHERE message_id = ? AND attachment_id = ?",
            (message_id, attachment_id),
        ).fetchone()

    def buscar_sha256(self, sha256_msg):
        """Devuelve las filas que ya tienen ese SHA256 de MSG (para trazabilidad)."""
        if not sha256_msg:
            return []
        return self.conexion.execute(
            "SELECT * FROM processed_attachments WHERE sha256_msg = ?",
            (sha256_msg,),
        ).fetchall()

    # ---------------------------------------------------------------- inserts

    def registrar_attachment(self, datos):
        """
        Inserta una fila nueva para un adjunto detectado, en estado PENDIENTE.

        'datos' es un dict con al menos: message_id, attachment_id,
        internet_message_id, received_datetime, sender, container_subject,
        original_attachment_name, graph_attachment_type.

        Devuelve el id de la fila insertada. Usa consulta parametrizada.
        """
        ahora = a_iso(ahora_utc())
        cursor = self.conexion.execute(
            """
            INSERT INTO processed_attachments (
                message_id, attachment_id, internet_message_id,
                received_datetime, sender, container_subject,
                original_attachment_name, graph_attachment_type,
                download_status, conversion_status, analysis_status,
                first_seen_at, retry_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                datos.get("message_id"),
                datos.get("attachment_id"),
                datos.get("internet_message_id"),
                datos.get("received_datetime"),
                datos.get("sender"),
                datos.get("container_subject"),
                datos.get("original_attachment_name"),
                datos.get("graph_attachment_type"),
                DL_PENDIENTE,
                CONV_PENDIENTE,
                AN_PENDIENTE,
                ahora,
            ),
        )
        self.conexion.commit()
        return cursor.lastrowid

    # ---------------------------------------------------------------- updates

    def actualizar_descarga(self, message_id, attachment_id, *, estado,
                            sample_id=None, sample_path=None,
                            local_msg_path=None, local_eml_path=None,
                            sha256_msg=None, suspicious_subject=None):
        """Actualiza los datos de descarga de un adjunto."""
        self.conexion.execute(
            """
            UPDATE processed_attachments
               SET download_status = ?,
                   sample_id = COALESCE(?, sample_id),
                   sample_path = COALESCE(?, sample_path),
                   local_msg_path = COALESCE(?, local_msg_path),
                   local_eml_path = COALESCE(?, local_eml_path),
                   sha256_msg = COALESCE(?, sha256_msg),
                   suspicious_subject = COALESCE(?, suspicious_subject),
                   last_attempt_at = ?
             WHERE message_id = ? AND attachment_id = ?
            """,
            (
                estado, sample_id, sample_path, local_msg_path, local_eml_path,
                sha256_msg, suspicious_subject, a_iso(ahora_utc()),
                message_id, attachment_id,
            ),
        )
        self.conexion.commit()

    def actualizar_conversion(self, message_id, attachment_id, *, estado,
                              local_eml_path=None, sha256_eml=None,
                              marcar_procesado=False):
        """Actualiza el estado de conversion (y opcionalmente processed_at)."""
        processed_at = a_iso(ahora_utc()) if marcar_procesado else None
        self.conexion.execute(
            """
            UPDATE processed_attachments
               SET conversion_status = ?,
                   local_eml_path = COALESCE(?, local_eml_path),
                   sha256_eml = COALESCE(?, sha256_eml),
                   processed_at = COALESCE(?, processed_at),
                   last_attempt_at = ?
             WHERE message_id = ? AND attachment_id = ?
            """,
            (
                estado, local_eml_path, sha256_eml, processed_at,
                a_iso(ahora_utc()), message_id, attachment_id,
            ),
        )
        self.conexion.commit()

    def registrar_error(self, message_id, attachment_id, *, mensaje,
                        estado_descarga=None, estado_conversion=None):
        """
        Registra un error: incrementa retry_count, guarda mensaje y timestamp,
        y opcionalmente cambia el estado de descarga o conversion.
        """
        self.conexion.execute(
            """
            UPDATE processed_attachments
               SET error_message = ?,
                   retry_count = retry_count + 1,
                   last_attempt_at = ?,
                   download_status = COALESCE(?, download_status),
                   conversion_status = COALESCE(?, conversion_status)
             WHERE message_id = ? AND attachment_id = ?
            """,
            (
                mensaje, a_iso(ahora_utc()), estado_descarga, estado_conversion,
                message_id, attachment_id,
            ),
        )
        self.conexion.commit()

    # --------------------------------------------------------------- checkpoint

    def obtener_ultima_revision(self):
        """Devuelve el texto ISO de la ultima revision exitosa, o None."""
        fila = self.conexion.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (CLAVE_ULTIMA_REVISION,),
        ).fetchone()
        return fila["value"] if fila else None

    def actualizar_ultima_revision(self, valor_iso):
        """Guarda/actualiza el checkpoint de la ultima revision exitosa."""
        self.conexion.execute(
            """
            INSERT INTO app_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (CLAVE_ULTIMA_REVISION, valor_iso),
        )
        self.conexion.commit()

    # ------------------------------------------------------------- status/hist

    def obtener_status(self):
        """Devuelve un dict con contadores para el modo --status."""
        def contar(consulta, params=()):
            fila = self.conexion.execute(consulta, params).fetchone()
            return fila[0] if fila else 0

        return {
            "total": contar("SELECT COUNT(*) FROM processed_attachments"),
            "descargados": contar(
                "SELECT COUNT(*) FROM processed_attachments WHERE download_status = ?",
                (DL_DESCARGADO,),
            ),
            "pendientes": contar(
                "SELECT COUNT(*) FROM processed_attachments WHERE download_status = ?",
                (DL_PENDIENTE,),
            ),
            "errores": contar(
                "SELECT COUNT(*) FROM processed_attachments WHERE download_status = ?",
                (DL_ERROR,),
            ),
            "conversiones_ok": contar(
                "SELECT COUNT(*) FROM processed_attachments WHERE conversion_status = ?",
                (CONV_CONVERTIDO,),
            ),
            "ultima_revision": self.obtener_ultima_revision(),
        }

    def obtener_historial(self, limite=20):
        """Devuelve las ultimas filas (para el modo --history)."""
        return self.conexion.execute(
            """
            SELECT received_datetime, suspicious_subject, container_subject,
                   download_status, conversion_status
              FROM processed_attachments
             ORDER BY first_seen_at DESC
             LIMIT ?
            """,
            (limite,),
        ).fetchall()

    # ------------------------------------------------------------------ cerrar

    def cerrar(self):
        """Cierra la conexion con la base."""
        self.conexion.close()
