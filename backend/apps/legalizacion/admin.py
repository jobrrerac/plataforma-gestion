from django.contrib import admin

from .models import TipoActividad


@admin.register(TipoActividad)
class TipoActividadAdmin(admin.ModelAdmin):
    list_display = ["nombre", "requiere_proyecto", "activo", "orden"]
    list_filter = ["requiere_proyecto", "activo"]
    list_editable = ["activo", "orden"]
    ordering = ["orden", "nombre"]
