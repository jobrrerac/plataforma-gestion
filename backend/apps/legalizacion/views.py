"""Pantalla donde el ingeniero legaliza su día."""

from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.core.models import Proyecto

from . import services as svc
from .models import DiaLegalizado, RegistroHoras


class LegalizarDiaView(LoginRequiredMixin, View):
    """Un día, sus renglones y el cierre.

    Accesible a cualquier usuario autenticado: un PM o un Admin también tienen
    que legalizar su tiempo. Lo que está acotado es el alcance de los datos —
    cada quien opera únicamente sobre su propio recurso.
    """

    template = "legalizacion/dia.html"
    login_url = "/login/"

    # -- contexto ----------------------------------------------------------

    def _fecha_pedida(self, request):
        crudo = request.GET.get("fecha") or request.POST.get("fecha")
        if crudo:
            try:
                return date.fromisoformat(crudo)
            except ValueError:
                return None
        return date.today()

    def _ctx(self, request, fecha, **extra):
        recurso = svc.recurso_de(request.user)
        ctx = {
            "recurso": recurso,
            "fecha": fecha,
            "hoy": date.today(),
            "actividades": svc.actividades_disponibles(),
            "proyectos": Proyecto.objects.filter(estado="ACTIVO").order_by("-facturable", "codigo"),
        }

        if recurso and fecha:
            estado = svc.estado_del_dia(recurso, fecha)
            ctx["estado_dia"] = estado
            ctx["pendientes"] = svc.dias_pendientes(recurso)[:10]

            if estado["habil"]:
                dia = DiaLegalizado.objects.filter(recurso=recurso, fecha=fecha).first()
                ctx["dia"] = dia
                ctx["resumen"] = svc.resumen(dia) if dia else None

        ctx.update(extra)
        return ctx

    # -- GET ---------------------------------------------------------------

    def get(self, request):
        fecha = self._fecha_pedida(request)
        if fecha is None:
            return render(request, self.template, self._ctx(request, date.today(), error="Fecha inválida."))
        return render(request, self.template, self._ctx(request, fecha))

    # -- POST --------------------------------------------------------------

    def post(self, request):
        fecha = self._fecha_pedida(request)
        recurso = svc.recurso_de(request.user)

        if recurso is None:
            return render(request, self.template, self._ctx(request, fecha,
                          error="Tu cuenta no está vinculada a ningún recurso."))
        if fecha is None:
            return render(request, self.template, self._ctx(request, date.today(), error="Fecha inválida."))

        accion = request.POST.get("accion")
        manejadores = {
            "agregar": self._agregar,
            "quitar": self._quitar,
            "registrar": self._registrar,
        }
        manejador = manejadores.get(accion)
        if manejador is None:
            return render(request, self.template, self._ctx(request, fecha, error="Acción inválida."))

        try:
            return manejador(request, recurso, fecha)
        except (ValidationError, PermissionDenied) as exc:
            mensaje = "; ".join(getattr(exc, "messages", [str(exc)]))
            return render(request, self.template, self._ctx(request, fecha, error=mensaje))

    def _agregar(self, request, recurso, fecha):
        dia = svc.obtener_o_crear_dia(recurso, fecha)

        actividad = svc.actividades_disponibles().filter(pk=request.POST.get("tipo_actividad")).first()
        if actividad is None:
            raise ValidationError("Elige una actividad.")

        proyecto = None
        if actividad.requiere_proyecto:
            proyecto = Proyecto.objects.filter(pk=request.POST.get("proyecto")).first()

        try:
            horas = request.POST.get("horas", "").replace(",", ".")
            horas = float(horas)
        except ValueError:
            raise ValidationError("Las horas tienen que ser un número.") from None

        svc.agregar_renglon(
            dia=dia,
            tipo_actividad=actividad,
            horas=horas,
            detalle=request.POST.get("detalle", ""),
            proyecto=proyecto,
        )
        return redirect(f"{request.path}?fecha={fecha.isoformat()}")

    def _quitar(self, request, recurso, fecha):
        renglon = RegistroHoras.objects.filter(
            pk=request.POST.get("renglon"), dia__recurso=recurso,
        ).first()
        if renglon is None:
            raise ValidationError("Ese renglón no existe.")

        svc.quitar_renglon(renglon)
        return redirect(f"{request.path}?fecha={fecha.isoformat()}")

    def _registrar(self, request, recurso, fecha):
        dia = DiaLegalizado.objects.filter(recurso=recurso, fecha=fecha).first()
        if dia is None:
            raise ValidationError("No hay nada que registrar en este día.")

        svc.registrar_dia(dia, request.user)
        messages.success(
            request,
            f"Día {fecha:%d/%m/%Y} registrado con {dia.total_horas} h. Ya no se puede modificar.",
        )
        return redirect(f"{request.path}?fecha={fecha.isoformat()}")
