from datetime import date
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import SoloLecturaOAdmin, EsAdminOPM
from .models import DiaNoLaborable, Indisponibilidad
from .serializers import DiaNoLaborableSerializer, IndisponibilidadSerializer
from .services import feriados_en_rango


class FeriadosView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = int(request.query_params.get("year", date.today().year))
        return Response(feriados_en_rango(date(year, 1, 1), date(year, 12, 31)))


class DiaNoLaborableViewSet(viewsets.ModelViewSet):
    queryset = DiaNoLaborable.objects.all()
    serializer_class = DiaNoLaborableSerializer
    # Días no laborables globales: solo Admin los crea/modifica/borra
    permission_classes = [SoloLecturaOAdmin]

    def perform_create(self, serializer):
        serializer.save(creado_por=self.request.user)


class IndisponibilidadViewSet(viewsets.ModelViewSet):
    serializer_class = IndisponibilidadSerializer
    # Indisponibilidades por recurso: PM y Admin pueden gestionarlas
    permission_classes = [EsAdminOPM]

    def get_queryset(self):
        qs = Indisponibilidad.objects.select_related("recurso").all()
        recurso_id = self.request.query_params.get("recurso")
        if recurso_id:
            qs = qs.filter(recurso_id=recurso_id)
        return qs

    def perform_destroy(self, instance):
        instance.delete()
