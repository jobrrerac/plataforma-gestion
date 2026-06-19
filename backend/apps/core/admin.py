from django.contrib import admin
from .models import Recurso, Proyecto, Skill


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ["nombre", "total_recursos"]
    search_fields = ["nombre"]

    @admin.display(description="Recursos")
    def total_recursos(self, obj):
        return obj.recursos.filter(activo=True).count()


@admin.register(Recurso)
class RecursoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "email", "banda", "skills_display", "activo", "created_at"]
    list_filter = ["banda", "activo", "skills"]
    search_fields = ["nombre", "email"]
    filter_horizontal = ["skills"]
    list_per_page = 50
    exclude = ["deleted_at", "created_at", "updated_at"]

    @admin.display(description="Skills")
    def skills_display(self, obj):
        return ", ".join(obj.skills.values_list("nombre", flat=True)) or "—"


@admin.register(Proyecto)
class ProyectoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "cliente", "estado", "pm", "fecha_inicio", "fecha_fin"]
    list_filter = ["estado"]
    search_fields = ["codigo", "nombre", "cliente"]
    exclude = ["deleted_at", "created_at", "updated_at"]
