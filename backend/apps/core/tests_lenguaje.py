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

# Imperativos y presentes de voseo. Van con acento en la ultima silaba, que es
# justo lo que los distingue del tuteo ("activa" / "activá") y lo que hace que
# se puedan buscar sin caer en falsos positivos.
FORMAS = [
    # imperativos
    "activá", "registrá", "mirá", "poné", "tené", "hacé", "escribí", "elegí",
    "seleccioná", "revisá", "ingresá", "verificá", "completá", "cambiá",
    "guardá", "probá", "usá", "dejá", "confirmá", "buscá", "agregá", "añadí",
    "describí", "indicá", "pedí", "solicitá", "aprobá", "devolvé", "corregí",
    "marcá", "abrí", "cerrá", "enviá", "presioná", "apretá", "recordá",
    # imperativos con pronombre pegado, que pierden el acento escrito
    "asegurate", "fijate", "acordate", "borralo", "guardalo", "revisalo",
    # presentes
    "tenés", "podés", "querés", "sabés", "necesitás", "debés", "aceptás",
]

PATRON = re.compile(r"\b(" + "|".join(FORMAS) + r")\b", re.IGNORECASE)

# Este fichero contiene la lista de arriba, asi que se caza a si mismo.
YO = Path(__file__).resolve()


def ficheros_revisados():
    """Plantillas y código de la aplicación: lo que acaba viendo una persona."""
    for patron in ("templates/**/*.html", "apps/**/*.py"):
        for f in BASE.glob(patron):
            if f.is_file() and f.resolve() != YO:
                yield f


class ElTextoVisibleVaEnTuteoTests(SimpleTestCase):
    def test_no_hay_voseo(self):
        hallazgos = []
        for fichero in ficheros_revisados():
            for n, linea in enumerate(
                fichero.read_text(encoding="utf-8").splitlines(), start=1
            ):
                for encontrado in PATRON.findall(linea):
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
