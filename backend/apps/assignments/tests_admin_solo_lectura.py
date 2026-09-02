"""Abrir una asignación con permiso de solo ver no puede reventar.

Lo reportó Carmen pulsando «Editar» en el listado del admin: `KeyError:
'horas_totales'`, página en blanco con el traceback.

La causa no tenía nada que ver con su rol. Cuando alguien tiene `view` pero no
`change`, el admin de Django construye el formulario **sin ningún campo
editable** —los pinta todos como texto— y `AsignacionForm.__init__` daba por
hecho que `horas_totales`, `dias_habiles` e `intensidad_diaria` estaban ahí.

Le pasaba a cualquiera en esa situación: un Ingeniero, un Visor, o un PM
mirando una asignación que no puede tocar. Un 500 en una pantalla de consulta.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso


class AdminConSoloLecturaTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm_ro", "pm@test.com", "clave-larga-123")
        proyecto = Proyecto.objects.create(
            codigo="V-25188808/Q", nombre="Simulador", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
        )
        recurso = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando", email="daniel@test.com", banda="SR",
        )
        self.asignacion = Asignacion.objects.create(
            recurso=recurso, proyecto=proyecto, modo_asignacion="RANGO",
            fecha_inicio=date(2026, 9, 14), fecha_fin=date(2026, 9, 17),
            dias_habiles=4, horas_totales=34, intensidad_diaria=Decimal("8.5"),
            estado="APROBADA", solicitada_por=self.pm,
        )

        # Staff con `view` y sin `change`: es la combinación que rompía.
        self.mirón = User.objects.create_user(
            "miron", "miron@test.com", "clave-larga-123", is_staff=True,
        )
        grupo = Group.objects.create(name="SoloVer")
        grupo.permissions.add(Permission.objects.get(codename="view_asignacion"))
        self.mirón.groups.add(grupo)
        self.client.force_login(self.mirón)

    def _url(self):
        return f"/admin/assignments/asignacion/{self.asignacion.pk}/change/"

    def test_la_pantalla_abre_en_vez_de_reventar(self):
        respuesta = self.client.get(self._url())
        self.assertEqual(
            respuesta.status_code, 200,
            "abrir una asignación con permiso de solo ver devolvió "
            f"{respuesta.status_code}",
        )

    def test_se_ven_los_datos_aunque_no_se_puedan_editar(self):
        """Si no se viera nada, la pantalla no reventaria pero tampoco serviria."""
        respuesta = self.client.get(self._url())
        contenido = respuesta.content.decode()
        self.assertIn("V-25188808/Q", contenido)
        self.assertIn("Guzman-Mejia Daniel-Fernando", contenido)

    def test_un_admin_sigue_viendo_los_campos_editables(self):
        """El arreglo no puede dejar sin formulario a quien si puede editar.

        `has_change_permission` exige rol Admin, asi que el formulario completo
        solo lo ve un Admin. De ahi que el KeyError alcanzara a todos los demas:
        PM, Ingeniero y Visor pulsaban Editar y se llevaban un 500.
        """
        from apps.accounts.roles import ADMIN

        admin = User.objects.create_user(
            "admin_ro", "admin@test.com", "clave-larga-123", is_staff=True,
        )
        admin.groups.add(Group.objects.get_or_create(name=ADMIN)[0])
        self.client.force_login(admin)

        respuesta = self.client.get(self._url())
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('name="horas_totales"', respuesta.content.decode())

    def test_un_pm_tambien_puede_abrirla(self):
        """Es el caso reportado: Carmen era PM, no Admin."""
        from apps.accounts.roles import PM

        self.mirón.groups.add(Group.objects.get_or_create(name=PM)[0])
        self.assertEqual(self.client.get(self._url()).status_code, 200)
