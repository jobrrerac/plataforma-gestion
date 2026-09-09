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

            # Estas cinco pantallas existian en el admin pero ningun Admin las
            # veia: solo el superusuario, que se salta los permisos y por eso
            # nadie lo noto. Un Admin de verdad entraba a /admin/ y le faltaban
            # las cesiones, las liberaciones, los dias legalizados y —lo mas
            # absurdo— el listado de firmas forzadas, que se construyo
            # precisamente para que alguien lo leyera.
            #
            # Solo `view` donde el ModelAdmin bloquea escribir. Dar `change` de
            # un modelo que la pantalla no deja cambiar no habilita nada y
            # miente sobre lo que el rol puede hacer.
            ("assignments",  "cesionhoras",             ["view"]),
            ("assignments",  "liberacionrecurso",       ["view"]),
            # Las horas y sus dias son de solo lectura en el admin. Aqui hubo
            # `add/change/delete` mientras el inline de renglones era editable,
            # que era la forma de corregir un dia ya firmado: sin autor, sin
            # motivo, sin copia de lo que decia antes, y borrando de paso la
            # firma del PM. Eso ahora se hace reabriendo el dia, que deja
            # constancia de las tres cosas.
            #
            # Se quedan en `view` a la vez que las clases del admin niegan
            # escribir. Ninguna de las dos cosas sobra: el permiso no frena a un
            # superusuario, y la clase sola dejaria un permiso concedido que no
            # habilita nada, listo para volver a abrir el agujero sin querer.
            ("legalizacion", "registrohoras",           ["view"]),
            ("legalizacion", "dialegalizado",           ["view"]),
            # Resolver un cambio de contrasena pendiente si es una accion de
            # Admin que se hace desde el admin.
            ("accounts",     "cambiopasswordpendiente", ["change", "view"]),
            # Solo `view`: la pantalla es de solo lectura y el modelo es
            # append-only. Dar `change` mentiria sobre lo que se puede hacer.
            ("legalizacion", "reaperturadia",           ["view"]),
            # El unico sitio del modulo donde el Admin escribe: mover la fecha
            # desde la que se reclaman dias sin registrar, sin desplegar. Sin
            # `add` ni `delete` porque es una fila unica.
            ("legalizacion", "parametroslegalizacion",  ["change", "view"]),
            # Seguimiento. El Admin corrige y borra —en blando— porque es quien
            # limpia un bloqueante mal abierto o una observacion duplicada. Los
            # dos modelos se operan desde su pantalla, no desde el admin; esto
            # es para el mantenimiento y para leer el historico.
            ("seguimiento",  "bloqueante",              ["add", "change", "delete", "view"]),
            ("seguimiento",  "feedback",                ["add", "change", "delete", "view"]),
            ("seguimiento",  "acciondeseguimiento",     ["add", "change", "delete", "view"]),
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
