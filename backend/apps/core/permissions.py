from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.accounts.roles import es_admin, es_admin_o_pm


class EsAdmin(BasePermission):
    """Solo usuarios del grupo Admin (o superusuarios)."""
    message = "Se requiere rol Admin para esta acción."

    def has_permission(self, request, view):
        return es_admin(request.user)


class EsAdminOPM(BasePermission):
    """Usuarios del grupo Admin o PM (o superusuarios)."""
    message = "Se requiere rol Admin o PM para esta acción."

    def has_permission(self, request, view):
        return es_admin_o_pm(request.user)


class SoloLecturaOAdmin(BasePermission):
    """
    GET/HEAD/OPTIONS: cualquier usuario autenticado.
    POST/PUT/PATCH/DELETE: solo Admin.
    """
    message = "Se requiere rol Admin para modificar este recurso."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return True
        return es_admin(request.user)
