from .base import *  # noqa: F401, F403

DEBUG = False

# ---------------------------------------------------------------------------
# Estáticos
# ---------------------------------------------------------------------------
# En Container Apps no hay nginx delante: el ingress habla directo con gunicorn,
# así que los estáticos los sirve el propio proceso. WhiteNoise va justo después
# de SecurityMiddleware y antes que todo lo demás: un fichero estático no
# necesita sesión, CSRF ni tocar la base de datos.
MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
    "whitenoise.middleware.WhiteNoiseMiddleware",
)

# Comprime (gzip + brotli) y versiona cada fichero con un hash en el nombre, lo
# que permite servirlos con cache inmutable. Exige haber corrido collectstatic,
# que la imagen hace en tiempo de build (ver backend/Dockerfile).
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# Necesario detrás del ingress de Azure para que pasen los POST con CSRF
# (ej: https://miapp.azurewebsites.net)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Cookies seguras (requiere HTTPS en el servidor)
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True

# La sesion caduca por INACTIVIDAD, no por reloj.
#
# Antes eran 8 horas contadas desde el login, que es lo peor de los dos mundos:
# demasiado para una pantalla que alguien deja abierta y se va, y a la vez echa
# a quien lleva ocho horas trabajando de verdad, en mitad de lo que este
# haciendo.
#
# Con SESSION_SAVE_EVERY_REQUEST la marca de tiempo se renueva en cada peticion,
# asi que el contador mide tiempo sin tocar nada. Quien esta trabajando no se
# entera; una pantalla olvidada se cierra sola en una hora.
#
# Una hora y no menos porque el almuerzo ronda esa duracion: bajarlo a 30
# minutos obligaria a volver a entrar varias veces al dia y la gente acabaria
# dejando la sesion abierta a proposito, que es justo lo contrario de lo que se
# busca. Y no mas porque el escenario que preocupa —el puesto compartido con la
# aplicacion abierta— sigue vivo todo ese rato.
SESSION_COOKIE_AGE = 3600  # 1 hora SIN ACTIVIDAD
SESSION_SAVE_EVERY_REQUEST = True

# Cerrar el navegador cierra la sesion. No sustituye a lo de arriba (mucha gente
# no cierra nunca el navegador), pero cubre el caso del portatil prestado.
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

# Headers de seguridad
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# Azure Container Apps (y cualquier proxy/LB) termina TLS en el ingress y reenvía HTTP al
# contenedor. Sin este header Django no detecta HTTPS y puede entrar en loop de redirección.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True

# Las sondas de Container Apps llegan por HTTP desde dentro del entorno, sin
# X-Forwarded-Proto. Sin esta exención recibirían un 301, la plataforma daría la
# revisión por no sana y la app nunca llegaría a recibir tráfico.
# Las rutas van sin la barra inicial: Django compara contra request.path[1:].
SECURE_REDIRECT_EXEMPT = [
    r"^healthz/$",
    r"^readyz/$",
]
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True  # activar tras confirmar que el dominio funciona bien varios días

# Cache compartido entre workers/instancias. El rate limiting del login cuenta
# intentos en el cache: con LocMemCache (default) el contador es por proceso y
# gunicorn corre varios workers, así que el límite real se multiplica.
# Requiere crear la tabla una vez: python manage.py createcachetable
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

# Sin BasicAuthentication en producción
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # type: ignore[name-defined]  # noqa: F405
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
}

# Log a stdout: Container Apps recoge la salida estándar del contenedor y la
# envía a Log Analytics. Escribir a un archivo dentro del contenedor perdería
# los registros en cada reinicio.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"},
    },
    "handlers": {
        "consola": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {"handlers": ["consola"], "level": "INFO"},
    "loggers": {
        # Altas, cambios de rol y accesos denegados por SSO.
        "apps.accounts.oidc": {"level": "INFO", "propagate": True},
        # Ruido de las sondas: solo interesa cuando algo falla de verdad.
        "django.server": {"level": "WARNING", "propagate": True},
    },
}
