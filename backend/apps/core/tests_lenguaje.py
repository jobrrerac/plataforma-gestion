"""El texto visible va en tuteo, y eso se comprueba.

Es una de las convenciones del proyecto: español colombiano con tuteo —«describe
la actividad», no «describí»—. Se escribió una vez y se fue erosionando sola: al
revisar esto había **25 apariciones de voseo** repartidas entre diez ficheros,
casi todas en mensajes de error y en los desplegables de las pantallas de
solicitud. Nadie las metió a propósito; es lo que pasa cuando una convención solo
vive en un documento.

Lo mismo que hace `tests_arquitectura.py` con las capas: una regla que no se
comprueba no es una regla, es una intención.

**Qué NO comprueba.** Solo caza las formas de voseo de la lista, que son las que
aparecen de verdad. No es un corrector de estilo ni sabe de gramática: un texto
puede pasar esto y seguir sonando raro. Sirve para que no vuelva a entrar lo que
ya hubo que sacar una vez.

**Dónde mira.** Solo dentro de `backend/`: las plantillas y el texto de la
aplicación. Los documentos de la raíz —README, COMANDOS— quedan fuera a
propósito, y no por descuido: en el contenedor de desarrollo se monta `backend/`
como raíz, así que una prueba que mirase más arriba pasaría en CI y no vería nada
en local. Un detector que mira cosas distintas según dónde corra es peor que no
tenerlo, porque da verde igual.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

# El directorio que contiene `apps/` y `templates/`. Es `backend/` en el
# repositorio y `/app` dentro del contenedor: la misma carpeta con dos nombres.
BASE = Path(__file__).resolve().parents[2]

# Formas de voseo, en dos grupos. La división no es un capricho.
#
# INEQUIVOCAS: solo pueden ser voseo. «activá» no es ninguna otra cosa en
# castellano —la primera persona de ese verbo es «activé»—, así que buscarlas no
# produce falsos positivos en ningún contexto.
INEQUIVOCAS = [
    # imperativos de verbos en -ar, y los de -er que no chocan con nada
    "activá", "registrá", "mirá", "seleccioná", "revisá", "ingresá",
    "verificá", "completá", "cambiá", "guardá", "probá", "usá", "dejá",
    "confirmá", "buscá", "agregá", "indicá", "solicitá", "aprobá", "marcá",
    "presioná", "apretá", "recordá", "poné", "tené", "hacé", "devolvé",
    "cerrá", "enviá",
    # imperativos con pronombre pegado, que pierden el acento escrito
    "asegurate", "fijate", "acordate", "borralo", "guardalo", "revisalo",
    # presentes
    "tenés", "podés", "querés", "sabés", "necesitás", "debés", "aceptás",
]

# AMBIGUAS: en los verbos en -er/-ir, el imperativo de voseo se escribe **igual**
# que la primera persona del pretérito. «yo escribí» y «escribí vos» coinciden
# letra por letra.
#
# Esto no es teórico: la primera versión de esta prueba las buscaba en todas
# partes y marcó como voseo el comentario «lo que se escribió sobre mí, y lo que
# yo escribí», que es castellano impecable. Una prueba que marca lo correcto se
# desactiva a la semana, y entonces deja de cazar también lo que sí estaba mal.
#
# Se buscan solo en las **plantillas**: allí todo el texto se le dice al lector,
# así que un «escribí» es una orden. En el código Python la prosa narra
# —docstrings, comentarios— y la primera persona es normal.
AMBIGUAS = [
    "escribí", "elegí", "añadí", "describí", "pedí", "corregí", "abrí",
]


def _patron(formas):
    return re.compile(r"\b(" + "|".join(formas) + r")\b", re.IGNORECASE)


PATRON = _patron(INEQUIVOCAS)
PATRON_PLANTILLAS = _patron(INEQUIVOCAS + AMBIGUAS)

# Este fichero contiene la lista de arriba, asi que se caza a si mismo.
YO = Path(__file__).resolve()


def ficheros_revisados():
    """Plantillas y código, cada uno con el patrón que le corresponde."""
    for glob, patron in (
        ("templates/**/*.html", PATRON_PLANTILLAS),
        ("apps/**/*.py", PATRON),
    ):
        for f in BASE.glob(glob):
            if f.is_file() and f.resolve() != YO:
                yield f, patron


class ElTextoVisibleVaEnTuteoTests(SimpleTestCase):
    def test_no_hay_voseo(self):
        hallazgos = []
        for fichero, patron in ficheros_revisados():
            for n, linea in enumerate(
                fichero.read_text(encoding="utf-8").splitlines(), start=1
            ):
                for encontrado in patron.findall(linea):
                    hallazgos.append(f"{fichero.relative_to(BASE)}:{n} — «{encontrado}»")

        self.assertEqual(
            hallazgos, [],
            "El texto visible va en tuteo colombiano («describe»), no en voseo "
            "(«describí»).\n  " + "\n  ".join(hallazgos),
        )

    def test_esta_mirando_algo(self):
        """La forma en que esta prueba se rompe sin avisar.

        Si `BASE` deja de apuntar donde debe —al mover el fichero de sitio, o
        porque el contenedor monta otra cosa—, los `glob` no encuentran nada y
        la prueba de arriba da verde para siempre sin haber leído una línea. Ya
        pasó al escribirla: apuntaba un nivel por encima y no revisaba nada.
        """
        cuantos = sum(1 for _ in ficheros_revisados())
        self.assertGreater(
            cuantos, 50,
            f"solo se revisaron {cuantos} ficheros desde {BASE}; "
            "el detector no está mirando la aplicación",
        )

    def test_el_detector_detecta(self):
        """Sin esto, una lista mal escrita daría verde y nadie se enteraría."""
        self.assertTrue(PATRON.search("Seleccioná un recurso"))
        self.assertTrue(PATRON.search("Activá 'Días flexibles'"))
        self.assertTrue(PATRON.search("Debés elegir uno"))

    def test_en_las_plantillas_tambien_caza_las_ambiguas(self):
        """En una plantilla todo el texto se le dice al lector, así que un
        «escribí» solo puede ser una orden."""
        self.assertTrue(PATRON_PLANTILLAS.search("Escribí qué hiciste"))
        self.assertTrue(PATRON_PLANTILLAS.search("Elegí un proyecto"))
        self.assertTrue(PATRON_PLANTILLAS.search("Pedí la reapertura"))

    def test_en_el_codigo_no_confunde_un_preterito_con_una_orden(self):
        """«Lo que yo escribí» es castellano impecable, y la primera versión de
        esta prueba lo marcaba como voseo. Marcar lo correcto es la forma más
        rápida de que alguien acabe desactivando el detector entero."""
        for correcto in (
            "lo que se escribió sobre mí, y lo que yo escribí",
            "pedí el token y me lo dieron",
            "elegí este enfoque por lo que costaba el otro",
            "abrí una excepción para ese caso",
            "corregí el desvío en la misma migración",
        ):
            self.assertIsNone(
                PATRON.search(correcto), f"falso positivo sobre «{correcto}»",
            )

    def test_y_no_se_ceba_con_el_tuteo(self):
        """La forma correcta no puede saltar: una prueba que marca lo bueno se
        desactiva a la semana."""
        for bueno in (
            "Selecciona un recurso",
            "Activa 'Días flexibles' para extender la fecha fin",
            "Debes elegir uno",
            "describe la actividad",
            # Futuros y condicionales de tuteo, que tambien llevan acento final.
            "Podrás corregirlo después",
            "Verás el resultado en la cola",
        ):
            self.assertIsNone(PATRON.search(bueno), f"falso positivo sobre «{bueno}»")


class LasEtiquetasDePlantillaCabenEnUnaLineaTests(SimpleTestCase):
    """Una etiqueta partida en dos líneas deja de ser una etiqueta.

    El lexer de plantillas de Django no cruza saltos de línea. Un
    `{% include ... %}` escrito en tres líneas no falla: **se imprime tal cual**
    en la pantalla, con las llaves y todo, sin error y sin aviso en el log.

    Pasó de verdad, y por el peor camino posible: el ejemplo de uso escrito
    dentro de `componentes/franja_pagina.html` estaba partido en tres líneas.
    Alguien lo copió y la cabecera de Novedades se quedó sin título y sin enlace
    de «Volver» — y nadie lo vio hasta mirar el HTML.

    Los comentarios `{% comment %}` sí pueden ocupar varias líneas: lo que se
    busca son las etiquetas de apertura, no lo que encierran.
    """

    # `{% ... %}` con un salto de linea dentro.
    PARTIDA = re.compile(r"\{%[^%]*?\n.*?%\}", re.DOTALL)

    def _plantillas(self):
        for f in (BASE / "templates").rglob("*.html"):
            yield f

    def test_ninguna_etiqueta_cruza_un_salto_de_linea(self):
        hallazgos = []
        for fichero in self._plantillas():
            texto = fichero.read_text(encoding="utf-8")
            for coincidencia in self.PARTIDA.finditer(texto):
                etiqueta = coincidencia.group(0)
                primera = etiqueta.lstrip("{% ").split()
                # `{% comment %}...{% endcomment %}` encierra varias lineas a
                # proposito: lo que importa es que la etiqueta de apertura no
                # este partida, y esa cabe de sobra en una linea.
                if primera and primera[0] in ("comment", "endcomment"):
                    continue
                linea = texto[:coincidencia.start()].count("\n") + 1
                hallazgos.append(
                    f"{fichero.relative_to(BASE)}:{linea} — "
                    f"{' '.join(etiqueta.split())[:70]}"
                )

        self.assertEqual(
            hallazgos, [],
            "Estas etiquetas ocupan varias líneas, así que Django NO las "
            "interpreta: se imprimen literalmente en la pantalla.\n  "
            + "\n  ".join(hallazgos),
        )

    def test_el_detector_detecta(self):
        self.assertTrue(self.PARTIDA.search('{% include "x.html" with\n  a="b" %}'))

    def test_y_no_se_ceba_con_una_etiqueta_normal(self):
        self.assertIsNone(self.PARTIDA.search('{% include "x.html" with a="b" %}\n'))

    def test_esta_mirando_algo(self):
        """La misma trampa que la prueba del tuteo: si `BASE` deja de apuntar
        donde debe, esto da verde sin haber leído una plantilla."""
        self.assertGreater(sum(1 for _ in self._plantillas()), 15)
