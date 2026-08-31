"""Cola de aprobación de horas.

Se aprueba **actividad por actividad**, no el día entero. Cada PM firma los
renglones de sus proyectos; el Admin puede firmar cualquiera, y es el único que
puede con los que no cuelgan de ningún proyecto (formación, estudio), que si no
no se aprobarían nunca.

Las actividades se agrupan por día porque quien aprueba necesita ver la jornada
completa para juzgar sus horas en contexto — pero solo puede pulsar sobre las
suyas.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.roles import es_admin, es_admin_o_pm

from . import services as svc
from .models import RegistroHoras


class AprobarHorasView(LoginRequiredMixin, UserPassesTestMixin, View):
    template = "legalizacion/aprobar.html"
    login_url = "/login/"
    raise_exception = True

    def test_func(self):
        return es_admin_o_pm(self.request.user)

    def _ctx(self, request, **extra):
        ctx = {
            "dias": svc.dias_por_aprobar(request.user),
            "es_admin": es_admin(request.user),
        }
        ctx.update(extra)
        return ctx

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        registro = (
            RegistroHoras.objects
            .select_related("dia", "dia__recurso", "proyecto", "tipo_actividad")
            .filter(pk=request.POST.get("registro"))
            .first()
        )
        if registro is None:
            return render(request, self.template, self._ctx(request, error="Esa actividad no existe."))

        etiqueta = registro.proyecto.codigo if registro.proyecto_id else registro.tipo_actividad.nombre
        quien = registro.dia.recurso.nombre
        fecha = registro.dia.fecha

        accion = request.POST.get("accion")
        try:
            if accion == "aprobar":
                svc.aprobar_registro(registro, request.user)
                messages.success(
                    request,
                    f"Aprobadas {registro.horas} h de «{etiqueta}» — {quien}, {fecha:%d/%m/%Y}.",
                )
            elif accion == "devolver":
                svc.devolver_registro(registro, request.user, request.POST.get("motivo", ""))
                messages.success(
                    request,
                    f"«{etiqueta}» devuelta a {quien} para corregir. "
                    "El resto de actividades del día no se han tocado.",
                )
            else:
                return render(request, self.template, self._ctx(request, error="Acción inválida."))
        except (ValidationError, PermissionDenied) as exc:
            mensaje = "; ".join(getattr(exc, "messages", [str(exc)]))
            return render(request, self.template, self._ctx(request, error=mensaje))

        return redirect("horas-aprobar")
