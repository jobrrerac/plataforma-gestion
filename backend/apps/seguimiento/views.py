"""Pantallas del seguimiento: bloqueantes y feedback.

Las dos son de escritura rápida a propósito. Un formulario largo para reportar
un bloqueo consigue que nadie reporte bloqueos, y una plantilla de evaluación
con doce campos consigue que se rellene sin leerla.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views import View

from apps.accounts.roles import es_admin, es_admin_o_pm
from apps.core.models import Proyecto, Recurso

from . import services as svc
from .models import Feedback


def _mensaje_de_error(exc):
    return "; ".join(getattr(exc, "messages", [str(exc)]))


class BloqueantesView(LoginRequiredMixin, View):
    """Lo que frena a la gente, y el panel de lo que ya se pasó de plazo.

    Accesible a cualquiera que haya entrado: el bloqueante lo reporta quien lo
    sufre. Lo que cambia según el rol es el alcance de lo que se ve, y eso lo
    decide el servicio, no la plantilla.
    """

    template = "seguimiento/bloqueantes.html"
    login_url = "/login/"

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        accion = request.POST.get("accion")
        try:
            if accion == "abrir":
                self._abrir(request)
            elif accion == "resolver":
                self._resolver(request)
            else:
                messages.error(request, "Acción desconocida.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _mensaje_de_error(exc))
        return redirect("bloqueantes")

    # -- acciones ----------------------------------------------------------

    def _abrir(self, request):
        recurso = svc.recurso_de(request.user)
        if es_admin(request.user) and request.POST.get("recurso"):
            recurso = Recurso.objects.filter(pk=request.POST["recurso"]).first()
        if recurso is None:
            raise ValidationError(
                "Tu usuario no está asociado a ningún recurso, así que no hay "
                "a nombre de quién reportarlo."
            )

        proyecto = None
        if request.POST.get("proyecto"):
            proyecto = Proyecto.objects.filter(pk=request.POST["proyecto"]).first()

        bloquea_usuario = None
        if request.POST.get("bloquea_usuario"):
            bloquea_usuario = User.objects.filter(pk=request.POST["bloquea_usuario"]).first()

        bloqueante = svc.abrir_bloqueante(
            recurso, request.user, request.POST.get("necesito", ""),
            bloquea_usuario=bloquea_usuario,
            bloquea_nombre=request.POST.get("bloquea_nombre", ""),
            proyecto=proyecto,
            mientras_tanto=request.POST.get("mientras_tanto", ""),
        )
        messages.success(
            request,
            f"Bloqueante registrado. Si sigue abierto en "
            f"{svc.HORAS_PARA_ESCALAR} h, se avisa solo. Va a cuenta de "
            f"{bloqueante.bloqueador}.",
        )

    def _resolver(self, request):
        bloqueante = svc.bloqueantes_visibles(request.user).filter(
            pk=request.POST.get("bloqueante")
        ).first()
        if bloqueante is None:
            raise ValidationError("Ese bloqueante no existe o no es tuyo.")
        svc.resolver_bloqueante(
            bloqueante, request.user, request.POST.get("como_se_resolvio", ""),
        )
        messages.success(
            request,
            f"Resuelto tras {bloqueante.horas_abierto:.0f} h. Queda registrado "
            f"el tiempo que tomó.",
        )

    # -- contexto ----------------------------------------------------------

    def _ctx(self, request):
        visibles = svc.bloqueantes_visibles(request.user)
        abiertos = [b for b in visibles.filter(resuelto_en__isnull=True)]
        # Los resueltos son historia: se muestran pocos y solo para que se vea
        # que cerrarlos sirve para algo.
        resueltos = list(
            visibles.filter(resuelto_en__isnull=False).order_by("-resuelto_en")[:15]
        )

        propio = svc.recurso_de(request.user)
        return {
            "abiertos": abiertos,
            "vencidos": [b for b in abiertos if b.vencido],
            "resueltos": resueltos,
            "horas_para_escalar": svc.HORAS_PARA_ESCALAR,
            "alertas": svc.alertas(request.user),
            "es_admin": es_admin(request.user),
            "mi_recurso": propio,
            # Para el selector de a quién le toca desbloquear: los PM y
            # delegados de los proyectos de esa persona, más los Admin. No se
            # ofrece la lista entera de usuarios: elegir de 200 nombres es peor
            # que escribirlo a mano.
            "posibles_bloqueadores": self._posibles_bloqueadores(request.user, propio),
            "mis_proyectos": self._proyectos_de(propio),
            "recursos": Recurso.objects.filter(activo=True).order_by("nombre")
                        if es_admin(request.user) else [],
        }

    def _proyectos_de(self, recurso):
        if recurso is None:
            return Proyecto.objects.none()
        return Proyecto.objects.filter(
            asignaciones__recurso=recurso,
            asignaciones__estado__in=["APROBADA", "SOLICITADA"],
        ).distinct().order_by("codigo")

    def _posibles_bloqueadores(self, usuario, recurso):
        from django.db.models import Q

        proyectos = self._proyectos_de(recurso)
        ids = set()
        for proyecto in proyectos:
            if proyecto.pm_id:
                ids.add(proyecto.pm_id)
            if proyecto.aprobador_delegado_id:
                ids.add(proyecto.aprobador_delegado_id)
        return User.objects.filter(
            Q(pk__in=ids) | Q(groups__name="Admin")
        ).distinct().order_by("first_name", "username")


class FeedbackView(LoginRequiredMixin, View):
    """Observaciones en las dos direcciones, en una sola línea de tiempo.

    Que se lean juntas es el punto. Una observación sobre una persona al lado de
    lo que esa persona dijo de las condiciones en las que trabajaba es lo que
    permite distinguir un problema de desempeño de un problema de entrada — que
    es la distinción que este módulo existe para poder hacer.
    """

    template = "seguimiento/feedback.html"
    login_url = "/login/"

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        try:
            direccion = request.POST.get("direccion")
            if direccion == Feedback.PROYECTO_A_RECURSO:
                self._sobre_la_persona(request)
            elif direccion == Feedback.RECURSO_A_PROYECTO:
                self._sobre_el_proyecto(request)
            else:
                messages.error(request, "Dirección de feedback desconocida.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _mensaje_de_error(exc))
        return redirect("feedback")

    # -- acciones ----------------------------------------------------------

    def _proyecto_de(self, request):
        if request.POST.get("proyecto"):
            return Proyecto.objects.filter(pk=request.POST["proyecto"]).first()
        return None

    def _sobre_la_persona(self, request):
        recurso = Recurso.objects.filter(pk=request.POST.get("recurso")).first()
        if recurso is None:
            raise ValidationError("Elige sobre quién es la observación.")

        svc.registrar_feedback(
            recurso=recurso,
            autor=request.user,
            direccion=Feedback.PROYECTO_A_RECURSO,
            proyecto=self._proyecto_de(request),
            momento=request.POST.get("momento") or Feedback.SEMANAL,
            situacion=request.POST.get("situacion", "").strip(),
            conducta=request.POST.get("conducta", "").strip(),
            impacto=request.POST.get("impacto", "").strip(),
            tipo=request.POST.get("tipo", ""),
            dimension=request.POST.get("dimension", ""),
        )
        messages.success(request, "Observación registrada. La persona puede leerla.")

    def _sobre_el_proyecto(self, request):
        recurso = svc.recurso_de(request.user)
        if recurso is None:
            raise ValidationError(
                "Tu usuario no está asociado a ningún recurso."
            )

        def entero(nombre):
            valor = request.POST.get(nombre, "").strip()
            return int(valor) if valor.isdigit() else None

        svc.registrar_feedback(
            recurso=recurso,
            autor=request.user,
            direccion=Feedback.RECURSO_A_PROYECTO,
            proyecto=self._proyecto_de(request),
            momento=request.POST.get("momento") or Feedback.SEMANAL,
            claridad_objetivo=entero("claridad_objetivo"),
            tuve_que_intuir=request.POST.get("tuve_que_intuir") == "si",
            que_intui=request.POST.get("que_intui", "").strip(),
            horas_hasta_respuesta=entero("horas_hasta_respuesta"),
            cambio_alcance=request.POST.get("cambio_alcance") == "si",
            veces_cambio_alcance=entero("veces_cambio_alcance"),
            que_ahorraria_tiempo=request.POST.get("que_ahorraria_tiempo", "").strip(),
        )
        messages.success(
            request,
            "Gracias. Esto lo lee el equipo de Colombia, no el jefe de proyecto.",
        )

    # -- contexto ----------------------------------------------------------

    def _ctx(self, request):
        propio = svc.recurso_de(request.user)
        recursos_observables = []
        if es_admin(request.user):
            recursos_observables = list(Recurso.objects.filter(activo=True).order_by("nombre"))
        elif es_admin_o_pm(request.user) or svc._proyectos_que_dirige(request.user).exists():
            recursos_observables = [
                r for r in Recurso.objects.filter(activo=True).order_by("nombre")
                if svc.puede_observar_a(request.user, r)
            ]

        return {
            "historial": svc.feedback_visible(request.user)[:40],
            "puede_observar": bool(recursos_observables),
            "recursos_observables": recursos_observables,
            "mi_recurso": propio,
            "mis_proyectos": BloqueantesView()._proyectos_de(propio),
            "dimensiones": Feedback.DIMENSION_CHOICES,
            "momentos": Feedback.MOMENTO_CHOICES,
            "P2R": Feedback.PROYECTO_A_RECURSO,
            "R2P": Feedback.RECURSO_A_PROYECTO,
        }
