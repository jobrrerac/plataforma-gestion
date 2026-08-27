from django.conf import settings
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache

from apps.accounts.models import CambioPasswordPendiente

_MAX_INTENTOS = 5
_BLOQUEO_SEGUNDOS = 15 * 60  # 15 minutos


def _ip_cliente(request):
    """
    IP del cliente para rate limiting. De X-Forwarded-For solo el ÚLTIMO
    valor es confiable: lo añade nuestro proxy (nginx / ingress de Azure);
    los anteriores los envía el cliente y son falsificables — usar el primero
    permitiría rotar el header para saltarse el límite o bloquear a terceros.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


def _claves(request):
    """Claves de conteo por IP y por username (el username limita fuerza bruta
    dirigida a una cuenta aunque el atacante cambie de IP)."""
    claves = [f"login_fail_ip_{_ip_cliente(request)}"]
    username = (request.POST.get("username") or "").strip().lower()
    if username:
        claves.append(f"login_fail_user_{username}")
    return claves


class LoginRateLimitView(LoginView):
    """
    LoginView estándar de Django con bloqueo por IP y por usuario tras N
    intentos fallidos. No requiere paquetes externos; usa el cache de Django.

    Nota: con LocMemCache (default) el contador es por proceso; si en Azure
    se corre con varios workers/instancias, configurar CACHES con Redis o
    DatabaseCache para que el límite sea global.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            for key in _claves(request):
                if cache.get(key, 0) >= _MAX_INTENTOS:
                    minutos = _BLOQUEO_SEGUNDOS // 60
                    return HttpResponseForbidden(
                        f"Demasiados intentos fallidos. Espere {minutos} minutos e intente de nuevo."
                    )
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        for key in _claves(self.request):
            intentos = cache.get(key, 0) + 1
            cache.set(key, intentos, timeout=_BLOQUEO_SEGUNDOS)
        return super().form_invalid(form)

    def form_valid(self, form):
        for key in _claves(self.request):
            cache.delete(key)
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        # El botón de Microsoft solo aparece si el SSO está configurado. El
        # formulario de usuario/contraseña se muestra siempre: es la vía de
        # entrada cuando Entra falla o el secreto de cliente ha caducado.
        contexto["sso_habilitado"] = bool(
            getattr(settings, "OIDC_HABILITADO", False)
            and getattr(settings, "OIDC_RP_CLIENT_ID", "")
        )
        return contexto


class CambiarPasswordView(PasswordChangeView):
    """
    Cambio de contraseña obligatorio. Usa `PasswordChangeForm` estándar (valida
    la actual y aplica los validadores de fortaleza de Django a la nueva). Al
    completarse, elimina el `CambioPasswordPendiente` para levantar el bloqueo
    del middleware.
    """

    template_name = "registration/password_change_form.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        CambioPasswordPendiente.objects.filter(usuario=self.request.user).delete()
        return response



@never_cache
def salud(request):
    """Endpoint de salud para las sondas de Container Apps.

    Comprueba que el proceso responde y que la base de datos está alcanzable:
    un contenedor vivo que no puede consultar la base no sirve para nada, y sin
    esta comprobación la plataforma lo daría por sano.

    Está exento de la redirección a HTTPS (`SECURE_REDIRECT_EXEMPT` en
    settings/production.py) porque las sondas llegan por HTTP dentro del
    entorno y un 301 las haría fallar.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - cualquier fallo aquí es "no sano"
        return JsonResponse({"estado": "degradado", "base_datos": str(exc)}, status=503)

    return JsonResponse({"estado": "ok", "base_datos": "ok"})


@never_cache
def listo(request):
    """Sonda de arranque: solo confirma que el proceso acepta peticiones.

    Separada de `salud` a propósito: durante el arranque en frío la app puede
    estar levantando antes de que la base responda, y reiniciar el contenedor
    por eso alargaría el arranque en vez de arreglarlo.
    """
    return HttpResponse("ok", content_type="text/plain")
