"""Qué declaró antes esta persona para algo parecido, y cuánto le costó.

Es lo que le falta a quien aprueba. Un renglón dice «Integración Oracle · 8 h» y
no hay con qué contrastarlo: ¿ocho horas en eso son muchas o son lo de siempre?
El precedente responde eso con datos propios de la casa, no con una opinión.

**Filtrar primero, buscar después.** El vecino más parecido del universo entero
es ruido; dentro de la misma persona o del mismo proyecto es precedente. Por eso
el alcance —misma persona o mismo proyecto, doce meses— se aplica antes que la
similitud, no como un filtro posterior.

**Los devueltos van primero.** `motivo_devolucion` es la única etiqueta real de
qué rechaza de verdad un aprobador en esta empresa: si algo parecido ya se
devolvió, eso pesa más que diez aprobaciones rutinarias.

Esta es la mitad **léxica** de la búsqueda híbrida del diseño. Encuentra lo que
se parece en las palabras: el copiar y pegar, las variantes de una misma tarea,
los textos calcados con otra fecha. No encuentra lo que significa lo mismo dicho
de otra forma — eso es la mitad vectorial, que espera a que se decida dónde
corre el modelo de embeddings.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.postgres.search import TrigramSimilarity

from apps.legalizacion.models import DiaLegalizado, RegistroHoras

# Por debajo de esto, «parecido» deja de significar nada: son coincidencias de
# dos o tres letras sueltas. Es el umbral por defecto de pg_trgm.
UMBRAL = 0.3

# Cuánto se mira hacia atrás. Más de un año deja de ser precedente y pasa a ser
# arqueología: cambian los proyectos, las personas y la forma de escribir.
MESES = 12

# Cuántos se enseñan por renglón. Tres caben en la pantalla y el cuarto ya no se
# lee: quien aprueba está mirando una cola, no un informe.
MAXIMO = 3

# Tope de renglones que reciben precedentes en una pantalla. La consulta es una
# por renglón, así que sin tope una cola de cien serían cien consultas. Solo se
# buscan para los que ya llevan una señal, que son los que hay que mirar.
MAX_RENGLONES = 20


@dataclass
class Precedente:
    fecha: date
    persona: str
    horas: float
    destino: str
    devuelto: bool
    motivo: str
    similitud: float
    misma_persona: bool = True

    @property
    def frase(self):
        """Una frase en pasado, no una etiqueta.

        Con el formato de antes —«25/08/2026 · 4,5 h · INT-DEPART · devuelto»—
        la palabra «devuelto» quedaba justo encima de los botones Aprobar y
        Devolver, y se leia como el estado del renglon que se esta mirando o
        como algo que la pantalla acababa de hacer. Es ninguna de las dos: es lo
        que paso con otro renglon, otro dia. Dicho como frase no se confunde.
        """
        quien = "declaró" if self.misma_persona else f"{self.persona} declaró"
        desenlace = (
            f" y se lo devolvieron: «{self.motivo}»" if self.devuelto and self.motivo
            else " y se lo devolvieron" if self.devuelto
            else " y se aprobó"
        )
        return (
            f"El {self.fecha:%d/%m/%Y} {quien} {self.horas:g} h en {self.destino} "
            f"con un texto parecido{desenlace}."
        )


def buscar(registro, dia, limite=MAXIMO):
    """Renglones parecidos que ya se declararon y alguien miró.

    Solo mira días REGISTRADOS o APROBADOS: un día abierto todavía se está
    escribiendo y no es precedente de nada.
    """
    texto = (registro.detalle or "").strip()
    if not texto:
        return []

    desde = dia.fecha - timedelta(days=MESES * 30)
    similitud = TrigramSimilarity("detalle", texto)

    candidatos = (
        RegistroHoras.objects
        .filter(
            dia__fecha__gte=desde,
            dia__fecha__lt=dia.fecha,
            dia__estado__in=[DiaLegalizado.REGISTRADO, DiaLegalizado.APROBADO],
        )
        .exclude(pk=registro.pk)
        .select_related("dia", "dia__recurso", "proyecto", "tipo_actividad")
    )

    # El alcance va antes que la similitud: la misma persona, o el mismo
    # proyecto. Buscar en todo el histórico devolvería parecidos de gente que no
    # tiene nada que ver, y eso no ayuda a decidir.
    if registro.proyecto_id:
        candidatos = candidatos.filter(
            dia__recurso_id=dia.recurso_id
        ) | candidatos.filter(proyecto_id=registro.proyecto_id)
    else:
        candidatos = candidatos.filter(dia__recurso_id=dia.recurso_id)

    encontrados = (
        candidatos.annotate(parecido=similitud)
        .filter(parecido__gte=UMBRAL)
        .order_by("-parecido", "-dia__fecha")[: limite * 3]
    )

    precedentes = [
        Precedente(
            fecha=r.dia.fecha,
            persona=r.dia.recurso.nombre,
            horas=float(r.horas),
            destino=(
                r.proyecto.codigo if r.proyecto_id else r.tipo_actividad.nombre
            ),
            devuelto=r.estado == RegistroHoras.DEVUELTO,
            motivo=r.motivo_devolucion or "",
            similitud=round(r.parecido, 2),
            misma_persona=r.dia.recurso_id == dia.recurso_id,
        )
        for r in encontrados
    ]

    # Lo devuelto primero: es la única señal de qué se rechaza de verdad aquí.
    precedentes.sort(key=lambda p: (not p.devuelto, -p.similitud))
    return precedentes[:limite]


def adjuntar(dias):
    """Cuelga los precedentes de cada renglón marcado de la cola.

    Solo de los marcados, y hasta un tope: la consulta es una por renglón, y una
    cola de cien no puede convertirse en cien consultas. Los de rutina no los
    necesitan — precisamente porque no hay nada que decidir en ellos.
    """
    pendientes = [
        (dia, registro)
        for dia in dias
        for registro in dia.pendientes_mios
        if getattr(registro, "evaluacion", None) and registro.evaluacion.senales
    ]
    for dia, registro in pendientes[:MAX_RENGLONES]:
        registro.precedentes = buscar(registro, dia)
