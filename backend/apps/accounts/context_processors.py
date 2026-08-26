"""Roles disponibles en las plantillas.

Sin esto, los templates solo pueden mirar `user.is_staff`, que no es lo mismo
que el rol del proyecto: el RBAC vive en los grupos Admin/PM/Ingeniero
(ver `apps.accounts.roles`). Usar `is_staff` como sustituto funciona por
casualidad mientras los datos acompañen, y deja de funcionar en cuanto alguien
crea un PM sin acceso al admin de Django.
"""

from apps.accounts import roles


def roles_usuario(request):
    usuario = getattr(request, "user", None)
    return {
        "es_admin": roles.es_admin(usuario),
        "es_admin_o_pm": roles.es_admin_o_pm(usuario),
        "puede_ver_costos": roles.puede_ver_costos(usuario),
    }
