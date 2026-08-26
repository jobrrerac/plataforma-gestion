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
# La descripcion no es decorativa: tres categorias parecidas sin una frase que
# las separe se rellenan al azar, y entonces el informe de en que se va el
# tiempo deja de significar nada. Cada una responde a una pregunta distinta:
#
#   Formacion      ¿hay temario y alguien que lo imparte?
#   Estudio        ¿lo hiciste por tu cuenta, sin programa?
#   Entrenamiento  ¿fue practica en el puesto, con acompanamiento?
ACTIVIDADES = [
    (
        "Proyecto", True, 10,
        "Trabajo imputable a un proyecto concreto, de cliente o interno. "
        "Indica cual y que hiciste ese dia.",
    ),
    (
        "Formacion", False, 20,
        "Curso, certificacion o taller con temario definido, dentro o fuera de "
        "Inetum. Hay un programa y alguien que lo imparte.",
    ),
    (
        "Estudio", False, 30,
        "Aprendizaje por tu cuenta, sin programa: leer documentacion, investigar "
        "una tecnologia o preparar una prueba de concepto.",
    ),
    (
        "Entrenamiento", False, 40,
        "Practica en el puesto con acompanamiento: transferencia de conocimiento "
        "de un companero, o puesta a punto en las herramientas de un proyecto.",
    ),
]


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

        self.stdout.write(
            self.style.SUCCESS(
                "\nCatalogo listo. Los proyectos internos se dan de alta como "
                "proyectos normales, marcando facturable = False."
            )
        )
