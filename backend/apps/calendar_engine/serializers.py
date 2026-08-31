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
        # En un PATCH puede venir solo una de las dos fechas, o ninguna. Leerlas
        # con `data[...]` reventaba con KeyError y devolvia un 500 en vez de un
        # error de validacion; la que falte se toma de lo que ya hay guardado.
        inicio = data.get("fecha_inicio") or getattr(self.instance, "fecha_inicio", None)
        fin = data.get("fecha_fin") or getattr(self.instance, "fecha_fin", None)
        if inicio and fin and fin < inicio:
            raise serializers.ValidationError("fecha_fin debe ser mayor o igual a fecha_inicio.")
        return data


class IndisponibilidadUpdateSerializer(IndisponibilidadSerializer):
    """Serializer para editar una novedad ya creada.

    Tres campos que no pueden cambiar despues del alta:

    - `recurso`: quien es el dueno de la novedad se decide al crearla, y para un
      ingeniero lo impone el servidor a partir de su cuenta. Dejarlo editable
      permitia coger una novedad propia y apuntarla a otra persona con un PATCH,
      saltandose por completo esa comprobacion.
    - `origen` y `external_id`: identifican de donde vino el dato. Poder
      convertir una entrada manual en una de SAP —o cambiarle el identificador
      de integracion— rompe la conciliacion con el sistema de origen y deja el
      trabajo idempotente pisando registros que no le tocan.
    """

    class Meta(IndisponibilidadSerializer.Meta):
        read_only_fields = IndisponibilidadSerializer.Meta.read_only_fields + [
            "recurso", "origen", "external_id",
        ]
