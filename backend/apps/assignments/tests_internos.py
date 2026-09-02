"""Proyectos internos con equipo: aceleradores y productos propios.

Son proyectos a los que se asigna gente, a diferencia de los internos
administrativos, donde no hay equipo y cualquiera imputa horas. Tres reglas los
definen, y las tres salen de la misma idea: **el trabajo facturable manda**.

1. Sus asignaciones **no ocupan capacidad**. La persona sigue apareciendo
   disponible para cliente, porque el acelerador va a ceder.
2. Cuando se aprueba una asignación de cliente que se solape, el interno **queda
   en 0** esos días. Las horas no se recuperan: eran tiempo de bench.
3. Solo quien está asignado puede **imputarles horas**.

Lo que más se cuida aquí es que la regla 1 no se filtre a los proyectos de
cliente. Si un proyecto facturable dejara de ocupar capacidad, se podría asignar
a la misma persona dos veces el mismo día y nadie se enteraría hasta que
alguien no apareciera.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.assignments.models import Asignacion, LiberacionRecurso, LogAuditoria
from apps.assignments.services import (
    aprobar_asignacion,
    ceder_ante_cliente,
    internos_que_cederian,
    mapa_carga,
    mapa_carga_interna,
    puede_asignar,
)
from apps.core.models import Proyecto, Recurso

# Lunes a jueves a proposito: el viernes el tope de jornada es 8 h y no 8,5,
# y una asignacion de 8,5 h/dia chocaria con el calendario, no con la regla
# que se quiere probar.
LUNES = date(2026, 9, 14)
JUEVES = date(2026, 9, 17)


class BaseInternos(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm_int", "pm@test.com", "clave-larga-123")
        self.cliente = Proyecto.objects.create(
            codigo="V-25188808/Q", nombre="Simulador", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm, facturable=True,
        )
        self.acelerador = Proyecto.objects.create(
            codigo="SDLC-001", nombre="Acelerador SDLC", cliente="Inetum",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
            facturable=False, interno_con_equipo=True,
        )
        self.administrativo = Proyecto.objects.create(
            codigo="INT-DEPART", nombre="Departamentales", cliente="Inetum",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
            facturable=False, interno_con_equipo=False,
        )
        self.recurso = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando", email="daniel@test.com", banda="SR",
        )

    def _asignacion(self, proyecto, estado="APROBADA", inicio=LUNES, fin=JUEVES,
                    intensidad="8.5"):
        return Asignacion.objects.create(
            recurso=self.recurso, proyecto=proyecto, modo_asignacion="RANGO",
            fecha_inicio=inicio, fecha_fin=fin, dias_habiles=4, horas_totales=34,
            intensidad_diaria=Decimal(intensidad), estado=estado, solicitada_por=self.pm,
        )


class NoOcupanCapacidadTests(BaseInternos):
    def test_el_acelerador_no_cuenta_en_la_capacidad(self):
        self._asignacion(self.acelerador)
        carga = mapa_carga([self.recurso.pk], LUNES, JUEVES)[self.recurso.pk]
        self.assertEqual(carga.get(LUNES, 0.0), 0.0)

    def test_pero_si_se_ve_en_el_mapa_de_lo_interno(self):
        """El dashboard lo pinta en gris: ocupa tiempo aunque no capacidad."""
        self._asignacion(self.acelerador)
        interna = mapa_carga_interna([self.recurso.pk], LUNES, JUEVES)[self.recurso.pk]
        self.assertEqual(interna[LUNES], 8.5)

    def test_se_puede_aprobar_cliente_encima_del_acelerador(self):
        """La regla entera existe para esto: el acelerador no bloquea al cliente."""
        self._asignacion(self.acelerador)
        nueva = self._asignacion(self.cliente, estado="SOLICITADA")
        ok, conflicto = puede_asignar(nueva)
        self.assertTrue(ok, f"bloqueó por el acelerador el {conflicto}")

    def test_un_interno_administrativo_si_ocupa_capacidad(self):
        """INT-DEPART no es un acelerador: no cede, y por tanto ocupa."""
        self._asignacion(self.administrativo)
        carga = mapa_carga([self.recurso.pk], LUNES, JUEVES)[self.recurso.pk]
        self.assertEqual(carga[LUNES], 8.5)

    def test_un_proyecto_de_cliente_sigue_ocupando(self):
        """Si esto se rompiera, se podria asignar a la misma persona dos veces
        el mismo dia sin que nada avisara."""
        self._asignacion(self.cliente)
        carga = mapa_carga([self.recurso.pk], LUNES, JUEVES)[self.recurso.pk]
        self.assertEqual(carga[LUNES], 8.5)

        otra = self._asignacion(self.cliente, estado="SOLICITADA")
        ok, _ = puede_asignar(otra)
        self.assertFalse(ok, "aprobó dos proyectos de cliente a la vez")

    def test_pidiendo_la_carga_completa_si_aparece(self):
        self._asignacion(self.acelerador)
        carga = mapa_carga(
            [self.recurso.pk], LUNES, JUEVES, solo_capacidad=False,
        )[self.recurso.pk]
        self.assertEqual(carga[LUNES], 8.5)


class CedeAnteElClienteTests(BaseInternos):
    def test_al_aprobar_cliente_el_acelerador_queda_en_cero(self):
        interna = self._asignacion(self.acelerador)
        cliente = self._asignacion(self.cliente, estado="SOLICITADA")

        aprobar_asignacion(cliente, self.pm)

        interna.refresh_from_db()
        self.assertEqual(interna.horas_totales, 0)
        self.assertEqual(interna.dias_habiles, 0)
        # Sigue existiendo: cede, no se borra.
        self.assertEqual(interna.estado, "APROBADA")

    def test_las_horas_no_se_recuperan_despues(self):
        """Decision explicita: eran tiempo de bench y el bench se acabo. La
        fecha fin no se mueve."""
        interna = self._asignacion(self.acelerador)
        fin_original = interna.fecha_fin

        aprobar_asignacion(self._asignacion(self.cliente, estado="SOLICITADA"), self.pm)

        interna.refresh_from_db()
        self.assertEqual(interna.fecha_fin, fin_original)

    def test_queda_una_liberacion_visible_y_reversible(self):
        interna = self._asignacion(self.acelerador)
        aprobar_asignacion(self._asignacion(self.cliente, estado="SOLICITADA"), self.pm)

        liberacion = LiberacionRecurso.objects.get(asignacion=interna)
        self.assertEqual(liberacion.estado, "APROBADA")
        self.assertEqual(liberacion.politica, "REDUCIR")
        self.assertIn("V-25188808/Q", liberacion.motivo)

    def test_queda_escrito_en_el_log_y_se_ve_que_fue_automatico(self):
        interna = self._asignacion(self.acelerador)
        aprobar_asignacion(self._asignacion(self.cliente, estado="SOLICITADA"), self.pm)

        entrada = LogAuditoria.objects.get(asignacion=interna, accion="LIBERAR")
        self.assertTrue(entrada.detalle["automatica"])
        self.assertEqual(entrada.detalle["proyecto_cliente"], "V-25188808/Q")

    def test_solo_cede_la_parte_que_se_solapa(self):
        interna = self._asignacion(self.acelerador, inicio=LUNES, fin=date(2026, 9, 25))
        cliente = self._asignacion(
            self.cliente, estado="SOLICITADA", inicio=LUNES, fin=date(2026, 9, 16),
        )
        aprobar_asignacion(cliente, self.pm)

        liberacion = LiberacionRecurso.objects.get(asignacion=interna)
        self.assertEqual(liberacion.fecha_inicio, LUNES)
        self.assertEqual(liberacion.fecha_fin, date(2026, 9, 16))
        # Los dias posteriores siguen siendo del acelerador.
        interna.refresh_from_db()
        self.assertGreater(interna.dias_habiles, 0)

    def test_un_interno_administrativo_no_cede(self):
        """No tiene equipo ni prioridad que ceder: es gasto de estructura."""
        interna = self._asignacion(self.administrativo)
        self.assertEqual(internos_que_cederian(self._asignacion(self.cliente, estado="SOLICITADA")), [])
        interna.refresh_from_db()
        self.assertEqual(interna.horas_totales, 34)

    def test_aprobar_un_interno_no_hace_ceder_a_otro(self):
        """Solo el trabajo facturable desplaza."""
        interna = self._asignacion(self.acelerador)
        otro = Proyecto.objects.create(
            codigo="SDLC-002", nombre="Otro acelerador", cliente="Inetum",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm,
            facturable=False, interno_con_equipo=True,
        )
        aprobar_asignacion(self._asignacion(otro, estado="SOLICITADA"), self.pm)

        interna.refresh_from_db()
        self.assertEqual(interna.horas_totales, 34)

    def test_sin_nada_interno_no_pasa_nada(self):
        cliente = self._asignacion(self.cliente, estado="SOLICITADA")
        self.assertEqual(ceder_ante_cliente(cliente, self.pm), [])


class AvisoAntesDeFirmarTests(BaseInternos):
    """El punto 1 del diseño: quien aprueba decide, y para decidir tiene que ver."""

    def test_dice_que_trabajo_interno_cederia(self):
        self._asignacion(self.acelerador)
        cliente = self._asignacion(self.cliente, estado="SOLICITADA")

        cediendo = internos_que_cederian(cliente)
        self.assertEqual(len(cediendo), 1)
        interna, ini, fin = cediendo[0]
        self.assertEqual(interna.proyecto, self.acelerador)
        self.assertEqual((ini, fin), (LUNES, JUEVES))

    def test_es_solo_lectura(self):
        """Consultarlo no puede cambiar nada: se llama al pintar la pantalla."""
        interna = self._asignacion(self.acelerador)
        internos_que_cederian(self._asignacion(self.cliente, estado="SOLICITADA"))

        interna.refresh_from_db()
        self.assertEqual(interna.horas_totales, 34)
        self.assertFalse(LiberacionRecurso.objects.exists())

    def test_sin_solape_de_fechas_no_avisa(self):
        self._asignacion(self.acelerador, inicio=LUNES, fin=JUEVES)
        lejano = self._asignacion(
            self.cliente, estado="SOLICITADA",
            inicio=date(2026, 10, 5), fin=date(2026, 10, 9),
        )
        self.assertEqual(internos_que_cederian(lejano), [])


class MarcaSoloParaInternosTests(BaseInternos):
    def test_un_proyecto_facturable_no_puede_llevar_la_marca(self):
        """Sus asignaciones dejarian de ocupar capacidad y cederian ante otro
        cliente: lo contrario de lo que se espera del trabajo que se cobra."""
        self.cliente.interno_con_equipo = True
        with self.assertRaises(ValidationError) as ctx:
            self.cliente.full_clean()
        self.assertIn("interno_con_equipo", ctx.exception.message_dict)

    def test_un_proyecto_interno_si(self):
        self.acelerador.full_clean()  # no lanza


class SoloLosAsignadosImputanHorasTests(BaseInternos):
    """Un acelerador tiene equipo, igual que un proyecto de cliente.

    Dejar que cualquiera le impute horas convertiria su coste en humo: nadie
    sabria cuanto costo de verdad construirlo.
    """

    def _disponibles(self):
        from apps.legalizacion.services import proyectos_disponibles
        return set(
            proyectos_disponibles(self.recurso, LUNES).values_list("codigo", flat=True)
        )

    def test_el_asignado_lo_ve(self):
        self._asignacion(self.acelerador)
        self.assertIn("SDLC-001", self._disponibles())

    def test_quien_no_esta_asignado_no_lo_ve(self):
        self.assertNotIn("SDLC-001", self._disponibles())

    def test_una_asignacion_solo_solicitada_no_basta(self):
        self._asignacion(self.acelerador, estado="SOLICITADA")
        self.assertNotIn("SDLC-001", self._disponibles())

    def test_fuera_de_las_fechas_de_la_asignacion_tampoco(self):
        self._asignacion(self.acelerador, inicio=date(2026, 10, 5), fin=date(2026, 10, 9))
        self.assertNotIn("SDLC-001", self._disponibles())

    def test_los_administrativos_siguen_saliendo_para_todos(self):
        """Sin ellos, alguien en bench no tendria con que cuadrar su jornada."""
        self.assertIn("INT-DEPART", self._disponibles())
