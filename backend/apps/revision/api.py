"""Punto de entrada del triaje: clasifica una cola de aprobación ya cargada.

`legalizacion` llama aquí con un import diferido y tolerante: si esta app no
está instalada, la cola se pinta como siempre. Esa es la razón de que el módulo
viva aparte — no para desplegarlo por su cuenta, que sigue siendo el mismo
monolito, sino para poder apagarlo sin migración de vuelta.

Todo el contexto se precarga en tres consultas fijas, sean diez renglones o
doscientos. Una consulta por fila convertiría una pantalla en una espera, y es
justo la pantalla que ya sufre por tener cien filas.
"""

from collections import defaultdict

from apps.assignments.models import Asignacion
from apps.legalizacion.models import RegistroHoras

from . import senales as sn


class Contexto:
    """Lo que las reglas necesitan saber, cargado de una vez para toda la cola."""

    def __init__(self, dias):
        self.dias = dias
        self._plan = defaultdict(float)          # (recurso, proyecto, fecha) -> h/día
        self._plan_dia = defaultdict(float)      # (recurso, fecha) -> h/día en proyectos
        self._detalles = defaultdict(set)        # (recurso, texto) -> {fechas}
        self._devoluciones = defaultdict(int)    # recurso -> nº devueltos recientes
        if dias:
            self._cargar()

    # ── carga ───────────────────────────────────────────────────────────────

    def _cargar(self):
        fechas = [d.fecha for d in self.dias]
        recursos = {d.recurso_id for d in self.dias}
        desde, hasta = min(fechas), max(fechas)

        # 1. El plan: asignaciones aprobadas que cubren alguna de esas fechas.
        #    Solo APROBADAS — una solicitud pendiente no autoriza a imputar.
        asignaciones = Asignacion.objects.filter(
            recurso_id__in=recursos, estado="APROBADA",
            fecha_inicio__lte=hasta, fecha_fin__gte=desde,
        ).values_list(
            "recurso_id", "proyecto_id", "fecha_inicio", "fecha_fin",
            "intensidad_diaria", "jornada_completa",
        )
        for recurso_id, proyecto_id, inicio, fin, intensidad, jornada_completa in asignaciones:
            for fecha in fechas:
                if not (inicio <= fecha <= fin):
                    continue
                # `jornada_completa` guarda un 8.0 de relleno en intensidad y la
                # carga real es el tope del día; se pide al motor para no
                # duplicar aquí la regla de lunes-jueves 8,5 y viernes 8.
                if jornada_completa:
                    from apps.assignments.services import capacidad_maxima_dia
                    horas = capacidad_maxima_dia(fecha)
                else:
                    horas = float(intensidad or 0)
                self._plan[(recurso_id, proyecto_id, fecha)] += horas
                self._plan_dia[(recurso_id, fecha)] += horas

        # 2. Detalles de otros días, para detectar el copiar y pegar.
        desde_rep, hasta_rep = sn.ventana_repeticion(desde)
        _, hasta_rep = sn.ventana_repeticion(hasta)
        otros = RegistroHoras.objects.filter(
            dia__recurso_id__in=recursos,
            dia__fecha__gte=desde_rep, dia__fecha__lte=hasta_rep,
        ).values_list("dia__recurso_id", "dia__fecha", "detalle")
        for recurso_id, fecha, detalle in otros:
            self._detalles[(recurso_id, sn.normalizar(detalle))].add(fecha)

        # 3. Devoluciones recientes: contexto del día, no señal del renglón.
        devueltos = RegistroHoras.objects.filter(
            dia__recurso_id__in=recursos,
            estado=RegistroHoras.DEVUELTO,
            dia__fecha__gte=sn.ventana_devoluciones(desde),
        ).values_list("dia__recurso_id", flat=True)
        for recurso_id in devueltos:
            self._devoluciones[recurso_id] += 1

    # ── lo que preguntan las reglas ─────────────────────────────────────────

    def horas_planificadas(self, recurso_id, proyecto_id, fecha):
        """Horas que el plan preveía ese día en ese proyecto, o None si no había plan."""
        return self._plan.get((recurso_id, proyecto_id, fecha))

    def plan_del_dia(self, recurso_id, fecha):
        """Total de horas planificadas en proyectos ese día."""
        return self._plan_dia.get((recurso_id, fecha), 0.0)

    def dias_con_ese_detalle(self, recurso_id, texto_normalizado, excepto):
        """Otras fechas en las que esa persona escribió exactamente lo mismo."""
        fechas = self._detalles.get((recurso_id, texto_normalizado), set())
        return sorted(f for f in fechas if f != excepto)

    def devoluciones_recientes(self, recurso_id):
        return self._devoluciones.get(recurso_id, 0)


def clasificar(dias):
    """Anota cada renglón con su evaluación y cada día con su banda.

    Muta los objetos que recibe, igual que `dias_por_aprobar` ya hace con
    `pendientes_mios` y `detalle`. Devuelve el recuento por banda para la
    cabecera de la pantalla.
    """
    ctx = Contexto(dias)
    recuento = {sn.RUTINA: 0, sn.REVISAR: 0, sn.ATENCION: 0}

    for dia in dias:
        for registro in list(dia.pendientes_mios) + list(dia.otros):
            registro.evaluacion = sn.evaluar(registro, dia, ctx)

        # La banda del día es la peor de lo que esta persona puede firmar: no
        # tiene sentido subir un día al carril de atención por un renglón de
        # otro PM que este ni siquiera puede tocar.
        propios = [r.evaluacion.banda for r in dia.pendientes_mios]
        dia.banda = max(propios, key=lambda b: sn.ORDEN[b]) if propios else sn.RUTINA
        dia.banda_etiqueta = sn.ETIQUETAS[dia.banda]
        dia.devoluciones_recientes = (
            ctx.devoluciones_recientes(dia.recurso_id)
            if ctx.devoluciones_recientes(dia.recurso_id) >= sn.MINIMO_DEVOLUCIONES
            else 0
        )
        for registro in dia.pendientes_mios:
            recuento[registro.evaluacion.banda] += 1

    # Lo que necesita mirarse primero, primero. Dentro de cada banda se conserva
    # el orden que traía (fecha y nombre), que es el que hace la lista legible.
    dias.sort(key=lambda d: -sn.ORDEN[d.banda])
    return recuento
