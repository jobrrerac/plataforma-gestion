"""Flujo de novedades: el ingeniero registra, el Admin aprueba.

Reglas que sostiene este módulo:

- Una novedad solo descuenta capacidad cuando está APROBADA. Ese filtro vive en
  `services.CalendarioRango` y `services.es_habil`; aquí solo se mueve el estado.
- El ingeniero registra novedades de SU recurso y de ningún otro.
- Aprobar y rechazar es exclusivo del Admin.
- Cancelar una novedad pendiente es un soft-delete: la fila se conserva.
"""

from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.roles import es_admin
from apps.core.models import Recurso

from .models import Indisponibilidad

# Ventana máxima hacia atrás para registrar una novedad. Se permite algo de
# retroactividad porque la gente avisa tarde, pero no reescribir el pasado
# lejano: los meses cerrados ya se usaron para planificar y facturar.
DIAS_RETROACTIVIDAD_MAX = 60

# Qué hacer con una asignación cuyo período se cruza con la ausencia. Son las
# mismas dos políticas de `Asignacion.POLITICA_CHOICES`, y se aplican con la
# maquinaria de `LiberacionRecurso`.
POLITICAS_VALIDAS = ("RECOMPUTAR", "REDUCIR")


def recurso_de(usuario):
    """Recurso asociado a la cuenta, o None.

    El enlace lo mantiene el SSO por email (ver `apps.accounts.oidc`), y también
    puede hacerse a mano desde el admin.
    """
    return Recurso.objects.filter(usuario=usuario).first()


def novedades_de(recurso, incluir_canceladas=False):
    """Novedades del recurso, de la más reciente a la más antigua."""
    qs = Indisponibilidad.all_objects if incluir_canceladas else Indisponibilidad.objects
    return (
        qs.filter(recurso=recurso)
        .select_related("revisada_por", "solicitada_por")
        .order_by("-fecha_inicio")
    )


def pendientes():
    """Cola de aprobación del Admin."""
    return (
        Indisponibilidad.objects.filter(estado="PENDIENTE")
        .select_related("recurso", "solicitada_por")
        .order_by("fecha_inicio")
    )


def _validar_fechas(fecha_inicio, fecha_fin):
    if fecha_fin < fecha_inicio:
        raise ValidationError("La fecha de fin no puede ser anterior a la de inicio.")

    limite = date.today() - timedelta(days=DIAS_RETROACTIVIDAD_MAX)
    if fecha_inicio < limite:
        raise ValidationError(
            f"No se pueden registrar novedades con más de {DIAS_RETROACTIVIDAD_MAX} "
            "días de antigüedad. Pide a un administrador que la cargue por ti."
        )


def _validar_sin_solape(recurso, fecha_inicio, fecha_fin, excluir_pk=None):
    """Impide dos novedades vivas sobre los mismos días.

    Cuentan tanto las aprobadas como las pendientes: si ya hay una solicitud en
    revisión para esas fechas, mandar otra encima solo genera trabajo duplicado
    al Admin y ambigüedad sobre cuál vale.
    """
    solapadas = Indisponibilidad.objects.filter(
        recurso=recurso,
        estado__in=["PENDIENTE", "APROBADA"],
        fecha_inicio__lte=fecha_fin,
        fecha_fin__gte=fecha_inicio,
    )
    if excluir_pk:
        solapadas = solapadas.exclude(pk=excluir_pk)

    choque = solapadas.first()
    if choque:
        raise ValidationError(
            f"Ya tienes una novedad {choque.get_estado_display().lower()} "
            f"del {choque.fecha_inicio} al {choque.fecha_fin} que se cruza con estas fechas."
        )


@transaction.atomic
def registrar_novedad(usuario, fecha_inicio, fecha_fin, tipo, motivo=""):
    """Alta de una novedad por parte del propio ingeniero. Queda PENDIENTE."""
    recurso = recurso_de(usuario)
    if recurso is None:
        raise ValidationError(
            "Tu cuenta no está vinculada a ningún recurso, así que no se puede "
            "registrar la novedad a tu nombre. Contacta con un administrador."
        )

    _validar_fechas(fecha_inicio, fecha_fin)
    _validar_sin_solape(recurso, fecha_inicio, fecha_fin)

    return Indisponibilidad.objects.create(
        recurso=recurso,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        tipo=tipo,
        motivo=motivo[:200],
        origen="MANUAL",
        estado="PENDIENTE",
        solicitada_por=usuario,
    )


@transaction.atomic
def registrar_por_autoridad(usuario, recurso, fecha_inicio, fecha_fin, tipo, motivo=""):
    """Alta hecha por un PM o un Admin: nace ya APROBADA.

    Si la registra alguien con autoridad no hay nada que aprobar, y obligar a un
    segundo paso solo conseguiría que quedaran novedades pendientes para siempre
    sin descontar capacidad.
    """
    _validar_fechas(fecha_inicio, fecha_fin)
    _validar_sin_solape(recurso, fecha_inicio, fecha_fin)

    return Indisponibilidad.objects.create(
        recurso=recurso,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        tipo=tipo,
        motivo=motivo[:200],
        origen="MANUAL",
        estado="APROBADA",
        solicitada_por=usuario,
        revisada_por=usuario,
        revisada_en=timezone.now(),
    )


def _exigir_admin(usuario):
    if not es_admin(usuario):
        raise PermissionDenied("Solo un administrador puede revisar novedades.")


def _exigir_pendiente(novedad):
    if novedad.estado != "PENDIENTE":
        raise ValidationError(
            f"Esta novedad ya está {novedad.get_estado_display().lower()}; no se puede volver a revisar."
        )


@transaction.atomic
def aprobar_novedad(novedad, admin, politicas=None):
    """Aprueba la novedad. A partir de aquí sí descuenta capacidad.

    Si las fechas se cruzan con asignaciones APROBADAS, hay que decidir qué pasa
    con cada una — no se puede aprobar una ausencia y dejar el compromiso como
    estaba, porque su `fecha_fin` se calculó contando esos días como hábiles:

        RECOMPUTAR  el trabajo se empuja al final, se conservan las horas
        REDUCIR     se recortan las horas, se conserva la ventana

    `politicas` es {asignacion_id: "RECOMPUTAR"|"REDUCIR"}. Falta alguna y la
    aprobación se rechaza entera: aprobar "a medias" dejaría unos proyectos
    ajustados y otros con fechas silenciosamente falsas.

    Se reutiliza la maquinaria de `LiberacionRecurso`, que ya tiene resueltos el
    recomputo, la reducción, los snapshots para poder anular y el rastro en
    LogAuditoria. Escribir aquí una segunda implementación de lo mismo sería
    duplicar la parte más delicada del sistema.
    """
    from apps.assignments.services import aprobar_liberacion, solicitar_liberacion

    _exigir_admin(admin)
    # Bloquea la fila: dos administradores aprobando a la vez podrían pisarse y
    # dejar revisada_por con el que perdió la carrera.
    novedad = Indisponibilidad.objects.select_for_update().get(pk=novedad.pk)
    _exigir_pendiente(novedad)

    afectadas = list(asignaciones_afectadas(novedad))
    politicas = politicas or {}

    faltantes = [a for a in afectadas if politicas.get(a.pk) not in POLITICAS_VALIDAS]
    if faltantes:
        raise ValidationError(
            "Hay que decidir qué hacer con "
            + ", ".join(a.proyecto.codigo for a in faltantes)
            + ": mover el trabajo al final (RECOMPUTAR) o reducir las horas (REDUCIR)."
        )

    # ORDEN CRÍTICO: las liberaciones van ANTES de marcar la novedad como
    # aprobada. `solicitar_liberacion` cuenta los días hábiles con carga de la
    # ventana, y en cuanto la novedad está APROBADA esos días dejan de ser
    # hábiles: contaría cero y fallaría con "la ventana no contiene días hábiles
    # con carga para liberar".
    for asignacion in afectadas:
        try:
            liberacion = solicitar_liberacion(
                asignacion=asignacion,
                fecha_inicio=max(novedad.fecha_inicio, asignacion.fecha_inicio),
                fecha_fin=min(novedad.fecha_fin, asignacion.fecha_fin),
                politica=politicas[asignacion.pk],
                motivo=f"{novedad.get_tipo_display()} de {novedad.recurso.nombre}",
                actor=admin,
            )
            aprobar_liberacion(liberacion, admin)
        except ValueError as exc:
            # La transacción entera se deshace: o se ajustan todos los
            # compromisos afectados, o no se aprueba nada.
            raise ValidationError(
                f"No se pudo ajustar {asignacion.proyecto.codigo}: {exc}"
            ) from exc

    novedad.estado = "APROBADA"
    novedad.revisada_por = admin
    novedad.revisada_en = timezone.now()
    novedad.save(update_fields=["estado", "revisada_por", "revisada_en", "updated_at"])
    return novedad


@transaction.atomic
def rechazar_novedad(novedad, admin, motivo=""):
    _exigir_admin(admin)
    novedad = Indisponibilidad.objects.select_for_update().get(pk=novedad.pk)
    _exigir_pendiente(novedad)

    novedad.estado = "RECHAZADA"
    novedad.revisada_por = admin
    novedad.revisada_en = timezone.now()
    novedad.motivo_rechazo = motivo[:200]
    novedad.save(
        update_fields=["estado", "revisada_por", "revisada_en", "motivo_rechazo", "updated_at"]
    )
    return novedad


@transaction.atomic
def cancelar_novedad(novedad, usuario):
    """El solicitante retira su propia novedad mientras siga pendiente.

    Una vez aprobada ya afecta a la planificación, así que retirarla es decisión
    del Admin, no de quien la pidió.

    El estado se relee de la base bajo bloqueo en vez de fiarse del objeto que
    llega: entre que el ingeniero carga su panel y pulsa "Cancelar" puede haber
    pasado un Admin aprobando. Con el estado en memoria, ese clic cancelaría una
    novedad ya aprobada y el recurso volvería a contar como disponible sin que
    nadie lo hubiera decidido.
    """
    recurso = recurso_de(usuario)
    if novedad.recurso_id != getattr(recurso, "pk", None) and not es_admin(usuario):
        raise PermissionDenied("Solo puedes cancelar tus propias novedades.")

    novedad = Indisponibilidad.objects.select_for_update().get(pk=novedad.pk)
    if novedad.estado != "PENDIENTE":
        raise ValidationError(
            "Esta novedad ya fue revisada por un administrador, así que no se "
            "puede cancelar. Pide que la retiren si ya no aplica."
        )

    novedad.delete()  # soft-delete: conserva la fila
    return novedad


def asignaciones_afectadas(novedad):
    """Asignaciones aprobadas del recurso que se cruzan con estas fechas.

    Contexto para el Admin al aprobar: los días de la novedad dejan de ser
    hábiles, pero la `fecha_fin` de esas asignaciones se calculó contando con
    ellos. Aprobar no las recalcula, así que conviene ver a qué compromisos
    afecta antes de decidir.
    """
    from apps.assignments.models import Asignacion

    return (
        Asignacion.objects.filter(
            recurso=novedad.recurso,
            estado="APROBADA",
            fecha_inicio__lte=novedad.fecha_fin,
            fecha_fin__gte=novedad.fecha_inicio,
        )
        .select_related("proyecto")
        .order_by("fecha_inicio")
    )
