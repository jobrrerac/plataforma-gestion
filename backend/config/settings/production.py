from .base import *  # noqa: F401, F403

DEBUG = False

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# Necesario detrás del ingress de Azure para que pasen los POST con CSRF
# (ej: https://miapp.azurewebsites.net)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Cookies seguras (requiere HTTPS en el servidor)
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 28800  # 8 horas
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

# Headers de seguridad
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
REFERRER_POLICY = "same-origin"

# Azure Container Apps (y cualquier proxy/LB) termina TLS en el ingress y reenvía HTTP al
# contenedor. Sin este header Django no detecta HTTPS y puede entrar en loop de redirección.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True  # activar tras confirmar que el dominio funciona bien varios días

# Sin BasicAuthentication en producción
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # type: ignore[name-defined]  # noqa: F405
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
}
