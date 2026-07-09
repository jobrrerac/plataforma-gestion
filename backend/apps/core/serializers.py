from rest_framework import serializers

from apps.accounts.roles import puede_ver_datos_personales
from .models import Recurso, Proyecto


class RecursoSerializer(serializers.ModelSerializer):
    banda_display = serializers.CharField(source="get_banda_display", read_only=True)

    class Meta:
        model = Recurso
        fields = ["id", "nombre", "email", "banda", "banda_display", "activo", "created_at"]
        read_only_fields = ["created_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request and not puede_ver_datos_personales(request.user):
            data.pop("email", None)
        return data


class ProyectoSerializer(serializers.ModelSerializer):
    pm_username = serializers.CharField(source="pm.username", read_only=True)

    class Meta:
        model = Proyecto
        fields = [
            "id", "codigo", "codigo_pep", "grafo", "nombre", "cliente",
            "fecha_inicio", "fecha_fin", "estado",
            "pm", "pm_username",
        ]
