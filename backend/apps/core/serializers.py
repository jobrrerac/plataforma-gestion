from rest_framework import serializers
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
        if request:
            u = request.user
            puede_ver_email = u.is_superuser or u.groups.filter(name__in=["Admin", "PM"]).exists()
            if not puede_ver_email:
                data.pop("email", None)
        return data


class ProyectoSerializer(serializers.ModelSerializer):
    pm_username = serializers.CharField(source="pm.username", read_only=True)

    class Meta:
        model = Proyecto
        fields = [
            "id", "codigo", "codigo_pep", "nombre", "cliente",
            "fecha_inicio", "fecha_fin", "estado",
            "pm", "pm_username",
        ]
