"""Cola de aprobación de horas.

La revisa el PM de los proyectos implicados. El Admin ve todas: es la válvula de
escape para que las horas de nadie se queden bloqueadas porque su PM esté de
vacaciones, se haya ido o simplemente tarde.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.roles import es_admin, es_admin_o_pm

from . import services as svc
from .models import DiaLegalizado


class AprobarHorasView(LoginRequiredMixin, UserPassesTestMixin, View):
    template = "legalizacion/aprobar.html"
    login_url = "/login/"
    raise_exception = True

    def test_func(self):
        return es_admin_o_pm(self.request.user)

    def _ctx(self, request, **extra):
        dias = list(
            svc.dias_por_aprobar(request.user).prefetch_related(
                "registros__proyecto", "registros__tipo_actividad"
            )
        )
        # El desglose se calcula aquí y no en la plantilla: quien aprueba
        # necesita ver a qué se fueron las horas, no solo el total.
        for dia in dias:
            dia.detalle = svc.resumen(dia)

        ctx = {
            "dias": dias,
            "es_admin": es_admin(request.user),
        }
        ctx.update(extra)
        return ctx

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        dia = DiaLegalizado.objects.filter(pk=request.POST.get("dia")).first()
        if dia is None:
            return render(request, self.template, self._ctx(request, error="Ese día no existe."))

        accion = request.POST.get("accion")
        try:
            if accion == "aprobar":
                svc.aprobar_dia(dia, request.user)
                messages.success(
                    request,
                    f"Aprobadas las {dia.total_horas} h de {dia.recurso.nombre} "
                    f"del {dia.fecha:%d/%m/%Y}.",
                )
            elif accion == "devolver":
                svc.devolver_dia(dia, request.user, request.POST.get("motivo", ""))
                messages.success(
                    request,
                    f"Día del {dia.fecha:%d/%m/%Y} devuelto a {dia.recurso.nombre} para corregir.",
                )
            else:
                return render(request, self.template, self._ctx(request, error="Acción inválida."))
        except (ValidationError, PermissionDenied) as exc:
            mensaje = "; ".join(getattr(exc, "messages", [str(exc)]))
            return render(request, self.template, self._ctx(request, error=mensaje))

        return redirect("horas-aprobar")
