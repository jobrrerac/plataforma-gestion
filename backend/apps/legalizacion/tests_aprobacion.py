"""Tests de la aprobación de horas.

La unidad de aprobación es la **actividad**, no el día. Lo que hay que blindar:
que cada PM firme lo suyo y solo lo suyo, que el Admin sirva de válvula cuando
no hay PM que reclame un renglón, y que devolver una actividad no arrastre a las
que ya firmó otro.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts import roles
from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion import services as svc
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad


def ultimo_miercoles():
    hoy = date.today()
    dias_atras = (hoy.weekday() - 2) % 7 or 7
    return hoy - timedelta(days=dias_atras)


class BaseAprobacion(TestCase):
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

        self.pm = User.objects.create_user(username="pm.suyo", password="Clave2026!")
        self.pm.groups.add(Group.objects.get(name=roles.PM))
        self.otro_pm = User.objects.create_user(username="pm.ajeno", password="Clave2026!")
        self.otro_pm.groups.add(Group.objects.get(name=roles.PM))

        self.admin = User.objects.create_user(username="admin1", password="Clave2026!")
        self.admin.groups.add(Group.objects.get(name=roles.ADMIN))

        self.fecha = ultimo_miercoles()

        self.proyecto = self._proyecto("V-22222222/B", "Proyecto Cliente", self.pm)
        self.proyecto_ajeno = self._proyecto("V-33333333/B", "Otro Cliente", self.otro_pm)

        self.t_proyecto = TipoActividad.objects.get(nombre="Proyecto")
        self.t_estudio = TipoActividad.objects.get(nombre="Estudio")

    def _proyecto(self, codigo, nombre, pm):
        proyecto = Proyecto.objects.create(
            codigo=codigo, nombre=nombre, cliente="ACME",
            fecha_inicio=date(2026, 1, 1), pm=pm, facturable=True,
        )
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=proyecto,
            fecha_inicio=self.fecha, fecha_fin=self.fecha,
            horas_totales=34, intensidad_diaria=8.5,
            estado="APROBADA", solicitada_por=pm,
        )
        return proyecto

    def _dia_registrado(self, con_proyecto=True):
        """Un día cerrado con una sola actividad de 8.5 h."""
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        if con_proyecto:
            svc.agregar_renglon(dia, self.t_proyecto, 8.5, "Desarrollo", proyecto=self.proyecto)
        else:
            svc.agregar_renglon(dia, self.t_estudio, 8.5, "Curso de Django")
        return svc.registrar_dia(dia, self.ing)

    def _dia_repartido(self):
        """El caso que motivó todo: 4 h de proyecto y 4.5 h internas.

        Cada mitad la firma alguien distinto, y antes no había forma de hacerlo:
        el día se aprobaba entero o no se aprobaba.
        """
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 4, "Desarrollo", proyecto=self.proyecto)
        svc.agregar_renglon(dia, self.t_estudio, 4.5, "Formación interna")
        return svc.registrar_dia(dia, self.ing)

    def _dia_dos_proyectos(self):
        """Dos proyectos de PM distintos en la misma jornada."""
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 4, "Cliente A", proyecto=self.proyecto)
        svc.agregar_renglon(dia, self.t_proyecto, 4.5, "Cliente B", proyecto=self.proyecto_ajeno)
        return svc.registrar_dia(dia, self.ing)

    def _renglon(self, dia, proyecto=None, tipo=None):
        qs = dia.registros.all()
        if proyecto is not None:
            return qs.get(proyecto=proyecto)
        return qs.get(tipo_actividad=tipo)


class AlcanceDeLaColaTests(BaseAprobacion):
    def test_el_pm_solo_ve_los_renglones_de_sus_proyectos(self):
        self._dia_repartido()
        pendientes = list(svc.registros_por_aprobar(self.pm))
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0].proyecto, self.proyecto)

    def test_el_pm_no_ve_la_actividad_interna(self):
        # Es el nudo del asunto: un PM no tiene forma de valorar las horas de
        # formación de nadie, así que no debe poder firmarlas.
        self._dia_repartido()
        tipos = {r.tipo_actividad.nombre for r in svc.registros_por_aprobar(self.pm)}
        self.assertNotIn("Estudio", tipos)

    def test_cada_pm_ve_solo_su_proyecto(self):
        self._dia_dos_proyectos()
        self.assertEqual(
            [r.proyecto for r in svc.registros_por_aprobar(self.pm)], [self.proyecto]
        )
        self.assertEqual(
            [r.proyecto for r in svc.registros_por_aprobar(self.otro_pm)], [self.proyecto_ajeno]
        )

    def test_el_admin_lo_ve_todo(self):
        self._dia_repartido()
        self.assertEqual(len(list(svc.registros_por_aprobar(self.admin))), 2)

    def test_una_actividad_sin_proyecto_solo_la_ve_el_admin(self):
        self._dia_registrado(con_proyecto=False)
        self.assertEqual(len(list(svc.registros_por_aprobar(self.admin))), 1)
        self.assertEqual(len(list(svc.registros_por_aprobar(self.pm))), 0)

    def test_un_ingeniero_no_aprueba_nada(self):
        self._dia_registrado()
        self.assertEqual(len(list(svc.registros_por_aprobar(self.ing))), 0)

    def test_un_dia_abierto_no_esta_en_la_cola(self):
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 8.5, "Desarrollo", proyecto=self.proyecto)
        self.assertEqual(len(list(svc.registros_por_aprobar(self.admin))), 0)

    def test_la_cola_agrupa_por_dia_separando_lo_propio(self):
        self._dia_repartido()
        dias = svc.dias_por_aprobar(self.pm)
        self.assertEqual(len(dias), 1)
        # Ve la jornada entera para tener contexto, pero solo firma la suya.
        self.assertEqual(len(dias[0].pendientes_mios), 1)
        self.assertEqual(len(dias[0].otros), 1)


class AprobarTests(BaseAprobacion):
    def test_el_pm_firma_su_renglon_y_queda_constancia(self):
        dia = self._dia_repartido()
        renglon = svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        self.assertEqual(renglon.estado, RegistroHoras.APROBADO)
        self.assertEqual(renglon.aprobado_por, self.pm)
        self.assertIsNotNone(renglon.aprobado_en)

    def test_aprobar_una_actividad_no_aprueba_el_dia(self):
        # La regresión que se venía a arreglar: firmar 4 h de proyecto daba por
        # buenas también las 4.5 h internas, que ese PM no había mirado.
        dia = self._dia_repartido()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.REGISTRADO)
        interna = self._renglon(dia, tipo=self.t_estudio)
        self.assertEqual(interna.estado, RegistroHoras.PENDIENTE)

    def test_el_dia_se_aprueba_cuando_lo_estan_todas(self):
        dia = self._dia_repartido()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        svc.aprobar_registro(self._renglon(dia, tipo=self.t_estudio), self.admin)

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)

    def test_dos_pm_firman_cada_uno_lo_suyo(self):
        dia = self._dia_dos_proyectos()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto_ajeno), self.otro_pm)

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)

    def test_un_pm_no_puede_firmar_el_proyecto_de_otro(self):
        dia = self._dia_dos_proyectos()
        with self.assertRaises(PermissionDenied):
            svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto_ajeno), self.pm)

    def test_un_pm_no_puede_firmar_una_actividad_sin_proyecto(self):
        dia = self._dia_repartido()
        with self.assertRaises(PermissionDenied) as ctx:
            svc.aprobar_registro(self._renglon(dia, tipo=self.t_estudio), self.pm)
        self.assertIn("administrador", str(ctx.exception))

    def test_el_admin_puede_firmar_en_lugar_del_pm(self):
        dia = self._dia_repartido()
        renglon = svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.admin)
        self.assertEqual(renglon.aprobado_por, self.admin)

    def test_el_ingeniero_no_se_aprueba_a_si_mismo(self):
        dia = self._dia_registrado()
        with self.assertRaises(PermissionDenied):
            svc.aprobar_registro(dia.registros.first(), self.ing)

    def test_no_se_aprueba_dos_veces(self):
        dia = self._dia_registrado()
        renglon = dia.registros.first()
        svc.aprobar_registro(renglon, self.pm)
        with self.assertRaises(ValidationError):
            svc.aprobar_registro(renglon, self.pm)

    def test_el_error_de_aprobar_dos_veces_dice_la_verdad(self):
        # Mezclar alcance y estado producía un mensaje mentiroso: al PM
        # legítimo se le decía que el proyecto no era suyo.
        dia = self._dia_registrado()
        renglon = dia.registros.first()
        svc.aprobar_registro(renglon, self.pm)
        with self.assertRaises(ValidationError) as ctx:
            svc.aprobar_registro(renglon, self.pm)
        self.assertIn("ya está aprobada", str(ctx.exception))

    def test_no_se_aprueba_una_actividad_de_un_dia_abierto(self):
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 8.5, "Desarrollo", proyecto=self.proyecto)
        with self.assertRaises(ValidationError):
            svc.aprobar_registro(dia.registros.first(), self.pm)


class DevolverTests(BaseAprobacion):
    def test_devolver_una_actividad_reabre_el_dia(self):
        dia = self._dia_repartido()
        svc.devolver_registro(self._renglon(dia, proyecto=self.proyecto), self.pm, "Falta el ticket")

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.ABIERTO)
        self.assertTrue(dia.editable)

    def test_devolver_no_toca_lo_que_ya_firmo_otro(self):
        # El motivo de bajar la aprobación al renglón: que devolver una
        # actividad no obligue a rehacer trabajo ya validado por otro PM.
        dia = self._dia_dos_proyectos()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        svc.devolver_registro(
            self._renglon(dia, proyecto=self.proyecto_ajeno), self.otro_pm, "Detalle insuficiente"
        )

        firmada = self._renglon(dia, proyecto=self.proyecto)
        self.assertEqual(firmada.estado, RegistroHoras.APROBADO)
        self.assertEqual(firmada.aprobado_por, self.pm)

    def test_el_motivo_es_obligatorio(self):
        dia = self._dia_registrado()
        with self.assertRaises(ValidationError):
            svc.devolver_registro(dia.registros.first(), self.pm, "   ")

    def test_el_motivo_llega_a_quien_lo_registro(self):
        dia = self._dia_registrado()
        svc.devolver_registro(dia.registros.first(), self.pm, "Falta el detalle del ticket")

        dia.refresh_from_db()
        self.assertIn("ticket", dia.motivo_devolucion)
        self.assertIn("ticket", dia.registros.first().motivo_devolucion)

    def test_un_pm_ajeno_no_puede_devolver(self):
        dia = self._dia_dos_proyectos()
        with self.assertRaises(PermissionDenied):
            svc.devolver_registro(self._renglon(dia, proyecto=self.proyecto_ajeno), self.pm, "no")

    def test_lo_aprobado_no_se_puede_editar_al_corregir(self):
        """La actividad firmada sobrevive a que se reescriba el día."""
        dia = self._dia_dos_proyectos()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        svc.devolver_registro(
            self._renglon(dia, proyecto=self.proyecto_ajeno), self.otro_pm, "Corrige el detalle"
        )

        dia.refresh_from_db()
        svc.guardar_renglones(dia, [{
            "tipo_actividad": self.t_estudio, "proyecto": None,
            "horas": "4.5", "detalle": "Formación, ya corregido",
        }])

        estados = sorted(r.estado for r in dia.registros.all())
        self.assertEqual(estados, [RegistroHoras.APROBADO, RegistroHoras.PENDIENTE])
        self.assertTrue(dia.registros.filter(proyecto=self.proyecto, estado="APROBADO").exists())

    def test_al_corregir_no_se_puede_pasar_de_jornada_contando_lo_aprobado(self):
        dia = self._dia_dos_proyectos()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)  # 4 h firmadas
        svc.devolver_registro(
            self._renglon(dia, proyecto=self.proyecto_ajeno), self.otro_pm, "Corrige"
        )
        dia.refresh_from_db()

        with self.assertRaises(ValidationError) as ctx:
            svc.guardar_renglones(dia, [{
                "tipo_actividad": self.t_estudio, "proyecto": None,
                "horas": "8.5", "detalle": "Demasiadas",
            }])
        self.assertIn("aprobadas", str(ctx.exception))

    def test_al_reenviar_lo_corregido_vuelve_a_la_cola_y_lo_firmado_no(self):
        dia = self._dia_dos_proyectos()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        svc.devolver_registro(
            self._renglon(dia, proyecto=self.proyecto_ajeno), self.otro_pm, "Corrige"
        )
        dia.refresh_from_db()
        svc.guardar_renglones(dia, [{
            "tipo_actividad": self.t_estudio, "proyecto": None,
            "horas": "4.5", "detalle": "Corregido",
        }])
        svc.registrar_dia(dia, self.ing)

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.REGISTRADO)
        self.assertEqual(dia.motivo_devolucion, "")
        # La firma del primer PM sigue en pie: no tiene que volver a aprobar.
        self.assertNotIn(
            self._renglon(dia, proyecto=self.proyecto).pk,
            [r.pk for r in svc.registros_por_aprobar(self.pm)],
        )

    def test_si_solo_quedaba_lo_aprobado_el_dia_nace_aprobado(self):
        dia = self._dia_repartido()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        svc.devolver_registro(self._renglon(dia, tipo=self.t_estudio), self.admin, "Rehazlo")
        dia.refresh_from_db()

        svc.guardar_renglones(dia, [{
            "tipo_actividad": self.t_estudio, "proyecto": None,
            "horas": "4.5", "detalle": "Rehecho",
        }])
        svc.registrar_dia(dia, self.ing)
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.REGISTRADO)


class PantallaAprobacionTests(BaseAprobacion):
    def test_el_pm_entra(self):
        self.client.force_login(self.pm)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 200)

    def test_el_ingeniero_recibe_403(self):
        self.client.force_login(self.ing)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 403)

    def test_aprobar_desde_la_pantalla(self):
        dia = self._dia_repartido()
        renglon = self._renglon(dia, proyecto=self.proyecto)
        self.client.force_login(self.pm)
        resp = self.client.post(reverse("horas-aprobar"), {
            "accion": "aprobar", "registro": renglon.pk,
        })
        self.assertRedirects(resp, reverse("horas-aprobar"), fetch_redirect_response=False)
        renglon.refresh_from_db()
        self.assertEqual(renglon.estado, RegistroHoras.APROBADO)

    def test_devolver_desde_la_pantalla(self):
        dia = self._dia_repartido()
        renglon = self._renglon(dia, proyecto=self.proyecto)
        self.client.force_login(self.pm)
        self.client.post(reverse("horas-aprobar"), {
            "accion": "devolver", "registro": renglon.pk, "motivo": "Falta detalle",
        })
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.ABIERTO)

    def test_el_pm_no_ve_boton_para_la_actividad_interna(self):
        self._dia_repartido()
        self.client.force_login(self.pm)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertIn("no te corresponde firmarlo", html)

    def test_la_cola_muestra_el_desglose_no_solo_el_total(self):
        self._dia_repartido()
        self.client.force_login(self.admin)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertIn("Formación interna", html)
        self.assertIn("Desarrollo", html)


class FacturablesTests(BaseAprobacion):
    def test_el_resumen_separa_facturables_de_lo_que_no(self):
        dia = self._dia_repartido()
        datos = svc.resumen(dia)
        self.assertEqual(datos["facturables"], Decimal("4.0"))
        self.assertEqual(datos["no_facturables"], Decimal("4.5"))

    def test_el_resumen_distingue_lo_ya_firmado(self):
        dia = self._dia_repartido()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        datos = svc.resumen(dia)
        self.assertEqual(datos["aprobadas"], Decimal("4.0"))
        self.assertEqual(len(datos["pendientes"]), 1)


class AprobadorDelegadoTests(BaseAprobacion):
    """Un proyecto puede designar a alguien que apruebe sus horas sin ser PM.

    El punto del diseño: hasta ahora la autorización era **primero el rol** y
    después el alcance, así que un ingeniero designado moría en el primer
    filtro. Ahora la designación en el proyecto **es** la autorización.

    Lo que NO le da: costos, tarifas, ni ninguna otra pantalla. Eso lo sigue
    decidiendo `roles.py`, y esto no lo toca.
    """

    def setUp(self):
        super().setUp()
        # Un ingeniero cualquiera, sin rol de PM ni de Admin.
        self.delegado = User.objects.create_user(username="delegado.ing", password="Clave2026!")
        self.delegado.groups.add(Group.objects.get(name=roles.INGENIERO))
        self.proyecto.aprobador_delegado = self.delegado
        self.proyecto.save(update_fields=["aprobador_delegado"])

    def test_el_delegado_puede_aprobar_aunque_sea_ingeniero(self):
        dia = self._dia_repartido()
        renglon = self._renglon(dia, proyecto=self.proyecto)
        svc.aprobar_registro(renglon, self.delegado)
        renglon.refresh_from_db()
        self.assertEqual(renglon.estado, RegistroHoras.APROBADO)
        self.assertEqual(renglon.aprobado_por, self.delegado)

    def test_el_delegado_solo_alcanza_su_proyecto(self):
        dia = self._dia_dos_proyectos()
        with self.assertRaises(PermissionDenied):
            svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto_ajeno), self.delegado)

    def test_el_delegado_no_aprueba_actividades_sin_proyecto(self):
        # Sin proyecto no hay a quién delegar: eso es del Admin.
        dia = self._dia_repartido()
        with self.assertRaises(PermissionDenied):
            svc.aprobar_registro(self._renglon(dia, tipo=self.t_estudio), self.delegado)

    def test_la_cola_del_delegado_trae_lo_suyo(self):
        self._dia_dos_proyectos()
        pendientes = list(svc.registros_por_aprobar(self.delegado))
        self.assertEqual([r.proyecto for r in pendientes], [self.proyecto])

    def test_el_delegado_entra_a_la_pantalla(self):
        """Sin esto se quedaría fuera de su propia pantalla por no ser PM."""
        self._dia_repartido()
        self.client.force_login(self.delegado)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 200)

    def test_al_delegado_le_sale_el_enlace_en_el_menu(self):
        self.client.force_login(self.delegado)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("/horas/aprobar/", html)

    def test_un_ingeniero_sin_delegacion_sigue_fuera(self):
        """La delegación no puede convertirse en una puerta para cualquiera."""
        otro = User.objects.create_user(username="otro.ing", password="Clave2026!")
        otro.groups.add(Group.objects.get(name=roles.INGENIERO))
        self._dia_repartido()

        self.client.force_login(otro)
        self.assertEqual(self.client.get(reverse("horas-aprobar")).status_code, 403)
        self.assertEqual(len(list(svc.registros_por_aprobar(otro))), 0)

    def test_el_delegado_ingeniero_sigue_sin_ver_costos(self):
        """Regla no negociable: el rol Ingeniero NUNCA ve costos.

        Aprobar horas no puede ser una puerta trasera a las tarifas.
        """
        from apps.accounts import roles as r
        self.assertFalse(r.puede_ver_costos(self.delegado))
        self.assertFalse(r.puede_ver_datos_personales(self.delegado))

    def test_el_pm_sigue_pudiendo_con_su_proyecto(self):
        """Designar un delegado no le quita nada al PM."""
        dia = self._dia_repartido()
        svc.aprobar_registro(self._renglon(dia, proyecto=self.proyecto), self.pm)
        self.assertEqual(self._renglon(dia, proyecto=self.proyecto).aprobado_por, self.pm)

    def test_devolver_tambien_lo_puede_el_delegado(self):
        dia = self._dia_repartido()
        svc.devolver_registro(
            self._renglon(dia, proyecto=self.proyecto), self.delegado, "Detalla mejor",
        )
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.ABIERTO)
