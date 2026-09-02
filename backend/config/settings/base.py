from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# En Docker las vars llegan por env_file; en local sin Docker, lee el .env de la raíz del repo
environ.Env.read_env(BASE_DIR.parent / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost"])

# Máscaras de formato SAP (proyecto/PEP/grafo). False = solo avisos; True = bloquea al guardar.
SAP_VALIDACION_ESTRICTA = env.bool("SAP_VALIDACION_ESTRICTA", default=False)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "mozilla_django_oidc",
    "apps.core",
    "apps.calendar_engine",
    "apps.assignments",
    "apps.accounts",
    "apps.dashboard",
    "apps.legalizacion",
    # Triaje de la cola de aprobacion. Se puede quitar de aqui: la pantalla
    # vuelve a pintarse sin bandas y nada mas cambia.
    "apps.revision",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.ForzarCambioPasswordMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Roles del proyecto en plantillas. `user.is_staff` no equivale
                # al rol: el RBAC vive en los grupos Admin/PM/Ingeniero.
                "apps.accounts.context_processors.roles_usuario",
                "apps.accounts.context_processors.aviso_caducidad_sso",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env("POSTGRES_PORT", default="5432"),
        # Reutilizar conexiones entre requests: sin esto cada request abre una
        # conexión nueva (caro con una BD gestionada en Azure).
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            # Azure Database for PostgreSQL rechaza las conexiones sin TLS
            # (require_secure_transport = ON). En local el postgres de
            # desarrollo no tiene certificado, por eso el default es "prefer":
            # usa TLS si está disponible y no falla si no lo está.
            "sslmode": env("POSTGRES_SSLMODE", default="prefer"),
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# En desarrollo y en los tests los estáticos los sirve la app `staticfiles` de
# Django directamente desde las carpetas de cada app. WhiteNoise y el
# almacenamiento con manifiesto se activan solo en producción
# (ver settings/production.py): exigen un `collectstatic` previo.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "20/min",
        "user": "200/min",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
}

# ---------------------------------------------------------------------------
# Autenticación: login local + SSO de Microsoft Entra ID
# ---------------------------------------------------------------------------
# Los dos backends conviven y se prueban en orden. ModelBackend va primero para
# que el superusuario de emergencia y las cuentas locales sigan entrando aunque
# Entra esté caído o el secreto de cliente haya caducado.
#
# OIDC_HABILITADO=False deja la app funcionando solo con usuario/contraseña: es
# el interruptor de emergencia si el SSO falla en producción.

OIDC_HABILITADO = env.bool("OIDC_HABILITADO", default=False)

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "apps.accounts.oidc.EntraOIDCBackend",
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/login/"

_OIDC_TENANT = env("OIDC_TENANT_ID", default="")
_OIDC_AUTORIDAD = f"https://login.microsoftonline.com/{_OIDC_TENANT}"

OIDC_RP_CLIENT_ID = env("OIDC_RP_CLIENT_ID", default="")
OIDC_RP_CLIENT_SECRET = env("OIDC_RP_CLIENT_SECRET", default="")

# Entra firma los tokens con RS256 y publica sus claves en el endpoint JWKS.
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_OP_JWKS_ENDPOINT = f"{_OIDC_AUTORIDAD}/discovery/v2.0/keys"
OIDC_OP_AUTHORIZATION_ENDPOINT = f"{_OIDC_AUTORIDAD}/oauth2/v2.0/authorize"
OIDC_OP_TOKEN_ENDPOINT = f"{_OIDC_AUTORIDAD}/oauth2/v2.0/token"
OIDC_OP_USER_ENDPOINT = "https://graph.microsoft.com/oidc/userinfo"

# Solo lo necesario para identificar a la persona. Sin permisos de directorio:
# los roles llegan dentro del propio id_token.
OIDC_RP_SCOPES = "openid email profile"

# Sin MFA ni acceso condicional por decisión de producto: el objetivo es no
# memorizar varias contraseñas. Eso se configura en Entra (requiere licencia
# P1), no aquí, pero se deja anotado para que nadie lo dé por supuesto.

# true  = a quien entre por SSO y no exista en Django se le crea la cuenta.
# false = solo entran usuarios ya existentes (hay que precrearlos).
OIDC_CREAR_USUARIOS = env.bool("OIDC_CREAR_USUARIOS", default=True)

# Equivalencias de dominio entre Entra y la identidad de negocio.
#
# El tenant `inetumoffshore.onmicrosoft.com` no tiene verificado el dominio
# corporativo, así que los UPN son `nombre@inetumoffshore.onmicrosoft.com`
# mientras que en la plataforma (y en `Recurso.email`) las personas son
# `nombre@inetum.com`. Sin esta traducción el SSO no reconocería a nadie y
# crearía cuentas duplicadas, dejando huérfano todo el historial de
# asignaciones.
#
# Formato: dominio_del_token=dominio_canonico[,otro=otro]
OIDC_DOMINIO_ALIAS = env.dict("OIDC_DOMINIO_ALIAS", default={})

# Equivalencias explícitas entre una identidad de Entra y una cuenta concreta
# de Django, para los casos en que el UPN no deriva de ningún email de negocio.
#
# El caso que motiva esto: `admin@inetumoffshore.onmicrosoft.com` es la cuenta
# administrativa del tenant, y debe entrar como el superusuario `inetum_admin`
# que ya existe en la plataforma, no como una cuenta nueva llamada "admin".
#
# Una cuenta enlazada así conserva su identidad de negocio: el SSO NO le
# sobreescribe email ni nombre, porque el alias significa "esta identidad de
# Entra ES esta cuenta", no "cópiale los datos del token".
#
# Formato: upn_de_entra=username_de_django[,otro=otro]
OIDC_USUARIO_ALIAS = env.dict("OIDC_USUARIO_ALIAS", default={})

# Fecha ISO en la que caduca el secreto de cliente del SSO, inyectada por
# Terraform. Azure no avisa de esto por ningún canal: el día que ocurre, el
# botón de Microsoft deja de funcionar sin mensaje ni correo, y hay que saber
# dónde mirar para entender por qué. La aplicación lo avisa por su cuenta a los
# Admin con antelación, porque para entonces es probable que quien montó el
# despliegue ya no esté.
#
# Vacío = sin aviso (desarrollo local, o SSO desactivado).
OIDC_SECRETO_CADUCA = env("OIDC_SECRETO_CADUCA", default="")
OIDC_DIAS_AVISO_CADUCIDAD = env.int("OIDC_DIAS_AVISO_CADUCIDAD", default=60)

# Cierra la sesión de Django al salir; no cierra la sesión del navegador con
# Microsoft (eso obligaría a volver a poner la contraseña en otras apps).
OIDC_STORE_ID_TOKEN = True

# Tras un login correcto, adónde va el usuario.
OIDC_REDIRECT_REQUIRE_HTTPS = not DEBUG
