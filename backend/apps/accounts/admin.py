"""
RBAC: Admin Django gestiona los grupos (Admin, PM, Ingeniero).
Crear los grupos en: /admin/auth/group/
SSO con Entra ID se implementa en la fase A2.
"""
from django.contrib import admin

from apps.accounts.models import CambioPasswordPendiente


@admin.register(CambioPasswordPendiente)
class CambioPasswordPendienteAdmin(admin.ModelAdmin):
    list_display = ("usuario", "motivo", "creado_en")
    search_fields = ("usuario__username", "usuario__email")
    readonly_fields = ("creado_en",)
    autocomplete_fields = ("usuario",)
