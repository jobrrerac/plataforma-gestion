from django.contrib import admin

from apps.core.admin_mixins import SoftDeleteAdminMixin

from .models import DiaLegalizado, RegistroHoras, TipoActividad


@admin.register(TipoActividad)
class TipoActividadAdmin(admin.ModelAdmin):
    list_display = ["nombre", "descripcion", "requiere_proyecto", "activo", "orden"]
    list_filter = ["requiere_proyecto", "activo"]
    list_editable = ["activo", "orden"]
    ordering = ["orden", "nombre"]


class RegistroHorasInline(admin.TabularInline):
    model = RegistroHoras
    extra = 0
    fields = ["tipo_actividad", "proyecto", "horas", "detalle"]


@admin.register(DiaLegalizado)
class DiaLegalizadoAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ["recurso", "fecha", "estado", "total_horas", "jornada_esperada", "registrado_en"]
    list_filter = ["estado", "fecha"]
    search_fields = ["recurso__nombre", "recurso__email"]
    date_hierarchy = "fecha"
    inlines = [RegistroHorasInline]
    readonly_fields = ["registrado_en", "aprobado_por", "aprobado_en", "total_horas"]
    exclude = ["deleted_at", "created_at", "updated_at"]
