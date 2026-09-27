"""
graph_client.py
---------------
Toda la comunicacion con Microsoft Graph. ESTRICTAMENTE SOLO LECTURA.

Reglas de seguridad (ver .kiro/steering/security.md):
  - El unico metodo de red es _get(): solo HTTP GET contra el buzon.
    NO existe (a proposito) ningun helper para POST/PATCH/PUT/DELETE.
  - TLS siempre verificado (comportamiento por defecto de requests; jamas verify=False).
  - Toda request tiene timeout.
  - Manejo de 401/403/404/429/5xx con reintentos acotados y respeto de Retry-After.
    Nunca loops infinitos.
  - Nunca se loggean el token, el secreto ni la cabecera Authorization.

No contiene logica de SQLite.
"""

import base64
import logging
import time

import msal
import requests

logger = logging.getLogger(__name__)

URL_LOGIN = "https://login.microsoftonline.com"
URL_GRAPH = "https://graph.microsoft.com/v1.0"
SCOPE = ["https://graph.microsoft.com/.default"]

# Parametros de red y reintentos.
TIMEOUT_SEGUNDOS = 60
MAX_REINTENTOS = 5
BACKOFF_BASE_SEGUNDOS = 2          # espera = base * (2 ** intento), acotada
BACKOFF_MAX_SEGUNDOS = 60
CODIGOS_REINTENTABLES = (500, 502, 503, 504)


class GraphError(Exception):
    """Error al comunicarse con Microsoft Graph (mensaje entendible en espanol)."""


class GraphAuthError(GraphError):
    """Error de autenticacion (no se pudo obtener el token)."""


class GraphClient:
    """Cliente READ ONLY de Microsoft Graph."""

    def __init__(self, tenant_id, client_id, client_secret, mailbox,
                 max_download_bytes=None):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.mailbox = mailbox
        # Limite defensivo de tamano de descarga (bytes). Si viene, se rechaza
        # un adjunto ANTES de cargar su cuerpo en memoria, usando la cabecera
        # Content-Length. sample.py aplica ademas un segundo control sobre los
        # bytes ya escritos (defensa en profundidad). None = sin limite aqui.
        self.max_download_bytes = max_download_bytes
        self._token = None
        self._sesion = requests.Session()

    # --------------------------------------------------------------- auth

    def obtener_token(self):
        """
        Obtiene un access token con OAuth 2.0 Client Credentials (MSAL).
        No es una llamada al buzon, sino al endpoint de login de Microsoft.
        """
        try:
            app = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                authority=f"{URL_LOGIN}/{self.tenant_id}",
                client_credential=self.client_secret,
            )
            # MSAL cachea tokens; primero intenta silencioso.
            resultado = app.acquire_token_silent(SCOPE, account=None)
            if not resultado:
                resultado = app.acquire_token_for_client(scopes=SCOPE)
        except Exception as exc:  # noqa: BLE001 - se traduce a mensaje claro
            raise GraphAuthError(
                "No se pudo iniciar la autenticacion con Microsoft. "
                "Revisa la conexion de red y los datos del .env."
            ) from exc

        if not resultado or "access_token" not in resultado:
            # No incluir tokens ni secretos en el mensaje.
            descripcion = ""
            if isinstance(resultado, dict):
                descripcion = resultado.get("error_description", "").splitlines()[:1]
                descripcion = descripcion[0] if descripcion else resultado.get("error", "")
            raise GraphAuthError(
                "Autenticacion fallida contra Microsoft Graph. "
                "Verifica TENANT_ID, CLIENT_ID, CLIENT_SECRET y el permiso Mail.Read. "
                f"Detalle: {descripcion}"
            )

        self._token = resultado["access_token"]
        logger.info("Autenticacion correcta")
        return True

    def _cabeceras(self):
        if not self._token:
            raise GraphError("Debes autenticar (obtener_token) antes de consultar.")
        return {"Authorization": f"Bearer {self._token}"}

    # ---------------------------------------------- unico metodo de red (GET)

    def _get(self, url, params=None, stream=False):
        """
        Realiza un HTTP GET con TLS verificado, timeout y reintentos acotados.

        Este es el UNICO metodo que habla con el buzon: garantiza READ ONLY.
        No hay (a proposito) metodos POST/PATCH/PUT/DELETE.
        """
        intento = 0
        while True:
            intento += 1
            try:
                respuesta = self._sesion.get(
                    url,
                    headers=self._cabeceras(),
                    params=params,
                    timeout=TIMEOUT_SEGUNDOS,
                    stream=stream,
                    # verify por defecto = True (TLS verificado). Nunca desactivar.
                )
            except requests.exceptions.RequestException as exc:
                if intento <= MAX_REINTENTOS:
                    self._esperar(intento)
                    continue
                raise GraphError(
                    "Error de red al consultar Microsoft Graph tras varios intentos."
                ) from exc

            codigo = respuesta.status_code

            if codigo == 200:
                return respuesta

            if codigo == 401:
                raise GraphError(
                    "Microsoft Graph respondio 401 (no autorizado). "
                    "Revisa las credenciales del .env."
                )
            if codigo == 403:
                raise GraphError(
                    "Microsoft Graph respondio 403 (prohibido). "
                    "Falta el permiso Mail.Read o el Admin Consent."
                )
            if codigo == 404:
                raise GraphError(
                    "Microsoft Graph respondio 404 (no encontrado). "
                    "Revisa el buzon (MAILBOX) o el recurso solicitado."
                )
            if codigo == 429:
                # Throttling: respetar Retry-After.
                if intento <= MAX_REINTENTOS:
                    espera = self._retry_after(respuesta, intento)
                    logger.warning(
                        "Microsoft Graph respondio 429 (throttling). "
                        "Esperando %d segundos antes de reintentar.", espera
                    )
                    time.sleep(espera)
                    continue
                raise GraphError("Microsoft Graph sigue limitando las solicitudes (429).")
            if codigo in CODIGOS_REINTENTABLES:
                if intento <= MAX_REINTENTOS:
                    logger.warning(
                        "Microsoft Graph respondio %d. Reintentando (%d/%d).",
                        codigo, intento, MAX_REINTENTOS
                    )
                    self._esperar(intento)
                    continue
                raise GraphError(
                    f"Microsoft Graph respondio {codigo} tras varios intentos."
                )

            # Otros codigos: no reintentar.
            raise GraphError(f"Microsoft Graph respondio con codigo inesperado {codigo}.")

    def _validar_tamano(self, respuesta):
        """
        Rechaza una descarga demasiado grande ANTES de leer su cuerpo.

        Usa la cabecera Content-Length que envia Microsoft Graph. Si el tamano
        declarado supera max_download_bytes, se aborta sin cargar el cuerpo en
        memoria (proteccion de recursos, ver steering security.md seccion 9).

        Si no hay limite configurado o la cabecera no viene, no bloquea aqui:
        sample.py aplica el control final sobre los bytes ya escritos.
        """
        if not self.max_download_bytes:
            return
        valor = respuesta.headers.get("Content-Length")
        if not valor:
            return
        try:
            tamano = int(valor)
        except ValueError:
            return
        if tamano > self.max_download_bytes:
            # Cerrar la conexion sin descargar el cuerpo.
            respuesta.close()
            raise GraphError(
                "El adjunto supera el tamano maximo permitido "
                f"({tamano} bytes > {self.max_download_bytes} bytes). "
                "No se descarga."
            )

    def _esperar(self, intento):
        """Espera con backoff exponencial acotado."""
        espera = min(BACKOFF_BASE_SEGUNDOS * (2 ** (intento - 1)), BACKOFF_MAX_SEGUNDOS)
        time.sleep(espera)

    def _retry_after(self, respuesta, intento):
        """Calcula la espera para un 429 respetando Retry-After si viene."""
        valor = respuesta.headers.get("Retry-After")
        if valor:
            try:
                return min(int(valor), BACKOFF_MAX_SEGUNDOS)
            except ValueError:
                pass
        return min(BACKOFF_BASE_SEGUNDOS * (2 ** (intento - 1)), BACKOFF_MAX_SEGUNDOS)

    # --------------------------------------------------------- leer mensajes

    def buscar_mensajes(self, desde_iso, hasta_iso):
        """
        Lista los correos de la Bandeja de entrada recibidos entre desde_iso y
        hasta_iso (texto ISO en UTC). Solo GET, con $select, filtro y paginacion.
        """
        url = f"{URL_GRAPH}/users/{self.mailbox}/mailFolders/inbox/messages"
        params = {
            "$filter": (
                f"receivedDateTime ge {desde_iso} and "
                f"receivedDateTime le {hasta_iso}"
            ),
            "$select": "id,subject,from,receivedDateTime,isRead,hasAttachments,internetMessageId",
            "$orderby": "receivedDateTime desc",
            "$top": 50,
        }

        mensajes = []
        respuesta = self._get(url, params=params)
        datos = respuesta.json()
        mensajes.extend(datos.get("value", []))

        # Paginacion con @odata.nextLink.
        siguiente = datos.get("@odata.nextLink")
        while siguiente:
            respuesta = self._get(siguiente)
            datos = respuesta.json()
            mensajes.extend(datos.get("value", []))
            siguiente = datos.get("@odata.nextLink")

        return mensajes

    def obtener_adjuntos(self, message_id):
        """Devuelve la lista de adjuntos de un correo (solo GET)."""
        url = f"{URL_GRAPH}/users/{self.mailbox}/messages/{message_id}/attachments"
        respuesta = self._get(url)
        return respuesta.json().get("value", [])

    def descargar_file_attachment(self, message_id, attachment):
        """
        Devuelve los bytes de un fileAttachment.
        Prefiere contentBytes (base64); si no viene, usa /$value.
        """
        contenido_b64 = attachment.get("contentBytes")
        if contenido_b64 is not None:
            # Estimar el tamano real desde el largo base64 (aprox 3/4) para
            # rechazar adjuntos enormes antes de decodificarlos en memoria.
            if self.max_download_bytes:
                tamano_estimado = (len(contenido_b64) * 3) // 4
                if tamano_estimado > self.max_download_bytes:
                    raise GraphError(
                        "El adjunto supera el tamano maximo permitido "
                        f"({tamano_estimado} bytes aprox > "
                        f"{self.max_download_bytes} bytes). No se descarga."
                    )
            return base64.b64decode(contenido_b64)

        att_id = attachment["id"]
        url = (
            f"{URL_GRAPH}/users/{self.mailbox}/messages/{message_id}"
            f"/attachments/{att_id}/$value"
        )
        respuesta = self._get(url, stream=True)
        self._validar_tamano(respuesta)  # aborta si es demasiado grande
        return respuesta.content

    def descargar_item_attachment(self, message_id, attachment_id):
        """
        Devuelve el contenido MIME (.eml) de un itemAttachment mediante /$value.
        """
        url = (
            f"{URL_GRAPH}/users/{self.mailbox}/messages/{message_id}"
            f"/attachments/{attachment_id}/$value"
        )
        respuesta = self._get(url, stream=True)
        self._validar_tamano(respuesta)  # aborta si es demasiado grande
        return respuesta.content
