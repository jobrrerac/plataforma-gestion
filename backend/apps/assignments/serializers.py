from rest_framework import serializers
from .models import Asignacion, LogAuditoria


class AsignacionSerializer(serializers.ModelSerializer):
    recurso_nombre = serializers.CharField(source="recurso.nombre", read_only=True)
    proyecto_codigo = serializers.CharField(source="proyecto.codigo", read_only=True)
    proyecto_nombre = serializers.CharField(source="proyecto.nombre", read_only=True)
    alerta_intensidad = serializers.SerializerMethodField()
    estado_display = serializers.CharField(source="get_estado_display", read_only=True)

    class Meta:
        model = Asignacion
        fields = [
            "id", "recurso", "recurso_nombre",
            "proyecto", "proyecto_codigo", "proyecto_nombre",
            "horas_totales", "intensidad_diaria",
            "fecha_inicio", "fecha_fin",
            "politica_ausencia", "estado", "estado_display",
            "solicitada_por", "created_at",
            "alerta_intensidad",
        ]
        read_only_fields = ["fecha_fin", "estado", "solicitada_por", "created_at"]

    def get_alerta_intensidad(self, obj):
        return float(obj.intensidad_diaria) > 8


class AsignacionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Asignacion
        fields = [
            "recurso", "proyecto",
            "horas_totales", "intensidad_diaria",
            "fecha_inicio", "politica_ausencia",
        ]

    def validate_horas_totales(self, value):
        if float(value) <= 0:
            raise serializers.ValidationError("Debe ser mayor a 0.")
        return value

    def validate_intensidad_diaria(self, value):
        if float(value) <= 0:
            raise serializers.ValidationError("Debe ser mayor a 0.")
        return value


class LogAuditoriaSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)
    accion_display = serializers.CharField(source="get_accion_display", read_only=True)

    class Meta:
        model = LogAuditoria
        fields = ["id", "accion", "accion_display", "actor", "actor_username", "timestamp", "detalle"]
