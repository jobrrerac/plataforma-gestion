from django.contrib import admin
from .models import Recurso, Proyecto


@admin.register(Recurso)
class RecursoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "email", "banda", "activo", "created_at"]
    list_filter = ["banda", "activo"]
    search_fields = ["nombre", "email"]
    list_per_page = 50


@admin.register(Proyecto)
class ProyectoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "cliente", "estado", "pm", "fecha_inicio", "fecha_fin"]
    list_filter = ["estado"]
    search_fields = ["codigo", "nombre", "cliente"]
