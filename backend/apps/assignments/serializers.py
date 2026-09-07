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
        # Alerta si supera la jornada del viernes (la más restrictiva)
        return float(obj.intensidad_diaria) > 8.0


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


# Palabras que marcan una cifra de dinero dentro de `detalle`. El filtro es por
# nombre y no por una lista cerrada de claves a proposito: `detalle` es un JSON
# libre al que cada accion mete lo suyo, y una lista cerrada se queda vieja en
# cuanto alguien anade un campo.
#
# `monto` esta aqui porque `monto_descontado` —lo que cuesta una cesion de
# horas— es dinero y no lleva ni «tarifa» ni «costo» en el nombre. Fue justo el
# que se escapo de la primera revision.
#
# La contrapartida honesta: esto depende de que los campos de dinero se sigan
# llamando asi. Un campo futuro llamado `valor_hora` o `importe` se colaria. Si
# aparece uno, va aqui — y la prueba de abajo existe para que se note.
PALABRAS_DE_DINERO = ("tarifa", "costo", "monto", "precio", "importe")


def _sin_dinero(valor):
    """Copia de `valor` sin ninguna clave que hable de dinero.

    Recursivo porque `tarifa_cambios` es una lista de diccionarios: filtrar
    solo el primer nivel dejaba los importes dentro, un nivel mas abajo.
    """
    if isinstance(valor, dict):
        return {
            k: _sin_dinero(v)
            for k, v in valor.items()
            if not any(p in k.lower() for p in PALABRAS_DE_DINERO)
        }
    if isinstance(valor, list):
        return [_sin_dinero(v) for v in valor]
    return valor


class LogAuditoriaSerializer(serializers.ModelSerializer):
    """El log de auditoria, sin cifras de dinero para quien no puede verlas.

    `detalle` es un JSON libre en el que las acciones guardan lo suyo, y varias
    guardan ahi la tarifa del dia, el costo estimado o el monto de una cesion.
    Como el endpoint solo exigia estar autenticado, un Ingeniero veia todo eso
    con solo conocer el id de una asignacion — contra la regla de que ese rol
    **nunca** ve costos.

    Se filtra el dinero y no el `detalle` entero: taparlo todo dejaria una
    auditoria inutil justo para quien mas necesita consultarla, que es la
    persona sobre la que se actuo. Quien hizo que, cuando y por que motivo se
    sigue viendo.
    """

    actor_username = serializers.CharField(source="actor.username", read_only=True)
    accion_display = serializers.CharField(source="get_accion_display", read_only=True)
    detalle = serializers.SerializerMethodField()

    class Meta:
        model = LogAuditoria
        fields = ["id", "accion", "accion_display", "actor", "actor_username", "timestamp", "detalle"]

    def get_detalle(self, obj):
        # El import va dentro para no crear una dependencia de modulo hacia
        # `accounts` desde el cuerpo de este archivo (ver CAPAS en apps/core).
        from apps.accounts.roles import puede_ver_costos

        peticion = self.context.get("request")
        usuario = getattr(peticion, "user", None)
        # Sin peticion en el contexto no se puede saber quien pregunta, y ante
        # la duda se tapa: es la direccion segura de fallar para esta regla.
        if usuario is not None and puede_ver_costos(usuario):
            return obj.detalle
        return _sin_dinero(obj.detalle)
