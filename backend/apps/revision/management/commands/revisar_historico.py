"""Pasa las reglas del triaje sobre horas ya registradas, sin tocar nada.

El triaje se calcula al vuelo, así que en cuanto está desplegado ya ordena todo
lo que sigue pendiente. Lo que no se ve es **lo que ya se aprobó**, y ahí está lo
interesante: el renglón de estudio de 7,5 h se firmó sin que nada avisara, y la
única forma de saber si estas reglas sirven es aplicarlas a lo que ya pasó.

Es de **solo lectura**. No aprueba, no devuelve, no reabre ni marca nada. Lo que
imprime es una hipótesis —«esto se habría marcado»— no una acusación: cada uno
de esos renglones lo firmó una persona que pudo tener buenos motivos.

La cifra que importa está en el resumen: **cuántos de los ya aprobados habrían
salido en Rutina**. Si son casi todos, las reglas separan bien y el carril de
rutina se puede empezar a confiar. Si son pocos, o sobran reglas o el umbral
está mal puesto, y conviene saberlo antes de construir nada encima.

Uso:
    python manage.py revisar_historico
    python manage.py revisar_historico --desde 2026-08-01 --hasta 2026-09-30
    python manage.py revisar_historico --solo-aprobados --detalle
"""

from collections import Counter
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.legalizacion.models import DiaLegalizado
from apps.revision import senales as sn
from apps.revision.api import Contexto

DIAS_POR_DEFECTO = 90


def leer_fecha(valor):
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor.strip(), formato).date()
        except ValueError:
            continue
    raise CommandError(f"'{valor}' no es una fecha válida (se espera aaaa-mm-dd).")


class Command(BaseCommand):
    help = (
        "Aplica las reglas del triaje a horas ya registradas y dice qué se habría "
        "marcado. Solo lectura: no aprueba, no devuelve, no cambia ningún estado."
    )

    def add_arguments(self, parser):
        parser.add_argument("--desde", help="Primer día a revisar (aaaa-mm-dd).")
        parser.add_argument("--hasta", help="Último día a revisar (aaaa-mm-dd).")
        parser.add_argument(
            "--solo-aprobados", action="store_true", dest="solo_aprobados",
            help="Solo los días ya aprobados, que es donde está la pregunta real.",
        )
        parser.add_argument(
            "--detalle", action="store_true",
            help="Lista cada renglón marcado, no solo el recuento.",
        )

    def handle(self, *args, **opciones):
        hoy = timezone.localdate()
        hasta = leer_fecha(opciones["hasta"]) if opciones["hasta"] else hoy
        desde = (
            leer_fecha(opciones["desde"]) if opciones["desde"]
            else hasta - timedelta(days=DIAS_POR_DEFECTO)
        )
        if desde > hasta:
            raise CommandError("La fecha de inicio es posterior a la de fin.")

        estados = (
            [DiaLegalizado.APROBADO] if opciones["solo_aprobados"]
            else [DiaLegalizado.REGISTRADO, DiaLegalizado.APROBADO]
        )
        dias = list(
            DiaLegalizado.objects
            .filter(fecha__gte=desde, fecha__lte=hasta, estado__in=estados)
            .select_related("recurso")
            .prefetch_related("registros__proyecto", "registros__tipo_actividad")
            .order_by("fecha", "recurso__nombre")
        )

        self.stdout.write("")
        self.stdout.write(f"Rango    {desde:%d/%m/%Y} a {hasta:%d/%m/%Y}")
        self.stdout.write(f"Estados  {', '.join(estados)}")
        self.stdout.write(f"Días     {len(dias)}")
        self.stdout.write(self.style.WARNING("Solo lectura: no se cambia ningún estado."))

        if not dias:
            self.stdout.write("")
            self.stdout.write("No hay días registrados en ese rango.")
            return

        contexto = Contexto(dias)
        bandas = Counter()
        por_senal = Counter()
        marcados = []

        for dia in dias:
            for registro in dia.registros.all():
                evaluacion = sn.evaluar(registro, dia, contexto)
                bandas[evaluacion.banda] += 1
                for senal in evaluacion.senales:
                    por_senal[senal.codigo] += 1
                if evaluacion.senales:
                    marcados.append((dia, registro, evaluacion))

        total = sum(bandas.values())
        self.stdout.write(f"Renglones {total}")

        if opciones["detalle"] and marcados:
            self.stdout.write("")
            self.stdout.write("SE HABRÍAN MARCADO:")
            for dia, registro, evaluacion in marcados:
                destino = (
                    registro.proyecto.codigo if registro.proyecto_id
                    else registro.tipo_actividad.nombre
                )
                estilo = self.style.ERROR if evaluacion.banda == sn.ATENCION else self.style.WARNING
                self.stdout.write(estilo(
                    f"  {evaluacion.etiqueta:9} {dia.fecha:%d/%m/%Y} "
                    f"{dia.recurso.nombre[:28]:28} {float(registro.horas):4.1f} h "
                    f"{destino[:14]:14} {registro.detalle[:38]}"
                ))
                for senal in evaluacion.senales:
                    self.stdout.write(f"              {senal.texto}")

        self.stdout.write("")
        self.stdout.write("POR BANDA:")
        for banda in (sn.ATENCION, sn.REVISAR, sn.RUTINA):
            n = bandas[banda]
            pct = (100.0 * n / total) if total else 0
            self.stdout.write(f"  {sn.ETIQUETAS[banda]:9} {n:5}   {pct:5.1f} %")

        if por_senal:
            self.stdout.write("")
            self.stdout.write("POR SEÑAL:")
            for codigo, n in por_senal.most_common():
                self.stdout.write(f"  {codigo:32} {n:5}")

        self.stdout.write("")
        rutina = bandas[sn.RUTINA]
        pct = (100.0 * rutina / total) if total else 0
        self.stdout.write(self.style.SUCCESS(
            f"{rutina} de {total} renglones ({pct:.0f} %) habrían salido en Rutina."
        ))
        self.stdout.write(
            "Los marcados no son errores: los firmó una persona y pudo tener sus "
            "motivos. Lo que dice esta cifra es cuánta cola se ahorra mirar."
        )
