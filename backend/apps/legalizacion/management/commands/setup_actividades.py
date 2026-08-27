from django.core.management.base import BaseCommand

from apps.legalizacion.models import TipoActividad

# Catalogo inicial. Solo "Proyecto" exige indicar a cual: el resto son
# actividades que no cuelgan de ningun proyecto.
#
# Lo que en el Excel de cargables aparecia como DEPARTAMENTALES o MANAGMENT NO
# esta aqui a proposito: son proyectos internos y hay que darlos de alta como
# proyectos, para que la clave foranea garantice que siempre signifiquen lo
# mismo en vez de convertirse en variantes escritas a mano.
#
# Solo dos actividades sin proyecto, y la linea que las separa es una sola
# pregunta: ¿habia alguien ensenandote?
#
#   Entrenamiento  si — curso, certificacion, taller o acompanamiento
#   Estudio        no — lo sacaste por tu cuenta
#
# "Formacion" se retiro: se solapaba tanto con "Entrenamiento" que la gente
# habria elegido entre las dos al azar, y dos cajones que se rellenan al azar
# no permiten responder en que se fue el tiempo. Entrenamiento absorbe lo que
# antes era formacion.
ACTIVIDADES = [
    (
        "Proyecto", True, 10,
        "Trabajo imputable a un proyecto concreto, de cliente o interno. "
        "Indica cual y que hiciste ese dia.",
    ),
    (
        "Entrenamiento", False, 20,
        "Alguien te formo: curso, certificacion, taller, o acompanamiento de un "
        "companero para ponerte a punto.",
    ),
    (
        "Estudio", False, 30,
        "Lo sacaste por tu cuenta, sin nadie ensenando: leer documentacion, "
        "investigar una tecnologia o preparar una prueba de concepto.",
    ),
]

# Actividades retiradas del catalogo. Se DESACTIVAN en vez de borrarse, para no
# perder las horas que ya se imputaron a ellas. Se listan aqui explicitamente y
# no se deduce "todo lo que no este en ACTIVIDADES", porque eso desactivaria
# tambien las que un Admin haya creado a mano.
RETIRADAS = ["Formacion"]


class Command(BaseCommand):
    help = "Crea o actualiza el catalogo de tipos de actividad."

    def handle(self, *args, **options):
        for nombre, requiere_proyecto, orden, descripcion in ACTIVIDADES:
            actividad, creado = TipoActividad.objects.update_or_create(
                nombre=nombre,
                defaults={
                    "requiere_proyecto": requiere_proyecto,
                    "orden": orden,
                    "descripcion": descripcion,
                },
            )
            estado = "creado" if creado else "actualizado"
            marca = "con proyecto" if requiere_proyecto else "sin proyecto"
            self.stdout.write(self.style.SUCCESS(f"  ✓ {nombre} {estado} ({marca})"))

        retiradas = TipoActividad.objects.filter(nombre__in=RETIRADAS, activo=True)
        for actividad in retiradas:
            actividad.activo = False
            actividad.save(update_fields=["activo"])
            self.stdout.write(
                self.style.WARNING(f"  – {actividad.nombre} retirada (se conserva el historico)")
            )

        self.stdout.write(
            self.style.SUCCESS(
                "\nCatalogo listo. Los proyectos internos se dan de alta como "
                "proyectos normales, marcando facturable = False."
            )
        )
