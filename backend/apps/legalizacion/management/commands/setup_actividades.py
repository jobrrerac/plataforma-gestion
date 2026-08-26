from django.core.management.base import BaseCommand

from apps.legalizacion.models import TipoActividad

# Catalogo inicial. Solo "Proyecto" exige indicar a cual: el resto son
# actividades que no cuelgan de ningun proyecto.
#
# Lo que en el Excel de cargables aparecia como DEPARTAMENTALES o MANAGMENT NO
# esta aqui a proposito: son proyectos internos y hay que darlos de alta como
# proyectos, para que la clave foranea garantice que siempre signifiquen lo
# mismo en vez de convertirse en variantes escritas a mano.
ACTIVIDADES = [
    ("Proyecto", True, 10),
    ("Formacion", False, 20),
    ("Estudio", False, 30),
    ("Entrenamiento", False, 40),
]


class Command(BaseCommand):
    help = "Crea o actualiza el catalogo de tipos de actividad."

    def handle(self, *args, **options):
        for nombre, requiere_proyecto, orden in ACTIVIDADES:
            actividad, creado = TipoActividad.objects.update_or_create(
                nombre=nombre,
                defaults={"requiere_proyecto": requiere_proyecto, "orden": orden},
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
