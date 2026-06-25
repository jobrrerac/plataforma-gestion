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
