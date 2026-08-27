"""Reglas de la legalización de horas.

Las tres que sostienen el módulo:

1. **El día tiene que cuadrar.** La suma de los renglones debe dar exactamente
   la jornada. Rellenar hasta cuadrar —aunque sea con estudio o con un proyecto
   interno— es lo que hace fiable el informe de facturables.
2. **Registrar es irreversible.** Una vez cerrado el día no se edita; hace falta
   que alguien autorice reabrirlo.
3. **Un día no hábil no se legaliza.** Fin de semana, feriado o ausencia
   aprobada: no hay nada que declarar, y la ausencia ya la aprobó alguien en el
   panel de novedades.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.assignments.services import capacidad_maxima_dia
from apps.calendar_engine.services import CalendarioRango
from apps.core.models import Recurso

from .models import DiaLegalizado, RegistroHoras, TipoActividad

# Hasta cuántos días atrás se puede legalizar. La gente rellena tarde, pero
# dejar el pasado abierto para siempre vacía de sentido el cierre.
DIAS_ATRAS_MAX = 30


def recurso_de(usuario):
    """Recurso asociado a la cuenta, o None."""
    return Recurso.objects.filter(usuario=usuario).first()


def jornada_esperada(fecha: date) -> Decimal:
    """Horas que hay que legalizar ese día. 0 si no es laborable en general."""
    if fecha.weekday() >= 5:
        return Decimal("0")
    return Decimal(str(capacidad_maxima_dia(fecha)))


def estado_del_dia(recurso, fecha: date) -> dict:
    """Qué se puede hacer con un día concreto, y por qué.

    Devuelve el motivo por el que un día no se legaliza en vez de un simple
    booleano: la pantalla necesita distinguir «es sábado» de «estás de
    vacaciones», porque no significan lo mismo para quien lo lee.
    """
    cal = CalendarioRango(fecha, fecha, [recurso])
    motivo = cal.motivo_no_habil(fecha, recurso)

    return {
        "fecha": fecha,
        "habil": motivo is None,
        "motivo_no_habil": motivo,
        "tipo_ausencia": cal.tipo_ausencia(fecha, recurso) if motivo == "AUSENCIA" else None,
        "jornada": jornada_esperada(fecha) if motivo is None else Decimal("0"),
    }


def obtener_o_crear_dia(recurso, fecha: date) -> DiaLegalizado:
    """El día de trabajo de esa persona, creándolo abierto si aún no existe."""
    _validar_fecha_legalizable(recurso, fecha)

    dia, _ = DiaLegalizado.objects.get_or_create(
        recurso=recurso,
        fecha=fecha,
        defaults={"jornada_esperada": jornada_esperada(fecha)},
    )
    return dia


def _validar_fecha_legalizable(recurso, fecha: date):
    hoy = date.today()
    if fecha > hoy:
        raise ValidationError("No se pueden legalizar horas de un día que todavía no ha pasado.")
    if fecha < hoy - timedelta(days=DIAS_ATRAS_MAX):
        raise ValidationError(
            f"Solo se pueden legalizar los últimos {DIAS_ATRAS_MAX} días. "
            "Para algo más antiguo, pídeselo a un administrador."
        )

    estado = estado_del_dia(recurso, fecha)
    if not estado["habil"]:
        raise ValidationError(_texto_no_habil(estado))


def _texto_no_habil(estado) -> str:
    textos = {
        "FINDE": "Es fin de semana: no hay horas que legalizar.",
        "FERIADO": "Es festivo: no hay horas que legalizar.",
        "NO_LABORABLE": "Es un día no laborable: no hay horas que legalizar.",
        "AUSENCIA": "Tienes una ausencia aprobada ese día, así que no hay que legalizarlo.",
    }
    return textos.get(estado["motivo_no_habil"], "Ese día no es laborable.")


def _exigir_editable(dia):
    if not dia.editable:
        raise ValidationError(
            "Este día ya fue registrado y no se puede modificar. "
            "Pide la reapertura si necesitas cambiar algo."
        )


@transaction.atomic
def agregar_renglon(dia, tipo_actividad, horas, detalle, proyecto=None):
    """Añade una línea al día. Solo mientras siga abierto."""
    _exigir_editable(dia)

    detalle = (detalle or "").strip()
    if not detalle:
        raise ValidationError("Falta decir qué hiciste. Es lo que permite legalizar estas horas.")

    horas = Decimal(str(horas))
    if horas <= 0:
        raise ValidationError("Las horas tienen que ser mayores que cero.")
    # Media hora es la unidad con la que se trabaja: nadie mide minutos.
    if (horas * 2) % 1 != 0:
        raise ValidationError("Las horas se registran en bloques de media hora (0.5, 1, 1.5...).")

    if tipo_actividad.requiere_proyecto and proyecto is None:
        raise ValidationError(f"«{tipo_actividad.nombre}» necesita que indiques a qué proyecto.")
    if not tipo_actividad.requiere_proyecto and proyecto is not None:
        # No es un capricho: dejar pasar un proyecto en una actividad que no lo
        # usa haría que esas horas aparecieran imputadas a un proyecto sin que
        # nadie lo haya decidido.
        raise ValidationError(f"«{tipo_actividad.nombre}» no se imputa a ningún proyecto.")

    total_actual = sum((r.horas for r in dia.registros.all()), Decimal("0"))
    if total_actual + horas > dia.jornada_esperada:
        disponible = dia.jornada_esperada - total_actual
        raise ValidationError(
            f"Te pasas de la jornada. Llevas {total_actual} h de {dia.jornada_esperada} h, "
            f"así que como mucho puedes añadir {disponible} h."
        )

    return RegistroHoras.objects.create(
        dia=dia,
        tipo_actividad=tipo_actividad,
        proyecto=proyecto,
        horas=horas,
        detalle=detalle[:300],
    )


@transaction.atomic
def quitar_renglon(renglon):
    _exigir_editable(renglon.dia)
    renglon.delete()  # soft-delete


def resumen(dia) -> dict:
    """Lo que se le enseña a la persona antes de cerrar el día."""
    registros = list(dia.registros.select_related("tipo_actividad", "proyecto"))
    total = sum((r.horas for r in registros), Decimal("0"))
    facturables = sum((r.horas for r in registros if r.facturable), Decimal("0"))

    return {
        "registros": registros,
        "total": total,
        "jornada": dia.jornada_esperada,
        "faltan": dia.jornada_esperada - total,
        "cuadra": total == dia.jornada_esperada,
        "facturables": facturables,
        "no_facturables": total - facturables,
    }


@transaction.atomic
def registrar_dia(dia, usuario):
    """Cierra el día. A partir de aquí no se edita.

    Es el punto de no retorno, y por eso la pantalla enseña el resumen y pide
    confirmación antes de llegar aquí.
    """
    propio = recurso_de(usuario)
    if dia.recurso_id != getattr(propio, "pk", None):
        raise PermissionDenied("Solo puedes registrar tus propios días.")

    # Se relee bajo bloqueo: con dos pestañas abiertas, la segunda confirmación
    # cerraría un día ya cerrado y pisaría la marca de tiempo.
    dia = DiaLegalizado.objects.select_for_update().get(pk=dia.pk)
    _exigir_editable(dia)

    datos = resumen(dia)
    if not datos["registros"]:
        raise ValidationError("No has registrado ninguna actividad.")
    if not datos["cuadra"]:
        faltan = datos["faltan"]
        if faltan > 0:
            raise ValidationError(
                f"Faltan {faltan} h para completar la jornada de {dia.jornada_esperada} h. "
                "Complétalas con lo que corresponda: estudio, formación o un proyecto interno."
            )
        raise ValidationError(
            f"Te pasas en {-faltan} h de la jornada de {dia.jornada_esperada} h."
        )

    dia.estado = DiaLegalizado.REGISTRADO
    dia.total_horas = datos["total"]
    dia.registrado_en = timezone.now()
    dia.save(update_fields=["estado", "total_horas", "registrado_en", "updated_at"])
    return dia


def dias_pendientes(recurso, desde=None, hasta=None):
    """Días hábiles sin legalizar, del más reciente al más antiguo.

    Es lo que hace usable la pantalla: en vez de obligar a acordarse de qué
    días faltan, se los enseña.
    """
    hoy = date.today()
    hasta = hasta or hoy
    desde = desde or (hoy - timedelta(days=DIAS_ATRAS_MAX))

    cal = CalendarioRango(desde, hasta, [recurso])
    cerrados = set(
        DiaLegalizado.objects.filter(
            recurso=recurso, fecha__gte=desde, fecha__lte=hasta,
        ).exclude(estado=DiaLegalizado.ABIERTO).values_list("fecha", flat=True)
    )

    pendientes = []
    fecha = desde
    while fecha <= hasta:
        if cal.es_habil(fecha, recurso) and fecha not in cerrados:
            pendientes.append(fecha)
        fecha += timedelta(days=1)
    return sorted(pendientes, reverse=True)


def actividades_disponibles():
    return TipoActividad.objects.filter(activo=True)


# ---------------------------------------------------------------------------
# Aprobación
# ---------------------------------------------------------------------------


def dias_por_aprobar(usuario):
    """Días registrados que este usuario puede aprobar.

    - **Admin**: todos. Es la válvula de escape — si un PM está de vacaciones,
      se va de la empresa o simplemente tarda, las horas de su gente no pueden
      quedarse bloqueadas para siempre.
    - **PM**: los días que tocan alguno de sus proyectos.
    - **Cualquier otro**: ninguno.

    Un día puede mezclar proyectos de varios PM. Basta con que uno de ellos sea
    suyo para poder aprobarlo entero: exigir la firma de todos convertiría un
    trámite diario en una cadena de esperas, y el dato que se valida —cuántas
    horas dedicó esa persona— es el mismo para todos.

    Un día sin ningún proyecto (solo estudio o entrenamiento) no tiene PM que lo
    reclame. Ahí el Admin es el único que puede aprobarlo, y por eso su alcance
    total no es un lujo: sin él, esos días no se aprobarían nunca.
    """
    from apps.accounts.roles import es_admin, es_admin_o_pm

    pendientes = DiaLegalizado.objects.filter(
        estado=DiaLegalizado.REGISTRADO
    ).select_related("recurso").order_by("fecha", "recurso__nombre")

    if es_admin(usuario):
        return pendientes
    if not es_admin_o_pm(usuario):
        return pendientes.none()

    return pendientes.filter(registros__proyecto__pm=usuario).distinct()


def puede_aprobar(usuario, dia) -> bool:
    """Si este día cae dentro de lo que esta persona puede revisar.

    Mira el alcance, no el estado. Son dos preguntas distintas y mezclarlas
    produce mensajes falsos: al intentar aprobar un día ya aprobado, un PM
    legítimo leía «no eres PM de ninguno de sus proyectos», que es mentira y
    manda a buscar el problema donde no está.
    """
    from apps.accounts.roles import es_admin, es_admin_o_pm

    if es_admin(usuario):
        return True
    if not es_admin_o_pm(usuario):
        return False
    return dia.registros.filter(proyecto__pm=usuario).exists()


def _exigir_aprobador(usuario, dia):
    if not puede_aprobar(usuario, dia):
        raise PermissionDenied(
            "No puedes revisar este día: no eres PM de ninguno de sus proyectos."
        )


def _exigir_registrado(dia):
    if dia.estado != DiaLegalizado.REGISTRADO:
        estados = {
            DiaLegalizado.ABIERTO: "todavía no lo ha aceptado quien lo registra",
            DiaLegalizado.APROBADO: "ya está aprobado",
        }
        raise ValidationError(f"Este día {estados.get(dia.estado, 'no se puede revisar')}.")


@transaction.atomic
def aprobar_dia(dia, usuario):
    """Da el día por bueno. Sus horas pasan a contar como legalizadas."""
    _exigir_aprobador(usuario, dia)

    # Se relee bajo bloqueo: si un PM y un Admin aprueban a la vez, sin esto el
    # segundo pisaría la firma del primero.
    dia = DiaLegalizado.objects.select_for_update().get(pk=dia.pk)
    _exigir_registrado(dia)

    dia.estado = DiaLegalizado.APROBADO
    dia.aprobado_por = usuario
    dia.aprobado_en = timezone.now()
    dia.motivo_devolucion = ""
    dia.save(update_fields=[
        "estado", "aprobado_por", "aprobado_en", "motivo_devolucion", "updated_at",
    ])
    return dia


@transaction.atomic
def devolver_dia(dia, usuario, motivo):
    """Reabre el día para que quien lo registró lo corrija.

    Es la única forma de deshacer un cierre, y exige motivo: devolver sin decir
    qué está mal solo produce un segundo intento a ciegas.
    """
    _exigir_aprobador(usuario, dia)

    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("Explica qué hay que corregir; el motivo lo verá quien lo registró.")

    dia = DiaLegalizado.objects.select_for_update().get(pk=dia.pk)
    _exigir_registrado(dia)

    dia.estado = DiaLegalizado.ABIERTO
    dia.registrado_en = None
    dia.motivo_devolucion = motivo[:300]
    dia.save(update_fields=["estado", "registrado_en", "motivo_devolucion", "updated_at"])
    return dia


def proyectos_disponibles(recurso, fecha: date):
    """Proyectos a los que esa persona puede imputar horas ese día.

    Dos grupos, por motivos distintos:

    - **De cliente**: solo aquellos en los que tenía una asignación APROBADA que
      cubría esa fecha. Ofrecer la lista completa invitaría a imputar horas a
      proyectos en los que nunca se estuvo, que es precisamente lo que el módulo
      viene a evitar. Se mira la fecha del día que se legaliza, no la de hoy: al
      rellenar un día de la semana pasada valen los proyectos de entonces.

    - **Internos**: siempre. Nadie recibe una asignación a «Departamentales», y
      sin ellos alguien en bench no tendría con qué completar la jornada — y
      como el día no cierra si no cuadra, se quedaría bloqueado sin salida.
    """
    from apps.core.models import Proyecto

    asignados = Proyecto.objects.filter(
        facturable=True,
        asignaciones__recurso=recurso,
        asignaciones__estado="APROBADA",
        asignaciones__deleted_at__isnull=True,
        asignaciones__fecha_inicio__lte=fecha,
        asignaciones__fecha_fin__gte=fecha,
    )
    internos = Proyecto.objects.filter(facturable=False, estado="ACTIVO")

    return (asignados | internos).distinct().order_by("-facturable", "codigo")


@transaction.atomic
def guardar_renglones(dia, renglones):
    """Reemplaza de una vez todos los renglones del día.

    La pantalla arma la lista en el navegador y no toca la base hasta que la
    persona pulsa Guardar: así puede componer el día, corregirse y reordenarse
    sin dejar a medias filas que luego nadie limpia.

    Se valida todo antes de escribir nada. Si un renglón falla, no se guarda
    ninguno: un día medio guardado es peor que uno sin guardar, porque parece
    completo.
    """
    _exigir_editable(dia)

    if not renglones:
        raise ValidationError("No has añadido ninguna actividad.")

    validados = []
    total = Decimal("0")

    for indice, crudo in enumerate(renglones, start=1):
        actividad = crudo.get("tipo_actividad")
        proyecto = crudo.get("proyecto")
        detalle = (crudo.get("detalle") or "").strip()

        if actividad is None:
            raise ValidationError(f"Actividad {indice}: falta indicar de qué se trata.")
        if not detalle:
            raise ValidationError(f"Actividad {indice}: falta decir qué hiciste.")

        try:
            horas = Decimal(str(crudo.get("horas")))
        except Exception:
            raise ValidationError(f"Actividad {indice}: las horas tienen que ser un número.") from None

        if horas <= 0:
            raise ValidationError(f"Actividad {indice}: las horas tienen que ser mayores que cero.")
        if (horas * 2) % 1 != 0:
            raise ValidationError(
                f"Actividad {indice}: las horas van en bloques de media hora (0.5, 1, 1.5...)."
            )

        if actividad.requiere_proyecto and proyecto is None:
            raise ValidationError(f"Actividad {indice}: «{actividad.nombre}» necesita un proyecto.")
        if not actividad.requiere_proyecto and proyecto is not None:
            raise ValidationError(
                f"Actividad {indice}: «{actividad.nombre}» no se imputa a ningún proyecto."
            )

        total += horas
        validados.append((actividad, proyecto, horas, detalle[:300]))

    if total > dia.jornada_esperada:
        raise ValidationError(
            f"Has registrado {total} h y la jornada de ese día es de {dia.jornada_esperada} h."
        )

    # Reemplazo completo: lo que llega es el día entero, no un añadido.
    dia.registros.all().delete()  # soft-delete
    RegistroHoras.objects.bulk_create([
        RegistroHoras(
            dia=dia, tipo_actividad=actividad, proyecto=proyecto,
            horas=horas, detalle=detalle,
        )
        for actividad, proyecto, horas, detalle in validados
    ])
    return dia
