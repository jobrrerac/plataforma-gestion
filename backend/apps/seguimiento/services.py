"""Reglas del seguimiento: quién ve qué, qué se puede registrar y qué se alerta.

Tres bloques: bloqueantes, feedback y alertas.

Las alertas son la razón de ser del módulo. El seguimiento de 27 personas no es
una ronda de 27 conversaciones —eso no escala y ya se demostró que no escala—:
es atender lo que el sistema levanta. La pregunta «¿por qué no hiciste
seguimiento uno a uno con todos?» tiene una mala respuesta, que es intentarlo, y
una buena: *el sistema levantó estas alertas y actué sobre todas, con fecha*.
"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import es_admin, es_admin_o_pm, es_visor
from apps.assignments.models import Asignacion
from apps.core.models import Recurso
from apps.legalizacion.models import RegistroHoras

from .models import HORAS_PARA_ESCALAR, Bloqueante, Feedback

# Cuántos días naturales se miran hacia atrás para decidir si alguien está sin
# trabajo real. Siete y no cinco para que un puente o un festivo no borre la
# señal justo la semana en que más falta hace.
DIAS_VENTANA_SIN_TAREA = 7

# Días sin imputar al proyecto de la asignación antes de preguntar. Uno solo es
# ruido —una formación, un día de soporte—; dos seguidos ya no.
DIAS_SIN_TAREA_PARA_ALERTAR = 2

# Cada cuánto se espera una observación del proyecto sobre cada persona.
DIAS_SIN_FEEDBACK_PARA_ALERTAR = 14


def recurso_de(usuario):
    return Recurso.objects.filter(usuario=usuario).first()


def _proyectos_que_dirige(usuario):
    """Proyectos donde esta persona firma: como PM o como delegada."""
    from django.db.models import Q

    from apps.core.models import Proyecto

    return Proyecto.objects.filter(
        Q(pm=usuario) | Q(aprobador_delegado=usuario)
    )


# ---------------------------------------------------------------------------
# Bloqueantes
# ---------------------------------------------------------------------------


def puede_abrir_bloqueante(usuario, recurso) -> bool:
    """Cada quien reporta lo suyo. El Admin puede hacerlo por alguien más.

    Que un Admin pueda abrirlo en nombre de otro no es una puerta trasera: en la
    práctica el bloqueo sale en una llamada o en un chat, y si registrarlo
    dependiera de que la persona entre a la aplicación, la mitad no llegaría.
    Queda constancia igual de quién lo reportó.
    """
    if recurso is None:
        return False
    if es_admin(usuario):
        return True
    propio = recurso_de(usuario)
    return propio is not None and propio.pk == recurso.pk


@transaction.atomic
def abrir_bloqueante(
    recurso, usuario, necesito, *, rol_que_resuelve="OTRO", bloquea_nombre="",
    proyecto=None, mientras_tanto="",
):
    """Registra algo que impide avanzar.

    Se pide poco a propósito: qué te frena y a qué rol le toca resolverlo. Un
    formulario largo para reportar un bloqueo consigue que la gente no reporte
    bloqueos, y pedirle a un junior recién llegado el nombre exacto de quien
    tiene que desatascarlo es pedirle un dato que muchas veces no tiene.
    """
    if not puede_abrir_bloqueante(usuario, recurso):
        raise PermissionDenied("Solo puedes reportar bloqueantes propios.")

    necesito = (necesito or "").strip()
    if not necesito:
        raise ValidationError("Di qué te está bloqueando para poder avanzar.")

    bloqueante = Bloqueante(
        recurso=recurso,
        proyecto=proyecto,
        necesito=necesito[:300],
        rol_que_resuelve=rol_que_resuelve or "OTRO",
        bloquea_nombre=(bloquea_nombre or "").strip()[:120],
        mientras_tanto=(mientras_tanto or "").strip()[:300],
        creado_por=usuario,
        # La persona concreta se DEDUCE, no se pregunta: si el bloqueo es del
        # jefe de proyecto y el proyecto tiene uno, ya sabemos quién es. Es lo
        # que permite seguir midiendo el tiempo de desbloqueo por persona en el
        # caso más frecuente sin añadir una pregunta al formulario.
        bloquea_usuario=(
            proyecto.pm if rol_que_resuelve == "PM" and proyecto and proyecto.pm_id
            else None
        ),
    )
    bloqueante.full_clean(exclude=["creado_en"])
    bloqueante.save()
    return bloqueante


def puede_resolver(usuario, bloqueante) -> bool:
    """Quién puede darlo por resuelto.

    Quien lo reportó, porque es quien sabe si de verdad pudo seguir. Quien
    figura como responsable de desbloquearlo, porque cerrar lo que uno mismo
    resolvió es lo natural. Y el Admin.

    Lo que **no** se hace es dejar que lo cierre cualquiera: el tiempo entre las
    dos fechas es el indicador, y si lo puede cerrar quien pasaba por ahí, el
    indicador deja de significar nada.
    """
    if es_admin(usuario):
        return True
    if bloqueante.creado_por_id == usuario.pk:
        return True
    if bloqueante.bloquea_usuario_id and bloqueante.bloquea_usuario_id == usuario.pk:
        return True
    propio = recurso_de(usuario)
    return propio is not None and propio.pk == bloqueante.recurso_id


@transaction.atomic
def resolver_bloqueante(bloqueante, usuario, como=""):
    """Cierra el bloqueante y con eso fija el tiempo que estuvo abierto."""
    bloqueante = Bloqueante.objects.select_for_update().get(pk=bloqueante.pk)

    if not puede_resolver(usuario, bloqueante):
        raise PermissionDenied(
            "Este bloqueante lo cierra quien lo reportó, quien tenía que "
            "resolverlo, o un administrador."
        )
    if not bloqueante.abierto:
        raise ValidationError("Este bloqueante ya estaba resuelto.")

    bloqueante.resuelto_en = timezone.now()
    bloqueante.resuelto_por = usuario
    bloqueante.como_se_resolvio = (como or "").strip()[:300]
    bloqueante.save(update_fields=[
        "resuelto_en", "resuelto_por", "como_se_resolvio", "updated_at",
    ])
    return bloqueante


def bloqueantes_visibles(usuario, *, solo_abiertos=False):
    """Lo que esta persona puede ver.

    - Admin: todo, porque es quien tiene que actuar sobre las alertas.
    - PM y delegados: los de sus proyectos, y los que le señalan a él.
    - Cualquier otro: los suyos.
    """
    qs = Bloqueante.objects.select_related(
        "recurso", "proyecto", "bloquea_usuario", "creado_por",
    )
    if solo_abiertos:
        qs = qs.filter(resuelto_en__isnull=True)

    if es_admin(usuario):
        return qs
    # El Visor mira y no escribe en ninguna parte: ese es su papel. Ve lo que
    # frena a cada persona porque es justo lo que hace falta para entender una
    # semana floja sin tener que preguntar.
    if es_visor(usuario):
        return qs

    from django.db.models import Q

    condiciones = Q(bloquea_usuario=usuario) | Q(creado_por=usuario)
    propio = recurso_de(usuario)
    if propio is not None:
        condiciones |= Q(recurso=propio)
    if es_admin_o_pm(usuario):
        condiciones |= Q(proyecto__in=_proyectos_que_dirige(usuario))
    return qs.filter(condiciones).distinct()


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


def puede_observar_a(usuario, recurso) -> bool:
    """Quién puede escribir una observación sobre una persona.

    El Admin, y quien dirige un proyecto al que esa persona está asignada. No
    cualquier PM sobre cualquiera: observar a alguien con quien no trabajas es
    justo el tipo de calificación que después no se puede sostener.
    """
    if es_admin(usuario):
        return True
    if not es_admin_o_pm(usuario) and not _proyectos_que_dirige(usuario).exists():
        return False
    return Asignacion.objects.filter(
        recurso=recurso,
        proyecto__in=_proyectos_que_dirige(usuario),
        estado__in=["APROBADA", "SOLICITADA"],
    ).exists()


@transaction.atomic
def registrar_feedback(*, recurso, autor, direccion, **campos):
    """Guarda una observación en cualquiera de las dos direcciones."""
    if direccion == Feedback.PROYECTO_A_RECURSO:
        if not puede_observar_a(autor, recurso):
            raise PermissionDenied(
                "Solo puede observar a esta persona quien dirige un proyecto al "
                "que está asignada, o un administrador."
            )
    elif direccion == Feedback.RECURSO_A_PROYECTO:
        propio = recurso_de(autor)
        if not es_admin(autor) and (propio is None or propio.pk != recurso.pk):
            raise PermissionDenied("Solo puedes opinar sobre tus propios proyectos.")
    else:
        raise ValidationError("Dirección de feedback desconocida.")

    feedback = Feedback(recurso=recurso, autor=autor, direccion=direccion, **campos)
    feedback.full_clean()
    feedback.save()
    return feedback


def feedback_visible(usuario, *, recurso=None):
    """Qué observaciones puede leer esta persona.

    Aquí hay una asimetría **deliberada**, y conviene entender por qué.

    Lo que el proyecto escribe sobre una persona, esa persona lo ve. No hay
    expediente secreto: un registro que se usa para evaluar a alguien y que esa
    persona no puede leer no se puede rebatir, y por tanto no se puede
    considerar justo.

    Lo que una persona escribe sobre el proyecto, en cambio, **no lo ve el jefe
    de proyecto individualmente**: lo ven ella y el Admin. La razón es práctica,
    no política. Quien depende de otro para que le asignen tareas y le resuelvan
    dudas no va a escribir «llevo tres días sin respuesta» si sabe que esa misma
    persona lo va a leer con su nombre encima. Ya pasó: el feedback honesto solo
    apareció cuando se pidió en privado y con la promesa de no escalarlo.

    Lo que sí llega al proyecto es el patrón agregado, no la entrada suelta. Se
    protege la observación individual y se publica la tendencia.
    """
    qs = Feedback.objects.select_related("recurso", "proyecto", "autor")
    if recurso is not None:
        qs = qs.filter(recurso=recurso)

    if es_admin(usuario):
        return qs

    from django.db.models import Q

    # El Visor ve lo que se observó **sobre** las personas, y solo eso. Lo que
    # alguien escribió sobre su proyecto sigue reservado a quien lo escribió y a
    # su manager: ampliar ese círculo, aunque sea a un rol de solo lectura,
    # rompe la promesa con la que se pidió.
    if es_visor(usuario):
        return qs.filter(
            Q(direccion=Feedback.PROYECTO_A_RECURSO) | Q(autor=usuario)
        )

    # Lo que se escribió sobre mí, y lo que yo escribí, siempre.
    condiciones = Q(autor=usuario)
    propio = recurso_de(usuario)
    if propio is not None:
        condiciones |= Q(recurso=propio)

    # Como PM veo lo que se observó sobre la gente de mis proyectos, pero solo
    # en esa dirección.
    if es_admin_o_pm(usuario):
        condiciones |= Q(
            direccion=Feedback.PROYECTO_A_RECURSO,
            proyecto__in=_proyectos_que_dirige(usuario),
        )
    return qs.filter(condiciones).distinct()


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------


def _asignaciones_activas(hoy):
    return (
        Asignacion.objects
        .filter(estado="APROBADA", fecha_inicio__lte=hoy)
        .filter(fecha_fin__gte=hoy)
        .select_related("recurso", "proyecto")
    )


def bloqueantes_vencidos():
    """Abiertos por encima del plazo. La alerta más directa que hay."""
    limite = timezone.now() - timedelta(hours=HORAS_PARA_ESCALAR)
    return list(
        Bloqueante.objects
        .filter(resuelto_en__isnull=True, creado_en__lte=limite)
        .select_related("recurso", "proyecto", "bloquea_usuario")
        .order_by("creado_en")
    )


def recursos_sin_tarea(hoy=None):
    """Gente con asignación viva que no está imputando a su proyecto.

    Es la traducción a datos de «si alguien no está recibiendo trabajo, somos
    nosotros los que nos tenemos que dar cuenta primero». Y no necesita ningún
    dato nuevo: sale de cruzar el plan (`Asignacion`) con lo declarado
    (`RegistroHoras`), que es exactamente para lo que se separaron los dos
    módulos.

    Cuenta los días en los que la persona **sí registró horas** pero ninguna fue
    a su proyecto. Un día sin registrar es otro problema —ya lo cubre la lista de
    días pendientes— y mezclarlos aquí produciría una alerta que se dispara por
    dos causas distintas y no dice cuál.
    """
    hoy = hoy or timezone.localdate()
    desde = hoy - timedelta(days=DIAS_VENTANA_SIN_TAREA)

    activas = list(_asignaciones_activas(hoy))
    if not activas:
        return []

    registros = (
        RegistroHoras.objects
        .filter(
            dia__recurso__in=[a.recurso_id for a in activas],
            dia__fecha__gte=desde,
            dia__fecha__lte=hoy,
        )
        .select_related("dia")
        .values_list("dia__recurso_id", "dia__fecha", "proyecto_id")
    )

    # {recurso: {fecha: {proyectos a los que imputó ese día}}}
    por_recurso = {}
    for recurso_id, fecha, proyecto_id in registros:
        por_recurso.setdefault(recurso_id, {}).setdefault(fecha, set()).add(proyecto_id)

    hallazgos = []
    for asignacion in activas:
        dias = por_recurso.get(asignacion.recurso_id, {})
        sueltos = [
            fecha for fecha, proyectos in dias.items()
            if asignacion.proyecto_id not in proyectos
        ]
        if len(sueltos) >= DIAS_SIN_TAREA_PARA_ALERTAR:
            hallazgos.append({
                "recurso": asignacion.recurso,
                "proyecto": asignacion.proyecto,
                "dias": sorted(sueltos, reverse=True),
            })
    return hallazgos


def recursos_sin_feedback(hoy=None):
    """Gente asignada sobre la que el proyecto no ha dicho nada.

    El primer síntoma de abandono no es una queja: es el silencio. Alguien de
    quien nadie dice nada durante dos semanas es exactamente el caso que ya se
    nos pasó una vez.
    """
    hoy = hoy or timezone.localdate()
    corte = hoy - timedelta(days=DIAS_SIN_FEEDBACK_PARA_ALERTAR)

    activas = list(_asignaciones_activas(hoy))
    if not activas:
        return []

    con_feedback = set(
        Feedback.objects
        .filter(
            direccion=Feedback.PROYECTO_A_RECURSO,
            fecha_observacion__gte=corte,
            recurso_id__in=[a.recurso_id for a in activas],
        )
        .values_list("recurso_id", flat=True)
    )
    return [
        {"recurso": a.recurso, "proyecto": a.proyecto, "desde": corte}
        for a in activas if a.recurso_id not in con_feedback
    ]


def alertas(usuario):
    """Todo lo que pide una acción, en un solo sitio y ordenado por urgencia.

    Solo para quien puede hacer algo al respecto. Enseñarle a un ingeniero que
    hay tres personas sin tarea no le sirve de nada y sí le cuenta cosas de sus
    compañeros que no le corresponden.
    """
    if not es_admin(usuario):
        return []

    salida = []
    for bloqueante in bloqueantes_vencidos():
        salida.append({
            "tipo": "BLOQUEANTE",
            "gravedad": "alta",
            "recurso": bloqueante.recurso,
            "texto": (
                f"{bloqueante.recurso.nombre} lleva {bloqueante.horas_abierto:.0f} h "
                f"esperando a {bloqueante.bloqueador}"
            ),
            "detalle": bloqueante.necesito,
            "accion": "Escalar por escrito, citando la fecha de alta",
            "objeto": bloqueante,
        })
    for hallazgo in recursos_sin_tarea():
        salida.append({
            "tipo": "SIN_TAREA",
            "gravedad": "alta",
            "recurso": hallazgo["recurso"],
            "texto": (
                f"{hallazgo['recurso'].nombre} lleva {len(hallazgo['dias'])} días "
                f"sin imputar a {hallazgo['proyecto'].codigo}"
            ),
            "detalle": ", ".join(f.strftime("%d/%m") for f in hallazgo["dias"]),
            "accion": "Confirmar si tiene tarea. Si no la tiene, reclamarla",
            "objeto": None,
        })
    for hallazgo in recursos_sin_feedback():
        salida.append({
            "tipo": "SIN_FEEDBACK",
            "gravedad": "media",
            "recurso": hallazgo["recurso"],
            "texto": (
                f"Nadie ha observado a {hallazgo['recurso'].nombre} en "
                f"{DIAS_SIN_FEEDBACK_PARA_ALERTAR} días"
            ),
            "detalle": f"Asignada a {hallazgo['proyecto'].codigo}",
            "accion": "Pedir la observación al jefe de proyecto",
            "objeto": None,
        })

    orden = {"alta": 0, "media": 1, "baja": 2}
    salida.sort(key=lambda a: orden.get(a["gravedad"], 9))
    return salida
