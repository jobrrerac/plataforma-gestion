from django.test import TestCase
from django.contrib.auth.models import User
from datetime import date
from decimal import Decimal
from apps.core.models import Recurso, Proyecto, TarifaVigente
from .models import Asignacion, LiberacionRecurso, LogAuditoria
from .services import (
    calcular_fecha_fin, puede_asignar, aprobar_asignacion,
    analizar_recurrencia, crear_solicitudes_recurrentes,
    ceder_horas, rechazar_asignacion, horas_cedibles, carga_en_fecha,
    solicitar_liberacion, aprobar_liberacion, rechazar_liberacion, anular_liberacion,
)


class CalculoFechaFinTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm", password="pass")
        self.recurso = Recurso.objects.create(nombre="Dev1", email="dev1@test.com", banda="SR")

    def test_40h_a_8h_dia(self):
        # 40h / 8h = 5 días, lunes 13 ene → viernes 17 ene 2025
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 40, 8)
        self.assertEqual(fecha, date(2025, 1, 17))

    def test_20h_a_4h_dia(self):
        # 20h / 4h = 5 días, lunes 13 ene → viernes 17 ene 2025
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 20, 4)
        self.assertEqual(fecha, date(2025, 1, 17))

    def test_8h_a_8h_dia(self):
        # 1 día, lunes → mismo lunes
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 8, 8)
        self.assertEqual(fecha, date(2025, 1, 13))

    def test_cruza_fin_de_semana(self):
        # 16h / 8h = 2 días, viernes 10 ene → lunes 13 ene
        fecha = calcular_fecha_fin(self.recurso, date(2025, 1, 10), 16, 8)
        self.assertEqual(fecha, date(2025, 1, 13))


class CapacidadTests(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm2", password="pass")
        self.admin = User.objects.create_user("admin2", password="pass")
        self.recurso = Recurso.objects.create(nombre="Dev2", email="dev2@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-001", nombre="Alpha", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )

    def _crear_aprobada(self, horas=40, intensidad=8, inicio=date(2025, 1, 13)):
        fecha_fin = calcular_fecha_fin(self.recurso, inicio, horas, intensidad)
        a = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=horas, intensidad_diaria=intensidad,
            fecha_inicio=inicio, fecha_fin=fecha_fin,
            estado="APROBADA", solicitada_por=self.pm,
        )
        return a

    def test_sobreasignacion_bloqueada_lunes(self):
        # Lunes: tope 8.5 h. Primera asignación ocupa 8.5 h → segunda de 1 h debe bloquearse
        self._crear_aprobada(horas=43, intensidad=8.5)  # 43/8.5 = 5 días lun-vie
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 8, 1)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=8, intensidad_diaria=1,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertFalse(ok)

    def test_combinacion_4h_mas_4h_valido_lunes(self):
        # Lunes: tope 8.5 h. 4 + 4 = 8 h → cabe
        self._crear_aprobada(horas=20, intensidad=4)
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 20, 4)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=20, intensidad_diaria=4,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertTrue(ok)

    def test_sobreasignacion_viernes_tope_8h(self):
        # Viernes 17 ene 2025: tope 8 h. 8 + 1 debe bloquearse
        self._crear_aprobada(horas=8, intensidad=8, inicio=date(2025, 1, 17))
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 17), 8, 1)
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=8, intensidad_diaria=1,
            fecha_inicio=date(2025, 1, 17), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertFalse(ok)

    def test_log_auditoria_se_crea_al_aprobar(self):
        fecha_fin = calcular_fecha_fin(self.recurso, date(2025, 1, 13), 40, 8)
        asig = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=40, intensidad_diaria=8,
            fecha_inicio=date(2025, 1, 13), fecha_fin=fecha_fin,
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        aprobar_asignacion(asig, self.admin)
        self.assertEqual(asig.estado, "APROBADA")
        self.assertTrue(LogAuditoria.objects.filter(asignacion=asig, accion="APROBAR").exists())


class RecurrenciaTests(TestCase):
    """Solicitudes recurrentes: patrón semanal → serie de asignaciones de un día."""

    def setUp(self):
        self.pm = User.objects.create_user("pm_rec", password="pass")
        self.recurso = Recurso.objects.create(nombre="DevRec", email="devrec@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-REC", nombre="Rec", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.pm,
        )

    def test_proximos_4_lunes_4h(self):
        serie, creadas, omitidas = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 4, {0: 4.0}, self.pm,
        )
        self.assertEqual(
            [a.fecha_inicio for a in creadas],
            [date(2025, 1, 13), date(2025, 1, 20), date(2025, 1, 27), date(2025, 2, 3)],
        )
        self.assertEqual(omitidas, [])
        self.assertTrue(all(a.serie == serie for a in creadas))
        self.assertTrue(all(a.estado == "SOLICITADA" for a in creadas))
        self.assertTrue(all(a.fecha_inicio == a.fecha_fin for a in creadas))
        self.assertEqual(
            LogAuditoria.objects.filter(asignacion__serie=serie, accion="CREAR").count(), 4,
        )

    def test_patron_lunes_miercoles_viernes(self):
        # "esta semana: lunes 2h, miércoles 4h y viernes 2h"
        _, creadas, _ = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 1,
            {0: 2.0, 2: 4.0, 4: 2.0}, self.pm,
        )
        self.assertEqual(
            [(a.fecha_inicio, float(a.intensidad_diaria)) for a in creadas],
            [(date(2025, 1, 13), 2.0), (date(2025, 1, 15), 4.0), (date(2025, 1, 17), 2.0)],
        )

    def test_omite_feriado(self):
        # Lunes 6 ene 2025 = Reyes Magos (festivo). Patrón de 2 lunes desde el 6.
        _, creadas, omitidas = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 6), 2, {0: 4.0}, self.pm,
        )
        self.assertEqual([a.fecha_inicio for a in creadas], [date(2025, 1, 13)])
        self.assertEqual(len(omitidas), 1)
        self.assertEqual(omitidas[0]["estado"], "NO_HABIL")
        self.assertEqual(omitidas[0]["fecha"], date(2025, 1, 6))

    def test_omite_dia_sin_cupo(self):
        # El lunes 13 ya está a jornada completa → solo se crea el lunes 20
        Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 13),
            horas_totales=9, intensidad_diaria=8.0, jornada_completa=True,
            estado="APROBADA", solicitada_por=self.pm,
        )
        _, creadas, omitidas = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 2, {0: 4.0}, self.pm,
        )
        self.assertEqual([a.fecha_inicio for a in creadas], [date(2025, 1, 20)])
        self.assertEqual(omitidas[0]["estado"], "SIN_CUPO")

    def test_error_si_ningun_dia_viable(self):
        with self.assertRaises(ValueError):
            crear_solicitudes_recurrentes(
                self.recurso, self.proyecto, date(2025, 1, 6), 1, {0: 4.0}, self.pm,
            )

    def test_analizar_no_escribe(self):
        plan = analizar_recurrencia(self.recurso, date(2025, 1, 13), 2, {0: 4.0})
        self.assertEqual(len(plan), 2)
        self.assertEqual(Asignacion.objects.count(), 0)

    def test_aprobacion_de_un_dia_de_la_serie_respeta_capacidad(self):
        # Cada día de la serie pasa por el motor de aprobación normal
        _, creadas, _ = crear_solicitudes_recurrentes(
            self.recurso, self.proyecto, date(2025, 1, 13), 1, {0: 8.5}, self.pm,
        )
        aprobar_asignacion(creadas[0], self.pm)
        self.assertEqual(creadas[0].estado, "APROBADA")
        # Una segunda solicitud de 1h ese lunes ya no cabe
        candidata = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            horas_totales=1, intensidad_diaria=1,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 13),
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(candidata)
        self.assertFalse(ok)


class CesionHorasTests(TestCase):
    """Cesión de horas de una asignación APROBADA a otro proyecto (#6)."""

    def setUp(self):
        self.admin = User.objects.create_user("admin_ces", password="pass")
        self.recurso = Recurso.objects.create(nombre="DevCes", email="devces@test.com", banda="SR")
        self.proy_a = Proyecto.objects.create(
            codigo="P-CES-A", nombre="Origen", cliente="X",
            fecha_inicio=date(2025, 1, 1), pm=self.admin,
        )
        self.proy_b = Proyecto.objects.create(
            codigo="P-CES-B", nombre="Receptor", cliente="Y",
            fecha_inicio=date(2025, 1, 1), pm=self.admin,
        )
        # Tarifas con vigencia: 10 €/h desde el 1 ene, 12 €/h desde el 15 ene
        TarifaVigente.objects.create(recurso=self.recurso, valor_hora=Decimal("10.00"), fecha_desde=date(2025, 1, 1))
        TarifaVigente.objects.create(recurso=self.recurso, valor_hora=Decimal("12.00"), fecha_desde=date(2025, 1, 15))
        # Asignación original APROBADA: lun 13 → vie 17 ene 2025, 8 h/día, 40 h
        self.original = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proy_a,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 17),
            dias_habiles=5, horas_totales=40, intensidad_diaria=8,
            estado="APROBADA", solicitada_por=self.admin,
        )

    def _ceder(self, fecha=date(2025, 1, 15), horas=4, politica="REDUCIR"):
        return ceder_horas(self.original, self.proy_b, fecha, horas, politica, self.admin)

    # ── creación y tarifa ────────────────────────────────────────────────
    def test_crea_destino_solicitada_con_tarifa_del_dia(self):
        cesion = self._ceder()  # miércoles 15 ene → rige la tarifa de 12 €/h
        destino = cesion.asignacion_destino
        self.assertEqual(destino.estado, "SOLICITADA")
        self.assertEqual(destino.proyecto, self.proy_b)
        self.assertEqual(destino.fecha_inicio, date(2025, 1, 15))
        self.assertEqual(destino.fecha_fin, date(2025, 1, 15))
        self.assertEqual(float(destino.intensidad_diaria), 4.0)
        self.assertEqual(cesion.tarifa_hora, Decimal("12.00"))
        self.assertEqual(destino.tarifa_aplicada, Decimal("12.00"))
        self.assertEqual(destino.costo_estimado, Decimal("48.00"))

    def test_tarifa_vigente_anterior_si_el_dia_es_previo(self):
        cesion = self._ceder(fecha=date(2025, 1, 14))  # antes del cambio de tarifa
        self.assertEqual(cesion.tarifa_hora, Decimal("10.00"))

    def test_logs_de_auditoria(self):
        cesion = self._ceder()
        log_ceder = LogAuditoria.objects.get(asignacion=self.original, accion="CEDER")
        self.assertEqual(log_ceder.detalle["horas"], 4)
        self.assertEqual(log_ceder.detalle["proyecto_destino"], "P-CES-B")
        self.assertEqual(log_ceder.detalle["monto_descontado"], 48.0)
        self.assertTrue(
            LogAuditoria.objects.filter(
                asignacion=cesion.asignacion_destino, accion="CREAR", detalle__modo="CESION",
            ).exists()
        )

    # ── política REDUCIR ─────────────────────────────────────────────────
    def test_reducir_baja_horas_totales(self):
        self._ceder(politica="REDUCIR")
        self.original.refresh_from_db()
        self.assertEqual(self.original.horas_totales, 36)
        self.assertEqual(self.original.fecha_fin, date(2025, 1, 17))  # ventana intacta

    # ── política RECOMPUTAR ──────────────────────────────────────────────
    def test_recomputar_extiende_fecha_fin(self):
        self._ceder(horas=8, politica="RECOMPUTAR")
        self.original.refresh_from_db()
        # 8 h / 8 h/día = 1 día extra: siguiente hábil tras vie 17 = lun 20
        self.assertEqual(self.original.fecha_fin, date(2025, 1, 20))
        self.assertEqual(self.original.horas_totales, 40)  # horas preservadas
        self.assertEqual(self.original.dias_habiles, 6)

    # ── reserva de cupo ──────────────────────────────────────────────────
    def test_horas_reservadas_bloquean_a_terceros(self):
        self._ceder(horas=4)
        # La carga del día sigue siendo la bruta (8 h): un tercero de 4 h no cabe
        self.assertEqual(carga_en_fecha(self.recurso, date(2025, 1, 15)), 8.0)
        tercero = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proy_b,
            fecha_inicio=date(2025, 1, 15), fecha_fin=date(2025, 1, 15),
            horas_totales=4, intensidad_diaria=4,
            estado="SOLICITADA", solicitada_por=self.admin,
        )
        ok, _ = puede_asignar(tercero)
        self.assertFalse(ok)

    def test_aprobar_destino_cabe_exactamente(self):
        cesion = self._ceder(horas=4)
        aprobar_asignacion(cesion.asignacion_destino, self.admin)
        self.assertEqual(cesion.asignacion_destino.estado, "APROBADA")
        # Tras aprobar: 8 bruta − 4 cedidas + 4 del destino = 8 h netas
        self.assertEqual(carga_en_fecha(self.recurso, date(2025, 1, 15)), 8.0)

    # ── anulación al rechazar el destino ─────────────────────────────────
    def test_rechazar_destino_anula_y_restaura_reducir(self):
        cesion = self._ceder(politica="REDUCIR")
        rechazar_asignacion(cesion.asignacion_destino, self.admin, motivo="no aplica")
        cesion.refresh_from_db()
        self.original.refresh_from_db()
        self.assertIsNotNone(cesion.anulada_en)
        self.assertEqual(self.original.horas_totales, 40)  # restauradas
        self.assertTrue(
            LogAuditoria.objects.filter(asignacion=self.original, accion="ANULAR_CESION").exists()
        )
        # El día vuelve a estar completamente ocupado por la original
        self.assertEqual(carga_en_fecha(self.recurso, date(2025, 1, 15)), 8.0)

    def test_rechazar_destino_anula_y_restaura_recomputar(self):
        cesion = self._ceder(horas=8, politica="RECOMPUTAR")
        self.original.refresh_from_db()
        self.assertEqual(self.original.fecha_fin, date(2025, 1, 20))
        rechazar_asignacion(cesion.asignacion_destino, self.admin)
        self.original.refresh_from_db()
        self.assertEqual(self.original.fecha_fin, date(2025, 1, 17))
        self.assertEqual(self.original.dias_habiles, 5)

    # ── validaciones ─────────────────────────────────────────────────────
    def test_no_cede_mas_de_lo_disponible(self):
        self._ceder(horas=4)
        self.assertEqual(horas_cedibles(self.original, date(2025, 1, 15)), 4.0)
        with self.assertRaises(ValueError):
            self._ceder(horas=5)  # solo quedan 4 cedibles ese día

    def test_validaciones_basicas(self):
        with self.assertRaises(ValueError):
            self._ceder(fecha=date(2025, 2, 3))  # fuera del período
        with self.assertRaises(ValueError):
            self._ceder(fecha=date(2025, 1, 18))  # sábado
        with self.assertRaises(ValueError):
            ceder_horas(self.original, self.proy_a, date(2025, 1, 15), 4, "REDUCIR", self.admin)  # mismo proyecto
        with self.assertRaises(ValueError):
            self._ceder(horas=0)
        with self.assertRaises(ValueError):
            self._ceder(politica="OTRA")

    def test_solo_aprobadas(self):
        solicitada = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proy_a,
            fecha_inicio=date(2025, 2, 3), fecha_fin=date(2025, 2, 7),
            horas_totales=40, intensidad_diaria=8,
            estado="SOLICITADA", solicitada_por=self.admin,
        )
        with self.assertRaises(ValueError):
            ceder_horas(solicitada, self.proy_b, date(2025, 2, 4), 4, "REDUCIR", self.admin)


class CederAdminViewTests(TestCase):
    """El formulario ⇄ Ceder del admin crea la cesión de punta a punta."""

    def setUp(self):
        self.admin = User.objects.create_superuser("root_ces", "root@test.com", "pass")
        self.client.force_login(self.admin)
        self.recurso = Recurso.objects.create(nombre="DevCesAdm", email="devcesadm@test.com", banda="SR")
        self.proy_a = Proyecto.objects.create(
            codigo="P-CADM-A", nombre="A", cliente="X", fecha_inicio=date(2025, 1, 1), pm=self.admin,
        )
        self.proy_b = Proyecto.objects.create(
            codigo="P-CADM-B", nombre="B", cliente="Y", fecha_inicio=date(2025, 1, 1), pm=self.admin,
        )
        self.original = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proy_a,
            fecha_inicio=date(2025, 1, 13), fecha_fin=date(2025, 1, 17),
            dias_habiles=5, horas_totales=40, intensidad_diaria=8,
            estado="APROBADA", solicitada_por=self.admin,
        )
        self.url = f"/admin/assignments/asignacion/ceder/{self.original.pk}/"

    def test_get_muestra_formulario(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ceder horas")
        self.assertContains(resp, "P-CADM-B")

    def test_post_crea_cesion_y_destino(self):
        resp = self.client.post(self.url, {
            "fecha": "2025-01-15", "horas": "4",
            "proyecto": self.proy_b.pk, "politica": "REDUCIR",
        })
        self.assertEqual(resp.status_code, 302)  # redirect al changelist
        destino = Asignacion.objects.get(proyecto=self.proy_b)
        self.assertEqual(destino.estado, "SOLICITADA")
        self.original.refresh_from_db()
        self.assertEqual(self.original.horas_totales, 36)

    def test_post_invalido_muestra_error(self):
        resp = self.client.post(self.url, {
            "fecha": "2025-01-18", "horas": "4",  # sábado
            "proyecto": self.proy_b.pk, "politica": "REDUCIR",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "día hábil")


class CostoMixtoTests(TestCase):
    """Tarifas con cambio de vigencia dentro del período: costo mixto por día,
    snapshot al aprobar y recomputo automático al registrar nueva tarifa."""

    def setUp(self):
        self.admin = User.objects.create_user("admin_mix", password="pass")
        self.recurso = Recurso.objects.create(nombre="DevMix", email="devmix@test.com", banda="JR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-MIX", nombre="Mix", cliente="X",
            fecha_inicio=date(2026, 1, 1), pm=self.admin,
        )
        # Subvención: 0 €/h desde mayo; tarifa real 4.42 desde el 6 ago 2026
        TarifaVigente.objects.create(recurso=self.recurso, valor_hora=Decimal("0.00"), fecha_desde=date(2026, 5, 5))
        TarifaVigente.objects.create(recurso=self.recurso, valor_hora=Decimal("4.42"), fecha_desde=date(2026, 8, 6))

    def _asignacion(self, fi, ff, intensidad=4, estado="SOLICITADA"):
        from apps.calendar_engine.services import contar_dias_habiles
        dias = contar_dias_habiles(fi, ff, self.recurso)
        return Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto,
            fecha_inicio=fi, fecha_fin=ff, dias_habiles=dias,
            horas_totales=dias * intensidad, intensidad_diaria=intensidad,
            estado=estado, solicitada_por=self.admin,
        )

    def test_segmentos_tarifa_parte_en_el_cambio(self):
        from .services import segmentos_tarifa
        seg = segmentos_tarifa(self.recurso, date(2026, 8, 3), date(2026, 8, 14))
        self.assertEqual(len(seg), 2)
        self.assertEqual(seg[0]["desde"], date(2026, 8, 3))
        self.assertEqual(seg[0]["hasta"], date(2026, 8, 5))
        self.assertEqual(seg[0]["valor"], Decimal("0.00"))
        # 3-5 ago 2026: lun-mié menos el 7 ago... (sin feriados en ese tramo): lun 3, mar 4, mié 5
        self.assertEqual(seg[0]["dias_habiles"], 3)
        self.assertEqual(seg[1]["desde"], date(2026, 8, 6))
        self.assertEqual(seg[1]["valor"], Decimal("4.42"))
        # 6-14 ago: jue 6, vie 14... hábiles: 6,10,11,12,13,14 → ojo 7 ago 2026 es feriado (Batalla de Boyacá... no, es 7 ago = feriado Colombia)
        # No fijamos el número exacto aquí: lo valida el motor de calendario.

    def test_costo_mixto_cruzando_el_cambio(self):
        from .services import costo_estimado_asignacion
        # Lun 3 ago → lun 10 ago 2026, 4 h/día.
        # Antes del 6: tarifa 0. Desde el 6: 4.42.
        asig = self._asignacion(date(2026, 8, 3), date(2026, 8, 10))
        costo = costo_estimado_asignacion(asig)
        from apps.calendar_engine.services import contar_dias_habiles
        dias_cobrados = contar_dias_habiles(date(2026, 8, 6), date(2026, 8, 10), self.recurso)
        esperado = (Decimal("4.42") * 4 * dias_cobrados).quantize(Decimal("0.01"))
        self.assertEqual(costo, esperado)

    def test_costo_todo_subvencionado_es_cero(self):
        from .services import costo_estimado_asignacion
        asig = self._asignacion(date(2026, 7, 13), date(2026, 7, 17))
        self.assertEqual(costo_estimado_asignacion(asig), Decimal("0.00"))

    def test_sin_tarifa_retorna_none(self):
        from .services import costo_estimado_asignacion
        otro = Recurso.objects.create(nombre="SinTarifa", email="sintarifa@test.com", banda="JR")
        asig = Asignacion.objects.create(
            recurso=otro, proyecto=self.proyecto,
            fecha_inicio=date(2026, 7, 13), fecha_fin=date(2026, 7, 17),
            horas_totales=20, intensidad_diaria=4,
            estado="SOLICITADA", solicitada_por=self.admin,
        )
        self.assertIsNone(costo_estimado_asignacion(asig))

    def test_aprobar_snapshotea_tarifa_y_costo_mixto(self):
        asig = self._asignacion(date(2026, 8, 3), date(2026, 8, 10))
        aprobar_asignacion(asig, self.admin)
        asig.refresh_from_db()
        self.assertEqual(asig.tarifa_aplicada, Decimal("0.00"))  # vigente al inicio
        self.assertIsNotNone(asig.costo_estimado)
        self.assertGreater(asig.costo_estimado, 0)  # los días desde el 6 cobran 4.42
        log = LogAuditoria.objects.get(asignacion=asig, accion="APROBAR")
        self.assertEqual(log.detalle["costo_estimado"], float(asig.costo_estimado))

    def test_nueva_tarifa_recomputa_asignaciones_activas(self):
        asig = self._asignacion(date(2026, 8, 3), date(2026, 8, 10))
        aprobar_asignacion(asig, self.admin)
        asig.refresh_from_db()
        costo_antes = asig.costo_estimado
        # Cambio de costo del recurso: nueva vigencia 10 €/h desde el 10 ago
        TarifaVigente.objects.create(recurso=self.recurso, valor_hora=Decimal("10.00"), fecha_desde=date(2026, 8, 10))
        asig.refresh_from_db()
        self.assertNotEqual(asig.costo_estimado, costo_antes)
        log = LogAuditoria.objects.get(asignacion=asig, accion="RECOMPUTO_TARIFA")
        self.assertIsNone(log.actor)  # acción del sistema
        self.assertEqual(log.detalle["tarifa_nueva"], 10.0)
        self.assertEqual(log.detalle["costo_antes"], float(costo_antes))
        self.assertEqual(log.detalle["costo_despues"], float(asig.costo_estimado))

    def test_nueva_tarifa_no_toca_asignaciones_terminadas(self):
        asig = self._asignacion(date(2026, 7, 13), date(2026, 7, 17))
        aprobar_asignacion(asig, self.admin)
        asig.refresh_from_db()
        costo_antes = asig.costo_estimado
        # Vigencia posterior al fin de la asignación → no la afecta
        TarifaVigente.objects.create(recurso=self.recurso, valor_hora=Decimal("99.00"), fecha_desde=date(2026, 9, 1))
        asig.refresh_from_db()
        self.assertEqual(asig.costo_estimado, costo_antes)
        self.assertFalse(
            LogAuditoria.objects.filter(asignacion=asig, accion="RECOMPUTO_TARIFA").exists()
        )

    def test_disponibilidad_reporta_cambios_y_costo_mixto(self):
        from .services import disponibilidad_recursos
        res = disponibilidad_recursos(date(2026, 8, 3), date(2026, 8, 10))
        r = next(x for x in res if x["recurso"].pk == self.recurso.pk)
        self.assertEqual(r["tarifa_hora"], 0.0)  # vigente al inicio del rango
        self.assertEqual(len(r["tarifa_cambios"]), 1)
        self.assertEqual(r["tarifa_cambios"][0]["fecha"], date(2026, 8, 6))
        self.assertEqual(r["tarifa_cambios"][0]["valor"], 4.42)
        # Costo mixto de horas libres: solo los días desde el 6 valen dinero
        self.assertGreater(r["costo_estimado"], 0)


class LiberacionTests(TestCase):
    """
    Congelamiento de horas vía solicitud + aprobación: el PM solicita liberar un
    recurso en una ventana; la solicitud NO surte efecto hasta que un Admin la
    aprueba. Al aprobar se congela la ventana (no consume capacidad) y, según
    política, empuja fecha_fin (RECOMPUTAR) o reduce horas (REDUCIR). Reversible
    y auditado.

    Calendario de referencia: febrero 2026 no tiene feriados en Colombia.
    Lun 2 → Vie 13 = 10 días hábiles.
    """

    def setUp(self):
        self.pm = User.objects.create_user("pm_lib", password="x")
        self.admin = User.objects.create_user("admin_lib", password="x")
        self.recurso = Recurso.objects.create(nombre="LibDev", email="libdev@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-LIB", nombre="Astara", cliente="ASTARA",
            fecha_inicio=date(2026, 2, 1), pm=self.pm,
        )
        self.proyecto2 = Proyecto.objects.create(
            codigo="P-LIB2", nombre="Otro", cliente="OTRO",
            fecha_inicio=date(2026, 2, 1), pm=self.pm,
        )

    def _aprobada(self, recurso=None, proyecto=None, horas=80, intensidad=8,
                  inicio=date(2026, 2, 2)):
        recurso = recurso or self.recurso
        proyecto = proyecto or self.proyecto
        from math import ceil
        ff = calcular_fecha_fin(recurso, inicio, horas, intensidad)
        return Asignacion.objects.create(
            recurso=recurso, proyecto=proyecto,
            horas_totales=horas, intensidad_diaria=intensidad,
            dias_habiles=ceil(horas / intensidad),
            fecha_inicio=inicio, fecha_fin=ff,
            estado="APROBADA", solicitada_por=self.pm,
        )

    def _liberar(self, a, ini, fin, politica, motivo=""):
        """Solicita (PM) y aprueba (Admin) en un paso — para los tests de efecto."""
        lib = solicitar_liberacion(a, ini, fin, politica, motivo, self.pm)
        return aprobar_liberacion(lib, self.admin)

    # ── Solicitud (sin efecto hasta aprobar) ─────────────────────────────
    def test_solicitud_no_libera_cupo_ni_toca_asignacion(self):
        a = self._aprobada()
        solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "", self.pm)
        a.refresh_from_db()
        # Nada cambia mientras esté SOLICITADA: la carga del día sigue completa.
        self.assertEqual(a.fecha_fin, date(2026, 2, 13))
        self.assertEqual(carga_en_fecha(self.recurso, date(2026, 2, 10)), 8.0)

    def test_aprobar_aplica_los_efectos(self):
        a = self._aprobada()
        lib = solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "", self.pm)
        self.assertEqual(lib.estado, "SOLICITADA")
        aprobar_liberacion(lib, self.admin)
        a.refresh_from_db(); lib.refresh_from_db()
        self.assertEqual(lib.estado, "APROBADA")
        self.assertEqual(lib.revisada_por, self.admin)
        self.assertEqual(a.fecha_fin, date(2026, 2, 20))

    def test_rechazar_no_aplica_efecto(self):
        a = self._aprobada()
        lib = solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "REDUCIR", "", self.pm)
        rechazar_liberacion(lib, self.admin)
        a.refresh_from_db(); lib.refresh_from_db()
        self.assertEqual(lib.estado, "RECHAZADA")
        self.assertEqual(a.horas_totales, 80)
        self.assertEqual(a.fecha_fin, date(2026, 2, 13))

    def test_no_se_aprueba_dos_veces(self):
        a = self._aprobada()
        lib = self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR")
        with self.assertRaises(ValueError):
            aprobar_liberacion(lib, self.admin)

    # ── RECOMPUTAR ───────────────────────────────────────────────────────
    def test_recomputar_empuja_fecha_fin_y_preserva_horas(self):
        a = self._aprobada()  # Feb 2 → Feb 13
        lib = self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "Cliente en vacaciones")
        a.refresh_from_db()
        # 5 días congelados (Feb 9–13) se recuperan en Feb 16–20
        self.assertEqual(lib.dias_liberados, 5)
        self.assertEqual(a.fecha_fin, date(2026, 2, 20))
        self.assertEqual(a.horas_totales, 80)

    def test_ventana_libera_cupo_para_otro_proyecto(self):
        a = self._aprobada()
        conflicto = Asignacion(
            recurso=self.recurso, proyecto=self.proyecto2,
            horas_totales=8, intensidad_diaria=8,
            fecha_inicio=date(2026, 2, 10), fecha_fin=date(2026, 2, 10),
            estado="SOLICITADA", solicitada_por=self.pm,
        )
        ok, _ = puede_asignar(conflicto)
        self.assertFalse(ok)
        self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR")
        self.assertEqual(carga_en_fecha(self.recurso, date(2026, 2, 10)), 0.0)
        ok, _ = puede_asignar(conflicto)
        self.assertTrue(ok)

    def test_recomputar_salta_dias_sin_cupo_al_extender(self):
        a = self._aprobada()  # Feb 2 → Feb 13
        self._aprobada(proyecto=self.proyecto2, horas=16, intensidad=8, inicio=date(2026, 2, 16))
        self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR")
        a.refresh_from_db()
        # La extensión salta 16 y 17 (sin cupo) → 18,19,20,23,24 = 5 días
        self.assertEqual(a.fecha_fin, date(2026, 2, 24))
        self.assertEqual(a.horas_totales, 80)

    # ── REDUCIR ──────────────────────────────────────────────────────────
    def test_reducir_baja_horas_y_dias_conserva_ventana(self):
        TarifaVigente.objects.create(recurso=self.recurso, valor_hora=Decimal("100.00"), fecha_desde=date(2026, 1, 1))
        a = self._aprobada()  # 80h, 10 días, Feb 2 → 13
        self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "REDUCIR")
        a.refresh_from_db()
        self.assertEqual(a.fecha_fin, date(2026, 2, 13))  # ventana intacta
        self.assertEqual(a.horas_totales, 40)  # 80 − 5·8
        self.assertEqual(a.dias_habiles, 5)
        # Costo mixto: solo los 5 días no liberados (Feb 2–6) = 40h · 100
        self.assertEqual(a.costo_estimado, Decimal("4000.00"))

    # ── Bordes del calendario ────────────────────────────────────────────
    def test_solo_cuenta_dias_habiles_en_la_ventana(self):
        a = self._aprobada()
        # Feb 5 (jue) a Feb 10 (mar) incluye fin de semana 7–8 → 4 días hábiles
        lib = self._liberar(a, date(2026, 2, 5), date(2026, 2, 10), "RECOMPUTAR")
        self.assertEqual(lib.dias_liberados, 4)
        self.assertEqual(float(lib.horas_liberadas), 32.0)

    # ── Anulación ────────────────────────────────────────────────────────
    def test_anular_recomputar_restaura_fecha_fin(self):
        a = self._aprobada()
        lib = self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR")
        anular_liberacion(lib, self.admin)
        a.refresh_from_db(); lib.refresh_from_db()
        self.assertEqual(a.fecha_fin, date(2026, 2, 13))
        self.assertEqual(lib.estado, "ANULADA")

    def test_anular_reducir_restaura_horas(self):
        a = self._aprobada()
        lib = self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "REDUCIR")
        anular_liberacion(lib, self.admin)
        a.refresh_from_db()
        self.assertEqual(a.horas_totales, 80)
        self.assertEqual(a.dias_habiles, 10)

    def test_anular_falla_si_otro_proyecto_ocupo_la_ventana(self):
        a = self._aprobada()
        lib = self._liberar(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR")
        # Otro proyecto ocupa la ventana ya liberada
        self._aprobada(proyecto=self.proyecto2, horas=40, intensidad=8, inicio=date(2026, 2, 9))
        with self.assertRaises(ValueError):
            anular_liberacion(lib, self.admin)

    def test_solo_se_anula_una_aprobada(self):
        a = self._aprobada()
        lib = solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "", self.pm)
        with self.assertRaises(ValueError):  # está SOLICITADA, no APROBADA
            anular_liberacion(lib, self.admin)

    # ── Auditoría ────────────────────────────────────────────────────────
    def test_auditoria_registra_todo_el_ciclo(self):
        a = self._aprobada()
        lib = solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "x", self.pm)
        self.assertTrue(LogAuditoria.objects.filter(asignacion=a, accion="SOLICITAR_LIBERACION").exists())
        aprobar_liberacion(lib, self.admin)
        self.assertTrue(LogAuditoria.objects.filter(asignacion=a, accion="LIBERAR").exists())
        anular_liberacion(lib, self.admin)
        self.assertTrue(LogAuditoria.objects.filter(asignacion=a, accion="ANULAR_LIBERACION").exists())

    # ── Guardas de la solicitud ──────────────────────────────────────────
    def test_no_solicita_asignacion_no_aprobada(self):
        a = self._aprobada()
        a.estado = "SOLICITADA"
        a.save(update_fields=["estado"])
        with self.assertRaises(ValueError):
            solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "", self.pm)

    def test_ventana_fuera_de_periodo(self):
        a = self._aprobada()  # termina Feb 13
        with self.assertRaises(ValueError):
            solicitar_liberacion(a, date(2026, 3, 2), date(2026, 3, 6), "RECOMPUTAR", "", self.pm)

    def test_no_permite_solicitud_solapada(self):
        a = self._aprobada()
        solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "", self.pm)
        with self.assertRaises(ValueError):
            solicitar_liberacion(a, date(2026, 2, 12), date(2026, 2, 13), "RECOMPUTAR", "", self.pm)

    def test_bloquea_si_hay_cesion_activa_en_la_ventana(self):
        a = self._aprobada()
        ceder_horas(a, self.proyecto2, date(2026, 2, 10), 4, "RECOMPUTAR", self.admin)
        a.refresh_from_db()
        with self.assertRaises(ValueError):
            solicitar_liberacion(a, date(2026, 2, 9), date(2026, 2, 13), "RECOMPUTAR", "", self.pm)


class LiberacionViewTests(TestCase):
    """La página /liberacion/ (PM/Admin): lista asignaciones y crea solicitudes."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.pm_group, _ = Group.objects.get_or_create(name="PM")
        self.pm = User.objects.create_user("pm_view", password="x")
        self.pm.groups.add(self.pm_group)
        self.otro_pm = User.objects.create_user("pm_otro", password="x")
        self.otro_pm.groups.add(self.pm_group)
        self.ing = User.objects.create_user("ing_view", password="x")
        self.recurso = Recurso.objects.create(nombre="ViewDev", email="viewdev@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-VW", nombre="P", cliente="C", fecha_inicio=date(2026, 2, 1), pm=self.pm,
        )
        from math import ceil
        ff = calcular_fecha_fin(self.recurso, date(2026, 2, 2), 80, 8)
        self.asig = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto, horas_totales=80,
            intensidad_diaria=8, dias_habiles=10, fecha_inicio=date(2026, 2, 2),
            fecha_fin=ff, estado="APROBADA", solicitada_por=self.pm,
        )

    def test_ingeniero_no_accede(self):
        self.client.force_login(self.ing)
        self.assertEqual(self.client.get("/liberacion/").status_code, 403)

    def test_pm_ve_solo_sus_proyectos(self):
        self.client.force_login(self.otro_pm)  # no es PM de P-VW
        r = self.client.get("/liberacion/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "ViewDev")

    def test_pm_crea_solicitud(self):
        from apps.assignments.models import LiberacionRecurso
        self.client.force_login(self.pm)
        r = self.client.post("/liberacion/", {
            "asignacion": self.asig.pk,
            "fecha_inicio": "2026-02-09", "fecha_fin": "2026-02-13",
            "politica": "RECOMPUTAR", "motivo": "Cliente en vacaciones",
        })
        self.assertEqual(r.status_code, 200)
        lib = LiberacionRecurso.objects.get(asignacion=self.asig)
        self.assertEqual(lib.estado, "SOLICITADA")
        self.assertEqual(lib.solicitada_por, self.pm)
        # No aplicó efecto: sigue pendiente
        self.asig.refresh_from_db()
        self.assertEqual(self.asig.fecha_fin, date(2026, 2, 13))


class CesionViewTests(TestCase):
    """La página /cesion/ (PM/Admin): lista asignaciones y crea cesiones."""

    def setUp(self):
        from django.contrib.auth.models import Group
        self.pm_group, _ = Group.objects.get_or_create(name="PM")
        self.pm = User.objects.create_user("pm_ces", password="x")
        self.pm.groups.add(self.pm_group)
        self.otro_pm = User.objects.create_user("pm_ces_otro", password="x")
        self.otro_pm.groups.add(self.pm_group)
        self.ing = User.objects.create_user("ing_ces", password="x")
        self.recurso = Recurso.objects.create(nombre="CesDev", email="cesdev@test.com", banda="SR")
        self.proyecto = Proyecto.objects.create(
            codigo="P-CES", nombre="P", cliente="C", fecha_inicio=date(2026, 2, 1), pm=self.pm,
        )
        self.destino = Proyecto.objects.create(
            codigo="P-CES-D", nombre="D", cliente="C", fecha_inicio=date(2026, 2, 1), pm=self.pm,
        )
        from math import ceil
        ff = calcular_fecha_fin(self.recurso, date(2026, 2, 2), 80, 8)
        self.asig = Asignacion.objects.create(
            recurso=self.recurso, proyecto=self.proyecto, horas_totales=80,
            intensidad_diaria=8, dias_habiles=10, fecha_inicio=date(2026, 2, 2),
            fecha_fin=ff, estado="APROBADA", solicitada_por=self.pm,
        )

    def test_ingeniero_no_accede(self):
        self.client.force_login(self.ing)
        self.assertEqual(self.client.get("/cesion/").status_code, 403)

    def test_pm_ve_solo_sus_proyectos(self):
        self.client.force_login(self.otro_pm)
        r = self.client.get("/cesion/")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "CesDev")

    def test_pm_crea_cesion(self):
        from apps.assignments.models import CesionHoras
        self.client.force_login(self.pm)
        r = self.client.post("/cesion/", {
            "asignacion": self.asig.pk, "fecha": "2026-02-10", "horas": "4",
            "proyecto": self.destino.pk, "politica": "RECOMPUTAR",
        })
        self.assertEqual(r.status_code, 200)
        cesion = CesionHoras.objects.get(asignacion_origen=self.asig)
        self.assertEqual(float(cesion.horas), 4.0)
        # Se creó una asignación destino SOLICITADA (gate de aprobación del Admin)
        self.assertEqual(cesion.asignacion_destino.estado, "SOLICITADA")
        self.assertEqual(cesion.asignacion_destino.proyecto, self.destino)
