from django.contrib import admin

from apps.core.admin_mixins import SoftDeleteAdminMixin

from .models import AccionDeSeguimiento, Bloqueante, Feedback


@admin.register(Bloqueante)
class BloqueanteAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Para leer el histórico y sacar el tiempo de desbloqueo por persona.

    Filtrando por `bloquea_usuario` sale cuánto tarda cada quien en desatascar,
    que es el indicador del lado proyecto y el único que no depende de que nadie
    del lado proyecto registre nada.
    """

    list_display = [
        "recurso", "necesito", "quien_desbloquea", "proyecto",
        "creado_en", "horas", "estado",
    ]
    list_filter = ["resuelto_en", "bloquea_usuario", "proyecto", "creado_en"]
    search_fields = ["recurso__nombre", "necesito", "bloquea_nombre"]
    date_hierarchy = "creado_en"
    readonly_fields = ["creado_en", "creado_por", "resuelto_en", "resuelto_por"]
    exclude = ["deleted_at", "created_at", "updated_at"]

    @admin.display(description="Desbloquea")
    def quien_desbloquea(self, obj):
        return obj.bloqueador

    @admin.display(description="Horas")
    def horas(self, obj):
        return f"{obj.horas_abierto:.0f}"

    @admin.display(description="Estado")
    def estado(self, obj):
        if not obj.abierto:
            return "resuelto"
        return "VENCIDO" if obj.vencido else "abierto"


@admin.register(Feedback)
class FeedbackAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """El histórico de observaciones, para leer patrones.

    Lo que se busca aquí no es la nota de nadie: es la **reincidencia**. Que la
    misma observación aparezca tres veces dice más que cualquier escala, y que un
    mismo autor califique bajo a todo el mundo dice más sobre el autor que sobre
    la gente que observa.
    """

    list_display = [
        "fecha_observacion", "direccion", "recurso", "proyecto",
        "autor", "en_nombre_de", "tipo", "dimension", "momento",
    ]
    list_filter = ["direccion", "momento", "tipo", "dimension", "proyecto", "autor", "en_nombre_de"]
    search_fields = ["recurso__nombre", "conducta", "impacto", "que_ahorraria_tiempo"]
    date_hierarchy = "fecha_observacion"
    readonly_fields = ["created_at", "updated_at"]
    exclude = ["deleted_at"]


@admin.register(AccionDeSeguimiento)
class AccionDeSeguimientoAdmin(SoftDeleteAdminMixin, admin.ModelAdmin):
    """Qué se hizo con lo que se leyó, y cuándo.

    Es la mitad que faltaba. Un recurso con tres observaciones a mejorar y
    ninguna acción registrada no es un problema de la persona: es un problema de
    seguimiento, y sin esta tabla esa distinción no se puede hacer.
    """

    list_display = ["creado_en", "recurso", "tipo", "autor", "feedback"]
    list_filter = ["tipo", "creado_en", "autor"]
    search_fields = ["recurso__nombre", "texto"]
    date_hierarchy = "creado_en"
    readonly_fields = ["creado_en"]
    exclude = ["deleted_at", "created_at", "updated_at"]
