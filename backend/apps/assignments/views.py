from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import EsAdmin, EsAdminOPM
from .models import Asignacion, LogAuditoria
from .serializers import AsignacionSerializer, AsignacionCreateSerializer, LogAuditoriaSerializer
from .services import calcular_fecha_fin, aprobar_asignacion, rechazar_asignacion, revocar_asignacion

# Acciones que solo Admin puede ejecutar
_ACCIONES_ADMIN = {"aprobar", "rechazar", "revocar", "update", "partial_update", "destroy"}
# Acciones que requieren PM o Admin
_ACCIONES_PM = {"create"}


class AsignacionViewSet(viewsets.ModelViewSet):
    def get_permissions(self):
        if self.action in _ACCIONES_ADMIN:
            return [EsAdmin()]
        if self.action in _ACCIONES_PM:
            return [EsAdminOPM()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Asignacion.objects.select_related("recurso", "proyecto", "solicitada_por")
        params = self.request.query_params
        if params.get("recurso"):
            qs = qs.filter(recurso_id=params["recurso"])
        if params.get("proyecto"):
            qs = qs.filter(proyecto_id=params["proyecto"])
        if params.get("estado"):
            qs = qs.filter(estado=params["estado"])
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return AsignacionCreateSerializer
        return AsignacionSerializer

    def update(self, request, *args, **kwargs):
        """Editar una asignación solo mientras siga SOLICITADA.

        Una asignación aprobada tiene tres cosas congeladas que este endpoint no
        sabe rehacer: la capacidad del recurso ya comprometida, el snapshot de
        tarifa y el costo estimado. `fecha_fin` es ademas de solo lectura en el
        serializer, asi que cambiar las horas la dejaba mintiendo.

        El resultado era que un Admin podia convertir una asignación aprobada en
        una sobreasignación, o dejar los datos financieros incoherentes, sin que
        nada fallara. Se cierra la puerta en vez de dejarla entreabierta: para
        cambiar una aprobada hay que revocarla y crear otra, que ademas deja
        rastro en la auditoría.
        """
        asignacion = self.get_object()
        if asignacion.estado != "SOLICITADA":
            return Response(
                {
                    "error": (
                        f"No se puede editar una asignación en estado "
                        f"'{asignacion.estado}'. Revócala y crea una nueva."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        """Recalcula `fecha_fin`: es derivada, no un dato que se pueda quedar viejo.

        `fecha_fin` es de solo lectura en el serializer, asi que sin esto una
        edición de horas, intensidad o fecha de inicio dejaba la fecha final
        anterior, y con ella el calendario y el dashboard.
        """
        instancia = serializer.instance
        datos = serializer.validated_data
        recurso = datos.get("recurso", instancia.recurso)
        fecha_inicio = datos.get("fecha_inicio", instancia.fecha_inicio)
        horas = datos.get("horas_totales", instancia.horas_totales)
        intensidad = datos.get("intensidad_diaria", instancia.intensidad_diaria)

        asignacion = serializer.save(
            fecha_fin=calcular_fecha_fin(recurso, fecha_inicio, horas, intensidad),
        )
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="CREAR", actor=self.request.user,
            detalle={
                "edicion": True,
                "fecha_fin_recalculada": str(asignacion.fecha_fin),
            },
        )

    def perform_create(self, serializer):
        data = serializer.validated_data
        fecha_fin = calcular_fecha_fin(
            data["recurso"], data["fecha_inicio"],
            data["horas_totales"], data["intensidad_diaria"],
        )
        asignacion = serializer.save(
            solicitada_por=self.request.user,
            fecha_fin=fecha_fin,
            estado="SOLICITADA",
        )
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="CREAR", actor=self.request.user,
            detalle={"fecha_fin_calculada": str(fecha_fin)},
        )

    @action(detail=True, methods=["post"])
    def aprobar(self, request, pk=None):
        asignacion = self.get_object()
        if asignacion.estado != "SOLICITADA":
            return Response(
                {"error": f"No se puede aprobar una asignación en estado '{asignacion.estado}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            aprobar_asignacion(asignacion, request.user)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(AsignacionSerializer(asignacion).data)

    @action(detail=True, methods=["post"])
    def rechazar(self, request, pk=None):
        asignacion = self.get_object()
        if asignacion.estado != "SOLICITADA":
            return Response(
                {"error": f"No se puede rechazar una asignación en estado '{asignacion.estado}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rechazar_asignacion(asignacion, request.user, motivo=request.data.get("motivo", ""))
        return Response(AsignacionSerializer(asignacion).data)

    @action(detail=True, methods=["post"])
    def revocar(self, request, pk=None):
        asignacion = self.get_object()
        if asignacion.estado != "APROBADA":
            return Response(
                {"error": "Solo se pueden revocar asignaciones aprobadas."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        revocar_asignacion(asignacion, request.user, motivo=request.data.get("motivo", ""))
        return Response(AsignacionSerializer(asignacion).data)

    @action(detail=True, methods=["get"])
    def log(self, request, pk=None):
        asignacion = self.get_object()
        logs = LogAuditoria.objects.filter(asignacion=asignacion)
        return Response(LogAuditoriaSerializer(logs, many=True).data)
