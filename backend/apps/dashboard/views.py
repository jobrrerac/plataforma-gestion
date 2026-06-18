from datetime import date, timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from apps.core.models import Recurso
from apps.assignments.models import Asignacion
from apps.calendar_engine.services import es_habil


class OcupacionDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/ocupacion.html"
    login_url = "/admin/login/"


class OcupacionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        hoy = date.today()
        fecha_inicio_str = request.query_params.get("fecha_inicio")
        fecha_fin_str = request.query_params.get("fecha_fin")

        try:
            fecha_inicio = date.fromisoformat(fecha_inicio_str) if fecha_inicio_str else date(hoy.year, hoy.month, 1)
            if fecha_fin_str:
                fecha_fin = date.fromisoformat(fecha_fin_str)
            else:
                siguiente = (fecha_inicio.replace(day=28) + timedelta(days=4))
                fecha_fin = siguiente.replace(day=1) - timedelta(days=1)
        except ValueError:
            return Response({"error": "Formato inválido. Use YYYY-MM-DD."}, status=400)

        if (fecha_fin - fecha_inicio).days > 90:
            return Response({"error": "El rango máximo es 90 días."}, status=400)

        recursos = list(Recurso.objects.filter(activo=True).order_by("nombre"))

        # Prefetch de todas las asignaciones aprobadas en el rango (una sola query)
        asignaciones = list(
            Asignacion.objects.filter(
                estado="APROBADA",
                fecha_inicio__lte=fecha_fin,
                fecha_fin__gte=fecha_inicio,
            ).select_related("recurso", "proyecto")
        )

        result = []
        for recurso in recursos:
            asig_recurso = [a for a in asignaciones if a.recurso_id == recurso.pk]

            detalle_por_dia = []
            cur = fecha_inicio
            while cur <= fecha_fin:
                habil = es_habil(cur, recurso)
                if habil:
                    asig_hoy = [a for a in asig_recurso if a.fecha_inicio <= cur <= a.fecha_fin]
                    horas = sum(float(a.intensidad_diaria) for a in asig_hoy)
                    detalle_por_dia.append({
                        "fecha": cur.isoformat(),
                        "horas_asignadas": round(horas, 2),
                        "porcentaje": min(100, round((horas / 8) * 100, 1)),
                        "proyectos": list({a.proyecto.codigo for a in asig_hoy}),
                    })
                else:
                    detalle_por_dia.append({
                        "fecha": cur.isoformat(),
                        "no_habil": True,
                        "horas_asignadas": 0,
                        "porcentaje": 0,
                        "proyectos": [],
                    })
                cur += timedelta(days=1)

            # Estado del día de hoy
            if fecha_inicio <= hoy <= fecha_fin:
                asig_hoy_list = [a for a in asig_recurso if a.fecha_inicio <= hoy <= a.fecha_fin]
                horas_hoy = sum(float(a.intensidad_diaria) for a in asig_hoy_list)
            else:
                horas_hoy = 0

            result.append({
                "id": recurso.pk,
                "nombre": recurso.nombre,
                "banda": recurso.get_banda_display(),
                "email": recurso.email,
                "estado": "BENCH" if horas_hoy == 0 else "OCUPADO",
                "horas_hoy": horas_hoy,
                "porcentaje_hoy": min(100, round((horas_hoy / 8) * 100, 1)),
                "asignaciones_activas": len(asig_recurso),
                "detalle_por_dia": detalle_por_dia,
            })

        return Response({
            "periodo": {"inicio": fecha_inicio.isoformat(), "fin": fecha_fin.isoformat()},
            "hoy": hoy.isoformat(),
            "recursos": result,
        })
