from django.conf import settings
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache

from apps.accounts.models import CambioPasswordPendiente

_MAX_INTENTOS = 5
_BLOQUEO_SEGUNDOS = 15 * 60  # 15 minutos


def _clave_usuario(request):
    """Clave del contador de fallos, siempre por cuenta.

    Antes se contaba tambien por IP y se bloqueaba si CUALQUIERA de los dos
    contadores llegaba al limite. Eso convertia el bloqueo en global: cinco
    intentos fallidos de una cuenta dejaban fuera a todo el mundo.

    Y no era solo por la NAT corporativa, que ya bastaria: detras del ingress de
    Azure el ultimo valor de X-Forwarded-For es a menudo la IP interna del
    propio proxy. En el cache de produccion habia literalmente una clave
    `login_fail_ip_100.100.0.31` —una direccion privada de Azure, de ningun
    cliente— compartida por todas las peticiones. No limitaba a un atacante:
    era un interruptor que cualquiera podia accionar para dejar la aplicacion
    inaccesible durante quince minutos.

    Contar por cuenta es lo que de verdad protege frente a fuerza bruta contra
    una cuenta concreta, que es el ataque que este bloqueo existe para frenar.
    """
    usuario = (request.POST.get("username") or "").strip().lower()
    return f"login_fail_user_{usuario}" if usuario else ""


class LoginRateLimitView(LoginView):
    """
    LoginView estandar de Django con bloqueo **por cuenta** tras N intentos
    fallidos. No requiere paquetes externos; usa el cache de Django.

    Limitacion conocida: no hay freno para un ataque de pulverizacion —probar
    una contrasena comun contra muchas cuentas distintas—, porque cada cuenta
    tiene su propio contador. Frenarlo requiere limitar por origen, y para eso
    hace falta resolver bien la IP real del cliente (numero de proxies de
    confianza) o apoyarse en el WAF de Azure. Se deja anotado en vez de fingir
    que el contador por IP lo cubria: no lo hacia, y ademas bloqueaba a todos.
    """

    def _bloqueada(self, request):
        clave = _clave_usuario(request)
        return bool(clave) and cache.get(clave, 0) >= _MAX_INTENTOS

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST" and self._bloqueada(request):
            minutos = _BLOQUEO_SEGUNDOS // 60
            # Se repinta el formulario con el aviso dentro. Antes se devolvia un
            # 403 en texto plano, fuera de la pagina: parecia una caida, no un
            # bloqueo con su motivo. Y 429 es el codigo que corresponde.
            contexto = self.get_context_data(form=self.get_form())
            contexto["error_bloqueo"] = (
                f"Demasiados intentos fallidos con esta cuenta. "
                f"Espere {minutos} minutos e intente de nuevo."
            )
            return self.render_to_response(contexto, status=429)
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        clave = _clave_usuario(self.request)
        if clave:
            cache.set(clave, cache.get(clave, 0) + 1, timeout=_BLOQUEO_SEGUNDOS)
        return super().form_invalid(form)

    def form_valid(self, form):
        clave = _clave_usuario(self.request)
        if clave:
            cache.delete(clave)
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
