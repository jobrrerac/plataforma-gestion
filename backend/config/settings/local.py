import sys

from .base import *  # noqa: F401, F403

DEBUG = True
CORS_ALLOW_ALL_ORIGINS = True

# BasicAuthentication solo en desarrollo para usar el DRF browsable API
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # type: ignore[name-defined]  # noqa: F405
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
}

# ---------------------------------------------------------------------------
# Entrar escribiendo solo el usuario (SOLO desarrollo)
# ---------------------------------------------------------------------------
# Para poder saltar entre roles sin recordar ni resetear contrasenas. Se activa
# con LOGIN_SIN_PASSWORD=True en el .env; por defecto NO esta activo, asi que
# hay que pedirlo a proposito.
#
# El backend vive en un modulo que `base.py` no importa, asi que produccion ni
# sabe que existe. Ademas exige DEBUG. Ver apps/accounts/backends_dev.py.
LOGIN_SIN_PASSWORD = env.bool("LOGIN_SIN_PASSWORD", default=False)

# Nunca durante los tests. Con la bandera activa, la suite entera autentica sin
# comprobar contrasenas: 93 pruebas pasaron a fallar la primera vez, y las que
# NO fallaban eran peores —las de bloqueo por intentos, por ejemplo, dejaban de
# probar nada porque cualquier login funcionaba. Un atajo de desarrollo no puede
# decidir si los tests de autenticacion significan algo.
if "test" in sys.argv:
    LOGIN_SIN_PASSWORD = False

if LOGIN_SIN_PASSWORD:
    # Delante del ModelBackend: si acierta, no se comprueba contrasena.
    AUTHENTICATION_BACKENDS = [
        "apps.accounts.backends_dev.LoginSinPasswordDevBackend",
        *AUTHENTICATION_BACKENDS,
    ]
