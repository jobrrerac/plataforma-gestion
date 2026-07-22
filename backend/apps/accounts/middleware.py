from django.shortcuts import redirect
from django.urls import reverse


class ForzarCambioPasswordMiddleware:
    """
    Si el usuario autenticado tiene un `CambioPasswordPendiente`, lo redirige a
    la página de cambio de contraseña y no lo deja acceder a nada más hasta que
    la cambie. Se excluyen las rutas imprescindibles para poder cambiarla o
    salir (la propia página de cambio, logout y estáticos), para no crear un
    bucle de redirecciones.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _rutas_permitidas(self):
        # Se calcula por request (reverse necesita el resolver cargado).
        return {
            reverse("password-cambiar"),
            reverse("logout"),
        }

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            path = request.path
            excluidas = self._rutas_permitidas()
            es_estatico = path.startswith("/static/") or path.startswith("/media/")
            if path not in excluidas and not es_estatico:
                # .exists() evita traer el objeto; una sola query indexada por el OneToOne.
                if CambioPasswordPendiente.objects.filter(usuario=user).exists():
                    return redirect("password-cambiar")
        return self.get_response(request)


# Import diferido para evitar cargar los modelos antes de que las apps estén listas.
from apps.accounts.models import CambioPasswordPendiente  # noqa: E402
