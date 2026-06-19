from django.contrib import admin
from .models import DiaNoLaborable, Indisponibilidad


@admin.register(DiaNoLaborable)
class DiaNoLaborableAdmin(admin.ModelAdmin):
    list_display = ["fecha", "descripcion", "creado_por", "creado_en"]
    ordering = ["fecha"]


@admin.register(Indisponibilidad)
class IndisponibilidadAdmin(admin.ModelAdmin):
    list_display = ["recurso", "tipo", "fecha_inicio", "fecha_fin", "origen"]
    list_filter = ["tipo", "origen"]
    search_fields = ["recurso__nombre"]
    exclude = ["deleted_at", "created_at", "updated_at"]
