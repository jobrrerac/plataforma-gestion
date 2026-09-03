"""Backend de PostgreSQL que se conecta con token de Entra, y si no, con contraseña.

Es el backend de siempre de Django con una sola cosa cambiada: de dónde sale la
credencial. Todo lo demás —el dialecto de SQL, las migraciones, el pool de
conexiones— es el de `django.db.backends.postgresql` sin tocar.

**La contraseña es la red, y la red tiene que estar puesta hasta que no haga
falta.** Hay dos formas de que el camino del token falle, y las dos acaban en la
contraseña:

1. **No hay identidad** (Docker local, una prueba, cualquier sitio fuera de
   Azure): `token.obtener()` devuelve `None` y no se intenta siquiera.
2. **Hay token pero el servidor lo rechaza** — el rol de la identidad no existe
   todavía en la base, le faltan permisos, o Entra tuvo un mal momento. Ahí se
   intenta la conexión, falla, se anota y **se reintenta con la contraseña**.

El caso 2 es el que hace que esto se pueda desplegar sin coreografía: da igual
si el rol se crea antes o después del despliegue, porque mientras no exista la
aplicación sigue entrando como siempre. Sin eso habría que sincronizar un
`CREATE ROLE` con un `docker push`, y esa es justo la clase de paso manual que
se olvida.

Tras un rechazo hay un tiempo de espera antes de volver a probar: sin él, cada
conexión pagaría dos intentos, y con `CONN_MAX_AGE = 60` eso es un intento
fallido por minuto y por worker para siempre.

Cuando se conecta con token cambia **también el usuario**: PostgreSQL no
autentica a `pgadmin` con un token de Entra, sino al rol que lleva la etiqueta
de seguridad de la identidad. Por eso `ENTRA["USER"]` va aparte de `USER`.
"""

import logging
import threading
import time

from django.db.backends.postgresql import base

from . import token as token_entra

logger = logging.getLogger(__name__)

# Cuánto se espera antes de volver a intentar el token después de que el
# servidor lo rechace. Diez minutos: suficiente para no castigar cada conexión,
# corto para que crear el rol surta efecto sin redesplegar nada.
ESPERA_TRAS_RECHAZO = 600

_candado = threading.Lock()
_reintentar_a_partir_de = 0.0


def _en_castigo(ahora):
    with _candado:
        return ahora < _reintentar_a_partir_de


def _castigar(ahora):
    global _reintentar_a_partir_de
    with _candado:
        _reintentar_a_partir_de = ahora + ESPERA_TRAS_RECHAZO


def _levantar_castigo():
    """Para las pruebas: olvida el rechazo anterior."""
    global _reintentar_a_partir_de
    with _candado:
        _reintentar_a_partir_de = 0.0


class DatabaseWrapper(base.DatabaseWrapper):
    def get_new_connection(self, conn_params):
        entra = self.settings_dict.get("ENTRA") or {}
        usuario = entra.get("USER")
        ahora = time.time()

        if not usuario or _en_castigo(ahora):
            return super().get_new_connection(conn_params)

        credencial = token_entra.obtener(entra.get("CLIENT_ID"))
        if not credencial:
            return super().get_new_connection(conn_params)

        con_token = dict(conn_params, user=usuario, password=credencial)
        try:
            return super().get_new_connection(con_token)
        except base.Database.Error as exc:
            # `Database.Error` y no solo `OperationalError`: lo que interesa es
            # que ningun fallo del camino nuevo deje a la aplicacion sin base.
            logger.warning(
                "El servidor rechazó el token de Entra para «%s» (%s). "
                "Se entra con la contraseña y no se reintenta en %d minutos.",
                usuario, type(exc).__name__, ESPERA_TRAS_RECHAZO // 60,
            )
            token_entra.olvidar()
            _castigar(ahora)
            return super().get_new_connection(conn_params)
