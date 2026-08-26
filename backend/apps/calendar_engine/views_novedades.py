"""Vistas de UI del flujo de novedades.

Dos pantallas:
- `/novedades/`        panel del ingeniero: registra las suyas y ve su estado.
- `/novedades/revisar/` cola del Admin: aprueba o rechaza.
"""

from datetime import date

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.roles import es_admin

from . import novedades as svc
from .models import Indisponibilidad


class NovedadesView(LoginRequiredMixin, View):
    """Panel propio. Accesible a cualquier usuario autenticado.

    No lleva restricción de rol a propósito: un PM o un Admin también tienen
    vacaciones. Lo que sí está acotado es el alcance de los datos — cada quien
    ve únicamente las novedades de su propio recurso, nunca las de otros.
    """

    template = "calendar_engine/novedades.html"
    login_url = "/login/"

    def _ctx(self, request, **extra):
        recurso = svc.recurso_de(request.user)
        ctx = {
            "recurso": recurso,
            "novedades": svc.novedades_de(recurso) if recurso else [],
            "tipos": Indisponibilidad.TIPO_CHOICES,
            "hoy": date.today(),
            "es_admin": es_admin(request.user),
            "pendientes_por_revisar": svc.pendientes().count() if es_admin(request.user) else 0,
        }
        ctx.update(extra)
        return ctx

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        accion = request.POST.get("accion", "crear")

        if accion == "cancelar":
            return self._cancelar(request)
        return self._crear(request)

    def _crear(self, request):
        try:
            fecha_inicio = date.fromisoformat(request.POST.get("fecha_inicio", ""))
            fecha_fin = date.fromisoformat(request.POST.get("fecha_fin", ""))
        except ValueError:
            return render(request, self.template, self._ctx(request, error="Fechas inválidas."))

        tipo = request.POST.get("tipo", "")
        if tipo not in dict(Indisponibilidad.TIPO_CHOICES):
            return render(request, self.template, self._ctx(request, error="Tipo de novedad inválido."))

        try:
            svc.registrar_novedad(
                usuario=request.user,
                fecha_inicio=fecha_inicio,
                fecha_fin=fecha_fin,
                tipo=tipo,
                motivo=request.POST.get("motivo", "").strip(),
            )
        except ValidationError as exc:
            return render(request, self.template, self._ctx(request, error="; ".join(exc.messages)))

        messages.success(request, "Novedad registrada. Queda pendiente de aprobación.")
        return redirect("novedades")

    def _cancelar(self, request):
        novedad = Indisponibilidad.objects.filter(pk=request.POST.get("novedad")).first()
        if novedad is None:
            return render(request, self.template, self._ctx(request, error="Novedad no encontrada."))

        try:
            svc.cancelar_novedad(novedad, request.user)
        except (ValidationError, PermissionDenied) as exc:
            mensaje = "; ".join(getattr(exc, "messages", [str(exc)]))
            return render(request, self.template, self._ctx(request, error=mensaje))

        messages.success(request, "Novedad cancelada.")
        return redirect("novedades")


class AdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Solo Admin. Un PM que llegue aquí recibe 403, no una redirección."""

    login_url = "/login/"
    raise_exception = True

    def test_func(self):
        return es_admin(self.request.user)


class NovedadesRevisarView(AdminRequiredMixin, View):
    """Cola de aprobación del Admin."""

    template = "calendar_engine/novedades_revisar.html"

    def _ctx(self, request, **extra):
        pendientes = list(svc.pendientes())
        # Contexto para decidir: los días de la novedad dejarán de ser hábiles,
        # pero la fecha_fin de las asignaciones vigentes se calculó contando con
        # ellos. Aprobar no las recalcula.
        for n in pendientes:
            n.afectadas = list(svc.asignaciones_afectadas(n))

        ctx = {"pendientes": pendientes, "hoy": date.today()}
        ctx.update(extra)
        return ctx

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        novedad = Indisponibilidad.objects.filter(pk=request.POST.get("novedad")).first()
        if novedad is None:
            return render(request, self.template, self._ctx(request, error="Novedad no encontrada."))

        accion = request.POST.get("accion")
        try:
            if accion == "aprobar":
                svc.aprobar_novedad(novedad, request.user)
                messages.success(
                    request,
                    f"Novedad de {novedad.recurso.nombre} aprobada. Ya descuenta capacidad.",
                )
            elif accion == "rechazar":
                svc.rechazar_novedad(
                    novedad, request.user, motivo=request.POST.get("motivo_rechazo", "").strip()
                )
                messages.success(request, f"Novedad de {novedad.recurso.nombre} rechazada.")
            else:
                return render(request, self.template, self._ctx(request, error="Acción inválida."))
        except (ValidationError, PermissionDenied) as exc:
            mensaje = "; ".join(getattr(exc, "messages", [str(exc)]))
            return render(request, self.template, self._ctx(request, error=mensaje))

        return redirect("novedades-revisar")
