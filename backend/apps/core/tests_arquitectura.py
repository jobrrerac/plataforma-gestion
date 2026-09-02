"""El monolito es modular de verdad, no solo de palabra.

Que sea modular tiene una consecuencia práctica y cara: si tocar un módulo
pudiera romper cualquier otro, cada cambio obligaría a repasar los 171 casos del
plan de QA a mano. El mapa de `docs/ARQUITECTURA_MODULOS.md` dice qué bloques
hay que reprobar según lo que se toque, y ese mapa solo vale si las
dependencias entre apps siguen siendo las que dice.

Por eso esto es un test y no un párrafo en un documento. Un acoplamiento nuevo
no se nota al escribirlo —un `from apps.x import y` no duele— y se descubre
meses después, cuando ya no se puede deshacer.

Se distinguen tres tipos de import entre apps, porque no acoplan igual:

- **Estructural**: en el cuerpo del módulo. Se ejecuta al importar la app, y es
  el que crea ciclos de importación reales. Es el que se vigila.
- **Diferido**: dentro de una función. Acopla en tiempo de ejecución pero no al
  cargar, y es la salida legítima para una dependencia puntual hacia arriba.
- **De comando**: en `management/commands/`. Una utilidad de mantenimiento como
  `limpiar_operacion` toca todas las tablas por definición; contarla como
  acoplamiento del módulo daría un grafo falso.
"""

import ast
from collections import defaultdict
from pathlib import Path

from django.test import SimpleTestCase

RAIZ_APPS = Path(__file__).resolve().parent.parent

# El orden es la arquitectura: cada app solo puede depender de las anteriores.
#
#   accounts         quién es y qué puede hacer
#   core             el maestro: recursos, proyectos, tarifas
#   calendar_engine  qué días son hábiles para cada quien
#   assignments      quién está asignado a qué y cuándo (el plan)
#   legalizacion     qué hizo cada quien con su jornada (lo declarado)
#   dashboard        pantallas que componen todo lo anterior
CAPAS = [
    "accounts",
    "core",
    "calendar_engine",
    "assignments",
    "legalizacion",
    "dashboard",
]


def _apps_del_proyecto():
    return {d.name for d in RAIZ_APPS.iterdir() if (d / "apps.py").exists()}


def analizar_imports():
    """Grafo de dependencias entre apps, separado por tipo de import."""
    apps = _apps_del_proyecto()
    estructural = defaultdict(set)
    diferido = defaultdict(set)
    de_comando = defaultdict(set)

    for app in sorted(apps):
        for ruta in (RAIZ_APPS / app).rglob("*.py"):
            partes = ruta.parts
            if "migrations" in partes or "__pycache__" in partes:
                continue
            if ruta.name.startswith("test"):
                continue

            arbol = ast.parse(ruta.read_text(encoding="utf-8"))
            # Marcar todo lo que cuelga de una función: ahí el import es diferido.
            dentro_de_funcion = set()
            for nodo in ast.walk(arbol):
                if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    dentro_de_funcion.update(id(h) for h in ast.walk(nodo))

            for nodo in ast.walk(arbol):
                if not isinstance(nodo, ast.ImportFrom):
                    continue
                modulo = nodo.module or ""
                if not modulo.startswith("apps."):
                    continue
                destino = modulo.split(".")[1]
                if destino == app or destino not in apps:
                    continue
                if "management" in partes:
                    de_comando[app].add(destino)
                elif id(nodo) in dentro_de_funcion:
                    diferido[app].add(destino)
                else:
                    estructural[app].add(destino)

    return estructural, diferido, de_comando


class ArquitecturaModularTests(SimpleTestCase):
    def setUp(self):
        self.estructural, self.diferido, self.de_comando = analizar_imports()

    def test_no_hay_ciclos_estructurales_entre_apps(self):
        """Dos apps que se importan mutuamente ya no son dos módulos.

        Y hacen inútil el mapa de impacto en QA: si A puede romper B y B puede
        romper A, tocar cualquiera obliga a probar las dos.
        """
        ciclos = sorted({
            tuple(sorted((a, b)))
            for a, destinos in self.estructural.items()
            for b in destinos
            if a in self.estructural.get(b, set())
        })
        self.assertEqual(
            ciclos, [],
            "Hay apps que se importan mutuamente en el cuerpo del módulo: "
            + ", ".join(f"{a} <-> {b}" for a, b in ciclos)
            + ". Si la dependencia es puntual, muévela dentro de la función que "
            "la usa; si no, algo pertenece a la otra app.",
        )

    def test_cada_app_solo_depende_de_las_capas_de_abajo(self):
        """El orden de CAPAS es la arquitectura; esto lo hace cumplir.

        `legalizacion` puede mirar el plan de `assignments` —para enseñar la
        tarea planificada al lado de lo declarado— pero `assignments` no puede
        depender de lo que la gente declaró después.
        """
        posicion = {app: i for i, app in enumerate(CAPAS)}
        infracciones = []
        for app, destinos in sorted(self.estructural.items()):
            if app not in posicion:
                continue
            for destino in sorted(destinos):
                if destino in posicion and posicion[destino] >= posicion[app]:
                    infracciones.append(f"{app} -> {destino}")
        self.assertEqual(
            infracciones, [],
            "Estas dependencias van hacia arriba o hacia el lado en la pila de "
            "capas: " + ", ".join(infracciones)
            + f". Orden esperado: {' < '.join(CAPAS)}.",
        )

    def test_todas_las_apps_estan_en_el_mapa_de_capas(self):
        """Una app nueva sin sitio en la pila deja el mapa de QA incompleto,
        y entonces nadie sabe qué hay que reprobar cuando cambia."""
        sin_ubicar = sorted(_apps_del_proyecto() - set(CAPAS))
        self.assertEqual(
            sin_ubicar, [],
            f"Apps sin capa asignada: {sin_ubicar}. Añádelas a CAPAS aquí y al "
            "mapa de docs/ARQUITECTURA_MODULOS.md, con sus bloques de QA.",
        )
