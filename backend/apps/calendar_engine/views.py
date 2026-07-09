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
