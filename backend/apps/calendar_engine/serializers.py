from rest_framework import serializers
from .models import DiaNoLaborable, Indisponibilidad


class DiaNoLaborableSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiaNoLaborable
        fields = ["id", "fecha", "descripcion", "creado_por", "creado_en"]
        read_only_fields = ["creado_por", "creado_en"]


class IndisponibilidadSerializer(serializers.ModelSerializer):
    recurso_nombre = serializers.CharField(source="recurso.nombre", read_only=True)

    class Meta:
        model = Indisponibilidad
        fields = [
            "id", "recurso", "recurso_nombre",
            "fecha_inicio", "fecha_fin", "tipo", "origen", "external_id",
            "estado", "motivo", "motivo_rechazo",
            "solicitada_por", "revisada_por", "revisada_en",
        ]
        # El estado solo se mueve por el flujo de aprobación
        # (apps.calendar_engine.novedades), nunca escribiéndolo por la API.
        read_only_fields = [
            "estado", "motivo_rechazo", "solicitada_por", "revisada_por", "revisada_en",
        ]

    def validate(self, data):
        if data["fecha_fin"] < data["fecha_inicio"]:
            raise serializers.ValidationError("fecha_fin debe ser mayor o igual a fecha_inicio.")
        return data
