"""Pantalla donde el ingeniero legaliza su día."""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View

from . import services as svc
from .models import DiaLegalizado


def _entero(valor):
    """Convierte a int lo que llega del formulario, o None si viene vacío."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


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
            # Flechas de navegación: moverse de día es lo que más se hace en
            # esta pantalla, y obligar a abrir el calendario para cada salto
            # convierte rellenar la semana en una tarea tediosa.
            "dia_anterior": fecha - timedelta(days=1) if fecha else None,
            "dia_siguiente": fecha + timedelta(days=1) if fecha else None,
            "actividades": svc.actividades_disponibles(),
            "proyectos": [],
        }

        if recurso and fecha:
            estado = svc.estado_del_dia(recurso, fecha)
            ctx["estado_dia"] = estado
            ctx["pendientes"] = svc.dias_pendientes(recurso)[:10]
            # Los proyectos dependen del día: valen los que esa persona tenía
            # asignados esa fecha, no los de hoy.
            ctx["proyectos"] = svc.proyectos_disponibles(recurso, fecha)

            if estado["habil"]:
                dia = DiaLegalizado.objects.filter(recurso=recurso, fecha=fecha).first()
                ctx["dia"] = dia
                ctx["resumen"] = svc.resumen(dia) if dia else None

                # Se muestra el formulario cuando aún no hay nada guardado, o
                # cuando la persona pidió volver atrás para corregir. Si ya hay
                # renglones guardados, lo que toca ver es el resumen.
                hay_guardado = bool(dia and dia.registros.exists())
                ctx["modo_edicion"] = (
                    not hay_guardado or request.GET.get("editar") == "1"
                ) and (dia is None or dia.editable)

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
            "guardar": self._guardar,
            "reabrir": self._reabrir,
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

    def _guardar(self, request, recurso, fecha):
        """Guarda de golpe el día completo que se armó en el navegador."""
        dia = svc.obtener_o_crear_dia(recurso, fecha)

        # Los proyectos se validan contra los que esa persona podía usar ese
        # día: el formulario los filtra, pero un POST a mano no.
        permitidos = {p.pk: p for p in svc.proyectos_disponibles(recurso, fecha)}
        actividades = {a.pk: a for a in svc.actividades_disponibles()}

        tipos = request.POST.getlist("renglon_tipo")
        proyectos = request.POST.getlist("renglon_proyecto")
        horas = request.POST.getlist("renglon_horas")
        detalles = request.POST.getlist("renglon_detalle")

        renglones = []
        for indice, tipo_id in enumerate(tipos):
            actividad = actividades.get(_entero(tipo_id))
            proyecto_id = _entero(proyectos[indice]) if indice < len(proyectos) else None

            proyecto = None
            if proyecto_id is not None:
                proyecto = permitidos.get(proyecto_id)
                if proyecto is None:
                    raise ValidationError(
                        f"Actividad {indice + 1}: no tenías ese proyecto asignado el "
                        f"{fecha:%d/%m/%Y}."
                    )

            renglones.append({
                "tipo_actividad": actividad,
                "proyecto": proyecto,
                "horas": (horas[indice] if indice < len(horas) else "").replace(",", "."),
                "detalle": detalles[indice] if indice < len(detalles) else "",
            })

        svc.guardar_renglones(dia, renglones)
        messages.success(request, "Actividades guardadas. Revisa el resumen antes de aceptar.")
        return redirect(f"{request.path}?fecha={fecha.isoformat()}")

    def _reabrir(self, request, recurso, fecha):
        """Vuelve al formulario para corregir antes de aceptar.

        Solo mientras el día siga abierto: una vez aceptado, se acabó.
        """
        dia = DiaLegalizado.objects.filter(recurso=recurso, fecha=fecha).first()
        if dia is None:
            raise ValidationError("No hay nada que corregir en este día.")
        if not dia.editable:
            raise ValidationError("Este día ya fue aceptado y no se puede modificar.")

        return redirect(f"{request.path}?fecha={fecha.isoformat()}&editar=1")

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
