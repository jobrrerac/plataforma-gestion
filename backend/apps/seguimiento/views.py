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
from django.urls import reverse
from django.views import View

from apps.accounts.roles import es_admin, es_admin_o_pm, puede_ver_todo
from apps.core.models import Proyecto, Recurso

from . import services as svc
from .models import AccionDeSeguimiento, Bloqueante, Feedback


def _mensaje_de_error(exc):
    return "; ".join(getattr(exc, "messages", [str(exc)]))


class BloqueantesView(LoginRequiredMixin, View):
    """Lo que frena a la gente. Dos modos, dos propósitos.

    - **registrar**: el formulario y los bloqueantes propios, para poder
      cerrarlos. Nada más. Quien entra a reportar que lleva dos días esperando
      no necesita ver primero un panel con los problemas de otras ocho personas.
    - **gestionar**: el panel de alertas, los filtros y todo lo que la persona
      alcanza a ver. Es la pantalla de quien tiene que actuar.

    Accesible a cualquiera que haya entrado: el bloqueante lo reporta quien lo
    sufre. Lo que cambia según el rol es el alcance de lo que se ve, y eso lo
    decide el servicio, no la plantilla.
    """

    login_url = "/login/"
    modo = "registrar"

    @property
    def template(self):
        return f"seguimiento/bloqueantes_{self.modo}.html"

    @property
    def ruta(self):
        return "bloqueantes" if self.modo == "registrar" else "bloqueantes-equipo"

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
        return redirect(self.ruta)

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

        bloqueante = svc.abrir_bloqueante(
            recurso, request.user, request.POST.get("necesito", ""),
            rol_que_resuelve=request.POST.get("rol_que_resuelve", "OTRO"),
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

        # En modo registrar solo se ven los propios, aunque la persona alcance
        # mas: la pantalla es para reportar y cerrar lo tuyo. Lo del equipo esta
        # a un clic, en Gestionar.
        propio_ = svc.recurso_de(request.user)
        if self.modo == "registrar":
            visibles = (
                visibles.filter(recurso=propio_) if propio_
                else visibles.none()
            )

        # Filtros. Solo tienen sentido para quien ve los de mas gente: a quien
        # solo ve los suyos, tres desplegables sobre una lista de dos le sobran.
        filtra = self.modo == "gestionar" and puede_ver_todo(request.user)
        f_proyecto = request.GET.get("proyecto", "") if filtra else ""
        f_recurso = request.GET.get("recurso", "") if filtra else ""
        f_estado = request.GET.get("estado", "") if filtra else ""

        if f_proyecto:
            visibles = visibles.filter(proyecto_id=f_proyecto)
        if f_recurso:
            visibles = visibles.filter(recurso_id=f_recurso)

        abiertos = list(visibles.filter(resuelto_en__isnull=True))
        # Los resueltos son historia: se muestran pocos y solo para que se vea
        # que cerrarlos sirve para algo. Con el filtro de estado en "resueltos"
        # se abre la mano, porque entonces es lo que se ha ido a buscar.
        tope = 60 if f_estado == "RESUELTOS" else 15
        resueltos = list(
            visibles.filter(resuelto_en__isnull=False).order_by("-resuelto_en")[:tope]
        )

        if f_estado == "ABIERTOS":
            resueltos = []
        elif f_estado == "VENCIDOS":
            abiertos = [b for b in abiertos if b.vencido]
            resueltos = []
        elif f_estado == "RESUELTOS":
            abiertos = []

        propio = svc.recurso_de(request.user)
        return {
            "abiertos": abiertos,
            "vencidos": [b for b in abiertos if b.vencido],
            "resueltos": resueltos,
            "horas_para_escalar": svc.HORAS_PARA_ESCALAR,
            # Las alertas son de la pantalla de gestion. En la de registrar
            # sobran: quien viene a reportar su bloqueo no tiene que atravesar
            # antes los problemas de otras ocho personas.
            "alertas": svc.alertas(request.user) if self.modo == "gestionar" else [],
            "es_admin": es_admin(request.user),
            "mi_recurso": propio,
            # Se pregunta por ROL, no por persona: un junior recién llegado sabe
            # que espera «al jefe de proyecto», no cómo se llama. La persona
            # concreta la deduce el servicio cuando puede.
            "roles": Bloqueante.ROL_CHOICES,
            "mis_proyectos": self._proyectos_de(propio),
            "recursos": Recurso.objects.filter(activo=True).order_by("nombre")
                        if es_admin(request.user) else [],
            # Filtros
            "puede_filtrar": filtra,
            "f_proyecto": f_proyecto,
            "f_recurso": f_recurso,
            "f_estado": f_estado,
            "recursos_filtro": (
                Recurso.objects.filter(activo=True).order_by("nombre") if filtra else []
            ),
            "proyectos_filtro": (
                Proyecto.objects.filter(bloqueantes__isnull=False)
                .distinct().order_by("codigo") if filtra else []
            ),
        }

    def _proyectos_de(self, recurso):
        if recurso is None:
            return Proyecto.objects.none()
        return Proyecto.objects.filter(
            asignaciones__recurso=recurso,
            asignaciones__estado__in=["APROBADA", "SOLICITADA"],
        ).distinct().order_by("codigo")


class FeedbackView(LoginRequiredMixin, View):
    """Observaciones en las dos direcciones, en una sola línea de tiempo.

    Que se lean juntas es el punto. Una observación sobre una persona al lado de
    lo que esa persona dijo de las condiciones en las que trabajaba es lo que
    permite distinguir un problema de desempeño de un problema de entrada — que
    es la distinción que este módulo existe para poder hacer.

    Dos modos:

    - **registrar**: tu propio feedback semanal y tu historial. Lo tuyo.
    - **gestionar**: se elige una persona y se ve **lo que da y lo que recibe**,
      con el formulario de observar al lado. Leer las dos cosas de alguien a la
      vez es lo que hace útil el módulo; obligar a saltar entre pantallas para
      compararlas es garantizar que nadie las compare.
    """

    login_url = "/login/"
    modo = "registrar"

    @property
    def template(self):
        return f"seguimiento/feedback_{self.modo}.html"

    @property
    def ruta(self):
        return "feedback" if self.modo == "registrar" else "feedback-equipo"

    def get(self, request):
        return render(request, self.template, self._ctx(request))

    def post(self, request):
        try:
            if request.POST.get("accion") == "seguimiento":
                self._accion(request)
                return redirect(
                    f"{reverse('feedback-equipo')}?recurso={request.POST.get('recurso', '')}"
                )
            direccion = request.POST.get("direccion")
            if direccion == Feedback.PROYECTO_A_RECURSO:
                self._sobre_la_persona(request)
            elif direccion == Feedback.RECURSO_A_PROYECTO:
                self._sobre_el_proyecto(request)
            else:
                messages.error(request, "Dirección de feedback desconocida.")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _mensaje_de_error(exc))
        return redirect(self.ruta)

    # -- acciones ----------------------------------------------------------

    def _proyecto_de(self, request):
        if request.POST.get("proyecto"):
            return Proyecto.objects.filter(pk=request.POST["proyecto"]).first()
        return None

    def _accion(self, request):
        recurso = Recurso.objects.filter(pk=request.POST.get("recurso")).first()
        if recurso is None:
            raise ValidationError("Elige sobre quién es la acción.")
        feedback = None
        if request.POST.get("feedback"):
            feedback = Feedback.objects.filter(pk=request.POST["feedback"]).first()
        svc.registrar_accion(
            recurso=recurso, autor=request.user,
            tipo=request.POST.get("tipo", ""),
            texto=request.POST.get("texto", ""),
            feedback=feedback,
        )
        messages.success(request, "Acción registrada en el seguimiento.")

    def _sobre_la_persona(self, request):
        recurso = Recurso.objects.filter(pk=request.POST.get("recurso")).first()
        if recurso is None:
            raise ValidationError("Elige sobre quién es la observación.")

        en_nombre_de = None
        if request.POST.get("en_nombre_de"):
            en_nombre_de = User.objects.filter(pk=request.POST["en_nombre_de"]).first()

        f = svc.registrar_feedback(
            recurso=recurso,
            autor=request.user,
            direccion=Feedback.PROYECTO_A_RECURSO,
            en_nombre_de=en_nombre_de,
            proyecto=self._proyecto_de(request),
            momento=request.POST.get("momento") or Feedback.SEMANAL,
            situacion=request.POST.get("situacion", "").strip(),
            conducta=request.POST.get("conducta", "").strip(),
            impacto=request.POST.get("impacto", "").strip(),
            tipo=request.POST.get("tipo", ""),
            dimension=request.POST.get("dimension", ""),
        )
        if f.transcrito:
            messages.success(
                request,
                f"Observación registrada en nombre de "
                f"{f.en_nombre_de.get_full_name() or f.en_nombre_de.username}. "
                f"Queda constancia de que la escribiste tú.",
            )
        else:
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
            comentario=request.POST.get("comentario", "").strip(),
            cambio_alcance=request.POST.get("cambio_alcance") == "si",
            veces_cambio_alcance=entero("veces_cambio_alcance"),
            que_ahorraria_tiempo=request.POST.get("que_ahorraria_tiempo", "").strip(),
        )
        messages.success(
            request,
            "Gracias. Esto solo lo ve tu manager en Colombia, no el jefe de proyecto.",
        )

    # -- contexto ----------------------------------------------------------

    def _proyectos_por_recurso(self, usuario, recursos):
        """Proyectos en los que ha participado cada persona observable.

        Se cruza con lo que quien observa alcanza: un PM ve los suyos, el Admin
        todos. Va al navegador porque el desplegable tiene que reaccionar al
        cambiar de persona sin recargar la pagina.

        Devuelve un **diccionario**, no una cadena. Serializarlo aqui y volver a
        pasarlo por `|json_script` en la plantilla lo codifica dos veces: el
        `JSON.parse` del navegador devuelve entonces una cadena en vez de un
        objeto, `datos[id]` es `undefined` y el desplegable de proyecto se queda
        vacio sin que salte ningun error. Es exactamente el sintoma que se
        reporto tres veces.
        """
        from apps.assignments.models import Asignacion

        alcance = None
        if not es_admin(usuario):
            alcance = set(
                svc._proyectos_que_dirige(usuario).values_list("pk", flat=True)
            )

        mapa = {}
        asignaciones = (
            Asignacion.objects
            .filter(recurso__in=recursos)
            .select_related("proyecto")
            .order_by("proyecto__codigo")
        )
        for a in asignaciones:
            if alcance is not None and a.proyecto_id not in alcance:
                continue
            entradas = mapa.setdefault(str(a.recurso_id), [])
            if not any(e["id"] == a.proyecto_id for e in entradas):
                entradas.append({
                    "id": a.proyecto_id,
                    "texto": f"{a.proyecto.codigo} · {a.proyecto.nombre}",
                })
        return mapa

    def _posibles_observadores(self, usuario, recursos):
        """A quién se le puede atribuir una observación transcrita.

        Los jefes de proyecto, sus aprobadores delegados y los Admin: las tres
        figuras que trabajan con estas personas y pueden haber visto la conducta.
        Se ofrece a **cualquiera que ya pueda observar**, no solo al Admin: un
        delegado también recibe por chat lo que el PM no entra a escribir, y
        obligarle a pedírselo al Admin es añadir un salto para nada.

        Se acota a quien dirige alguno de los proyectos de la gente observable.
        Ofrecer la lista entera invitaría a atribuir una observación a alguien
        que no trabaja con esa persona, y esa firma no la sostiene nadie.
        """
        from django.db.models import Q

        from apps.core.models import Proyecto

        proyectos = Proyecto.objects.filter(asignaciones__recurso__in=recursos).distinct()
        ids = set()
        for proyecto in proyectos:
            ids.add(proyecto.pm_id)
            if proyecto.aprobador_delegado_id:
                ids.add(proyecto.aprobador_delegado_id)
        return list(
            User.objects.filter(Q(pk__in=ids) | Q(groups__name="Admin"))
            .distinct().order_by("first_name", "username")
        )

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

        visible = svc.feedback_visible(request.user)

        # En modo gestionar se mira a UNA persona: lo que dio y lo que recibio,
        # enfrentado. Sin elegir a nadie se ve el historial entero, que sirve
        # para hacerse una idea pero no para decidir sobre alguien.
        elegido = None
        if self.modo == "gestionar" and request.GET.get("recurso"):
            elegido = Recurso.objects.filter(pk=request.GET["recurso"]).first()
            if elegido is not None:
                visible = visible.filter(recurso=elegido)

        recibido = [f for f in visible if f.direccion == Feedback.PROYECTO_A_RECURSO]
        dado = [f for f in visible if f.direccion == Feedback.RECURSO_A_PROYECTO]

        return {
            "modo": self.modo,
            "elegido": elegido,
            # Separadas y no en un solo listado: la pregunta que se hace quien
            # entra aqui es «que le dicen» frente a «que dice el», y mezclarlas
            # en orden cronologico obliga a reconstruir esa division a ojo.
            "recibido": recibido[:40],
            "dado": dado[:40],
            # Para transcribir: quien podria haber hecho la observacion.
            "posibles_observadores": (
                self._posibles_observadores(request.user, recursos_observables)
                if recursos_observables else []
            ),
            "acciones": (
                svc.acciones_de(request.user, elegido)[:30] if elegido else []
            ),
            "tipos_accion": AccionDeSeguimiento.TIPO_CHOICES,
            "puede_registrar_acciones": es_admin(request.user),
            "recursos_del_equipo": (
                Recurso.objects.filter(activo=True).order_by("nombre")
                if self.modo == "gestionar" and puede_ver_todo(request.user) else []
            ),
            "puede_observar": bool(recursos_observables),
            "recursos_observables": recursos_observables,
            # Para acotar el desplegable de proyecto a los de la persona elegida.
            # Sin esto se puede observar a alguien "en" un proyecto donde nunca
            # estuvo, y esa observacion no la puede sostener nadie despues.
            "proyectos_por_recurso": self._proyectos_por_recurso(
                request.user, recursos_observables,
            ),
            "mi_recurso": propio,
            "mis_proyectos": BloqueantesView()._proyectos_de(propio),
            "proyectos_que_dirijo": svc._proyectos_que_dirige(request.user).order_by("codigo"),
            "dimensiones": Feedback.DIMENSION_CHOICES,
            "momentos": Feedback.MOMENTO_CHOICES,
            "P2R": Feedback.PROYECTO_A_RECURSO,
            "R2P": Feedback.RECURSO_A_PROYECTO,
        }
