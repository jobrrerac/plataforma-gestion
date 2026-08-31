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

    bloqueadas = sum((r.horas for r in registros if r.bloqueado), Decimal("0"))
    devueltos = [r for r in registros if r.estado == RegistroHoras.DEVUELTO]

    return {
        "registros": registros,
        "total": total,
        "jornada": dia.jornada_esperada,
        "faltan": dia.jornada_esperada - total,
        "cuadra": total == dia.jornada_esperada,
        "facturables": facturables,
        "no_facturables": total - facturables,
        # Desglose por estado de aprobación: sin esto la pantalla no puede
        # distinguir lo que ya firmó un PM de lo que sigue esperando.
        "aprobadas": bloqueadas,
        "pendientes": [r for r in registros if r.estado == RegistroHoras.PENDIENTE],
        "devueltos": devueltos,
        "hay_devueltos": bool(devueltos),
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

    # Lo ya aprobado no se reenvía: sigue firmado. Lo devuelto vuelve a la cola
    # como pendiente, con el motivo ya limpio porque acaba de corregirse.
    dia.registros.exclude(estado=RegistroHoras.APROBADO).update(
        estado=RegistroHoras.PENDIENTE, motivo_devolucion=""
    )

    dia.estado = DiaLegalizado.REGISTRADO
    dia.total_horas = datos["total"]
    dia.registrado_en = timezone.now()
    dia.motivo_devolucion = ""
    dia.save(update_fields=[
        "estado", "total_horas", "registrado_en", "motivo_devolucion", "updated_at",
    ])
    # Si todo lo que quedaba ya estaba aprobado, el día nace aprobado.
    dia.recalcular_estado()
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
# Se aprueba **renglón a renglón**, no el día entero. Quien firma responde por
# un proyecto concreto: un PM no puede valorar las horas de formación de otro,
# ni las de un proyecto que no es suyo. Y cuando alguien reparte el día entre
# dos proyectos, aprobar el día completo dejaba que el primer PM en llegar
# decidiera también por el segundo.


def puede_aprobar_registro(usuario, registro) -> bool:
    """Si esta persona puede firmar este renglón concreto.

    - **PM del proyecto**: lo suyo, y solo lo suyo.
    - **Admin**: cualquiera. No es un lujo: los renglones sin proyecto
      —formación, estudio— no tienen PM que los reclame, y sin el Admin no se
      aprobarían nunca. Sirve además de válvula si un PM tarda o se va.

    Mira el alcance, no el estado: son dos preguntas distintas y mezclarlas
    produce mensajes falsos, como decirle a un PM legítimo que el renglón no es
    suyo cuando lo que pasa es que ya estaba aprobado.
    """
    from apps.accounts.roles import es_admin, es_admin_o_pm

    if es_admin(usuario):
        return True
    if not es_admin_o_pm(usuario):
        return False
    return bool(registro.proyecto_id and registro.proyecto.pm_id == usuario.pk)


def registros_por_aprobar(usuario):
    """Renglones pendientes que esta persona puede firmar."""
    from apps.accounts.roles import es_admin, es_admin_o_pm

    pendientes = (
        RegistroHoras.objects
        .filter(estado=RegistroHoras.PENDIENTE, dia__estado=DiaLegalizado.REGISTRADO)
        .select_related("dia", "dia__recurso", "proyecto", "tipo_actividad")
        .order_by("dia__fecha", "dia__recurso__nombre", "id")
    )
    if es_admin(usuario):
        return pendientes
    if not es_admin_o_pm(usuario):
        return pendientes.none()
    return pendientes.filter(proyecto__pm=usuario)


def dias_por_aprobar(usuario):
    """La misma cola, agrupada por día para poder pintarla.

    Cada día trae en `pendientes_mios` solo los renglones que esta persona
    puede firmar, y en `otros` el resto —visibles, pero sin botones—: quien
    aprueba necesita ver el día completo para juzgar sus horas en contexto, sin
    poder tocar lo que no le corresponde.
    """
    mios = list(registros_por_aprobar(usuario))
    if not mios:
        return []

    dias = {}
    for registro in mios:
        dias.setdefault(registro.dia_id, registro.dia)
    completos = (
        DiaLegalizado.objects.filter(pk__in=dias)
        .select_related("recurso")
        .prefetch_related("registros__proyecto", "registros__tipo_actividad")
        .order_by("fecha", "recurso__nombre")
    )

    aprobables = {r.pk for r in mios}
    resultado = []
    for dia in completos:
        registros = list(dia.registros.all())
        dia.pendientes_mios = [r for r in registros if r.pk in aprobables]
        dia.otros = [r for r in registros if r.pk not in aprobables]
        dia.detalle = resumen(dia)
        resultado.append(dia)
    return resultado


def _exigir_aprobador_registro(usuario, registro):
    if not puede_aprobar_registro(usuario, registro):
        if registro.proyecto_id:
            raise PermissionDenied(
                f"No puedes revisar esta actividad: no eres PM de «{registro.proyecto.codigo}»."
            )
        raise PermissionDenied(
            "Esta actividad no cuelga de ningún proyecto, así que solo la aprueba un administrador."
        )


def _exigir_pendiente(registro):
    if registro.estado != RegistroHoras.PENDIENTE:
        estados = {
            RegistroHoras.APROBADO: "ya está aprobada",
            RegistroHoras.DEVUELTO: "está devuelta, esperando corrección",
        }
        raise ValidationError(f"Esta actividad {estados.get(registro.estado, 'no se puede revisar')}.")
    if registro.dia.estado != DiaLegalizado.REGISTRADO:
        raise ValidationError("Este día todavía no lo ha aceptado quien lo registra.")


@transaction.atomic
def aprobar_registro(registro, usuario):
    """Da por buena una actividad. Sus horas pasan a contar como legalizadas."""
    _exigir_aprobador_registro(usuario, registro)

    # Se relee bajo bloqueo: si el PM y un Admin firman a la vez, sin esto el
    # segundo pisaría la firma del primero.
    registro = RegistroHoras.objects.select_for_update().select_related("dia").get(pk=registro.pk)
    _exigir_pendiente(registro)

    registro.estado = RegistroHoras.APROBADO
    registro.aprobado_por = usuario
    registro.aprobado_en = timezone.now()
    registro.motivo_devolucion = ""
    registro.save(update_fields=[
        "estado", "aprobado_por", "aprobado_en", "motivo_devolucion", "updated_at",
    ])

    registro.dia.recalcular_estado()
    return registro


@transaction.atomic
def devolver_registro(registro, usuario, motivo):
    """Devuelve una actividad para que se corrija.

    Solo esa: las que otro PM ya firmó siguen aprobadas y bloqueadas. Devolver
    el día entero obligaba a rehacer trabajo ya validado.
    """
    _exigir_aprobador_registro(usuario, registro)

    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError("Explica qué hay que corregir; el motivo lo verá quien lo registró.")

    registro = RegistroHoras.objects.select_for_update().select_related("dia").get(pk=registro.pk)
    _exigir_pendiente(registro)

    registro.estado = RegistroHoras.DEVUELTO
    registro.motivo_devolucion = motivo[:300]
    registro.save(update_fields=["estado", "motivo_devolucion", "updated_at"])

    # El día vuelve a estar abierto para que se pueda corregir, y se deja el
    # motivo a la vista de quien lo registró.
    dia = registro.dia
    dia.motivo_devolucion = motivo[:300]
    dia.save(update_fields=["motivo_devolucion", "updated_at"])
    dia.recalcular_estado()
    return registro


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
    """Reemplaza los renglones editables del día. Los aprobados no se tocan.

    La pantalla arma la lista en el navegador y no toca la base hasta que la
    persona pulsa Guardar: así puede componer el día, corregirse y reordenarse
    sin dejar a medias filas que luego nadie limpia.

    Se valida todo antes de escribir nada. Si un renglón falla, no se guarda
    ninguno: un día medio guardado es peor que uno sin guardar, porque parece
    completo.

    Lo que llega es **el día editable completo**, no un añadido: sustituye a lo
    que hubiera. La pantalla precarga lo ya guardado justamente por eso — si
    llegara vacía, guardar borraría el día. Es lo que ocurría: el editor
    arrancaba en blanco y volver a entrar para completar las horas que faltaban
    dejaba el día solo con lo último tecleado.

    Los renglones ya aprobados quedan fuera del reemplazo. Devolver una
    actividad no puede obligar a rehacer las que otro PM ya firmó.
    """
    _exigir_editable(dia)

    if not renglones:
        raise ValidationError("No has añadido ninguna actividad.")

    aprobados = list(dia.registros.filter(estado=RegistroHoras.APROBADO))
    horas_aprobadas = sum((r.horas for r in aprobados), Decimal("0"))

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

    if total + horas_aprobadas > dia.jornada_esperada:
        if horas_aprobadas:
            raise ValidationError(
                f"Has registrado {total} h y ya hay {horas_aprobadas} h aprobadas, "
                f"lo que suma {total + horas_aprobadas} h sobre una jornada de "
                f"{dia.jornada_esperada} h."
            )
        raise ValidationError(
            f"Has registrado {total} h y la jornada de ese día es de {dia.jornada_esperada} h."
        )

    # Reemplaza solo lo editable: lo aprobado se queda como está.
    dia.registros.exclude(estado=RegistroHoras.APROBADO).delete()  # soft-delete
    RegistroHoras.objects.bulk_create([
        RegistroHoras(
            dia=dia, tipo_actividad=actividad, proyecto=proyecto,
            horas=horas, detalle=detalle,
        )
        for actividad, proyecto, horas, detalle in validados
    ])
    return dia
