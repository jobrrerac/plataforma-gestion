"""Tests de la legalización del día.

Lo que hay que blindar aquí son las tres reglas que sostienen el módulo: el día
tiene que cuadrar, registrar es irreversible, y un día no hábil no se legaliza.
Si cualquiera de las tres cede, el informe de facturables deja de significar
algo.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts import roles
from apps.calendar_engine import novedades as novedades_svc
from apps.core.models import Proyecto, Recurso
from apps.legalizacion import services as svc
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad


def ultimo_miercoles():
    """Un miércoles ya pasado: día hábil, jornada de 8.5 h, y no es futuro."""
    hoy = date.today()
    dias_atras = (hoy.weekday() - 2) % 7 or 7
    return hoy - timedelta(days=dias_atras)


def ultimo_viernes():
    hoy = date.today()
    dias_atras = (hoy.weekday() - 4) % 7 or 7
    return hoy - timedelta(days=dias_atras)


class BaseLegalizacion(TestCase):
    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)
        call_command("setup_actividades", verbosity=0)

    def setUp(self):
        self.ing = User.objects.create_user(
            username="ana@inetum.com", email="ana@inetum.com", password="Clave2026!"
        )
        self.ing.groups.add(Group.objects.get(name=roles.INGENIERO))
        self.recurso = Recurso.objects.create(
            nombre="Ana Perez", email="ana@inetum.com", banda="SR", usuario=self.ing
        )
        self.pm = User.objects.create_user(username="pm1", password="Clave2026!")
        self.pm.groups.add(Group.objects.get(name=roles.PM))

        self.cliente = Proyecto.objects.create(
            codigo="V-11111111/A", nombre="Proyecto Cliente", cliente="ACME",
            fecha_inicio=date(2026, 1, 1), pm=self.pm, facturable=True,
        )
        self.interno = Proyecto.objects.create(
            codigo="INT-TEST", nombre="Departamentales", cliente="Inetum",
            fecha_inicio=date(2026, 1, 1), pm=self.pm, facturable=False,
        )
        self.t_proyecto = TipoActividad.objects.get(nombre="Proyecto")
        self.t_estudio = TipoActividad.objects.get(nombre="Estudio")

        self.fecha = ultimo_miercoles()

    def _dia(self, fecha=None):
        return svc.obtener_o_crear_dia(self.recurso, fecha or self.fecha)


class JornadaDelDiaTests(BaseLegalizacion):
    def test_un_miercoles_pide_ocho_y_media(self):
        self.assertEqual(svc.jornada_esperada(self.fecha), Decimal("8.5"))

    def test_un_viernes_pide_ocho(self):
        self.assertEqual(svc.jornada_esperada(ultimo_viernes()), Decimal("8.0"))

    def test_el_fin_de_semana_no_pide_nada(self):
        sabado = self.fecha + timedelta(days=3)
        self.assertEqual(svc.jornada_esperada(sabado), Decimal("0"))


class DiasNoLegalizablesTests(BaseLegalizacion):
    def test_no_se_legaliza_el_futuro(self):
        with self.assertRaises(ValidationError):
            svc.obtener_o_crear_dia(self.recurso, date.today() + timedelta(days=1))

    def test_no_se_legaliza_el_pasado_lejano(self):
        viejo = date.today() - timedelta(days=svc.DIAS_ATRAS_MAX + 5)
        with self.assertRaises(ValidationError):
            svc.obtener_o_crear_dia(self.recurso, viejo)

    def test_no_se_legaliza_un_fin_de_semana(self):
        sabado = self.fecha + timedelta(days=3)
        with self.assertRaises(ValidationError):
            svc.obtener_o_crear_dia(self.recurso, sabado)

    def test_un_dia_con_ausencia_aprobada_no_se_legaliza(self):
        # La ausencia ya la aprobó alguien en el panel de novedades: esa es la
        # fuente de verdad, y nadie teclea sus vacaciones dos veces.
        n = novedades_svc.registrar_novedad(self.ing, self.fecha, self.fecha, "VACACION")
        admin = User.objects.create_user(username="adm", password="x")
        admin.groups.add(Group.objects.get(name=roles.ADMIN))
        novedades_svc.aprobar_novedad(n, admin)

        estado = svc.estado_del_dia(self.recurso, self.fecha)
        self.assertFalse(estado["habil"])
        self.assertEqual(estado["motivo_no_habil"], "AUSENCIA")
        self.assertEqual(estado["tipo_ausencia"], "VACACION")

        with self.assertRaises(ValidationError):
            svc.obtener_o_crear_dia(self.recurso, self.fecha)

    def test_una_ausencia_pendiente_no_libera_del_registro(self):
        # Pendiente no es aprobada: el día sigue habiendo que legalizarlo.
        novedades_svc.registrar_novedad(self.ing, self.fecha, self.fecha, "PERMISO")
        self.assertTrue(svc.estado_del_dia(self.recurso, self.fecha)["habil"])


class RenglonesTests(BaseLegalizacion):
    def test_el_detalle_es_obligatorio(self):
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.agregar_renglon(dia, self.t_estudio, 2, "   ")

    def test_proyecto_exige_indicar_cual(self):
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.agregar_renglon(dia, self.t_proyecto, 4, "Desarrollo", proyecto=None)

    def test_una_actividad_sin_proyecto_no_lo_admite(self):
        # Aceptarlo imputaría horas a un proyecto sin que nadie lo decidiera.
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.agregar_renglon(dia, self.t_estudio, 2, "Curso", proyecto=self.cliente)

    def test_las_horas_van_en_bloques_de_media(self):
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.agregar_renglon(dia, self.t_estudio, 1.3, "Algo")

    def test_no_se_puede_pasar_de_la_jornada(self):
        dia = self._dia()
        svc.agregar_renglon(dia, self.t_proyecto, 8, "Desarrollo", proyecto=self.cliente)
        with self.assertRaises(ValidationError):
            svc.agregar_renglon(dia, self.t_estudio, 2, "Curso")

    def test_varios_renglones_en_el_mismo_dia(self):
        dia = self._dia()
        svc.agregar_renglon(dia, self.t_proyecto, 6, "Desarrollo", proyecto=self.cliente)
        svc.agregar_renglon(dia, self.t_proyecto, 1.5, "Reunion", proyecto=self.interno)
        svc.agregar_renglon(dia, self.t_estudio, 1, "Curso de Django")

        datos = svc.resumen(dia)
        self.assertEqual(datos["total"], Decimal("8.5"))
        self.assertTrue(datos["cuadra"])

    def test_solo_cuentan_como_facturables_las_de_cliente(self):
        dia = self._dia()
        svc.agregar_renglon(dia, self.t_proyecto, 6, "Desarrollo", proyecto=self.cliente)
        svc.agregar_renglon(dia, self.t_proyecto, 1.5, "Gestion", proyecto=self.interno)
        svc.agregar_renglon(dia, self.t_estudio, 1, "Curso")

        datos = svc.resumen(dia)
        self.assertEqual(datos["facturables"], Decimal("6"))
        self.assertEqual(datos["no_facturables"], Decimal("2.5"))

    def test_quitar_un_renglon_es_soft_delete(self):
        dia = self._dia()
        r = svc.agregar_renglon(dia, self.t_estudio, 2, "Curso")
        svc.quitar_renglon(r)

        self.assertFalse(RegistroHoras.objects.filter(pk=r.pk).exists())
        self.assertTrue(RegistroHoras.all_objects.filter(pk=r.pk).exists())


class CierreDelDiaTests(BaseLegalizacion):
    def _cuadrar(self, dia):
        svc.agregar_renglon(dia, self.t_proyecto, 8.5, "Desarrollo", proyecto=self.cliente)

    def test_no_se_cierra_un_dia_vacio(self):
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.registrar_dia(dia, self.ing)

    def test_no_se_cierra_si_faltan_horas(self):
        dia = self._dia()
        svc.agregar_renglon(dia, self.t_proyecto, 6, "Desarrollo", proyecto=self.cliente)
        with self.assertRaises(ValidationError):
            svc.registrar_dia(dia, self.ing)

    def test_se_cierra_cuando_cuadra(self):
        dia = self._dia()
        self._cuadrar(dia)
        svc.registrar_dia(dia, self.ing)

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.REGISTRADO)
        self.assertEqual(dia.total_horas, Decimal("8.5"))
        self.assertIsNotNone(dia.registrado_en)

    def test_un_dia_registrado_ya_no_se_edita(self):
        dia = self._dia()
        self._cuadrar(dia)
        svc.registrar_dia(dia, self.ing)

        dia.refresh_from_db()
        self.assertFalse(dia.editable)
        with self.assertRaises(ValidationError):
            svc.agregar_renglon(dia, self.t_estudio, 1, "Curso")

    def test_no_se_registra_dos_veces(self):
        dia = self._dia()
        self._cuadrar(dia)
        svc.registrar_dia(dia, self.ing)
        with self.assertRaises(ValidationError):
            svc.registrar_dia(dia, self.ing)

    def test_nadie_registra_el_dia_de_otro(self):
        otro = User.objects.create_user(username="beto@inetum.com", email="beto@inetum.com")
        Recurso.objects.create(nombre="Beto", email="beto@inetum.com", banda="JR", usuario=otro)

        dia = self._dia()
        self._cuadrar(dia)
        with self.assertRaises(PermissionDenied):
            svc.registrar_dia(dia, otro)

    def test_la_jornada_del_dia_queda_congelada(self):
        # Si la jornada legal cambia después, el día cerrado conserva con qué
        # regla se cerró.
        dia = self._dia()
        self.assertEqual(dia.jornada_esperada, Decimal("8.5"))
        self._cuadrar(dia)
        svc.registrar_dia(dia, self.ing)
        dia.refresh_from_db()
        self.assertEqual(dia.jornada_esperada, Decimal("8.5"))


class DiasPendientesTests(BaseLegalizacion):
    def test_un_dia_cerrado_deja_de_estar_pendiente(self):
        pendientes_antes = svc.dias_pendientes(self.recurso)
        self.assertIn(self.fecha, pendientes_antes)

        dia = self._dia()
        svc.agregar_renglon(dia, self.t_proyecto, 8.5, "Desarrollo", proyecto=self.cliente)
        svc.registrar_dia(dia, self.ing)

        self.assertNotIn(self.fecha, svc.dias_pendientes(self.recurso))

    def test_los_findes_no_salen_como_pendientes(self):
        for fecha in svc.dias_pendientes(self.recurso):
            self.assertLess(fecha.weekday(), 5)


class PantallaTests(BaseLegalizacion):
    def test_el_ingeniero_entra(self):
        self.client.force_login(self.ing)
        self.assertEqual(self.client.get(reverse("horas")).status_code, 200)

    def test_sin_autenticar_redirige_al_login(self):
        resp = self.client.get(reverse("horas"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_es_accesible_tambien_para_un_pm(self):
        # Un PM también legaliza su tiempo.
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(reverse("horas")).status_code, 200)

    def test_guardar_y_aceptar_desde_la_pantalla(self):
        self.client.force_login(self.ing)
        url = reverse("horas")

        # Paso 1: el día entero llega en un solo POST.
        self.client.post(url, {
            "accion": "guardar", "fecha": self.fecha.isoformat(),
            "renglon_tipo": [str(self.t_proyecto.pk), str(self.t_estudio.pk)],
            "renglon_proyecto": [str(self.interno.pk), ""],
            "renglon_horas": ["6.5", "2"],
            "renglon_detalle": ["Gestion del area", "Curso de Django"],
        })
        dia = DiaLegalizado.objects.get(recurso=self.recurso, fecha=self.fecha)
        self.assertEqual(dia.registros.count(), 2)
        self.assertEqual(dia.estado, DiaLegalizado.ABIERTO, "guardar no cierra el día")

        # Paso 2: aceptar.
        self.client.post(url, {"accion": "registrar", "fecha": self.fecha.isoformat()})
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.REGISTRADO)

    def test_guardar_reemplaza_lo_anterior(self):
        # Lo que llega es el día completo, no un añadido.
        self.client.force_login(self.ing)
        url = reverse("horas")
        base = {"accion": "guardar", "fecha": self.fecha.isoformat()}

        self.client.post(url, dict(base, **{
            "renglon_tipo": [str(self.t_estudio.pk)],
            "renglon_proyecto": [""], "renglon_horas": ["3"],
            "renglon_detalle": ["Primera version"],
        }))
        self.client.post(url, dict(base, **{
            "renglon_tipo": [str(self.t_estudio.pk)],
            "renglon_proyecto": [""], "renglon_horas": ["5"],
            "renglon_detalle": ["Segunda version"],
        }))

        dia = DiaLegalizado.objects.get(recurso=self.recurso, fecha=self.fecha)
        self.assertEqual(dia.registros.count(), 1)
        self.assertEqual(dia.registros.first().detalle, "Segunda version")

    def test_no_se_puede_imputar_a_un_proyecto_no_asignado(self):
        # El formulario ya no lo ofrece, pero un POST a mano sí podría.
        self.client.force_login(self.ing)
        resp = self.client.post(reverse("horas"), {
            "accion": "guardar", "fecha": self.fecha.isoformat(),
            "renglon_tipo": [str(self.t_proyecto.pk)],
            "renglon_proyecto": [str(self.cliente.pk)],
            "renglon_horas": ["8.5"],
            "renglon_detalle": ["Trabajo inventado"],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(RegistroHoras.objects.count(), 0)

    def test_un_dia_de_vacaciones_no_ofrece_formulario(self):
        n = novedades_svc.registrar_novedad(self.ing, self.fecha, self.fecha, "VACACION")
        admin = User.objects.create_user(username="adm2", password="x")
        admin.groups.add(Group.objects.get(name=roles.ADMIN))
        novedades_svc.aprobar_novedad(n, admin)

        self.client.force_login(self.ing)
        html = self.client.get(f"{reverse('horas')}?fecha={self.fecha.isoformat()}").content.decode()
        self.assertIn("no laborable", html)
        self.assertNotIn('name="detalle"', html)


class DashboardAcotadoTests(BaseLegalizacion):
    """Un ingeniero ve su ocupación, no la del equipo."""

    def setUp(self):
        super().setUp()
        self.recurso.activo = True
        self.recurso.save(update_fields=["activo"])
        Recurso.objects.create(nombre="Otro Recurso", email="otro@inetum.com", banda="JR")

    def _recursos_visibles(self, usuario):
        self.client.force_login(usuario)
        datos = self.client.get("/api/dashboard/ocupacion/").json()
        return {r["nombre"] for r in datos["recursos"]}

    def test_el_ingeniero_solo_se_ve_a_si_mismo(self):
        self.assertEqual(self._recursos_visibles(self.ing), {"Ana Perez"})

    def test_el_pm_ve_a_todo_el_mundo(self):
        self.assertIn("Otro Recurso", self._recursos_visibles(self.pm))


class ProyectosDisponiblesTests(BaseLegalizacion):
    """Cada quien ve los proyectos que tenía asignados ESE día."""

    def _asignar(self, proyecto, inicio, fin, estado="APROBADA"):
        from apps.assignments.models import Asignacion

        return Asignacion.objects.create(
            recurso=self.recurso, proyecto=proyecto,
            fecha_inicio=inicio, fecha_fin=fin,
            horas_totales=34, intensidad_diaria=8.5,
            estado=estado, solicitada_por=self.pm,
        )

    def _codigos(self, fecha=None):
        return {p.codigo for p in svc.proyectos_disponibles(self.recurso, fecha or self.fecha)}

    def test_sin_asignacion_no_aparece_el_de_cliente(self):
        # Ofrecer la lista completa invitaría a imputar horas a proyectos en
        # los que nunca se estuvo, que es lo que el módulo viene a evitar.
        self.assertNotIn(self.cliente.codigo, self._codigos())

    def test_con_asignacion_que_cubre_el_dia_si_aparece(self):
        self._asignar(self.cliente, self.fecha, self.fecha)
        self.assertIn(self.cliente.codigo, self._codigos())

    def test_una_asignacion_de_otras_fechas_no_cuenta(self):
        futuro = self.fecha + timedelta(days=20)
        self._asignar(self.cliente, futuro, futuro)
        self.assertNotIn(self.cliente.codigo, self._codigos())

    def test_una_asignacion_sin_aprobar_no_habilita_el_proyecto(self):
        self._asignar(self.cliente, self.fecha, self.fecha, estado="SOLICITADA")
        self.assertNotIn(self.cliente.codigo, self._codigos())

    def test_los_internos_estan_siempre(self):
        # Nadie recibe una asignación a «Departamentales», y sin ellos alguien
        # en bench no podría completar la jornada — y como el día no cierra si
        # no cuadra, se quedaría bloqueado sin salida.
        self.assertIn(self.interno.codigo, self._codigos())

    def test_alguien_sin_ninguna_asignacion_puede_cerrar_su_dia(self):
        self.assertTrue(
            svc.proyectos_disponibles(self.recurso, self.fecha).exists(),
            "siempre debe quedar algo con lo que cuadrar el día",
        )


class GuardadoEnLoteTests(BaseLegalizacion):
    def test_se_valida_todo_antes_de_escribir_nada(self):
        # Un día medio guardado es peor que uno sin guardar: parece completo.
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.guardar_renglones(dia, [
                {"tipo_actividad": self.t_estudio, "proyecto": None,
                 "horas": "2", "detalle": "Curso"},
                {"tipo_actividad": self.t_proyecto, "proyecto": None,
                 "horas": "3", "detalle": "Sin proyecto"},
            ])
        self.assertEqual(dia.registros.count(), 0)

    def test_no_se_admite_una_lista_vacia(self):
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.guardar_renglones(dia, [])

    def test_no_se_puede_pasar_de_la_jornada(self):
        dia = self._dia()
        with self.assertRaises(ValidationError):
            svc.guardar_renglones(dia, [
                {"tipo_actividad": self.t_estudio, "proyecto": None,
                 "horas": "9", "detalle": "Curso larguisimo"},
            ])

    def test_guardar_no_es_aceptar(self):
        # Guardar deja el día abierto: primero se ve el resumen.
        dia = self._dia()
        svc.guardar_renglones(dia, [
            {"tipo_actividad": self.t_estudio, "proyecto": None,
             "horas": "8.5", "detalle": "Curso"},
        ])
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.ABIERTO)
        self.assertTrue(dia.editable)
