"""
Pruebas de phishing-msg-collector.

Usan unittest (libreria estandar; no agrega dependencias). No dependen del buzon
real: el cliente de Graph se reemplaza por un doble de prueba (fake) sencillo.

Cubren: sanitizacion de filename, anti path traversal, deteccion .msg,
hashes, creacion de base + UNIQUE, deduplicacion, metadata, estado READY,
reintentos y el flujo del collector con un fileAttachment simulado.

Ejecutar (con el entorno virtual activado):
    python -m unittest discover -s tests -v
"""

import os
import sqlite3
import sys
import tempfile
import unittest

# Permitir importar los modulos del proyecto (carpeta padre).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sample as smp        # noqa: E402
import database as db       # noqa: E402


# ---------------------------------------------------------------------------
#  Sanitizacion y path traversal
# ---------------------------------------------------------------------------

class TestSanitizacion(unittest.TestCase):

    def test_reemplaza_caracteres_prohibidos(self):
        r = smp.sanitizar_nombre('a/b\\c:d*e?f"g<h>i|j', 150)
        for ch in '/\\:*?"<>|':
            self.assertNotIn(ch, r)

    def test_sin_asunto_si_vacio(self):
        self.assertEqual(smp.sanitizar_nombre("", 150), smp.NOMBRE_SIN_ASUNTO)
        self.assertEqual(smp.sanitizar_nombre(None, 150), smp.NOMBRE_SIN_ASUNTO)

    def test_neutraliza_path_traversal(self):
        r = smp.sanitizar_nombre("../../etc/passwd", 150)
        self.assertNotIn("..", r)
        self.assertNotIn("/", r)

    def test_elimina_control_y_saltos(self):
        r = smp.sanitizar_nombre("linea1\nlinea2\ttab\x00null", 150)
        self.assertNotIn("\n", r)
        self.assertNotIn("\t", r)
        self.assertNotIn("\x00", r)

    def test_trunca_longitud(self):
        r = smp.sanitizar_nombre("A" * 500, 150)
        self.assertLessEqual(len(r), 150)


class TestRutaSegura(unittest.TestCase):

    def test_crear_directorio_muestra_dentro_de_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = smp.crear_directorio_muestra(tmp, "20260101_000000_abcd1234")
            base = os.path.abspath(os.path.join(tmp, "pending"))
            self.assertTrue(os.path.commonpath([base, destino]) == base)
            self.assertTrue(os.path.isdir(destino))

    def test_ruta_segura_rechaza_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(smp.SampleError):
                smp.ruta_segura_en(tmp, "../fuera.txt")

    def test_nombre_carpeta_incluye_asunto_y_sample_id(self):
        sid = "20260101_000000_abcd1234"
        nombre = smp.nombre_carpeta_muestra("Correo sospechoso", sid, 150)
        self.assertIn("Correo sospechoso", nombre)
        self.assertTrue(nombre.endswith(sid))

    def test_nombre_carpeta_sin_asunto(self):
        sid = "20260101_000000_abcd1234"
        nombre = smp.nombre_carpeta_muestra("", sid, 150)
        self.assertEqual(nombre, f"{smp.NOMBRE_SIN_ASUNTO}__{sid}")

    def test_nombre_carpeta_respeta_max_len(self):
        sid = "20260101_000000_abcd1234"
        nombre = smp.nombre_carpeta_muestra("A" * 500, sid, 150)
        self.assertLessEqual(len(nombre), 150)
        self.assertTrue(nombre.endswith(sid))

    def test_nombre_carpeta_neutraliza_traversal(self):
        sid = "20260101_000000_abcd1234"
        nombre = smp.nombre_carpeta_muestra("../../etc/passwd", sid, 150)
        self.assertNotIn("..", nombre)
        self.assertNotIn("/", nombre)


# ---------------------------------------------------------------------------
#  Deteccion .msg y hashes
# ---------------------------------------------------------------------------

class TestDeteccionYHashes(unittest.TestCase):

    def test_es_msg_case_insensitive(self):
        self.assertTrue(smp.es_msg("correo.msg"))
        self.assertTrue(smp.es_msg("CORREO.MSG"))
        self.assertTrue(smp.es_msg("Correo.Msg"))
        self.assertFalse(smp.es_msg("correo.eml"))
        self.assertFalse(smp.es_msg(""))

    def test_sha256_de_bytes_conocido(self):
        # SHA256 de "abc".
        esperado = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        self.assertEqual(smp.sha256_de_bytes(b"abc"), esperado)

    def test_calcular_sha256_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "x.bin")
            with open(ruta, "wb") as f:
                f.write(b"abc")
            esperado = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
            self.assertEqual(smp.calcular_sha256(ruta), esperado)


# ---------------------------------------------------------------------------
#  Guardado seguro (.part -> rename, 0 bytes, limite)
# ---------------------------------------------------------------------------

class TestGuardadoSeguro(unittest.TestCase):

    def test_guardar_msg_rechaza_vacio(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(smp.SampleError):
                smp.guardar_msg(b"", tmp, "archivo", 1000)

    def test_guardar_msg_rechaza_grande(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(smp.SampleError):
                smp.guardar_msg(b"x" * 100, tmp, "archivo", 10)

    def test_guardar_msg_ok_y_no_deja_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = smp.guardar_msg(b"contenido", tmp, "archivo", 1000)
            self.assertTrue(os.path.exists(ruta))
            self.assertFalse(os.path.exists(ruta + ".part"))


# ---------------------------------------------------------------------------
#  Base de datos: creacion, UNIQUE, dedup, reintentos, checkpoint
# ---------------------------------------------------------------------------

class TestDatabase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ruta = os.path.join(self.tmp.name, "test.db")
        self.base = db.Database(ruta)
        self.base.inicializar_base()

    def tearDown(self):
        self.base.cerrar()
        self.tmp.cleanup()

    def _datos(self, mid="m1", aid="a1"):
        return {
            "message_id": mid, "attachment_id": aid,
            "internet_message_id": "im1", "received_datetime": "2026-01-01T00:00:00Z",
            "sender": "x@y.cl", "container_subject": "cont",
            "original_attachment_name": "correo.msg",
            "graph_attachment_type": "#microsoft.graph.fileAttachment",
        }

    def test_registrar_y_existe(self):
        self.assertFalse(self.base.attachment_existe("m1", "a1"))
        self.base.registrar_attachment(self._datos())
        self.assertTrue(self.base.attachment_existe("m1", "a1"))

    def test_unique_message_attachment(self):
        self.base.registrar_attachment(self._datos())
        with self.assertRaises(sqlite3.IntegrityError):
            self.base.registrar_attachment(self._datos())

    def test_buscar_sha256_trazabilidad(self):
        # Dos adjuntos distintos con el mismo hash: ambos deben quedar.
        self.base.registrar_attachment(self._datos("m1", "a1"))
        self.base.registrar_attachment(self._datos("m2", "a2"))
        self.base.actualizar_descarga("m1", "a1", estado=db.DL_DESCARGADO, sha256_msg="H")
        self.base.actualizar_descarga("m2", "a2", estado=db.DL_DESCARGADO, sha256_msg="H")
        filas = self.base.buscar_sha256("H")
        self.assertEqual(len(filas), 2)

    def test_reintento_incrementa_contador(self):
        self.base.registrar_attachment(self._datos())
        self.base.registrar_error("m1", "a1", mensaje="fallo", estado_descarga=db.DL_ERROR)
        fila = self.base.obtener_attachment("m1", "a1")
        self.assertEqual(fila["retry_count"], 1)
        self.assertEqual(fila["download_status"], db.DL_ERROR)

    def test_checkpoint(self):
        self.assertIsNone(self.base.obtener_ultima_revision())
        self.base.actualizar_ultima_revision("2026-01-01T00:00:00Z")
        self.assertEqual(self.base.obtener_ultima_revision(), "2026-01-01T00:00:00Z")
        # UPSERT: actualizar de nuevo.
        self.base.actualizar_ultima_revision("2026-01-02T00:00:00Z")
        self.assertEqual(self.base.obtener_ultima_revision(), "2026-01-02T00:00:00Z")


# ---------------------------------------------------------------------------
#  metadata.json y READY
# ---------------------------------------------------------------------------

class TestArtefactos(unittest.TestCase):

    def test_metadata_no_contiene_secretos(self):
        correo = {
            "id": "m1", "subject": "Asunto", "receivedDateTime": "2026-01-01T00:00:00Z",
            "internetMessageId": "im1",
            "from": {"emailAddress": {"address": "x@y.cl"}},
        }
        adjunto = {"id": "a1", "name": "correo.msg", "@odata.type": "#microsoft.graph.fileAttachment"}
        meta = smp.construir_metadata(
            "sid1", "phishing@itau.cl", correo, adjunto,
            suspicious_subject="Sospechoso", msg_rel="Sospechoso.msg", eml_rel="Sospechoso.eml",
            sha256_msg="H1", sha256_eml="H2",
            download_status="DESCARGADO", conversion_status="CONVERTIDO",
        )
        texto = str(meta).lower()
        self.assertNotIn("client_secret", texto)
        self.assertNotIn("access_token", texto)
        self.assertEqual(meta["hashes"]["sha256_msg"], "H1")

    def test_crear_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = smp.crear_ready(tmp)
            self.assertTrue(os.path.exists(ruta))
            self.assertTrue(ruta.endswith("READY"))


# ---------------------------------------------------------------------------
#  Flujo del Collector con un cliente de Graph simulado (fake)
# ---------------------------------------------------------------------------

class FakeGraph:
    """Doble de prueba del cliente de Graph. No hace red."""

    def __init__(self, mensajes, adjuntos_por_mensaje, contenido):
        self._mensajes = mensajes
        self._adjuntos = adjuntos_por_mensaje
        self._contenido = contenido
        self.autenticado = False

    def obtener_token(self):
        self.autenticado = True
        return True

    def buscar_mensajes(self, desde, hasta):
        return self._mensajes

    def obtener_adjuntos(self, message_id):
        return self._adjuntos.get(message_id, [])

    def descargar_file_attachment(self, message_id, attachment):
        return self._contenido

    def descargar_item_attachment(self, message_id, attachment_id):
        return self._contenido


class TestFlujoCollector(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Config minima simulada (solo los atributos que usa el collector).
        class Cfg:
            mailbox = "phishing@itau.cl"
            download_dir = os.path.join(self.tmp.name, "downloads")
            max_attachment_size_mb = 100
            max_filename_length = 150
            create_eml = False  # evitamos depender de extract-msg con bytes falsos
            @property
            def max_attachment_size_bytes(self):
                return self.max_attachment_size_mb * 1024 * 1024
        self.cfg = Cfg()
        self.base = db.Database(os.path.join(self.tmp.name, "t.db"))
        self.base.inicializar_base()

    def tearDown(self):
        self.base.cerrar()
        self.tmp.cleanup()

    def test_dry_run_no_escribe(self):
        from collector import Collector
        from utils import ahora_utc
        from datetime import timedelta
        mensajes = [{
            "id": "m1", "subject": "Prueba", "hasAttachments": True,
            "receivedDateTime": "2026-01-01T00:00:00Z",
            "from": {"emailAddress": {"address": "x@y.cl"}},
        }]
        adjuntos = {"m1": [{"id": "a1", "name": "x.msg",
                            "@odata.type": "#microsoft.graph.fileAttachment"}]}
        fake = FakeGraph(mensajes, adjuntos, b"contenido-msg")
        col = Collector(self.cfg, fake, self.base)
        col.ejecutar(ahora_utc() - timedelta(hours=1), ahora_utc(), dry_run=True, force=False)
        # En dry-run no se registra nada en la base.
        self.assertFalse(self.base.attachment_existe("m1", "a1"))

    def test_flujo_item_attachment_genera_ready_y_dedup(self):
        from collector import Collector
        from utils import ahora_utc
        from datetime import timedelta
        mensajes = [{
            "id": "m1", "subject": "Correo sospechoso", "hasAttachments": True,
            "receivedDateTime": "2026-01-01T00:00:00Z", "internetMessageId": "im1",
            "from": {"emailAddress": {"address": "x@y.cl"}},
        }]
        # itemAttachment: el contenido MIME se guarda como .eml (no requiere extract-msg).
        adjuntos = {"m1": [{"id": "a1", "name": "adjunto",
                            "@odata.type": "#microsoft.graph.itemAttachment"}]}
        fake = FakeGraph(mensajes, adjuntos, b"From: a@b\r\nSubject: x\r\n\r\ncuerpo")
        col = Collector(self.cfg, fake, self.base)

        resumen, ok = col.ejecutar(
            ahora_utc() - timedelta(hours=1), ahora_utc(), dry_run=False, force=False
        )
        self.assertTrue(ok)
        self.assertEqual(resumen.guardados, 1)
        self.assertTrue(self.base.attachment_existe("m1", "a1"))

        # Debe existir la carpeta de la muestra con READY y metadata.json.
        fila = self.base.obtener_attachment("m1", "a1")
        dir_muestra = fila["sample_path"]
        self.assertTrue(os.path.exists(os.path.join(dir_muestra, "READY")))
        self.assertTrue(os.path.exists(os.path.join(dir_muestra, "metadata.json")))

        # Segunda ejecucion: dedup, no se guarda de nuevo.
        resumen2, _ = col.ejecutar(
            ahora_utc() - timedelta(hours=1), ahora_utc(), dry_run=False, force=False
        )
        self.assertEqual(resumen2.guardados, 0)
        self.assertEqual(resumen2.omitidos_dedup, 1)


if __name__ == "__main__":
    unittest.main()
