"""Backend de desarrollo: entrar escribiendo solo el usuario.

SOLO PARA EL DOCKER LOCAL. Nunca en producción, ni en nada expuesto a una red.

Existe para no tener que recordar ni resetear contraseñas mientras se prueban
roles distintos: se escribe el nombre de usuario, la contraseña se deja vacía o
con cualquier cosa, y se entra.

Tres cerrojos, y hacen falta los tres a la vez:

1. Este módulo **no se importa desde `base.py`**. Solo `settings/local.py` lo
   añade a `AUTHENTICATION_BACKENDS`, así que producción ni sabe que existe.
2. `DEBUG` tiene que estar activo.
3. `LOGIN_SIN_PASSWORD` tiene que valer `True` explícitamente.

Si alguno falla, el backend se comporta como si no estuviera. Un cerrojo que se
puede olvidar no es un cerrojo, y por eso son tres en vez de uno.

Hay un test —`SinPasswordNoLlegaAProduccionTests`— que lee `production.py` y
falla si este backend aparece ahí.
"""

import logging

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm

logger = logging.getLogger(__name__)


class LoginSinPasswordDevBackend:
    """Autentica por nombre de usuario, sin comprobar la contraseña."""

    def _habilitado(self):
        return bool(getattr(settings, "DEBUG", False)) and bool(
            getattr(settings, "LOGIN_SIN_PASSWORD", False)
        )

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not self._habilitado():
            return None
        if not username:
            return None

        User = get_user_model()
        usuario = User.objects.filter(username__iexact=username, is_active=True).first()
        if usuario is None:
            return None

        # Se registra siempre: si esto aparece en un log que no sea el del
        # portátil de alguien, es una emergencia.
        logger.warning(
            "DEV: login sin contraseña de '%s'. Esto no debe verse fuera de local.",
            usuario.username,
        )
        return usuario

    def get_user(self, user_id):
        if not self._habilitado():
            return None
        User = get_user_model()
        return User.objects.filter(pk=user_id, is_active=True).first()


class LoginSinPasswordForm(AuthenticationForm):
    """Formulario de desarrollo que autentica aunque la contrasena venga vacia.

    No basta con marcar el campo como opcional. `AuthenticationForm.clean()`
    solo llama a `authenticate()` cuando la contrasena tiene contenido:

        if username is not None and password:

    Con la contrasena vacia se saltaba esa rama, el formulario quedaba valido
    con `user_cache = None`, y el fallo aparecia despues al intentar iniciar
    sesion con None —con un mensaje sobre backends que no tenia nada que ver.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].required = False

    def clean(self):
        usuario = self.cleaned_data.get("username")
        clave = self.cleaned_data.get("password")
        if usuario and not clave:
            self.user_cache = authenticate(self.request, username=usuario, password="")
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
            return self.cleaned_data
        return super().clean()
