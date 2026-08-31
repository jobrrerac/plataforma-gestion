from datetime import date

from django.core.exceptions import (
    PermissionDenied as DjangoPermissionDenied,
    ValidationError as DjangoValidationError,
)
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.accounts.roles import es_admin_o_pm
from apps.core.permissions import SoloLecturaOAdmin, EsAdmin
from . import novedades as novedades_svc
from .models import DiaNoLaborable, Indisponibilidad
from .serializers import (
    DiaNoLaborableSerializer,
    IndisponibilidadSerializer,
    IndisponibilidadUpdateSerializer,
)
from .services import feriados_en_rango


class FeriadosView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            year = int(request.query_params.get("year", date.today().year))
        except (TypeError, ValueError):
            return Response({"error": "Año inválido."}, status=400)
        if not (2000 <= year <= 2100):
            return Response({"error": "Año fuera de rango (2000–2100)."}, status=400)
        return Response(feriados_en_rango(date(year, 1, 1), date(year, 12, 31)))


class DiasNoHabilesView(APIView):
    """
    Días no hábiles globales (feriados de Colombia + días no laborables de la
    empresa) en un rango. Alimenta el pintado en rojo de los datepickers.
    Los fines de semana no se incluyen: el cliente los resuelve por weekday.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            desde = date.fromisoformat(request.query_params.get("desde", ""))
            hasta = date.fromisoformat(request.query_params.get("hasta", ""))
        except (TypeError, ValueError):
            return Response({"error": "Parámetros desde/hasta requeridos (YYYY-MM-DD)."}, status=400)
        if hasta < desde or (hasta - desde).days > 1100:
            return Response({"error": "Rango inválido (máximo 3 años)."}, status=400)

        dias = [
            {"fecha": f["fecha"], "nombre": f["nombre"], "tipo": "FERIADO"}
            for f in feriados_en_rango(desde, hasta)
        ]
        for dnl in DiaNoLaborable.objects.filter(fecha__gte=desde, fecha__lte=hasta):
            dias.append({"fecha": dnl.fecha.isoformat(), "nombre": dnl.descripcion, "tipo": "NO_LABORABLE"})
        dias.sort(key=lambda d: d["fecha"])
        return Response(dias)


class DiaNoLaborableViewSet(viewsets.ModelViewSet):
    queryset = DiaNoLaborable.objects.all()
    serializer_class = DiaNoLaborableSerializer
    # Días no laborables globales: solo Admin los crea/modifica/borra
    permission_classes = [SoloLecturaOAdmin]

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class IndisponibilidadViewSet(viewsets.ModelViewSet):
    """Novedades por recurso (vacaciones y permisos).

    Alcance según el rol:
    - Admin y PM ven y gestionan las de todo el mundo; lo que crean nace ya
      APROBADO, porque quien tiene autoridad no necesita aprobarse a sí mismo.
    - El resto solo ve y crea las de SU propio recurso, y quedan PENDIENTES.

    El permiso de Django (`add_indisponibilidad`) es por modelo, no por fila:
    el alcance "solo las suyas" lo impone este ViewSet filtrando por el recurso
    vinculado a la cuenta.
    """

    serializer_class = IndisponibilidadSerializer

    def get_serializer_class(self):
        # Al editar, tres campos dejan de ser escribibles: quien es el dueno de
        # la novedad y de donde vino el dato se deciden al crearla.
        if self.action in ("update", "partial_update"):
            return IndisponibilidadUpdateSerializer
        return IndisponibilidadSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Indisponibilidad.objects.select_related("recurso")

        if not es_admin_o_pm(self.request.user):
            recurso = novedades_svc.recurso_de(self.request.user)
            if recurso is None:
                return qs.none()
            qs = qs.filter(recurso=recurso)

        recurso_id = self.request.query_params.get("recurso")
        if recurso_id:
            qs = qs.filter(recurso_id=recurso_id)
        return qs

    def perform_create(self, serializer):
        datos = serializer.validated_data
        try:
            if es_admin_o_pm(self.request.user):
                novedad = novedades_svc.registrar_por_autoridad(
                    usuario=self.request.user,
                    recurso=datos["recurso"],
                    fecha_inicio=datos["fecha_inicio"],
                    fecha_fin=datos["fecha_fin"],
                    tipo=datos["tipo"],
                    motivo=datos.get("motivo", ""),
                )
            else:
                # `recurso` del payload se ignora a propósito: un ingeniero solo
                # puede registrar novedades a su propio nombre, y aceptarlo del
                # cliente permitiría pedir vacaciones para otra persona.
                novedad = novedades_svc.registrar_novedad(
                    usuario=self.request.user,
                    fecha_inicio=datos["fecha_inicio"],
                    fecha_fin=datos["fecha_fin"],
                    tipo=datos["tipo"],
                    motivo=datos.get("motivo", ""),
                )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc

        serializer.instance = novedad

    def perform_destroy(self, instance):
        if es_admin_o_pm(self.request.user):
            instance.delete()  # soft-delete
            return
        try:
            novedades_svc.cancelar_novedad(instance, self.request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        except DjangoPermissionDenied as exc:
            raise PermissionDenied(str(exc)) from exc

    @action(detail=True, methods=["post"], permission_classes=[EsAdmin])
    def aprobar(self, request, pk=None):
        novedad = self.get_object()
        try:
            novedades_svc.aprobar_novedad(novedad, request.user)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        return Response(self.get_serializer(novedad).data)

    @action(detail=True, methods=["post"], permission_classes=[EsAdmin])
    def rechazar(self, request, pk=None):
        novedad = self.get_object()
        try:
            novedades_svc.rechazar_novedad(
                novedad, request.user, motivo=request.data.get("motivo_rechazo", "")
            )
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        return Response(self.get_serializer(novedad).data)
