"""
collector.py
------------
Orquesta el flujo: Microsoft Graph (READ ONLY) -> SQLite -> muestra en disco.

No contiene logica de IA ni de analisis. Su unica responsabilidad es recolectar y
preparar evidencia de forma segura y trazable, dejando cada muestra lista (READY)
con analysis_status=PENDIENTE para el futuro Analyzer.

Seguridad: ver .kiro/steering/security.md. El contenido del correo es UNTRUSTED;
se sanitiza y nunca se ejecuta. El .msg original es evidencia inmutable.
"""

import logging
import os

import database as db
import sample as smp
from graph_client import GraphError
from utils import a_iso, ahora_utc, desde_iso

logger = logging.getLogger(__name__)

TIPO_FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"
TIPO_ITEM_ATTACHMENT = "#microsoft.graph.itemAttachment"


class Resumen:
    """Contadores del resultado de una ejecucion (para el resumen final)."""

    def __init__(self):
        self.correos = 0
        self.adjuntos_candidatos = 0
        self.nuevos = 0
        self.omitidos_dedup = 0
        self.guardados = 0
        self.eml_ok = 0
        self.errores = 0
        self.ya_observadas = 0

    def como_dict(self):
        return self.__dict__.copy()


class Collector:
    def __init__(self, config, cliente_graph, base_datos):
        self.cfg = config
        self.graph = cliente_graph
        self.db = base_datos

    def _es_candidato(self, adjunto):
        """
        Decide si un adjunto interesa: fileAttachment con nombre .msg, o cualquier
        itemAttachment (mensaje incrustado). Deteccion .msg case-insensitive.
        """
        tipo = adjunto.get("@odata.type", "")
        nombre = adjunto.get("name", "")
        if TIPO_ITEM_ATTACHMENT in tipo:
            return True
        if TIPO_FILE_ATTACHMENT in tipo and smp.es_msg(nombre):
            return True
        # Algunos tenants no traen el tipo en la lista; usar el nombre como respaldo.
        if smp.es_msg(nombre):
            return True
        return False

    def ejecutar(self, desde_dt, hasta_dt, *, dry_run=False, force=False):
        """
        Recolecta los adjuntos de la ventana [desde_dt, hasta_dt].

        Politica de checkpoint (no perder correos):
          - Si Graph falla por completo (autenticacion o listado), se propaga el
            error y el llamador NO actualiza last_successful_check.
          - Si el listado fue exitoso, al terminar se actualiza el checkpoint a
            'hasta_dt' aunque algunos adjuntos individuales hayan fallado
            (esos se reintentan por su estado en SQLite).

        Devuelve (Resumen, checkpoint_ok: bool).
        """
        resumen = Resumen()
        desde = a_iso(desde_dt)
        hasta = a_iso(hasta_dt)
        logger.info("Buzon: %s", self.cfg.mailbox)
        logger.info("Ventana de consulta: %s a %s (UTC)", desde, hasta)

        # Autenticacion y listado: si fallan, es fallo total de Graph.
        self.graph.obtener_token()
        mensajes = self.graph.buscar_mensajes(desde, hasta)
        resumen.correos = len(mensajes)
        logger.info("Correos encontrados: %d", resumen.correos)

        for correo in mensajes:
            if not correo.get("hasAttachments"):
                continue
            message_id = correo["id"]
            try:
                adjuntos = self.graph.obtener_adjuntos(message_id)
            except GraphError as exc:
                # Error parcial: se registra y se continua con el resto.
                logger.warning("No se pudieron listar adjuntos de un correo: %s", exc)
                continue

            for adjunto in adjuntos:
                if not self._es_candidato(adjunto):
                    continue
                resumen.adjuntos_candidatos += 1
                self._procesar_adjunto(correo, adjunto, resumen,
                                       dry_run=dry_run, force=force)

        return resumen, True

    def _procesar_adjunto(self, correo, adjunto, resumen, *, dry_run, force):
        """Procesa un adjunto candidato de forma segura y trazable."""
        message_id = correo["id"]
        attachment_id = adjunto.get("id", "")
        nombre_original = adjunto.get("name", "")
        tipo = adjunto.get("@odata.type", "")

        # Deduplicacion (salvo --force).
        ya_existe = self.db.attachment_existe(message_id, attachment_id)
        if ya_existe and not force:
            resumen.omitidos_dedup += 1
            logger.info("Adjunto ya procesado, se omite (dedup): %s", nombre_original)
            return

        if dry_run:
            resumen.nuevos += 1
            logger.info(
                "[DRY-RUN] Se descargaria el adjunto '%s' del correo '%s'.",
                nombre_original or "(sin nombre)", correo.get("subject", "(sin asunto)")
            )
            return

        # Registrar la fila (si no existe) en estado PENDIENTE.
        if not ya_existe:
            self.db.registrar_attachment({
                "message_id": message_id,
                "attachment_id": attachment_id,
                "internet_message_id": correo.get("internetMessageId", ""),
                "received_datetime": correo.get("receivedDateTime", ""),
                "sender": ((correo.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                "container_subject": correo.get("subject", ""),
                "original_attachment_name": nombre_original,
                "graph_attachment_type": tipo,
            })
        resumen.nuevos += 1

        try:
            self._descargar_y_preparar(correo, adjunto, resumen)
        except (smp.SampleError, GraphError) as exc:
            resumen.errores += 1
            logger.error("Error procesando '%s': %s", nombre_original, exc)
            self.db.registrar_error(
                message_id, attachment_id,
                mensaje=str(exc), estado_descarga=db.DL_ERROR,
            )

    def _descargar_y_preparar(self, correo, adjunto, resumen):
        """Descarga, guarda evidencia, convierte, escribe artefactos y READY."""
        message_id = correo["id"]
        attachment_id = adjunto.get("id", "")
        tipo = adjunto.get("@odata.type", "")
        max_bytes = self.cfg.max_attachment_size_bytes

        # Crear carpeta unica de la muestra.
        # El nombre combina el Asunto del correo contenedor (legible) con el
        # sample_id tecnico (unico), asi dos correos con el mismo asunto no
        # colisionan. El asunto interno del .msg no siempre esta disponible
        # (a veces no se puede parsear), por eso se usa el del contenedor aqui.
        sample_id = smp.generar_sample_id()
        nombre_carpeta = smp.nombre_carpeta_muestra(
            correo.get("subject"), sample_id, self.cfg.max_filename_length
        )
        dir_muestra = smp.crear_directorio_muestra(self.cfg.download_dir, nombre_carpeta)

        es_item = TIPO_ITEM_ATTACHMENT in tipo
        ruta_msg = None
        ruta_eml = None
        sha_msg = None
        sha_eml = None
        conversion_status = db.CONV_PENDIENTE
        suspicious_subject = None

        if es_item:
            # itemAttachment: Graph entrega MIME -> se guarda como .eml directamente.
            contenido = self.graph.descargar_item_attachment(message_id, attachment_id)
            # El nombre base se decide con el subject del correo contenedor,
            # ya que el MIME es el propio correo sospechoso.
            base = smp.sanitizar_nombre(
                correo.get("subject"), self.cfg.max_filename_length
            )
            ruta_eml = smp.guardar_eml(contenido, dir_muestra, base, max_bytes)
            sha_eml = smp.calcular_sha256(ruta_eml)
            conversion_status = db.CONV_NO_APLICA  # no hay MSG que convertir
            suspicious_subject = correo.get("subject", "")
            self.db.actualizar_descarga(
                message_id, attachment_id, estado=db.DL_DESCARGADO,
                sample_id=sample_id, sample_path=dir_muestra,
                local_eml_path=ruta_eml, suspicious_subject=suspicious_subject,
            )
        else:
            # fileAttachment .msg: descargar bytes y preservar el original.
            contenido = self.graph.descargar_file_attachment(message_id, adjunto)
            # Nombre base provisional (se refina con el subject interno del .msg).
            base_provisional = smp.sanitizar_nombre(
                correo.get("subject"), self.cfg.max_filename_length
            )
            ruta_msg = smp.guardar_msg(contenido, dir_muestra, base_provisional, max_bytes)
            sha_msg = smp.calcular_sha256(ruta_msg)

            # Refinar el nombre con el Subject del correo sospechoso (dentro del .msg).
            subject_interno = smp.leer_subject_de_msg(ruta_msg)
            suspicious_subject = subject_interno or correo.get("subject", "")
            base = smp.sanitizar_nombre(suspicious_subject, self.cfg.max_filename_length)

            # Si el nombre refinado difiere, renombrar el .msg (misma carpeta).
            if base != base_provisional:
                ruta_msg = smp.renombrar_en_muestra(dir_muestra, ruta_msg, base + ".msg")

            self.db.actualizar_descarga(
                message_id, attachment_id, estado=db.DL_DESCARGADO,
                sample_id=sample_id, sample_path=dir_muestra,
                local_msg_path=ruta_msg, sha256_msg=sha_msg,
                suspicious_subject=suspicious_subject,
            )

            # Deteccion de SHA256 repetido (muestra ya observada) sin perder trazas.
            previas = [f for f in self.db.buscar_sha256(sha_msg)
                       if f["attachment_id"] != attachment_id]
            if previas:
                resumen.ya_observadas += 1
                logger.info("MUESTRA YA OBSERVADA (SHA256 repetido): %s", sha_msg)

            # Intentar generar el .eml derivado.
            if self.cfg.create_eml:
                try:
                    ruta_eml = smp.convertir_msg_a_eml(ruta_msg, dir_muestra, base)
                    sha_eml = smp.calcular_sha256(ruta_eml)
                    conversion_status = db.CONV_CONVERTIDO
                    resumen.eml_ok += 1
                except smp.SampleError as exc:
                    conversion_status = db.CONV_ERROR
                    logger.warning("No se pudo convertir a EML (se conserva el MSG): %s", exc)
                    self.db.registrar_error(
                        message_id, attachment_id,
                        mensaje=f"Conversion EML: {exc}",
                        estado_conversion=db.CONV_ERROR,
                    )
            else:
                conversion_status = db.CONV_NO_APLICA

        resumen.guardados += 1

        # Rutas relativas para metadata (portabilidad).
        msg_rel = os.path.basename(ruta_msg) if ruta_msg else ""
        eml_rel = os.path.basename(ruta_eml) if ruta_eml else ""

        # metadata.json y hashes.txt.
        metadata = smp.construir_metadata(
            sample_id, self.cfg.mailbox, correo, adjunto,
            suspicious_subject=suspicious_subject,
            msg_rel=msg_rel, eml_rel=eml_rel,
            sha256_msg=sha_msg, sha256_eml=sha_eml,
            download_status=db.DL_DESCARGADO, conversion_status=conversion_status,
        )
        smp.crear_metadata(dir_muestra, metadata)
        smp.crear_hashes_txt(dir_muestra, sha_msg, sha_eml)

        # Actualizar conversion en SQLite y marcar procesado.
        self.db.actualizar_conversion(
            message_id, attachment_id, estado=conversion_status,
            local_eml_path=ruta_eml, sha256_eml=sha_eml, marcar_procesado=True,
        )

        # READY: la muestra esta consistente y lista para el Analyzer.
        smp.crear_ready(dir_muestra)
        logger.info("Muestra preparada (READY): %s", dir_muestra)
        logger.info("SHA256 MSG: %s", sha_msg or "(no aplica)")
