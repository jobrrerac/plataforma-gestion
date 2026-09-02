from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


class Command(BaseCommand):
    help = "Crea los grupos Admin, PM, Ingeniero y Visor con sus permisos base."

    def handle(self, *args, **options):
        # Permisos por app/modelo
        perms_admin = self._perms([
            ("assignments", "asignacion",    ["add", "change", "delete", "view"]),
            ("assignments", "logauditoria",  ["view"]),
            ("core",        "recurso",       ["add", "change", "delete", "view"]),
            ("core",        "proyecto",      ["add", "change", "delete", "view"]),
            ("core",        "skill",         ["add", "change", "delete", "view"]),
            ("core",        "cluster",       ["add", "change", "delete", "view"]),
            ("core",        "tarifavigente", ["add", "change", "delete", "view"]),
            ("calendar_engine", "dianolaborable",    ["add", "change", "delete", "view"]),
            ("calendar_engine", "indisponibilidad",  ["add", "change", "delete", "view"]),
            ("auth",        "user",          ["add", "change", "delete", "view"]),
            ("auth",        "group",         ["add", "change", "delete", "view"]),
            ("legalizacion", "tipoactividad", ["add", "change", "delete", "view"]),
        ])

        perms_pm = self._perms([
            ("assignments", "asignacion",   ["add", "view"]),
            ("assignments", "logauditoria", ["view"]),
            ("core",        "recurso",      ["view"]),
            ("core",        "proyecto",     ["view"]),
            ("core",        "skill",        ["view"]),
            ("core",        "cluster",      ["view"]),
            ("calendar_engine", "indisponibilidad", ["add", "change", "delete", "view"]),
        ])

        perms_ingeniero = self._perms([
            ("assignments", "asignacion",   ["view"]),
            ("core",        "recurso",      ["view"]),
            ("core",        "proyecto",     ["view"]),
            # Registra sus propias novedades (vacaciones y permisos). El alcance
            # "solo las suyas" no lo da el permiso de Django, que es por modelo:
            # lo imponen la vista y el ViewSet, que filtran por el recurso
            # vinculado a la cuenta. Sin `delete`: cancelar una novedad pendiente
            # es un soft-delete que pasa por el servicio, no un borrado directo.
            ("calendar_engine", "indisponibilidad", ["add", "view"]),
            # Catalogo de actividades: lo consulta al legalizar sus horas.
            ("legalizacion", "tipoactividad", ["view"]),
        ])

        # Solo `view`, en todo. El Visor supervisa: ve la operacion completa
        # —incluidas tarifas y costos— y no escribe en ninguna parte. Tampoco es
        # staff, asi que ni siquiera llega al /admin/; estos permisos existen
        # para que la API en modo lectura le responda.
        perms_visor = self._perms([
            ("assignments", "asignacion",    ["view"]),
            ("assignments", "logauditoria",  ["view"]),
            ("core",        "recurso",       ["view"]),
            ("core",        "proyecto",      ["view"]),
            ("core",        "skill",         ["view"]),
            ("core",        "cluster",       ["view"]),
            ("core",        "tarifavigente", ["view"]),
            ("calendar_engine", "dianolaborable",   ["view"]),
            ("calendar_engine", "indisponibilidad", ["view"]),
            ("legalizacion", "tipoactividad", ["view"]),
        ])

        grupos = {
            "Admin":     perms_admin,
            "PM":        perms_pm,
            "Ingeniero": perms_ingeniero,
            "Visor":     perms_visor,
        }

        for nombre, perms in grupos.items():
            grupo, creado = Group.objects.get_or_create(name=nombre)
            grupo.permissions.set(perms)
            estado = "creado" if creado else "actualizado"
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ {nombre} {estado} ({len(perms)} permisos)")
            )

        self.stdout.write(self.style.SUCCESS("\nGrupos listos. Asigna usuarios en /admin/auth/user/"))

    @staticmethod
    def _perms(spec):
        result = []
        for app, model, acciones in spec:
            try:
                ct = ContentType.objects.get(app_label=app, model=model)
            except ContentType.DoesNotExist:
                continue
            for accion in acciones:
                try:
                    result.append(Permission.objects.get(content_type=ct, codename=f"{accion}_{model}"))
                except Permission.DoesNotExist:
                    pass
        return result
