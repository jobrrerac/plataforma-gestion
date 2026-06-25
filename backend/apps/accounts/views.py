from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.http import HttpResponseForbidden

_MAX_INTENTOS = 5
_BLOQUEO_SEGUNDOS = 15 * 60  # 15 minutos


def _cache_key(request):
    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", ""))
        .split(",")[0]
        .strip()
    )
    return f"login_fail_{ip}"


class LoginRateLimitView(LoginView):
    """
    LoginView estándar de Django con bloqueo por IP tras N intentos fallidos.
    No requiere paquetes externos; usa el cache de Django (memcache/redis/local).
    """

    def dispatch(self, request, *args, **kwargs):
        key = _cache_key(request)
        intentos = cache.get(key, 0)
        if intentos >= _MAX_INTENTOS:
            minutos = _BLOQUEO_SEGUNDOS // 60
            return HttpResponseForbidden(
                f"Demasiados intentos fallidos. Espere {minutos} minutos e intente de nuevo."
            )
        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        key = _cache_key(self.request)
        intentos = cache.get(key, 0) + 1
        cache.set(key, intentos, timeout=_BLOQUEO_SEGUNDOS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_cache_key(self.request))
        return super().form_valid(form)
