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
from unittest import mock

from django.utils import timezone

from apps.core.models import AppendOnlyError
from apps.legalizacion.models import (
    DiaLegalizado, ReaperturaDia, RegistroHoras, TipoActividad,
)


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

    def test_al_delegado_le_sale_el_camino_en_el_menu(self):
        """Aprobar horas ya no cuelga de la barra: vive en el concentrador
        Gestionar. Lo que hay que comprobar es que al delegado le salga ese
        concentrador —si no, tendria que saberse la URL de memoria— y que dentro
        encuentre su pantalla."""
        self.client.force_login(self.delegado)

        barra = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn('href="/gestionar/"', barra)

        hub = self.client.get(reverse("gestionar"))
        self.assertIn("/horas/aprobar/", {i["url"] for i in hub.context["items"]})

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


class ReabrirUnDiaFirmadoTests(TestCase):
    """Deshacer las firmas de un día para que se pueda corregir.

    Hasta ahora, corregir un día aprobado solo se podía borrando y volviendo a
    registrar. Eso destruía lo anterior —así se perdieron 194 renglones en
    producción— y borraba de paso quién había firmado qué.

    Lo que se cuida aquí es que **deshacer una firma nunca sea silencioso**: si
    esta acción se pudiera usar sin dejar rastro, sería peor que el problema que
    viene a resolver, porque además parecería legítima.
    """

    def setUp(self):
        self.admin = User.objects.create_user("adm_reabre", "ar@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm = User.objects.create_user("pm_reabre", "pr@test.com", "clave-larga-1")
        self.pm.groups.add(Group.objects.get_or_create(name="PM")[0])

        self.recurso = Recurso.objects.create(
            nombre="Reabre Dias", email="rd@test.com", banda="SR",
        )
        self.tipo = TipoActividad.objects.create(nombre="Estudio RD", requiere_proyecto=False)
        self.dia = DiaLegalizado.objects.create(
            recurso=self.recurso, fecha=date(2026, 1, 6), estado=DiaLegalizado.APROBADO,
            total_horas=Decimal("4"), jornada_esperada=Decimal("8.5"),
        )
        self.firmados = [
            RegistroHoras.objects.create(
                dia=self.dia, tipo_actividad=self.tipo, horas=Decimal("2"),
                detalle=f"actividad {i}", estado=RegistroHoras.APROBADO,
                aprobado_por=self.pm, aprobado_en=timezone.now(),
            )
            for i in (1, 2)
        ]

    # ── lo que hace ─────────────────────────────────────────────────────────

    def test_devuelve_las_firmadas_y_abre_el_dia(self):
        cuantos = svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal")

        self.assertEqual(cuantos, 2)
        self.dia.refresh_from_db()
        self.assertEqual(self.dia.estado, DiaLegalizado.ABIERTO)
        for r in self.dia.registros.all():
            self.assertEqual(r.estado, RegistroHoras.DEVUELTO)
            self.assertIn("el proyecto esta mal", r.motivo_devolucion)

    def test_no_borra_ningun_renglon(self):
        """Justo lo contrario del borrar-y-volver-a-registrar que lo motivo."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal")
        self.assertEqual(RegistroHoras.all_objects.filter(dia=self.dia).count(), 2)

    def test_deshace_la_firma_de_verdad(self):
        """Dejar `aprobado_por` puesto haria creer que sigue aprobado."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal")
        for r in self.dia.registros.all():
            self.assertIsNone(r.aprobado_por)
            self.assertIsNone(r.aprobado_en)

    # ── el rastro ───────────────────────────────────────────────────────────

    def test_guarda_quien_firmaba_antes(self):
        """Sin esto, deshacer la firma borraria la respuesta a «quien aprobo
        esto», que es lo que hace util una auditoria."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal")

        rea = ReaperturaDia.objects.get(dia=self.dia)
        self.assertEqual(rea.actor, self.admin)
        self.assertEqual(rea.estado_anterior, DiaLegalizado.APROBADO)
        self.assertEqual(len(rea.firmas_revertidas), 2)
        self.assertEqual(rea.firmas_revertidas[0]["aprobado_por"], "pm_reabre")
        self.assertIsNotNone(rea.firmas_revertidas[0]["aprobado_en"])

    def test_el_rastro_no_se_puede_editar_ni_borrar(self):
        """Si se pudiera, quien tuviera motivos para tapar una reapertura seria
        justo quien puede hacerlo."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal")
        rea = ReaperturaDia.objects.get(dia=self.dia)

        with self.assertRaises(AppendOnlyError):
            rea.motivo = "otra cosa"
            rea.save()
        with self.assertRaises(AppendOnlyError):
            rea.delete()
        with self.assertRaises(AppendOnlyError):
            ReaperturaDia.objects.filter(pk=rea.pk).update(motivo="otra cosa")

    # ── lo que no deja hacer ────────────────────────────────────────────────

    def test_un_pm_no_puede_con_renglones_sin_proyecto(self):
        """Formación y estudio no tienen PM al que pertenecer: son del Admin,
        igual que para firmarlos."""
        with self.assertRaises(PermissionDenied):
            svc.reabrir_dia(self.dia, self.pm, "quiero deshacerlo")
        self.dia.refresh_from_db()
        self.assertEqual(self.dia.estado, DiaLegalizado.APROBADO)

    def test_sin_motivo_no_reabre(self):
        """Reabrir sin decir que esta mal deja a la persona adivinando."""
        with self.assertRaises(ValidationError):
            svc.reabrir_dia(self.dia, self.admin, "   ")
        self.dia.refresh_from_db()
        self.assertEqual(self.dia.estado, DiaLegalizado.APROBADO)
        self.assertEqual(ReaperturaDia.objects.count(), 0)

    def test_un_dia_ya_abierto_no_se_reabre(self):
        self.dia.estado = DiaLegalizado.ABIERTO
        self.dia.save(update_fields=["estado"])
        with self.assertRaises(ValidationError):
            svc.reabrir_dia(self.dia, self.admin, "por si acaso")

    def test_un_dia_sin_firmas_no_se_reabre(self):
        """Para eso esta el boton de devolver de la cola."""
        self.dia.registros.update(estado=RegistroHoras.PENDIENTE)
        self.dia.estado = DiaLegalizado.REGISTRADO
        self.dia.save(update_fields=["estado"])
        with self.assertRaises(ValidationError):
            svc.reabrir_dia(self.dia, self.admin, "no hay nada firmado")

    def test_no_toca_lo_que_no_estaba_firmado(self):
        suelto = RegistroHoras.objects.create(
            dia=self.dia, tipo_actividad=self.tipo, horas=Decimal("1"),
            detalle="pendiente de otro PM", estado=RegistroHoras.PENDIENTE,
        )
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal")

        suelto.refresh_from_db()
        self.assertEqual(
            suelto.estado, RegistroHoras.PENDIENTE,
            "un renglon que nadie habia firmado no se devuelve",
        )

    def test_si_falla_no_deja_nada_a_medias(self):
        """La reapertura se guarda antes de tocar las firmas, y todo va en una
        transaccion: o queda el rastro y las firmas deshechas, o ninguna cosa."""
        with mock.patch.object(
            RegistroHoras, "save", side_effect=RuntimeError("cayo la base")
        ):
            with self.assertRaises(RuntimeError):
                svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal")

        self.dia.refresh_from_db()
        self.assertEqual(self.dia.estado, DiaLegalizado.APROBADO)
        self.assertEqual(ReaperturaDia.objects.count(), 0)
        for r in self.dia.registros.all():
            self.assertEqual(r.estado, RegistroHoras.APROBADO)


class QuienPuedeReabrirTests(TestCase):
    """Deshacer una firma alcanza hasta donde alcanza ponerla.

    Al principio reabrir era solo del Admin, por miedo a que un PM deshiciera la
    firma de otro en un día repartido entre dos proyectos. El miedo era el
    correcto; la respuesta, no: dejaba al PM esperando a un Admin para arreglar
    horas de su propio proyecto, que es justo lo que hace que la gente vuelva a
    borrar y volver a registrar.

    La regla es la misma que para aprobar (`puede_aprobar_registro`): el PM del
    proyecto, su aprobador delegado y el Admin. Y se aplica **renglón a
    renglón**, así que en un día compartido cada quien devuelve lo suyo y lo del
    otro sigue firmado. El día ya sabía convivir con eso: un renglón devuelto lo
    reabre y los aprobados siguen bloqueados.
    """

    def setUp(self):
        self.admin = User.objects.create_user("adm_qr", "aqr@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.pm_a = User.objects.create_user("pm_a_qr", "pa@test.com", "clave-larga-1")
        self.pm_a.groups.add(Group.objects.get_or_create(name="PM")[0])
        self.pm_b = User.objects.create_user("pm_b_qr", "pb@test.com", "clave-larga-1")
        self.pm_b.groups.add(Group.objects.get_or_create(name="PM")[0])
        # Un ingeniero sin ningun rol de aprobacion, designado delegado.
        self.delegada = User.objects.create_user("del_qr", "dq@test.com", "clave-larga-1")
        self.delegada.groups.add(Group.objects.get_or_create(name="Ingeniero")[0])

        self.proy_a = Proyecto.objects.create(
            codigo="QR-A", nombre="Proyecto A", pm=self.pm_a,
            aprobador_delegado=self.delegada,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )
        self.proy_b = Proyecto.objects.create(
            codigo="QR-B", nombre="Proyecto B", pm=self.pm_b,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )

        self.recurso = Recurso.objects.create(
            nombre="Dia Compartido", email="dc@test.com", banda="SR",
        )
        self.tipo = TipoActividad.objects.create(nombre="Proyecto QR", requiere_proyecto=True)
        self.dia = DiaLegalizado.objects.create(
            recurso=self.recurso, fecha=date(2026, 1, 7), estado=DiaLegalizado.APROBADO,
            total_horas=Decimal("8"), jornada_esperada=Decimal("8.5"),
        )
        self.de_a = RegistroHoras.objects.create(
            dia=self.dia, tipo_actividad=self.tipo, proyecto=self.proy_a,
            horas=Decimal("4"), detalle="lo del proyecto A",
            estado=RegistroHoras.APROBADO, aprobado_por=self.pm_a, aprobado_en=timezone.now(),
        )
        self.de_b = RegistroHoras.objects.create(
            dia=self.dia, tipo_actividad=self.tipo, proyecto=self.proy_b,
            horas=Decimal("4"), detalle="lo del proyecto B",
            estado=RegistroHoras.APROBADO, aprobado_por=self.pm_b, aprobado_en=timezone.now(),
        )

    def test_el_pm_reabre_lo_de_su_proyecto(self):
        cuantos = svc.reabrir_dia(self.dia, self.pm_a, "las horas van al otro grafo")
        self.assertEqual(cuantos, 1)
        self.de_a.refresh_from_db()
        self.assertEqual(self.de_a.estado, RegistroHoras.DEVUELTO)

    def test_y_no_toca_lo_del_otro_proyecto(self):
        """Lo que evita que reabrir sea una forma de pisarle el trabajo a otro."""
        svc.reabrir_dia(self.dia, self.pm_a, "las horas van al otro grafo")
        self.de_b.refresh_from_db()
        self.assertEqual(self.de_b.estado, RegistroHoras.APROBADO)
        self.assertEqual(self.de_b.aprobado_por, self.pm_b)

    def test_el_dia_queda_abierto_con_lo_ajeno_aun_firmado(self):
        svc.reabrir_dia(self.dia, self.pm_a, "las horas van al otro grafo")
        self.dia.refresh_from_db()
        self.assertEqual(self.dia.estado, DiaLegalizado.ABIERTO)
        self.assertTrue(self.de_b.bloqueado or True)
        self.de_b.refresh_from_db()
        self.assertTrue(self.de_b.bloqueado, "lo aprobado sigue sin poder editarse")

    def test_el_rastro_solo_guarda_las_firmas_deshechas(self):
        svc.reabrir_dia(self.dia, self.pm_a, "las horas van al otro grafo")
        rea = ReaperturaDia.objects.get(dia=self.dia)
        self.assertEqual(len(rea.firmas_revertidas), 1)
        self.assertEqual(rea.firmas_revertidas[0]["registro"], self.de_a.pk)
        self.assertEqual(rea.actor, self.pm_a)

    def test_la_delegada_puede_aunque_sea_ingeniera(self):
        """La designación en el proyecto ES la autorización: si tuviera que
        pasar antes por un rol, designar a alguien no serviría de nada."""
        self.assertTrue(svc.puede_reabrir(self.delegada, self.dia))
        cuantos = svc.reabrir_dia(self.dia, self.delegada, "el detalle no alcanza")
        self.assertEqual(cuantos, 1)

    def test_un_pm_ajeno_al_dia_no_puede(self):
        otro = User.objects.create_user("pm_c_qr", "pc@test.com", "clave-larga-1")
        otro.groups.add(Group.objects.get(name="PM"))
        self.assertFalse(svc.puede_reabrir(otro, self.dia))
        with self.assertRaises(PermissionDenied):
            svc.reabrir_dia(self.dia, otro, "me apetece")

    def test_un_ingeniero_cualquiera_tampoco(self):
        ing = User.objects.create_user("ing_qr", "iq@test.com", "clave-larga-1")
        ing.groups.add(Group.objects.get(name="Ingeniero"))
        with self.assertRaises(PermissionDenied):
            svc.reabrir_dia(self.dia, ing, "quiero rehacer mi dia")

    def test_el_admin_los_reabre_todos(self):
        cuantos = svc.reabrir_dia(self.dia, self.admin, "hay que rehacer el dia entero")
        self.assertEqual(cuantos, 2)
        for r in (self.de_a, self.de_b):
            r.refresh_from_db()
            self.assertEqual(r.estado, RegistroHoras.DEVUELTO)

    def test_el_mensaje_distingue_no_poder_de_no_haber_nada(self):
        """Un solo mensaje para los dos casos manda al PM a buscar un fallo
        donde no lo hay."""
        otro = User.objects.create_user("pm_d_qr", "pd@test.com", "clave-larga-1")
        otro.groups.add(Group.objects.get(name="PM"))
        with self.assertRaises(PermissionDenied) as ctx:
            svc.reabrir_dia(self.dia, otro, "me apetece")
        self.assertIn("otro proyecto", str(ctx.exception))


class UnDiaReabiertoSePuedeCorregirTests(TestCase):
    """Reabrir un día viejo no puede dejarlo sin nadie que lo arregle.

    Legalizar tiene una ventana de 30 días hacia atrás, para que el pasado no
    quede abierto por inercia. Hasta ahora el mensaje decía «para algo más
    antiguo, pídeselo a un administrador», y el administrador lo resolvía
    editando el día en el admin de Django.

    Esa puerta se cerró: editaba horas firmadas sin dejar rastro. Pero cerrarla
    dejó una trampa peor —y la abrió el mismo cambio que la creó—: al reabrir un
    día de hace más de 30 días, sus horas dejan de contar como aprobadas y
    **nadie** puede volver a registrarlas. El día se queda peor que si no se
    hubiera tocado.

    Un día reabierto escapa a la ventana mientras siga abierto. No es una
    excepción cómoda: la ventana existe contra el olvido, y una reapertura es lo
    contrario del olvido —alguien con autoridad dijo, con nombre y motivo, que
    ese día concreto hay que corregirlo—. Se cierra sola en cuanto la persona
    vuelve a registrarlo.
    """

    def setUp(self):
        self.admin = User.objects.create_user("adm_viejo", "av@test.com", "clave-larga-1")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])
        self.recurso = Recurso.objects.create(
            nombre="Dia Antiguo", email="da@test.com", banda="SR",
        )
        self.tipo = TipoActividad.objects.create(nombre="Estudio viejo", requiere_proyecto=False)

        # Un dia HABIL bastante mas atras que la ventana.
        #
        # Se pregunta al calendario en vez de mirar solo el dia de la semana. La
        # primera version saltaba fines de semana y nada mas, asi que el 8 de
        # septiembre —cuando la resta cae en el 20 de julio, festivo nacional—
        # el dia no era legalizable por un motivo distinto del que se queria
        # probar y la prueba fallaba sola. Es la misma trampa que ya hizo fallar
        # `test_un_dia_futuro_no_ofrece_formulario` los jueves y los viernes:
        # una fecha calculada a ojo acaba aterrizando donde no debe.
        self.fecha = date.today() - timedelta(days=svc.DIAS_ATRAS_MAX + 20)
        while not svc.estado_del_dia(self.recurso, self.fecha)["habil"]:
            self.fecha -= timedelta(days=1)

        self.dia = DiaLegalizado.objects.create(
            recurso=self.recurso, fecha=self.fecha, estado=DiaLegalizado.APROBADO,
            total_horas=Decimal("4"), jornada_esperada=Decimal("8.5"),
        )
        RegistroHoras.objects.create(
            dia=self.dia, tipo_actividad=self.tipo, horas=Decimal("4"),
            detalle="lo que declaro entonces", estado=RegistroHoras.APROBADO,
            aprobado_por=self.admin, aprobado_en=timezone.now(),
        )

    def test_antes_de_reabrirlo_la_ventana_manda(self):
        """La ventana sigue en pie para todo lo demas: esto no la desactiva."""
        self.assertIn(
            "últimos", svc.motivo_no_legalizable(self.recurso, self.fecha),
        )

    def test_una_vez_reabierto_se_puede_corregir(self):
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal imputado")
        self.assertEqual(svc.motivo_no_legalizable(self.recurso, self.fecha), "")

    def test_y_se_puede_guardar_de_verdad(self):
        """`motivo_no_legalizable` es lo que pinta la pantalla; esta es la
        puerta que de verdad bloqueaba al guardar."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal imputado")
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        self.assertEqual(dia.pk, self.dia.pk)

    def test_sale_en_la_lista_de_pendientes(self):
        """Es la unica pantalla donde la persona se entera de que le devolvieron
        algo: dejarlo fuera equivale a no habersele dicho."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal imputado")
        self.assertIn(self.fecha, svc.dias_pendientes(self.recurso))

    def test_el_selector_llega_hasta_ese_dia(self):
        """Poder corregirlo pero no poder elegir la fecha es la misma trampa un
        paso mas alla."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal imputado")
        desde, _ = svc.rango_legalizable(self.recurso)
        self.assertLessEqual(desde, self.fecha)

    def test_no_le_abre_la_ventana_a_los_demas_dias(self):
        """La excepcion es ese dia, no el pasado entero de esa persona."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal imputado")
        otro = self.fecha - timedelta(days=7)
        self.assertIn("últimos", svc.motivo_no_legalizable(self.recurso, otro))

    def test_ni_a_los_dias_viejos_de_otra_persona(self):
        otro = Recurso.objects.create(nombre="Ajeno", email="aj@test.com", banda="SR")
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal imputado")
        self.assertIn("últimos", svc.motivo_no_legalizable(otro, self.fecha))

    def test_al_volver_a_registrarlo_la_ventana_vuelve(self):
        """La salida se cierra sola: si no, reabrir un dia una vez dejaria esa
        fecha editable para siempre."""
        svc.reabrir_dia(self.dia, self.admin, "el proyecto esta mal imputado")
        self.dia.refresh_from_db()
        self.dia.estado = DiaLegalizado.REGISTRADO
        self.dia.save(update_fields=["estado"])
        self.assertIn("últimos", svc.motivo_no_legalizable(self.recurso, self.fecha))

    def test_el_mensaje_manda_a_la_puerta_que_si_existe(self):
        """Decia «pideselo a un administrador», que era el formulario del admin.
        Ese formulario ya no deja escribir: el mensaje mandaba a una puerta
        cerrada."""
        texto = svc.motivo_no_legalizable(self.recurso, self.fecha)
        self.assertIn("reabra", texto)


class AprobarLosRutinariosTests(BaseAprobacion):
    """Firmar de un golpe lo interno sin avisos.

    Es el volumen que no aporta nada revisar de a uno —bench, estudio,
    formacion, departamentales— y el que hace que la cola se vea imposible y
    se acabe firmando todo sin mirar, que es el fallo que este modulo existe
    para evitar.

    Casi todas estas pruebas comprueban **que no entra** lo que no debe. Un
    boton de firma masiva que arrastra un renglon de mas es peor que no
    tenerlo.
    """

    def setUp(self):
        super().setUp()
        # El `_proyecto` de la base deja a esta persona planificada a jornada
        # completa en dos proyectos de cliente. Con el dia lleno de cliente,
        # cualquier renglon interno salta NO_FACTURABLE_CON_PLAN_LLENO —que es
        # correcto y esta probado aparte— y aqui lo que se mide es otra cosa:
        # alguien en bench, sin plan de cliente, imputando a lo interno.
        Asignacion.objects.update(estado="SOLICITADA")

        self.interno = Proyecto.objects.create(
            codigo="INT-DEPART", nombre="Actividades Departamentales",
            cliente="Inetum", fecha_inicio=date(2026, 1, 1), pm=self.pm,
            facturable=False,
        )

    def _dia_interno(self, *renglones):
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        for horas, detalle in renglones:
            svc.agregar_renglon(dia, self.t_estudio, horas, detalle)
        return svc.registrar_dia(dia, self.ing)

    def _rutinarios(self, usuario):
        dias = svc.dias_por_aprobar(usuario)
        svc.triar(dias, usuario)
        return svc.rutinarios_de(dias)

    def test_recoge_los_internos_sin_avisos(self):
        self._dia_interno(
            (4, "Ruta de aprendizaje de Databricks, modulos 1 a 4"),
            (4.5, "Documentacion del procedimiento de altas de personal"),
        )
        self.assertEqual(len(self._rutinarios(self.admin)), 2)

    def test_no_recoge_nada_facturable(self):
        """Las horas de cliente no se firman en bloque nunca.

        El renglon de cliente de esta prueba esta **dentro de su plan y bien
        descrito**: sin ninguna señal, o sea en Rutina. Es lo unico que prueba
        de verdad el filtro — con un renglon que ya trae aviso, quedaria fuera
        por el otro motivo y el filtro podria no existir.
        """
        Asignacion.objects.filter(proyecto=self.proyecto).update(
            estado="APROBADA", intensidad_diaria=Decimal("4.0"),
        )
        dia = svc.obtener_o_crear_dia(self.recurso, self.fecha)
        svc.agregar_renglon(dia, self.t_proyecto, 4, "Ajustes al conector de Oracle y pruebas",
                            proyecto=self.proyecto)
        svc.agregar_renglon(dia, self.t_estudio, 4.5,
                            "Documentacion del procedimiento de altas de personal")
        svc.registrar_dia(dia, self.ing)

        recogidos = self._rutinarios(self.admin)
        self.assertEqual(len(recogidos), 1, "solo la interna")
        self.assertFalse(recogidos[0].facturable)

    def test_no_recoge_lo_que_trae_un_aviso(self):
        self._dia_interno(
            (4, "Ruta de aprendizaje de Databricks, modulos 1 a 4"),
            (4.5, "muchas tareas"),
        )
        recogidos = self._rutinarios(self.admin)
        self.assertEqual(len(recogidos), 1)
        self.assertNotIn("muchas tareas", [r.detalle for r in recogidos])

    def test_un_pm_no_recoge_lo_que_no_puede_firmar(self):
        """`pendientes_mios` ya lo filtra, y esto lo deja escrito: los
        renglones sin proyecto son del Admin, y un PM no los ve aqui."""
        self._dia_interno((8.5, "Ruta de aprendizaje de Databricks, modulos 1 a 4"))
        self.assertEqual(self._rutinarios(self.pm), [])

    def test_el_boton_firma_y_lo_dice(self):
        self._dia_interno(
            (4, "Ruta de aprendizaje de Databricks, modulos 1 a 4"),
            (4.5, "Documentacion del procedimiento de altas de personal"),
        )
        self.client.force_login(self.admin)
        resp = self.client.post(reverse("horas-aprobar"), {"accion": "aprobar_rutinarios"})

        self.assertRedirects(resp, reverse("horas-aprobar"), fetch_redirect_response=False)
        self.assertEqual(
            RegistroHoras.objects.filter(estado=RegistroHoras.PENDIENTE).count(), 0,
        )
        for r in RegistroHoras.objects.all():
            self.assertEqual(r.aprobado_por, self.admin)
            self.assertFalse(r.aprobacion_forzada, "esto no es una firma forzada")

    def test_la_lista_se_recalcula_al_pulsar_no_llega_en_el_post(self):
        """La diferencia entre un boton y una promesa.

        Entre que se pinto la pantalla y llega el envio, alguien pudo empeorar
        un detalle. Si los ids viajaran en el formulario, este boton firmaria a
        ciegas lo que el triaje marco hace diez minutos.
        """
        dia = self._dia_interno(
            (4, "Ruta de aprendizaje de Databricks, modulos 1 a 4"),
            (4.5, "Documentacion del procedimiento de altas de personal"),
        )
        estropeado = dia.registros.first()
        estropeado.detalle = "varias cosas"
        estropeado.save(update_fields=["detalle"])

        self.client.force_login(self.admin)
        self.client.post(reverse("horas-aprobar"), {"accion": "aprobar_rutinarios"})

        estropeado.refresh_from_db()
        self.assertEqual(estropeado.estado, RegistroHoras.PENDIENTE)
        self.assertEqual(
            RegistroHoras.objects.filter(estado=RegistroHoras.APROBADO).count(), 1,
        )

    def test_sin_nada_rutinario_no_se_pinta_el_boton(self):
        self._dia_interno((8.5, "muchas tareas"))
        self.client.force_login(self.admin)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertNotIn("aprobar_rutinarios", html)

    def test_con_algo_rutinario_si(self):
        self._dia_interno(
            (4, "Ruta de aprendizaje de Databricks, modulos 1 a 4"),
            (4.5, "Documentacion del procedimiento de altas de personal"),
        )
        self.client.force_login(self.admin)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertIn("aprobar_rutinarios", html)
        self.assertIn("Aprobar los rutinarios (2)", html)

    def test_pulsarlo_dos_veces_no_rompe_nada(self):
        """El segundo envio llega cuando ya no queda nada, y lo dice."""
        self._dia_interno(
            (4, "Ruta de aprendizaje de Databricks, modulos 1 a 4"),
            (4.5, "Documentacion del procedimiento de altas de personal"),
        )
        self.client.force_login(self.admin)
        self.client.post(reverse("horas-aprobar"), {"accion": "aprobar_rutinarios"})
        resp = self.client.post(reverse("horas-aprobar"), {"accion": "aprobar_rutinarios"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("Ya no queda ninguna", resp.content.decode())


class LosCarrilesFiltranTests(BaseAprobacion):
    """Picar un carril deja a la vista solo los dias de esa banda.

    Es un filtro de la vista: se pinta todo y el navegador esconde lo que
    sobra. Aqui solo se comprueba que la pantalla trae lo que ese filtro
    necesita — la banda en cada tarjeta y el carril pulsable—, porque lo demas
    ocurre en el navegador y no hay como ejecutarlo desde aqui.
    """

    def test_los_carriles_son_botones_con_su_banda(self):
        self._dia_repartido()
        self.client.force_login(self.admin)
        html = self.client.get(reverse("horas-aprobar")).content.decode()

        for banda in ("atencion", "revisar", "rutina"):
            self.assertIn(f'data-banda="{banda}"', html)
        self.assertIn('aria-pressed="false"', html)

    def test_cada_tarjeta_lleva_la_clase_de_su_banda(self):
        """Es lo que el filtro mira. Sin esto no esconde nada y el boton miente."""
        self._dia_repartido()
        self.client.force_login(self.admin)
        html = self.client.get(reverse("horas-aprobar")).content.decode()
        self.assertRegex(html, r'class="dia-card d-(atencion|revisar|rutina)"')
