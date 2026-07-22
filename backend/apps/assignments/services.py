import uuid
from math import ceil
from decimal import Decimal
from datetime import date, timedelta
from django.db import transaction
from django.utils import timezone
from apps.calendar_engine.services import (
    CalendarioRango,
    calcular_fecha_fin as _cal_fecha_fin,
    contar_dias_habiles,
)
from .models import Asignacion, CesionHoras, LiberacionRecurso, LogAuditoria
from apps.core.models import TarifaVigente

# Jornada real: lun–jue 8.5 h, vie 8 h → máximo semanal 42 h
JORNADA_LUNES_JUEVES = 8.5
JORNADA_VIERNES = 8.0


def capacidad_maxima_dia(fecha: date) -> float:
    """Retorna la jornada máxima permitida según el día de la semana."""
    if fecha.weekday() == 4:  # viernes
        return JORNADA_VIERNES
    return JORNADA_LUNES_JUEVES  # lunes (0) a jueves (3)


def calcular_fecha_fin(recurso, fecha_inicio, horas_totales, intensidad_diaria):
    """Wrapper que convierte horas/intensidad a días y delega al motor de calendario."""
    dias = ceil(float(horas_totales) / float(intensidad_diaria))
    return _cal_fecha_fin(fecha_inicio, dias, recurso)


def calcular_horas_jornada_completa(fecha_inicio: date, fecha_fin: date, recurso=None) -> int:
    """Suma las horas máximas disponibles por cada día hábil del rango."""
    cal = CalendarioRango(fecha_inicio, fecha_fin, [recurso] if recurso is not None else None)
    total = 0.0
    fecha = fecha_inicio
    while fecha <= fecha_fin:
        if cal.es_habil(fecha, recurso):
            total += capacidad_maxima_dia(fecha)
        fecha += timedelta(days=1)
    return ceil(total)


def _iter_habiles(cal: CalendarioRango, recurso, fecha_inicio: date, fecha_fin: date):
    """Itera los días hábiles de un rango usando un calendario ya precargado."""
    fecha = fecha_inicio
    while fecha <= fecha_fin:
        if cal.es_habil(fecha, recurso):
            yield fecha
        fecha += timedelta(days=1)


def carga_propia(asignacion, fecha: date) -> float:
    """Carga real de una asignación en un día: jornada completa usa el tope del
    día (8.5 lun–jue / 8 vie), no el placeholder de intensidad_diaria."""
    if asignacion.jornada_completa:
        return capacidad_maxima_dia(fecha)
    return float(asignacion.intensidad_diaria)


def carga_en_fecha(recurso, fecha, excluir_id=None) -> float:
    """Carga del recurso en esa fecha (asignaciones APROBADAS netas de cesiones)."""
    rid = recurso.pk if hasattr(recurso, "pk") else recurso
    return mapa_carga([rid], fecha, fecha, excluir_id)[rid].get(fecha, 0.0)


def _ventanas_liberadas(asignacion_ids, fecha_inicio: date, fecha_fin: date) -> dict:
    """
    Ventanas de liberación APROBADAS de las asignaciones dadas que solapan el
    rango. Retorna dict[asignacion_id] -> [(ini, fin), ...]. Los días dentro de
    una ventana no consumen capacidad de esa asignación. Las solicitudes
    pendientes (SOLICITADA) NO liberan cupo: solo lo hacen al aprobarse.
    """
    ventanas: dict = {}
    if not asignacion_ids:
        return ventanas
    libs = LiberacionRecurso.objects.filter(
        asignacion_id__in=list(asignacion_ids),
        estado="APROBADA",
        fecha_inicio__lte=fecha_fin,
        fecha_fin__gte=fecha_inicio,
    ).values_list("asignacion_id", "fecha_inicio", "fecha_fin")
    for asig_id, ini, fin in libs:
        ventanas.setdefault(asig_id, []).append((ini, fin))
    return ventanas


def _dias_habiles_liberables(asignacion, win_inicio: date, win_fin: date) -> tuple[int, float]:
    """
    Días hábiles del recurso con carga real dentro de la intersección de la
    ventana [win_inicio, win_fin] con el período de la asignación, y la suma de
    sus horas. Respeta fin de semana, feriados e indisponibilidades vía el
    calendario. Retorna (dias, horas).
    """
    ini = max(win_inicio, asignacion.fecha_inicio)
    fin = min(win_fin, asignacion.fecha_fin)
    if ini > fin:
        return 0, 0.0
    cal = CalendarioRango(ini, fin, [asignacion.recurso_id])
    dias = 0
    horas = 0.0
    fecha = ini
    while fecha <= fin:
        if cal.es_habil(fecha, asignacion.recurso_id):
            dias += 1
            horas += carga_propia(asignacion, fecha)
        fecha += timedelta(days=1)
    return dias, horas


def mapa_carga(recurso_ids, fecha_inicio: date, fecha_fin: date, excluir_id=None) -> dict:
    """
    Precalcula la carga diaria (asignaciones APROBADAS) de varios recursos en
    un rango. Retorna dict[recurso_id][fecha] -> horas.

    Cesiones de horas: mientras la asignación destino de una cesión sigue
    SOLICITADA, las horas cedidas quedan RESERVADAS (la carga bruta del día no
    baja para terceros, así nadie más puede ocupar ese cupo). Se descuentan de
    la original solo cuando el destino está APROBADA (él ya carga sus horas) o
    cuando se está evaluando aprobar precisamente ese destino (excluir_id).

    Liberaciones (congelamiento): mientras una liberación está activa, la
    asignación no consume capacidad en los días de su ventana, así que esas
    fechas se omiten al sumar la carga (el cupo queda libre para terceros).
    """
    ids = list(recurso_ids)
    qs = Asignacion.objects.filter(
        recurso_id__in=ids,
        estado="APROBADA",
        fecha_inicio__lte=fecha_fin,
        fecha_fin__gte=fecha_inicio,
    )
    if excluir_id:
        qs = qs.exclude(pk=excluir_id)
    asignaciones = list(qs)

    ventanas_liberadas = _ventanas_liberadas([a.pk for a in asignaciones], fecha_inicio, fecha_fin)

    carga: dict = {rid: {} for rid in ids}
    for a in asignaciones:
        por_dia = carga.setdefault(a.recurso_id, {})
        ventanas = ventanas_liberadas.get(a.pk, ())
        fecha = max(a.fecha_inicio, fecha_inicio)
        fin = min(a.fecha_fin, fecha_fin)
        while fecha <= fin:
            if not any(ini <= fecha <= f for ini, f in ventanas):
                por_dia[fecha] = por_dia.get(fecha, 0.0) + carga_propia(a, fecha)
            fecha += timedelta(days=1)

    if asignaciones:
        recurso_de = {a.pk: a.recurso_id for a in asignaciones}
        cesiones = CesionHoras.objects.filter(
            asignacion_origen_id__in=recurso_de.keys(),
            fecha__gte=fecha_inicio, fecha__lte=fecha_fin,
            anulada_en__isnull=True,
        ).select_related("asignacion_destino")
        for c in cesiones:
            descuenta = (
                c.asignacion_destino_id == excluir_id
                or c.asignacion_destino.estado == "APROBADA"
            )
            if descuenta:
                por_dia = carga[recurso_de[c.asignacion_origen_id]]
                por_dia[c.fecha] = max(0.0, por_dia.get(c.fecha, 0.0) - float(c.horas))
    return carga


def puede_asignar(asignacion) -> tuple[bool, object]:
    """
    Verifica que en ningún día hábil del rango la carga no supere la jornada del día:
      lun–jue → 8.5 h, vie → 8 h (máx 42 h semanales).
    Retorna (True, None) si cabe, (False, fecha_conflicto) si hay sobreasignación.
    """
    recurso_id = asignacion.recurso_id
    cal = CalendarioRango(asignacion.fecha_inicio, asignacion.fecha_fin, [recurso_id])
    carga_dias = mapa_carga(
        [recurso_id], asignacion.fecha_inicio, asignacion.fecha_fin, excluir_id=asignacion.pk
    )[recurso_id]

    fecha = asignacion.fecha_inicio
    while fecha <= asignacion.fecha_fin:
        if cal.es_habil(fecha, recurso_id):
            carga = carga_dias.get(fecha, 0.0)
            if carga + carga_propia(asignacion, fecha) > capacidad_maxima_dia(fecha):
                return False, fecha
        fecha += timedelta(days=1)
    return True, None


def aprobar_asignacion(asignacion, actor):
    """
    Aprobación transaccional con select_for_update por recurso.
    Lanza ValueError si hay sobreasignación.
    """
    with transaction.atomic():
        recurso = asignacion.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion.recurso_id
        )
        ok, fecha_conflicto = puede_asignar(asignacion)
        if not ok:
            cap = capacidad_maxima_dia(fecha_conflicto)
            raise ValueError(
                f"Sobreasignación: {recurso.nombre} ya alcanza las {cap} h del {fecha_conflicto.strftime('%A %d/%m/%Y')}."
            )
        asignacion.estado = "APROBADA"
        # Snapshot al aprobar: tarifa de referencia (la del día de inicio) y
        # costo mixto por día. Si la tarifa cambia después, un recomputo
        # automático actualiza el costo y lo deja trazado en el log.
        tarifa_inicio = TarifaVigente.vigente_para(recurso, asignacion.fecha_inicio)
        asignacion.tarifa_aplicada = tarifa_inicio.valor_hora if tarifa_inicio else None
        asignacion.costo_estimado = costo_estimado_asignacion(asignacion)
        asignacion.save(update_fields=["estado", "tarifa_aplicada", "costo_estimado", "updated_at"])
        LogAuditoria.objects.create(
            asignacion=asignacion,
            accion="APROBAR",
            actor=actor,
            detalle={
                "recurso_id": recurso.pk,
                "fecha_fin": str(asignacion.fecha_fin),
                "tarifa_inicio": float(asignacion.tarifa_aplicada) if asignacion.tarifa_aplicada is not None else None,
                "costo_estimado": float(asignacion.costo_estimado) if asignacion.costo_estimado is not None else None,
            },
        )


def detalle_dias_recurso(recurso, fecha_inicio: date, fecha_fin: date) -> list:
    """Detalle de ocupación por día hábil para un único recurso."""
    cal = CalendarioRango(fecha_inicio, fecha_fin, [recurso])
    carga_dias = mapa_carga([recurso.pk], fecha_inicio, fecha_fin)[recurso.pk]
    result = []
    fecha = fecha_inicio
    while fecha <= fecha_fin:
        if cal.es_habil(fecha, recurso):
            cap = capacidad_maxima_dia(fecha)
            carga = carga_dias.get(fecha, 0.0)
            libre = max(0.0, cap - carga)
            pct_ocu = round(100.0 * min(carga, cap) / cap, 1) if cap > 0 else 0.0
            result.append({
                "fecha": fecha,
                "horas_cap": cap,
                "horas_ocupadas": round(min(carga, cap), 1),
                "horas_libres": round(libre, 1),
                "pct_ocupado": pct_ocu,
                "lleno": carga >= cap,
                "parcial": 0 < carga < cap,
            })
        fecha += timedelta(days=1)
    return result


def disponibilidad_recursos(
    fecha_inicio: date, fecha_fin: date,
    skills: list | None = None, nombre: str | None = None,
) -> list:
    """
    Devuelve la disponibilidad de cada recurso activo en el rango dado.
    Filtra por skills (lista de nombres) y/o por nombre del recurso
    (búsqueda parcial, sin distinguir mayúsculas).
    Ordena de más a menos disponible.
    """
    from apps.core.models import recursos_asignables

    qs = recursos_asignables().prefetch_related("recurso_skills__skill").order_by("nombre")
    if skills:
        qs = qs.filter(skills__nombre__in=skills).distinct()
    if nombre:
        qs = qs.filter(nombre__icontains=nombre.strip())

    recursos = list(qs)
    cal = CalendarioRango(fecha_inicio, fecha_fin, recursos)
    cargas = mapa_carga([r.pk for r in recursos], fecha_inicio, fecha_fin)

    # Vigencias de tarifa de todos los recursos en una query (costo mixto por día)
    tarifas_por_recurso: dict = {}
    for rid, fd, valor in TarifaVigente.objects.filter(
        recurso__in=recursos, fecha_desde__lte=fecha_fin,
    ).order_by("fecha_desde").values_list("recurso_id", "fecha_desde", "valor_hora"):
        tarifas_por_recurso.setdefault(rid, []).append((fd, valor))

    resultados = []
    for recurso in recursos:
        carga_dias = cargas.get(recurso.pk, {})
        tarifas = tarifas_por_recurso.get(recurso.pk, [])
        horas_cap = 0.0
        horas_ocupadas = 0.0
        dias_habiles = 0
        dias_sin_cupo = 0
        dias_con_carga = []
        costo_libre = Decimal("0")
        hay_tarifa = False

        fecha = fecha_inicio
        while fecha <= fecha_fin:
            if cal.es_habil(fecha, recurso):
                cap = capacidad_maxima_dia(fecha)
                carga = carga_dias.get(fecha, 0.0)
                libre = max(0.0, cap - carga)
                pct_ocu = round(100.0 * min(carga, cap) / cap, 1) if cap > 0 else 0.0
                horas_cap += cap
                horas_ocupadas += min(carga, cap)
                dias_habiles += 1
                if carga >= cap:
                    dias_sin_cupo += 1
                if carga > 0:
                    dias_con_carga.append({
                        "fecha": fecha,
                        "horas_ocupadas": round(min(carga, cap), 1),
                        "horas_libres": round(libre, 1),
                        "horas_cap": cap,
                        "pct_ocupado": pct_ocu,
                        "lleno": carga >= cap,
                    })
                # Costo mixto: horas libres del día × tarifa vigente ESE día
                tarifa_dia = _tarifa_del_dia(tarifas, fecha)
                if tarifa_dia is not None:
                    hay_tarifa = True
                    costo_libre += tarifa_dia * Decimal(str(libre))
            fecha += timedelta(days=1)

        horas_libres = max(0.0, horas_cap - horas_ocupadas)
        pct_libre = round(100.0 * horas_libres / horas_cap, 1) if horas_cap > 0 else 0.0

        tarifa_inicio = _tarifa_del_dia(tarifas, fecha_inicio)
        tarifa_hora = float(tarifa_inicio) if tarifa_inicio is not None else None
        costo_estimado = float(costo_libre.quantize(Decimal("0.01"))) if hay_tarifa else None
        # Cambios de tarifa dentro del rango (para indicar "desde el X pasa a Y")
        tarifa_cambios = [
            {"fecha": fd, "valor": float(v)}
            for fd, v in tarifas if fecha_inicio < fd <= fecha_fin
        ]

        resultados.append({
            "recurso": recurso,
            "skills": [
                {
                    "nombre": rs.skill.nombre,
                    "suficiencia": rs.suficiencia,
                    "estrellas": "★" * rs.suficiencia + "☆" * (5 - rs.suficiencia),
                }
                for rs in recurso.recurso_skills.all()
            ],
            "dias_habiles": dias_habiles,
            "horas_capacidad": round(horas_cap, 1),
            "horas_ocupadas": round(horas_ocupadas, 1),
            "horas_libres": round(horas_libres, 1),
            "porcentaje_libre": pct_libre,
            "porcentaje_ocupado": round(100.0 - pct_libre, 1),
            "dias_sin_cupo": dias_sin_cupo,
            "dias_con_carga": dias_con_carga,
            "tarifa_hora": tarifa_hora,
            "costo_estimado": costo_estimado,
            "tarifa_cambios": tarifa_cambios,
        })

    resultados.sort(key=lambda x: x["porcentaje_libre"], reverse=True)
    return resultados


def calcular_solicitud_horas(recurso, fecha_inicio: date, horas_target: float, intensidad: float | None = None, jornada_completa: bool = False) -> tuple:
    """
    Calcula (fecha_fin, dias_habiles, horas_reales, dias_bloqueados) para una solicitud
    por horas totales, saltando días donde la carga existente + intensidad excede la capacidad.
    A diferencia de calcular_fecha_fin(), respeta la ocupación real del recurso.
    """
    acum = 0.0
    dias_count = 0
    dias_bloqueados = []
    fecha = fecha_inicio
    limite = fecha_inicio + timedelta(days=730)

    cal = CalendarioRango(fecha_inicio, limite, [recurso])
    carga_dias = mapa_carga([recurso.pk], fecha_inicio, limite)[recurso.pk]

    while acum < horas_target and fecha <= limite:
        if cal.es_habil(fecha, recurso):
            cap = capacidad_maxima_dia(fecha)
            carga_existente = carga_dias.get(fecha, 0.0)
            h_dia = cap if jornada_completa else intensidad

            if carga_existente + h_dia > cap:
                dias_bloqueados.append(fecha)
            else:
                h_hoy = min(h_dia, horas_target - acum)
                acum += h_hoy
                dias_count += 1
                if acum >= horas_target:
                    return fecha, dias_count, int(ceil(acum)), dias_bloqueados
        fecha += timedelta(days=1)

    return fecha, dias_count, int(ceil(acum)), dias_bloqueados


def crear_solicitud_por_horas(recurso, proyecto, fecha_inicio, horas_target, intensidad, jornada_completa, solicitante):
    """Crea una Asignacion SOLICITADA en modo HORAS calculando fecha_fin respetando ocupación existente."""
    ff, dias, horas_reales, dias_bloqueados = calcular_solicitud_horas(
        recurso, fecha_inicio, float(horas_target), intensidad, jornada_completa
    )
    intens_dec = Decimal("8.0") if jornada_completa else Decimal(str(intensidad))
    asignacion = Asignacion.objects.create(
        recurso=recurso,
        proyecto=proyecto,
        modo_asignacion="HORAS",
        fecha_inicio=fecha_inicio,
        fecha_fin=ff,
        dias_habiles=dias,
        horas_totales=int(horas_target),
        intensidad_diaria=intens_dec,
        jornada_completa=jornada_completa,
        estado="SOLICITADA",
        solicitada_por=solicitante,
    )
    LogAuditoria.objects.create(
        asignacion=asignacion, accion="CREAR", actor=solicitante,
        detalle={"modo": "HORAS_FILL", "horas_target": int(horas_target), "dias": dias, "bloqueados": len(dias_bloqueados)},
    )
    return asignacion, dias_bloqueados


def analizar_conflictos(asignacion):
    """
    Detecta días con sobreasignación y calcula la nueva fecha_fin si se recomputa saltándolos.
    Retorna (conflict_dates: list[date], nueva_fecha_fin: date|None, nuevas_horas: int|None).
    """
    recurso_id = asignacion.recurso_id
    limite = asignacion.fecha_inicio + timedelta(days=730)
    cal = CalendarioRango(asignacion.fecha_inicio, limite, [recurso_id])
    carga_dias = mapa_carga(
        [recurso_id], asignacion.fecha_inicio, asignacion.fecha_fin, excluir_id=asignacion.pk
    )[recurso_id]

    conflict_dates = []
    conflict_set = set()
    fecha = asignacion.fecha_inicio
    while fecha <= asignacion.fecha_fin:
        if cal.es_habil(fecha, recurso_id):
            carga = carga_dias.get(fecha, 0.0)
            if carga + carga_propia(asignacion, fecha) > capacidad_maxima_dia(fecha):
                conflict_dates.append(fecha)
                conflict_set.add(fecha)
        fecha += timedelta(days=1)

    if not conflict_dates:
        return [], None, None

    needed = asignacion.dias_habiles or sum(
        1 for _ in _iter_habiles(cal, recurso_id, asignacion.fecha_inicio, asignacion.fecha_fin)
    )

    count = 0
    total_horas = 0.0
    nueva_fecha_fin = asignacion.fecha_inicio
    fecha = asignacion.fecha_inicio

    while count < needed and fecha <= limite:
        if cal.es_habil(fecha, recurso_id) and fecha not in conflict_set:
            count += 1
            total_horas += (
                capacidad_maxima_dia(fecha) if asignacion.jornada_completa
                else float(asignacion.intensidad_diaria or 8.0)
            )
            nueva_fecha_fin = fecha
        fecha += timedelta(days=1)

    nuevas_horas = ceil(total_horas) if asignacion.jornada_completa else asignacion.horas_totales
    return conflict_dates, nueva_fecha_fin, nuevas_horas


def aprobar_recomputando(asignacion, actor, nueva_fecha_fin, nuevas_horas):
    """Aprueba extendiendo fecha_fin para saltar los días conflictivos."""
    with transaction.atomic():
        recurso = asignacion.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion.recurso_id
        )
        asignacion.fecha_fin = nueva_fecha_fin
        if nuevas_horas is not None:
            asignacion.horas_totales = nuevas_horas
        ok, fecha_conflicto = puede_asignar(asignacion)
        if not ok:
            raise ValueError(
                f"Sigue habiendo conflicto el {fecha_conflicto.strftime('%d/%m/%Y')} tras recomputar."
            )
        asignacion.estado = "APROBADA"
        tarifa_inicio = TarifaVigente.vigente_para(recurso, asignacion.fecha_inicio)
        asignacion.tarifa_aplicada = tarifa_inicio.valor_hora if tarifa_inicio else None
        asignacion.costo_estimado = costo_estimado_asignacion(asignacion)
        asignacion.save(update_fields=[
            "estado", "fecha_fin", "horas_totales", "tarifa_aplicada", "costo_estimado", "updated_at",
        ])
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="APROBAR", actor=actor,
            detalle={
                "recurso_id": recurso.pk,
                "fecha_fin_recomputada": str(nueva_fecha_fin),
                "recomputo": True,
            },
        )


def crear_solicitud(recurso, proyecto, fecha_inicio, fecha_fin, intensidad_diaria, jornada_completa, solicitante):
    """Crea una Asignacion SOLICITADA en modo RANGO desde el flujo de solicitud de recursos."""
    dias = contar_dias_habiles(fecha_inicio, fecha_fin, recurso)
    if jornada_completa:
        intensidad = Decimal("8.0")
        horas = calcular_horas_jornada_completa(fecha_inicio, fecha_fin, recurso)
    else:
        intensidad = Decimal(str(intensidad_diaria))
        horas = ceil(dias * float(intensidad))
    asignacion = Asignacion.objects.create(
        recurso=recurso,
        proyecto=proyecto,
        modo_asignacion="RANGO",
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        dias_habiles=dias,
        horas_totales=horas,
        intensidad_diaria=intensidad,
        jornada_completa=jornada_completa,
        estado="SOLICITADA",
        solicitada_por=solicitante,
    )
    LogAuditoria.objects.create(
        asignacion=asignacion, accion="CREAR", actor=solicitante,
        detalle={"modo": "RANGO", "dias_habiles": dias, "horas_totales": horas},
    )
    return asignacion


# ── Tarifas: tramos de vigencia y costo mixto por día ───────────────────────
# La tarifa sigue el costo del recurso: puede cambiar dentro del período de
# una asignación. Todo costo se calcula por día con la tarifa vigente de ese
# día, y se recomputa automáticamente cuando se registra una nueva vigencia.


def _tarifa_del_dia(tarifas_ordenadas, fecha: date):
    """Tarifa vigente en la fecha, dada la lista [(fecha_desde, valor)] ordenada."""
    valor = None
    for fd, v in tarifas_ordenadas:
        if fd <= fecha:
            valor = v
        else:
            break
    return valor


def segmentos_tarifa(recurso, fecha_inicio: date, fecha_fin: date) -> list:
    """
    Divide [fecha_inicio, fecha_fin] en tramos de tarifa constante según las
    vigencias del recurso. Cada tramo: {"desde", "hasta", "valor" (None si no
    hay tarifa aplicable), "dias_habiles", "horas_max"}. Sirve para mostrar
    "la tarifa cambia el X" y para estimar costos mixtos en la UI.
    """
    tarifas = list(
        TarifaVigente.objects.filter(recurso=recurso, fecha_desde__lte=fecha_fin)
        .order_by("fecha_desde")
        .values_list("fecha_desde", "valor_hora")
    )
    cortes = [fecha_inicio] + [fd for fd, _ in tarifas if fecha_inicio < fd <= fecha_fin]

    cal = CalendarioRango(fecha_inicio, fecha_fin, [recurso])
    segmentos = []
    for i, desde in enumerate(cortes):
        hasta = (cortes[i + 1] - timedelta(days=1)) if i + 1 < len(cortes) else fecha_fin
        dias = 0
        horas_max = 0.0
        f = desde
        while f <= hasta:
            if cal.es_habil(f, recurso):
                dias += 1
                horas_max += capacidad_maxima_dia(f)
            f += timedelta(days=1)
        segmentos.append({
            "desde": desde,
            "hasta": hasta,
            "valor": _tarifa_del_dia(tarifas, desde),
            "dias_habiles": dias,
            "horas_max": round(horas_max, 1),
        })
    return segmentos


def costo_estimado_asignacion(asignacion):
    """
    Costo mixto de una asignación: por cada día hábil del rango, horas del día
    × tarifa vigente ESE día. Retorna Decimal o None si el recurso no tiene
    ninguna tarifa aplicable en el período.
    """
    tarifas = list(
        TarifaVigente.objects.filter(
            recurso_id=asignacion.recurso_id, fecha_desde__lte=asignacion.fecha_fin,
        ).order_by("fecha_desde").values_list("fecha_desde", "valor_hora")
    )
    if not tarifas:
        return None
    cal = CalendarioRango(asignacion.fecha_inicio, asignacion.fecha_fin, [asignacion.recurso_id])
    # Días congelados por una liberación activa no consumen horas → no cuestan.
    ventanas = _ventanas_liberadas(
        [asignacion.pk], asignacion.fecha_inicio, asignacion.fecha_fin
    ).get(asignacion.pk, ())
    total = Decimal("0")
    con_tarifa = False
    f = asignacion.fecha_inicio
    while f <= asignacion.fecha_fin:
        if cal.es_habil(f, asignacion.recurso_id) and not any(ini <= f <= fin for ini, fin in ventanas):
            valor = _tarifa_del_dia(tarifas, f)
            if valor is not None:
                total += valor * Decimal(str(carga_propia(asignacion, f)))
                con_tarifa = True
        f += timedelta(days=1)
    return total.quantize(Decimal("0.01")) if con_tarifa else None


# ── Cesión de horas entre proyectos (acuerdo entre PMs) ─────────────────────


def horas_cedibles(asignacion, fecha: date) -> float:
    """Horas de ese día que la asignación aún puede ceder (carga propia bruta
    menos lo ya cedido con cesiones activas)."""
    bruto = carga_propia(asignacion, fecha)
    cedidas = sum(
        float(c.horas)
        for c in CesionHoras.objects.filter(
            asignacion_origen=asignacion, fecha=fecha, anulada_en__isnull=True,
        )
    )
    return max(0.0, bruto - cedidas)


def _extender_fecha_fin_con_cupo(asignacion, dias_extra: int) -> date:
    """
    Próxima fecha_fin que agrega `dias_extra` días hábiles CON CUPO después de
    la fecha_fin actual (para recuperar horas cedidas con política RECOMPUTAR).
    Lanza ValueError si no hay cupo en el próximo año.
    """
    recurso_id = asignacion.recurso_id
    inicio = asignacion.fecha_fin + timedelta(days=1)
    limite = inicio + timedelta(days=365)
    cal = CalendarioRango(inicio, limite, [recurso_id])
    carga_dias = mapa_carga([recurso_id], inicio, limite)[recurso_id]

    encontrados = 0
    fecha = inicio
    while fecha <= limite:
        if cal.es_habil(fecha, recurso_id):
            necesita = carga_propia(asignacion, fecha)
            if carga_dias.get(fecha, 0.0) + necesita <= capacidad_maxima_dia(fecha):
                encontrados += 1
                if encontrados == dias_extra:
                    return fecha
        fecha += timedelta(days=1)
    raise ValueError("No hay días hábiles con cupo en el próximo año para recomputar la fecha fin.")


def ceder_horas(asignacion_origen, proyecto_destino, fecha: date, horas, politica: str, actor):
    """
    Cede `horas` del día `fecha` de una asignación APROBADA a otro proyecto.

    La original no se edita a mano: se crea una CesionHoras + una asignación
    destino SOLICITADA de un día (que pasa por la aprobación normal), y la
    política decide el efecto en la original:
      - RECOMPUTAR: extiende fecha_fin en días hábiles con cupo (recupera horas).
      - REDUCIR: baja horas_totales (y costo_estimado si está informado).

    La tarifa cargada al receptor y descontada del original es la VIGENTE del
    día laborado. Todo queda en LogAuditoria (CEDER en la original, CREAR en
    la destino). Retorna la CesionHoras creada.
    """
    horas = float(horas)
    with transaction.atomic():
        recurso = asignacion_origen.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion_origen.recurso_id
        )
        asignacion_origen.refresh_from_db()

        if asignacion_origen.estado != "APROBADA":
            raise ValueError("Solo se pueden ceder horas de asignaciones APROBADAS.")
        if proyecto_destino.pk == asignacion_origen.proyecto_id:
            raise ValueError("El proyecto destino debe ser distinto del proyecto original.")
        if proyecto_destino.estado != "ACTIVO":
            raise ValueError("El proyecto destino debe estar ACTIVO.")
        if not (asignacion_origen.fecha_inicio <= fecha <= asignacion_origen.fecha_fin):
            raise ValueError("La fecha no pertenece al período de la asignación.")
        if not CalendarioRango(fecha, fecha, [recurso]).es_habil(fecha, recurso):
            raise ValueError("La fecha indicada no es un día hábil para el recurso.")
        if horas <= 0:
            raise ValueError("Las horas a ceder deben ser mayores que 0.")
        disponibles = horas_cedibles(asignacion_origen, fecha)
        if horas > disponibles:
            raise ValueError(f"Ese día la asignación solo tiene {disponibles:g} h cedibles.")
        if politica not in dict(Asignacion.POLITICA_CHOICES):
            raise ValueError("Política inválida (use RECOMPUTAR o REDUCIR).")

        # Tarifa vigente del día laborado: se carga al receptor y se descuenta
        # del original (decisión de negocio)
        tarifa_obj = TarifaVigente.vigente_para(recurso, fecha)
        tarifa = tarifa_obj.valor_hora if tarifa_obj else None
        horas_dec = Decimal(str(horas))
        monto = (tarifa * horas_dec).quantize(Decimal("0.01")) if tarifa is not None else None

        destino = Asignacion.objects.create(
            recurso=recurso,
            proyecto=proyecto_destino,
            modo_asignacion="RANGO",
            fecha_inicio=fecha,
            fecha_fin=fecha,
            dias_habiles=1,
            horas_totales=ceil(horas),
            intensidad_diaria=horas_dec,
            jornada_completa=False,
            estado="SOLICITADA",
            solicitada_por=actor,
            tarifa_aplicada=tarifa,
            costo_estimado=monto,
        )
        cesion = CesionHoras.objects.create(
            asignacion_origen=asignacion_origen,
            asignacion_destino=destino,
            fecha=fecha,
            horas=horas_dec,
            politica=politica,
            tarifa_hora=tarifa,
            fecha_fin_original=asignacion_origen.fecha_fin,
            creado_por=actor,
        )

        detalle = {
            "cesion": cesion.pk,
            "destino": destino.pk,
            "proyecto_destino": proyecto_destino.codigo,
            "fecha": fecha.isoformat(),
            "horas": horas,
            "politica": politica,
            "tarifa_hora": float(tarifa) if tarifa is not None else None,
            "monto_descontado": float(monto) if monto is not None else None,
        }

        if politica == "REDUCIR":
            detalle["horas_totales_antes"] = asignacion_origen.horas_totales
            asignacion_origen.horas_totales = max(0, (asignacion_origen.horas_totales or 0) - ceil(horas))
            detalle["horas_totales_despues"] = asignacion_origen.horas_totales
            if asignacion_origen.costo_estimado is not None and monto is not None:
                detalle["costo_antes"] = float(asignacion_origen.costo_estimado)
                asignacion_origen.costo_estimado -= monto
                detalle["costo_despues"] = float(asignacion_origen.costo_estimado)
            asignacion_origen.save(update_fields=["horas_totales", "costo_estimado", "updated_at"])
        else:  # RECOMPUTAR
            dias_extra = ceil(horas / float(asignacion_origen.intensidad_diaria or 8.0))
            nueva_ff = _extender_fecha_fin_con_cupo(asignacion_origen, dias_extra)
            detalle["fecha_fin_antes"] = asignacion_origen.fecha_fin.isoformat()
            detalle["fecha_fin_despues"] = nueva_ff.isoformat()
            asignacion_origen.fecha_fin = nueva_ff
            asignacion_origen.dias_habiles = (asignacion_origen.dias_habiles or 0) + dias_extra
            asignacion_origen.save(update_fields=["fecha_fin", "dias_habiles", "updated_at"])

        LogAuditoria.objects.create(
            asignacion=asignacion_origen, accion="CEDER", actor=actor, detalle=detalle,
        )
        LogAuditoria.objects.create(
            asignacion=destino, accion="CREAR", actor=actor,
            detalle={"modo": "CESION", "origen": asignacion_origen.pk, "cesion": cesion.pk},
        )
    return cesion


def _anular_cesiones_recibidas(asignacion, actor):
    """
    Al rechazar/revocar una asignación nacida de una cesión, la cesión se anula
    y la original recupera lo que la política le quitó. Las horas del día nunca
    quedaron libres para terceros (estaban reservadas), así que no puede haber
    sobreasignación al restaurar.
    """
    pendientes = asignacion.cesiones_recibidas.filter(
        anulada_en__isnull=True
    ).select_related("asignacion_origen")
    for cesion in pendientes:
        origen = cesion.asignacion_origen
        cesion.anulada_en = timezone.now()
        cesion.save(update_fields=["anulada_en"])

        detalle = {
            "cesion": cesion.pk,
            "destino": asignacion.pk,
            "fecha": cesion.fecha.isoformat(),
            "horas": float(cesion.horas),
            "politica": cesion.politica,
        }
        if cesion.politica == "REDUCIR":
            detalle["horas_totales_antes"] = origen.horas_totales
            origen.horas_totales = (origen.horas_totales or 0) + ceil(float(cesion.horas))
            detalle["horas_totales_despues"] = origen.horas_totales
            if origen.costo_estimado is not None and cesion.tarifa_hora is not None:
                origen.costo_estimado += (cesion.tarifa_hora * cesion.horas).quantize(Decimal("0.01"))
            origen.save(update_fields=["horas_totales", "costo_estimado", "updated_at"])
        elif cesion.fecha_fin_original and origen.fecha_fin != cesion.fecha_fin_original:
            detalle["fecha_fin_antes"] = origen.fecha_fin.isoformat()
            detalle["fecha_fin_despues"] = cesion.fecha_fin_original.isoformat()
            origen.fecha_fin = cesion.fecha_fin_original
            origen.dias_habiles = contar_dias_habiles(
                origen.fecha_inicio, origen.fecha_fin, origen.recurso
            )
            origen.save(update_fields=["fecha_fin", "dias_habiles", "updated_at"])

        LogAuditoria.objects.create(
            asignacion=origen, accion="ANULAR_CESION", actor=actor, detalle=detalle,
        )


# ── Liberación temporal de recurso (congelamiento de horas) ─────────────────


def solicitar_liberacion(asignacion, fecha_inicio: date, fecha_fin: date, politica: str, motivo: str, actor):
    """
    Crea una SOLICITUD de liberación (estado SOLICITADA) para una asignación
    APROBADA durante [fecha_inicio, fecha_fin]. NO surte ningún efecto todavía:
    ni congela la ventana ni toca la asignación — eso ocurre al aprobarla.

    Valida lo que ya se puede validar en el momento de solicitar (estado,
    ventana dentro del período, días hábiles con carga, sin otra liberación
    viva ni cesiones activas en la ventana). Retorna la LiberacionRecurso.
    """
    if asignacion.estado != "APROBADA":
        raise ValueError("Solo se pueden liberar asignaciones APROBADAS.")
    if fecha_inicio > fecha_fin:
        raise ValueError("La fecha de inicio no puede ser posterior a la fecha fin.")
    if fecha_fin < asignacion.fecha_inicio or fecha_inicio > asignacion.fecha_fin:
        raise ValueError("La ventana de liberación no se solapa con el período de la asignación.")
    if politica not in dict(Asignacion.POLITICA_CHOICES):
        raise ValueError("Política inválida (use RECOMPUTAR o REDUCIR).")
    if LiberacionRecurso.objects.filter(
        asignacion=asignacion, estado__in=["SOLICITADA", "APROBADA"],
        fecha_inicio__lte=fecha_fin, fecha_fin__gte=fecha_inicio,
    ).exists():
        raise ValueError("Ya existe una liberación solicitada o aprobada que se solapa con esa ventana.")
    if CesionHoras.objects.filter(
        asignacion_origen=asignacion, anulada_en__isnull=True,
        fecha__gte=fecha_inicio, fecha__lte=fecha_fin,
    ).exists():
        raise ValueError("Hay cesiones activas dentro de la ventana; anúlelas antes de liberar.")

    dias, horas = _dias_habiles_liberables(asignacion, fecha_inicio, fecha_fin)
    if dias == 0:
        raise ValueError("La ventana no contiene días hábiles con carga para liberar.")

    with transaction.atomic():
        liberacion = LiberacionRecurso.objects.create(
            asignacion=asignacion,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            politica=politica,
            motivo=motivo or "",
            estado="SOLICITADA",
            dias_liberados=dias,
            horas_liberadas=Decimal(str(round(horas, 1))),
            solicitada_por=actor,
        )
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="SOLICITAR_LIBERACION", actor=actor,
            detalle={
                "liberacion": liberacion.pk,
                "ventana": [fecha_inicio.isoformat(), fecha_fin.isoformat()],
                "dias_liberados": dias,
                "horas_liberadas": round(horas, 1),
                "politica": politica,
                "motivo": motivo or "",
            },
        )
    return liberacion


def aprobar_liberacion(liberacion, actor):
    """
    Aprueba una solicitud de liberación SOLICITADA y aplica sus efectos: congela
    la ventana (deja de consumir capacidad) y, según política, empuja fecha_fin
    en días hábiles con cupo (RECOMPUTAR) o reduce horas_totales/días (REDUCIR).

    Transaccional con select_for_update por recurso. Revalida en el momento de
    aprobar (la asignación pudo cambiar desde la solicitud). Queda en LogAuditoria.
    """
    with transaction.atomic():
        liberacion = LiberacionRecurso.objects.select_for_update().get(pk=liberacion.pk)
        if liberacion.estado != "SOLICITADA":
            raise ValueError("Solo se pueden aprobar liberaciones en estado SOLICITADA.")
        asignacion = liberacion.asignacion
        recurso = asignacion.recurso.__class__.all_objects.select_for_update().get(
            pk=asignacion.recurso_id
        )
        asignacion.refresh_from_db()

        if asignacion.estado != "APROBADA":
            raise ValueError("La asignación ya no está APROBADA; no se puede liberar.")
        if liberacion.fecha_fin < asignacion.fecha_inicio or liberacion.fecha_inicio > asignacion.fecha_fin:
            raise ValueError("La ventana ya no se solapa con el período de la asignación.")

        dias, horas = _dias_habiles_liberables(asignacion, liberacion.fecha_inicio, liberacion.fecha_fin)
        if dias == 0:
            raise ValueError("La ventana ya no contiene días hábiles con carga para liberar.")

        # Aprobar primero (estado APROBADA) deja la ventana libre para el cálculo
        # de capacidad de los pasos siguientes.
        liberacion.estado = "APROBADA"
        liberacion.dias_liberados = dias
        liberacion.horas_liberadas = Decimal(str(round(horas, 1)))
        liberacion.fecha_fin_original = asignacion.fecha_fin if liberacion.politica == "RECOMPUTAR" else None
        liberacion.revisada_por = actor
        liberacion.revisada_en = timezone.now()
        liberacion.save(update_fields=[
            "estado", "dias_liberados", "horas_liberadas", "fecha_fin_original",
            "revisada_por", "revisada_en",
        ])

        detalle = {
            "liberacion": liberacion.pk,
            "ventana": [liberacion.fecha_inicio.isoformat(), liberacion.fecha_fin.isoformat()],
            "dias_liberados": dias,
            "horas_liberadas": round(horas, 1),
            "politica": liberacion.politica,
        }

        if liberacion.politica == "RECOMPUTAR":
            nueva_ff = _extender_fecha_fin_con_cupo(asignacion, dias)
            detalle["fecha_fin_antes"] = asignacion.fecha_fin.isoformat()
            detalle["fecha_fin_despues"] = nueva_ff.isoformat()
            asignacion.fecha_fin = nueva_ff
            asignacion.costo_estimado = costo_estimado_asignacion(asignacion)
            asignacion.save(update_fields=["fecha_fin", "costo_estimado", "updated_at"])
        else:  # REDUCIR
            detalle["horas_totales_antes"] = asignacion.horas_totales
            asignacion.horas_totales = max(0, (asignacion.horas_totales or 0) - ceil(horas))
            detalle["horas_totales_despues"] = asignacion.horas_totales
            detalle["dias_habiles_antes"] = asignacion.dias_habiles
            asignacion.dias_habiles = max(0, (asignacion.dias_habiles or 0) - dias)
            detalle["dias_habiles_despues"] = asignacion.dias_habiles
            asignacion.costo_estimado = costo_estimado_asignacion(asignacion)
            asignacion.save(update_fields=["horas_totales", "dias_habiles", "costo_estimado", "updated_at"])

        LogAuditoria.objects.create(
            asignacion=asignacion, accion="LIBERAR", actor=actor, detalle=detalle,
        )
    return liberacion


def rechazar_liberacion(liberacion, actor, motivo: str = ""):
    """Rechaza una solicitud de liberación SOLICITADA (sin efecto sobre la asignación)."""
    with transaction.atomic():
        liberacion = LiberacionRecurso.objects.select_for_update().get(pk=liberacion.pk)
        if liberacion.estado != "SOLICITADA":
            raise ValueError("Solo se pueden rechazar liberaciones en estado SOLICITADA.")
        liberacion.estado = "RECHAZADA"
        liberacion.revisada_por = actor
        liberacion.revisada_en = timezone.now()
        liberacion.save(update_fields=["estado", "revisada_por", "revisada_en"])
        LogAuditoria.objects.create(
            asignacion=liberacion.asignacion, accion="RECHAZAR_LIBERACION", actor=actor,
            detalle={"liberacion": liberacion.pk, "motivo": motivo or ""},
        )
    return liberacion


def anular_liberacion(liberacion, actor):
    """
    Revierte una liberación APROBADA: la asignación vuelve a reclamar su carga en
    la ventana. Restaura fecha_fin (RECOMPUTAR) u horas/días (REDUCIR) desde los
    snapshots. Como el cupo de la ventana se reclama, se revalida capacidad: si
    otro proyecto ya lo ocupó, se aborta con ValueError. Queda en LogAuditoria.
    """
    with transaction.atomic():
        liberacion = LiberacionRecurso.objects.select_for_update().get(pk=liberacion.pk)
        if liberacion.estado != "APROBADA":
            raise ValueError("Solo se pueden anular liberaciones APROBADAS.")
        asignacion = liberacion.asignacion
        asignacion.recurso.__class__.all_objects.select_for_update().get(pk=asignacion.recurso_id)
        asignacion.refresh_from_db()

        # Marcarla anulada dentro de la transacción: a partir de aquí la ventana
        # deja de estar liberada para el cálculo de capacidad.
        liberacion.estado = "ANULADA"
        liberacion.anulada_en = timezone.now()
        liberacion.save(update_fields=["estado", "anulada_en"])

        detalle = {
            "liberacion": liberacion.pk,
            "ventana": [liberacion.fecha_inicio.isoformat(), liberacion.fecha_fin.isoformat()],
            "politica": liberacion.politica,
        }

        if liberacion.politica == "RECOMPUTAR":
            detalle["fecha_fin_antes"] = asignacion.fecha_fin.isoformat()
            if liberacion.fecha_fin_original:
                asignacion.fecha_fin = liberacion.fecha_fin_original
            detalle["fecha_fin_despues"] = asignacion.fecha_fin.isoformat()
        else:  # REDUCIR
            detalle["horas_totales_antes"] = asignacion.horas_totales
            asignacion.horas_totales = (asignacion.horas_totales or 0) + ceil(float(liberacion.horas_liberadas))
            detalle["horas_totales_despues"] = asignacion.horas_totales
            asignacion.dias_habiles = (asignacion.dias_habiles or 0) + liberacion.dias_liberados

        ok, fecha_conflicto = puede_asignar(asignacion)
        if not ok:
            raise ValueError(
                f"No se puede anular: el recurso ya está ocupado el "
                f"{fecha_conflicto.strftime('%d/%m/%Y')} dentro de la ventana liberada."
            )

        asignacion.costo_estimado = costo_estimado_asignacion(asignacion)
        asignacion.save(update_fields=[
            "fecha_fin", "horas_totales", "dias_habiles", "costo_estimado", "updated_at",
        ])
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="ANULAR_LIBERACION", actor=actor, detalle=detalle,
        )
    return liberacion


# ── Solicitudes recurrentes (patrón semanal tipo "repetir sesión") ──────────

SEMANAS_MAX_RECURRENCIA = 26


def analizar_recurrencia(recurso, fecha_inicio: date, semanas: int, horas_por_dia: dict) -> list:
    """
    Expande un patrón semanal a sus fechas concretas dentro del horizonte.

    horas_por_dia: {weekday 0–4: horas > 0} — ej: {0: 4.0} = "los lunes 4 h";
    {0: 2.0, 2: 4.0, 4: 2.0} = "lunes 2 h, miércoles 4 h, viernes 2 h".

    Retorna un plan por ocurrencia: {"fecha", "horas", "estado", "carga_existente",
    "cap"} donde estado es "OK", "NO_HABIL" (feriado/indisponibilidad) o
    "SIN_CUPO" (la carga aprobada existente + horas supera la jornada del día).
    """
    fin = fecha_inicio + timedelta(days=7 * semanas - 1)
    cal = CalendarioRango(fecha_inicio, fin, [recurso])
    carga_dias = mapa_carga([recurso.pk], fecha_inicio, fin)[recurso.pk]

    plan = []
    fecha = fecha_inicio
    while fecha <= fin:
        horas = horas_por_dia.get(fecha.weekday())
        if horas:
            cap = capacidad_maxima_dia(fecha)
            carga = carga_dias.get(fecha, 0.0)
            if not cal.es_habil(fecha, recurso):
                estado = "NO_HABIL"
            elif carga + horas > cap:
                estado = "SIN_CUPO"
            else:
                estado = "OK"
            plan.append({
                "fecha": fecha,
                "horas": horas,
                "estado": estado,
                "carga_existente": round(carga, 1),
                "cap": cap,
            })
        fecha += timedelta(days=1)
    return plan


def crear_solicitudes_recurrentes(recurso, proyecto, fecha_inicio, semanas, horas_por_dia, solicitante):
    """
    Crea una asignación SOLICITADA de un día por cada ocurrencia viable del
    patrón, agrupadas bajo una misma serie. Los días no hábiles o sin cupo se
    omiten y se reportan. Retorna (serie, creadas, omitidas).

    Cada día es una Asignacion normal: el motor de capacidad, la aprobación con
    lock y la auditoría existentes aplican sin cambios.
    """
    plan = analizar_recurrencia(recurso, fecha_inicio, semanas, horas_por_dia)
    viables = [p for p in plan if p["estado"] == "OK"]
    omitidas = [p for p in plan if p["estado"] != "OK"]
    if not viables:
        raise ValueError("Ningún día del patrón tiene cupo disponible en el período indicado.")

    serie = uuid.uuid4()
    creadas = []
    with transaction.atomic():
        for p in viables:
            asignacion = Asignacion.objects.create(
                recurso=recurso,
                proyecto=proyecto,
                modo_asignacion="RANGO",
                fecha_inicio=p["fecha"],
                fecha_fin=p["fecha"],
                dias_habiles=1,
                horas_totales=ceil(p["horas"]),
                intensidad_diaria=Decimal(str(p["horas"])),
                jornada_completa=False,
                estado="SOLICITADA",
                solicitada_por=solicitante,
                serie=serie,
            )
            LogAuditoria.objects.create(
                asignacion=asignacion, accion="CREAR", actor=solicitante,
                detalle={
                    "modo": "RECURRENTE",
                    "serie": str(serie),
                    "horas": p["horas"],
                    "semanas": semanas,
                },
            )
            creadas.append(asignacion)
    return serie, creadas, omitidas


def rechazar_asignacion(asignacion, actor, motivo=""):
    with transaction.atomic():
        asignacion.estado = "RECHAZADA"
        asignacion.save(update_fields=["estado", "updated_at"])
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="RECHAZAR", actor=actor, detalle={"motivo": motivo}
        )
        _anular_cesiones_recibidas(asignacion, actor)


def revocar_asignacion(asignacion, actor, motivo=""):
    with transaction.atomic():
        asignacion.estado = "REVOCADA"
        asignacion.save(update_fields=["estado", "updated_at"])
        LogAuditoria.objects.create(
            asignacion=asignacion, accion="REVOCAR", actor=actor, detalle={"motivo": motivo}
        )
        _anular_cesiones_recibidas(asignacion, actor)
