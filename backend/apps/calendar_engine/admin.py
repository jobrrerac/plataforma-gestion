from django.contrib import admin
from django.utils import timezone
from .models import DiaNoLaborable, Indisponibilidad


@admin.register(DiaNoLaborable)
class DiaNoLaborableAdmin(admin.ModelAdmin):
    list_display = ["fecha", "descripcion", "creado_por", "creado_en"]
    ordering = ["fecha"]


@admin.register(Indisponibilidad)
class IndisponibilidadAdmin(admin.ModelAdmin):
    list_display = ["recurso", "tipo", "fecha_inicio", "fecha_fin", "estado", "origen"]
    list_filter = ["estado", "tipo", "origen"]
    search_fields = ["recurso__nombre"]
    exclude = ["deleted_at", "created_at", "updated_at"]
    readonly_fields = ["solicitada_por", "revisada_por", "revisada_en"]

    def save_model(self, request, obj, form, change):
        """Lo que se crea desde el admin nace APROBADO.

        Quien entra aquí ya tiene autoridad para aprobar, y dejarlo PENDIENTE
        haría que la novedad no descontara capacidad hasta que alguien se
        acordara de revisarla — justo el fallo silencioso que hay que evitar.
        """
        if not change:
            obj.solicitada_por = request.user
            if obj.estado == "PENDIENTE":
                obj.estado = "APROBADA"
                obj.revisada_por = request.user
                obj.revisada_en = timezone.now()
        super().save_model(request, obj, form, change)
