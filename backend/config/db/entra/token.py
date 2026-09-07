"""Token de Microsoft Entra para conectarse a PostgreSQL sin contraseña.

Azure Container Apps expone un endpoint local desde el que el contenedor pide
tokens en nombre de su identidad administrada. El token vale entre 5 y 60
minutos y se usa **en el sitio de la contraseña**: PostgreSQL no sabe nada de
Entra, solo recibe una cadena larga por el protocolo de siempre.

La gracia frente a la contraseña de `pgadmin` no es que sea más difícil de
adivinar —las dos son impredecibles— sino que **caduca sola y no la conoce
nadie**. No hay que rotarla, no se puede filtrar en un correo y no sobrevive al
contenedor que la pidió.

**Fuera de Azure esto no existe y no debe estorbar.** En el Docker de
desarrollo no hay `IDENTITY_ENDPOINT`, así que `obtener()` devuelve `None` y
quien llama se queda con la contraseña de siempre. Ese es el comportamiento
correcto, no un caso degradado.

Se usa `urllib` de la biblioteca estándar y no `requests` a propósito:
`requests` está instalado, pero como dependencia de `mozilla-django-oidc`, no
porque este proyecto la pida. Apoyarse en una dependencia transitiva es cómodo
hasta el día que el paquete de arriba la cambia.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# Identificador del recurso «PostgreSQL de Azure» ante Entra. No es una URL que
# se visite: es el `aud` que llevará el token para que el servidor lo acepte.
RECURSO = "https://ossrdbms-aad.database.windows.net"

# Versión mínima del endpoint de identidad de Container Apps.
API_VERSION = "2019-08-01"

# Cuánto antes de que caduque se pide uno nuevo. Un token que caduca mientras
# se abre la conexión da un fallo de autenticación indistinguible de una
# contraseña mala, así que conviene no apurar.
MARGEN_SEGUNDOS = 300

TIEMPO_LIMITE = 5  # el endpoint es local; si tarda más, algo va mal

_candado = threading.Lock()
_cache = {"token": None, "caduca_en": 0.0}


def _endpoint():
    """URL y cabecera del servicio de identidad, o `None` si no hay ninguno."""
    url = os.environ.get("IDENTITY_ENDPOINT")
    cabecera = os.environ.get("IDENTITY_HEADER")
    if not url or not cabecera:
        return None
    return url, cabecera


def disponible() -> bool:
    """Si este proceso corre donde hay una identidad administrada."""
    return _endpoint() is not None


def _pedir(client_id):
    url, cabecera = _endpoint()
    consulta = {"resource": RECURSO, "api-version": API_VERSION}
    # Obligatorio con identidad asignada por el usuario: sin `client_id`, el
    # servicio busca una identidad asignada por el sistema —que aquí no
    # existe— y devuelve un error que no dice eso.
    if client_id:
        consulta["client_id"] = client_id

    peticion = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(consulta)}",
        headers={"X-IDENTITY-HEADER": cabecera},
    )
    with urllib.request.urlopen(peticion, timeout=TIEMPO_LIMITE) as respuesta:
        datos = json.load(respuesta)
    return datos["access_token"], float(datos["expires_on"])


def obtener(client_id=None, ahora=None):
    """El token vigente, pidiéndolo si hace falta. `None` si no se puede.

    Nunca lanza: quien llama tiene que poder seguir con la contraseña. Un fallo
    aquí no puede tumbar la aplicación — para eso se dejó la contraseña activa
    en el servidor.
    """
    if not disponible():
        return None

    ahora = ahora if ahora is not None else time.time()
    with _candado:
        if _cache["token"] and ahora < _cache["caduca_en"] - MARGEN_SEGUNDOS:
            return _cache["token"]
        try:
            token, caduca_en = _pedir(client_id)
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
            # Sin `exc_info`: el cuerpo de la respuesta puede traer detalles del
            # endpoint de identidad y esto va a un log que se consulta a diario.
            logger.warning(
                "No se pudo obtener el token de Entra (%s). "
                "Se usará la contraseña.", type(exc).__name__,
            )
            _cache.update(token=None, caduca_en=0.0)
            return None
        _cache.update(token=token, caduca_en=caduca_en)
        logger.info(
            "Token de Entra obtenido; vale %d minutos.",
            max(0, int((caduca_en - ahora) // 60)),
        )
        return token


def olvidar():
    """Tira el token cacheado. Para las pruebas y para reintentar tras un fallo."""
    with _candado:
        _cache.update(token=None, caduca_en=0.0)
