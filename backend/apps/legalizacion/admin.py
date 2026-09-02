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


@admin.register(RegistroHoras)
class RegistroHorasAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Solo para leer qué avisos del triaje se están anulando.

    Pedir un motivo al firmar un día con avisos no sirve de nada si después
    nadie puede leerlo. Filtrando por «aprobación forzada» sale la lista de qué
    regla se salta todo el mundo, que es justo la que hay que corregir antes de
    añadir ninguna otra.

    **No se escribe desde aquí.** Aprobar es `aprobar_registro`, que relee bajo
    bloqueo y comprueba quién puede firmar qué; un formulario de admin sobre
    `estado` sería una segunda vía de aprobación sin ninguna de esas dos cosas.
    """

    list_display = [
        "dia", "destino", "horas", "estado",
        "aprobacion_forzada", "reglas_anuladas", "motivo_aprobacion",
    ]
    list_filter = ["aprobacion_forzada", "estado", "dia__fecha"]
    search_fields = ["dia__recurso__nombre", "detalle", "motivo_aprobacion"]
    date_hierarchy = "dia__fecha"
    ordering = ["-dia__fecha"]

    @admin.display(description="Destino")
    def destino(self, obj):
        return obj.proyecto.codigo if obj.proyecto_id else obj.tipo_actividad.nombre

    @admin.display(description="Avisos anulados")
    def reglas_anuladas(self, obj):
        return ", ".join(obj.senales_anuladas) or "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DiaLegalizado)
class DiaLegalizadoAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    list_display = ["recurso", "fecha", "estado", "total_horas", "jornada_esperada", "registrado_en"]
    list_filter = ["estado", "fecha"]
    search_fields = ["recurso__nombre", "recurso__email"]
    date_hierarchy = "fecha"
    inlines = [RegistroHorasInline]
    readonly_fields = ["registrado_en", "aprobado_por", "aprobado_en", "total_horas"]
    exclude = ["deleted_at", "created_at", "updated_at"]
