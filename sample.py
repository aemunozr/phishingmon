"""
sample.py
---------
Operaciones sobre la evidencia (la "muestra") en el filesystem local.

Responsabilidades:
  - Sanitizar el Subject para usarlo como nombre de archivo seguro.
  - Generar el sample_id y crear una carpeta unica por muestra.
  - Guardar el .msg de forma segura (.part -> validaciones -> rename atomico).
  - Guardar el .eml (MIME de itemAttachment, o convertido desde .msg).
  - Calcular SHA256.
  - Convertir .msg -> .eml con extract-msg + email (sin ejecutar contenido).
  - Crear metadata.json, hashes.txt y el marcador READY.

Seguridad (ver .kiro/steering/security.md):
  - Todo el contenido del correo es UNTRUSTED: se sanitiza, no se ejecuta.
  - Anti path traversal: la ruta final se valida dentro del directorio base.
  - .part + rename atomico; 0 bytes o exceso de tamano = error.
  - El .msg original es evidencia inmutable; nunca se sobrescribe.

Este modulo NO conoce credenciales de Microsoft Graph.
"""

import hashlib
import json
import contextlib
import os
import re
import sys
import uuid
from email.message import EmailMessage
from email.utils import formatdate

import extract_msg
import olefile

from utils import a_iso, ahora_utc


def _ruta_larga(ruta):
    """
    Devuelve una ruta apta para superar el limite de 260 caracteres de Windows.

    En Windows antepone el prefijo de ruta extendida '\\\\?\\' a la ruta absoluta,
    lo que permite rutas largas de forma nativa sin cambiar los nombres legibles.
    En Linux/macOS devuelve la ruta sin cambios (alli el limite es mucho mayor).

    Se usa en todas las operaciones de archivo (abrir, escribir, renombrar) para
    que el proyecto funcione tanto en Linux (entorno objetivo) como en Windows.
    """
    if sys.platform.startswith("win"):
        absoluta = os.path.abspath(ruta)
        if not absoluta.startswith("\\\\?\\"):
            return "\\\\?\\" + absoluta
        return absoluta
    return ruta

# Caracteres prohibidos en nombres de archivo (Windows/Linux) -> se reemplazan.
_CARACTERES_PROHIBIDOS = r'/\\:*?"<>|'
_TRADUCCION_PROHIBIDOS = {ord(c): "_" for c in _CARACTERES_PROHIBIDOS}

NOMBRE_SIN_ASUNTO = "sin_asunto"


class SampleError(Exception):
    """Error al preparar la muestra (mensaje entendible en espanol)."""


# ---------------------------------------------------------------------------
#  Sanitizacion de nombres (anti path traversal)
# ---------------------------------------------------------------------------

def sanitizar_nombre(subject, max_len=150):
    """
    Convierte un Subject (no confiable) en un nombre de archivo seguro.

    - Reemplaza / \\ : * ? " < > | por '_'.
    - Elimina NULL bytes, caracteres de control, saltos de linea y tabs.
    - Elimina cualquier componente de ruta y secuencias '..'.
    - Trunca a max_len.
    - Si queda vacio, usa 'sin_asunto'.
    """
    if not subject:
        return NOMBRE_SIN_ASUNTO

    texto = str(subject)

    # Quitar NULL bytes y caracteres de control (incluye \n, \r, \t).
    texto = texto.replace("\x00", "")
    texto = "".join(ch for ch in texto if ch == " " or ord(ch) >= 32)

    # Reemplazar caracteres prohibidos.
    texto = texto.translate(_TRADUCCION_PROHIBIDOS)

    # Neutralizar cualquier resto de ruta: quedarse solo con el basename y
    # eliminar secuencias de puntos que puedan formar '..'.
    texto = texto.replace("..", "_")
    texto = os.path.basename(texto)

    # Colapsar espacios y recortar.
    texto = re.sub(r"\s+", " ", texto).strip()

    # Evitar nombres peligrosos como '.' o '' tras la limpieza.
    if texto in ("", ".", ".."):
        return NOMBRE_SIN_ASUNTO

    # Truncar respetando el limite.
    if len(texto) > max_len:
        texto = texto[:max_len].strip()

    return texto or NOMBRE_SIN_ASUNTO


# ---------------------------------------------------------------------------
#  sample_id y carpeta de la muestra
# ---------------------------------------------------------------------------

def generar_sample_id():
    """
    Genera un identificador tecnico: YYYYMMDD_HHMMSS_HASHCORTO.
    El HASHCORTO es aleatorio para evitar colisiones dentro del mismo segundo.
    """
    marca = ahora_utc().strftime("%Y%m%d_%H%M%S")
    corto = uuid.uuid4().hex[:8]
    return f"{marca}_{corto}"


def _dir_pending(download_dir):
    """Devuelve la ruta absoluta de downloads/pending."""
    return os.path.abspath(os.path.join(download_dir, "pending"))


def nombre_carpeta_muestra(subject, sample_id, max_len=150):
    """
    Construye el nombre de la carpeta de la muestra combinando el Asunto
    (sanitizado y legible) con el sample_id tecnico, en el formato:

        <asunto_saneado>__<sample_id>

    El sample_id garantiza unicidad aunque dos correos tengan el mismo asunto
    (por ejemplo varios "[Reporte de Phishing] ..." identicos). Si no hay
    asunto, queda 'sin_asunto__<sample_id>'.

    El asunto se trunca dejando espacio para el sufijo '__<sample_id>', de modo
    que el nombre total no supere max_len.
    """
    # Reservar espacio para el separador y el sample_id.
    sufijo = f"__{sample_id}"
    espacio_asunto = max(1, max_len - len(sufijo))
    asunto = sanitizar_nombre(subject, espacio_asunto)
    return f"{asunto}{sufijo}"


def crear_directorio_muestra(download_dir, nombre_carpeta):
    """
    Crea downloads/pending/<nombre_carpeta>/ y valida (anti path traversal) que
    quede dentro del directorio base esperado. Devuelve la ruta absoluta.

    'nombre_carpeta' debe venir ya saneado (ver nombre_carpeta_muestra). Aun asi,
    se aplica una validacion de basename como defensa en profundidad.
    """
    base = _dir_pending(download_dir)
    # Defensa en profundidad: quedarse solo con el basename.
    nombre_carpeta = os.path.basename(nombre_carpeta)
    destino = os.path.abspath(os.path.join(base, nombre_carpeta))

    # Validacion anti path traversal: destino debe estar dentro de base.
    if os.path.commonpath([base, destino]) != base:
        raise SampleError("Ruta de muestra invalida (posible path traversal).")

    os.makedirs(_ruta_larga(destino), exist_ok=True)
    return destino


def ruta_segura_en(dir_muestra, nombre_archivo):
    """
    Devuelve la ruta de un archivo dentro de dir_muestra, validando que el
    nombre no escape del directorio (defensa en profundidad).
    """
    base = os.path.abspath(dir_muestra)
    destino = os.path.abspath(os.path.join(base, nombre_archivo))
    if os.path.commonpath([base, destino]) != base:
        raise SampleError("Nombre de archivo invalido (posible path traversal).")
    return destino


# ---------------------------------------------------------------------------
#  Hashes
# ---------------------------------------------------------------------------

def calcular_sha256(ruta):
    """Calcula el SHA256 de un archivo leyendo por bloques."""
    h = hashlib.sha256()
    with open(_ruta_larga(ruta), "rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def sha256_de_bytes(datos):
    """Calcula el SHA256 de un contenido en bytes."""
    return hashlib.sha256(datos).hexdigest()


# ---------------------------------------------------------------------------
#  Guardado seguro de archivos (.part -> rename atomico)
# ---------------------------------------------------------------------------

def _escribir_atomico(ruta_final, datos, modo):
    """
    Escribe 'datos' a un archivo temporal '.part', lo vuelca a disco (fsync) y
    luego lo renombra de forma atomica a 'ruta_final'.

    Asi el archivo final aparece SOLO cuando esta completo: nunca queda una
    muestra a medias que el Analyzer pudiera leer por error.

    - modo "wb": 'datos' son bytes (por ejemplo el .msg o el .eml).
    - modo "w" : 'datos' son texto (por ejemplo metadata.json).
    """
    ruta_part = ruta_final + ".part"
    codificacion = None if modo == "wb" else "utf-8"
    with open(_ruta_larga(ruta_part), modo, encoding=codificacion) as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())
    os.replace(_ruta_larga(ruta_part), _ruta_larga(ruta_final))
    return ruta_final


def _guardar_bytes_seguro(contenido, ruta_final, max_bytes):
    """
    Escribe 'contenido' a un archivo temporal .part, valida tamano y luego lo
    renombra de forma atomica a ruta_final.

    Reglas de seguridad:
      - 0 bytes -> error (no es una descarga valida).
      - tamano > max_bytes -> error (limite de recursos).
      - rename atomico: el archivo final aparece solo cuando esta completo.
    """
    if contenido is None or len(contenido) == 0:
        raise SampleError("El contenido descargado esta vacio (0 bytes).")
    if len(contenido) > max_bytes:
        raise SampleError(
            "El adjunto supera el tamano maximo permitido "
            f"({len(contenido)} bytes > {max_bytes} bytes)."
        )

    return _escribir_atomico(ruta_final, contenido, "wb")


def guardar_msg(contenido, dir_muestra, nombre_base, max_bytes):
    """
    Guarda el .msg original (evidencia primaria). Devuelve la ruta final.
    No sobrescribe si ya existe (la evidencia no se altera).
    """
    ruta = ruta_segura_en(dir_muestra, nombre_base + ".msg")
    if os.path.exists(_ruta_larga(ruta)):
        return ruta
    return _guardar_bytes_seguro(contenido, ruta, max_bytes)


def guardar_eml(contenido, dir_muestra, nombre_base, max_bytes):
    """
    Guarda un .eml a partir de contenido MIME (por ejemplo itemAttachment).
    Devuelve la ruta final.
    """
    ruta = ruta_segura_en(dir_muestra, nombre_base + ".eml")
    return _guardar_bytes_seguro(contenido, ruta, max_bytes)


# ---------------------------------------------------------------------------
#  Conversion .msg -> .eml
# ---------------------------------------------------------------------------

# ===========================================================================
#  ZONA AVANZADA - NO MODIFICAR salvo que conozcas bien Python y olefile.
#  Esto resuelve un defecto puntual de ciertos .msg de Outlook (streams OLE
#  vacios del boton "Report Phishing"). No ejecuta contenido del correo: solo
#  permite abrir el archivo para leer sus datos. Si se rompe, la conversion
#  .msg -> .eml fallara, pero el .msg original (la evidencia) se conserva igual.
# ===========================================================================
@contextlib.contextmanager
def _ole_tolerante():
    """
    Context manager que hace que 'olefile' tolere un defecto conocido e
    inofensivo: streams OLE vacios ("empty stream"). Algunos .msg validos de
    Outlook (por ejemplo los generados por el boton "Report Phishing") contienen
    streams vacios que olefile, en su modo estricto por defecto, trata como error
    fatal e impide abrir el archivo.

    Solo se degrada ESE defecto puntual, y unicamente durante la apertura del
    .msg; al salir se restaura el comportamiento original. No se ejecuta ningun
    contenido del correo: solo se leen datos.
    """
    original = olefile.OleFileIO._raise_defect

    def _raise_defect_tolerante(self, defect_level, message, exception_type=OSError):
        if message and "empty stream" in message.lower():
            return  # defecto inofensivo: no abortar la apertura
        return original(self, defect_level, message, exception_type)

    olefile.OleFileIO._raise_defect = _raise_defect_tolerante
    try:
        yield
    finally:
        olefile.OleFileIO._raise_defect = original


def _abrir_msg(ruta_msg):
    """
    Abre un .msg de forma robusta. Primero intenta normal; si falla por el
    defecto de olefile ("empty stream"), reintenta en modo tolerante.
    Devuelve el objeto Message (el llamador debe cerrarlo).
    """
    ruta = _ruta_larga(ruta_msg)
    try:
        return extract_msg.openMsg(ruta)
    except Exception:
        # Reintento tolerante para .msg con streams vacios (phish_alert, etc.).
        with _ole_tolerante():
            return extract_msg.openMsg(ruta)
# ===========================================================================
#  FIN ZONA AVANZADA
# ===========================================================================


def leer_subject_de_msg(ruta_msg):
    """
    Devuelve el Subject del correo sospechoso contenido en el .msg, o None.
    Solo extrae datos; no ejecuta contenido.
    """
    try:
        m = _abrir_msg(ruta_msg)
        try:
            return m.subject or None
        finally:
            m.close()
    except Exception:
        return None


def convertir_msg_a_eml(ruta_msg, dir_muestra, nombre_base):
    """
    Convierte el .msg en un .eml (MIME) usando extract-msg + email.

    Devuelve la ruta del .eml. Lanza SampleError si falla (el llamador debe
    conservar el .msg y registrar conversion_status=ERROR).
    """
    ruta_eml = ruta_segura_en(dir_muestra, nombre_base + ".eml")
    try:
        m = _abrir_msg(ruta_msg)
    except Exception as exc:  # noqa: BLE001
        raise SampleError("No se pudo abrir el .msg para convertirlo.") from exc

    try:
        eml = EmailMessage()
        if m.sender:
            eml["From"] = m.sender
        if m.to:
            eml["To"] = m.to
        if m.cc:
            eml["Cc"] = m.cc
        if m.subject:
            eml["Subject"] = m.subject
        eml["Date"] = m.date or formatdate(localtime=True)

        cuerpo_texto = m.body
        cuerpo_html = m.htmlBody
        if cuerpo_texto:
            eml.set_content(cuerpo_texto)
        else:
            eml.set_content("(Sin cuerpo de texto)")
        if cuerpo_html:
            if isinstance(cuerpo_html, bytes):
                cuerpo_html = cuerpo_html.decode("utf-8", errors="replace")
            eml.add_alternative(cuerpo_html, subtype="html")

        # Adjuntos internos del propio .msg (se preservan como datos, no se abren).
        for adj in m.attachments:
            datos = adj.data
            if datos is None:
                continue
            nombre = adj.longFilename or adj.shortFilename or "adjunto"
            if isinstance(datos, str):
                datos = datos.encode("utf-8", errors="replace")
            eml.add_attachment(
                datos, maintype="application", subtype="octet-stream",
                filename=sanitizar_nombre(nombre, 150),
            )

        # Escribir el .eml de forma atomica.
        _escribir_atomico(ruta_eml, eml.as_bytes(), "wb")
    except SampleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SampleError("Fallo la conversion de .msg a .eml.") from exc
    finally:
        try:
            m.close()
        except Exception:
            pass

    return ruta_eml


# ---------------------------------------------------------------------------
#  Artefactos: metadata.json, hashes.txt, READY
# ---------------------------------------------------------------------------

def crear_metadata(dir_muestra, datos):
    """
    Escribe metadata.json con el esquema acordado. 'datos' es un dict ya armado
    por el collector. No se guardan secretos.
    """
    ruta = ruta_segura_en(dir_muestra, "metadata.json")
    texto = json.dumps(datos, ensure_ascii=False, indent=2)
    return _escribir_atomico(ruta, texto, "w")


def crear_hashes_txt(dir_muestra, sha256_msg, sha256_eml):
    """Escribe hashes.txt con los SHA256 del MSG y del EML."""
    ruta = ruta_segura_en(dir_muestra, "hashes.txt")
    contenido = (
        "SHA256 MSG:\n"
        f"{sha256_msg or '(no disponible)'}\n\n"
        "SHA256 EML:\n"
        f"{sha256_eml or '(no disponible)'}\n"
    )
    with open(_ruta_larga(ruta), "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def crear_ready(dir_muestra):
    """
    Crea el archivo marcador READY. Debe llamarse SOLO cuando la muestra este
    completamente preparada y consistente.
    """
    ruta = ruta_segura_en(dir_muestra, "READY")
    with open(_ruta_larga(ruta), "w", encoding="utf-8") as f:
        f.write(a_iso(ahora_utc()) + "\n")
    return ruta


def es_msg(nombre_archivo):
    """Devuelve True si el nombre termina en .msg (case-insensitive)."""
    return bool(nombre_archivo) and nombre_archivo.lower().endswith(".msg")


def renombrar_en_muestra(dir_muestra, ruta_actual, nuevo_nombre):
    """
    Renombra un archivo dentro de la carpeta de la muestra a 'nuevo_nombre'
    (solo el nombre, sin ruta), validando anti path traversal y soportando
    rutas largas en Windows. Si el destino ya existe, no renombra.

    Devuelve la ruta final (nueva si se renombro, o la original si no).
    """
    destino = ruta_segura_en(dir_muestra, nuevo_nombre)
    if os.path.exists(_ruta_larga(destino)):
        return ruta_actual
    os.replace(_ruta_larga(ruta_actual), _ruta_larga(destino))
    return destino


def construir_metadata(sample_id, mailbox, correo, adjunto, *,
                       suspicious_subject, msg_rel, eml_rel,
                       sha256_msg, sha256_eml, download_status, conversion_status):
    """
    Construye el dict de metadata.json a partir de los datos disponibles.
    Centraliza el esquema para mantener consistencia (sin secretos).
    """
    remitente = ""
    frm = (correo.get("from") or {}).get("emailAddress") or {}
    remitente = frm.get("address", "")

    return {
        "sample_id": sample_id,
        "mailbox": mailbox,
        "received_datetime": correo.get("receivedDateTime", ""),
        "container_message": {
            "message_id": correo.get("id", ""),
            "internet_message_id": correo.get("internetMessageId", ""),
            "sender": remitente,
            "subject": correo.get("subject", ""),
        },
        "suspicious_message": {
            "subject": suspicious_subject or "",
        },
        "attachment": {
            "attachment_id": adjunto.get("id", ""),
            "original_name": adjunto.get("name", ""),
            "graph_type": adjunto.get("@odata.type", ""),
        },
        "files": {
            "msg": msg_rel or "",
            "eml": eml_rel or "",
        },
        "hashes": {
            "sha256_msg": sha256_msg or "",
            "sha256_eml": sha256_eml or "",
        },
        "collector": {
            "download_status": download_status or "",
            "conversion_status": conversion_status or "",
        },
    }
