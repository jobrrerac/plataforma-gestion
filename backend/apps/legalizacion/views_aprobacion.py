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

from apps.core.models import Proyecto

from . import services as svc
from .models import DiaLegalizado, RegistroHoras


class AprobarHorasView(LoginRequiredMixin, UserPassesTestMixin, View):
    template = "legalizacion/aprobar.html"
    login_url = "/login/"
    raise_exception = True

    def test_func(self):
        """Entra quien tenga algo que aprobar, no quien tenga cierto rol.

        Un aprobador delegado puede ser ingeniero: si el permiso se pidiera por
        rol se quedaria fuera de su propia pantalla. Se pregunta por la
        capacidad, que es lo que de verdad importa aqui.
        """
        usuario = self.request.user
        return es_admin_o_pm(usuario) or Proyecto.objects.filter(
            aprobador_delegado=usuario
        ).exists()

    def _ctx(self, request, **extra):
        dias = svc.dias_por_aprobar(request.user)
        # El triaje ordena la cola y explica por que; si el modulo no esta,
        # `recuento` viene vacio y la pantalla se pinta como antes.
        recuento = svc.triar(dias, request.user)
        ctx = {
            "dias": dias,
            "recuento": recuento,
            "hay_triaje": bool(recuento),
            "es_admin": es_admin(request.user),
        }
        ctx.update(extra)
        return ctx

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        if request.POST.get("accion") == "aprobar_dia":
            return self._aprobar_dia(request)

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

    def _aprobar_dia(self, request):
        """Firma de una vez un día interno completo. Solo Admin.

        El servicio revalida la elegibilidad por su cuenta: aquí no se decide
        nada, solo se traduce el resultado a un mensaje.
        """
        dia = (
            DiaLegalizado.objects
            .filter(pk=request.POST.get("dia"))
            .select_related("recurso")
            .first()
        )
        if dia is None:
            return render(request, self.template, self._ctx(request, error="Ese día no existe."))
        try:
            cuantos = svc.aprobar_dia_completo(dia, request.user)
        except (ValidationError, PermissionDenied) as exc:
            mensaje = "; ".join(getattr(exc, "messages", [str(exc)]))
            return render(request, self.template, self._ctx(request, error=mensaje))

        messages.success(
            request,
            f"Aprobadas las {cuantos} actividades internas de {dia.recurso.nombre}, "
            f"{dia.fecha:%d/%m/%Y}.",
        )
        return redirect("horas-aprobar")
