from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from .models import Recurso, Proyecto
from .serializers import RecursoSerializer, ProyectoSerializer


class RecursoViewSet(viewsets.ModelViewSet):
    serializer_class = RecursoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Recurso.objects.all()
        activo = self.request.query_params.get("activo")
        banda = self.request.query_params.get("banda")
        if activo is not None:
            qs = qs.filter(activo=activo.lower() == "true")
        if banda:
            qs = qs.filter(banda=banda)
        return qs

    def perform_destroy(self, instance):
        instance.delete()


class ProyectoViewSet(viewsets.ModelViewSet):
    serializer_class = ProyectoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Proyecto.objects.all()
        estado = self.request.query_params.get("estado")
        if estado:
            qs = qs.filter(estado=estado)
        return qs

    def perform_destroy(self, instance):
        instance.delete()
