"""Contexto global de plantillas: roles y avisos de operación.

Sin esto, los templates solo pueden mirar `user.is_staff`, que no es lo mismo
que el rol del proyecto: el RBAC vive en los grupos Admin/PM/Ingeniero
(ver `apps.accounts.roles`). Usar `is_staff` como sustituto funciona por
casualidad mientras los datos acompañen, y deja de funcionar en cuanto alguien
crea un PM sin acceso al admin de Django.
"""

from datetime import datetime, timezone as tz

from django.conf import settings

from apps.accounts import roles


def roles_usuario(request):
    usuario = getattr(request, "user", None)
    return {
        "es_admin": roles.es_admin(usuario),
        "es_admin_o_pm": roles.es_admin_o_pm(usuario),
        "puede_ver_costos": roles.puede_ver_costos(usuario),
        # Aprobar horas ya no depende del rol: un proyecto puede designar a
        # cualquiera como aprobador delegado. Sin esto el enlace no le
        # aparecería y tendría que conocer la URL de memoria.
        "puede_aprobar_horas": _puede_aprobar_horas(usuario),
    }


def _puede_aprobar_horas(usuario):
    if not usuario or not usuario.is_authenticated:
        return False
    if roles.es_admin_o_pm(usuario):
        return True
    from apps.core.models import Proyecto
    return Proyecto.objects.filter(aprobador_delegado=usuario).exists()


def _dias_para_caducar():
    """Días que faltan para que caduque el secreto del SSO, o None.

    None si no está configurado (desarrollo local) o si la fecha es ilegible:
    un aviso roto es peor que ninguno, porque enseña a la gente a ignorar la
    barra amarilla.
    """
    valor = getattr(settings, "OIDC_SECRETO_CADUCA", "")
    if not valor:
        return None

    try:
        # Entra devuelve ISO 8601 en UTC, a veces con "Z" en lugar de +00:00.
        caduca = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

    if caduca.tzinfo is None:
        caduca = caduca.replace(tzinfo=tz.utc)

    # Se restan fechas, no instantes: `timedelta.days` redondea hacia abajo, así
    # que "caducó hace 5 días y 2 horas" daría -6 y el aviso diría un día de
    # más. Contar días de calendario es además lo que espera quien lo lee.
    return (caduca.date() - datetime.now(tz.utc).date()).days


def aviso_caducidad_sso(request):
    """Avisa a los Admin de que el secreto del SSO está por caducar.

    Existe porque Azure no avisa de esto por ningún canal. El día que caduca, el
    botón "Iniciar sesión con Microsoft" deja de funcionar sin mensaje, sin
    correo y sin nada en el portal salvo que alguien vaya a mirarlo a propósito.
    Quien lo herede no tiene por qué saber que esa fecha existe.

    Solo se muestra a Admin: es una tarea de operación, y llenar la pantalla de
    todo el mundo con algo que no pueden resolver solo educa a ignorar avisos.
    """
    if not roles.es_admin(getattr(request, "user", None)):
        return {}

    dias = _dias_para_caducar()
    if dias is None or dias > getattr(settings, "OIDC_DIAS_AVISO_CADUCIDAD", 60):
        return {}

    return {
        "sso_dias_para_caducar": dias,
        "sso_secreto_caducado": dias < 0,
        # Las plantillas de Django no tienen valor absoluto, y el caso "caducó
        # hace N días" necesita el número en positivo.
        "sso_dias_desde_caducidad": abs(dias),
    }
