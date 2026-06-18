from django.contrib import admin
from .models import Asignacion, LogAuditoria


class LogAuditoriaInline(admin.TabularInline):
    model = LogAuditoria
    extra = 0
    readonly_fields = ["accion", "actor", "timestamp", "detalle"]
    can_delete = False
    show_change_link = False


@admin.register(Asignacion)
class AsignacionAdmin(admin.ModelAdmin):
    list_display = [
        "recurso", "proyecto", "estado",
        "horas_totales", "intensidad_diaria",
        "fecha_inicio", "fecha_fin",
    ]
    list_filter = ["estado", "politica_ausencia", "proyecto"]
    search_fields = ["recurso__nombre", "proyecto__codigo"]
    readonly_fields = ["fecha_fin", "tarifa_aplicada", "costo_estimado", "solicitada_por", "created_at"]
    inlines = [LogAuditoriaInline]


@admin.register(LogAuditoria)
class LogAuditoriaAdmin(admin.ModelAdmin):
    list_display = ["asignacion", "accion", "actor", "timestamp"]
    readonly_fields = ["asignacion", "accion", "actor", "timestamp", "detalle"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
