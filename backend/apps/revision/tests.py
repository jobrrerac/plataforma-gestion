"""Las reglas deterministas del triaje.

Dos casos reales las motivaron y aquí están los dos, con sus números:

    Estudio · 7,5 h · «Escoger certificación y ver qué ruta de estudio tomar»
    INT-DEPART · 8,5 h de 8,5 · «muchas tareas»

Ambos se aprobaron sin que nada avisara. Si una refactorización deja de
marcarlos, estas pruebas fallan y por eso están escritas por su nombre.

Lo demás que se cuida:

- que las reglas **no** marquen lo normal, que es lo que decide si la pantalla
  sirve o se vuelve ruido que se ignora;
- que la banda de un día salga de lo que quien mira puede firmar, no de
  renglones de otro PM;
- que el número de consultas no crezca con el tamaño de la cola.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.assignments.models import Asignacion
from apps.core.models import Proyecto, Recurso
from apps.legalizacion import services as svc
from apps.legalizacion.models import DiaLegalizado, RegistroHoras, TipoActividad
from apps.revision import senales as sn
from apps.revision.api import Contexto, clasificar

LUNES = date(2026, 9, 14)  # jornada de 8,5 h


class BaseTriaje(TestCase):
    def setUp(self):
        self.pm = User.objects.create_user("pm_tri", "pm@test.com", "x")
        self.cliente = Proyecto.objects.create(
            codigo="V-25188808/Q", nombre="Simulador", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm, facturable=True,
        )
        self.interno = Proyecto.objects.create(
            codigo="INT-DEPART", nombre="Actividades Departamentales", cliente="Inetum",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=self.pm, facturable=False,
        )
        self.recurso = Recurso.objects.create(
            nombre="Guzman-Mejia Daniel-Fernando", email="daniel@test.com", banda="SR",
        )
        self.de_proyecto = TipoActividad.objects.create(
            nombre="Proyecto", requiere_proyecto=True,
        )
        self.estudio = TipoActividad.objects.create(
            nombre="Estudio", requiere_proyecto=False,
        )

    def _dia(self, fecha=LUNES, jornada="8.5"):
        return DiaLegalizado.objects.create(
            recurso=self.recurso, fecha=fecha, estado=DiaLegalizado.REGISTRADO,
            total_horas=Decimal(jornada), jornada_esperada=Decimal(jornada),
        )

    def _renglon(self, dia, horas, detalle, proyecto=None, tipo=None):
        return RegistroHoras.objects.create(
            dia=dia, tipo_actividad=tipo or self.de_proyecto, proyecto=proyecto,
            horas=Decimal(str(horas)), detalle=detalle,
        )

    def _plan(self, proyecto, intensidad, inicio=LUNES, fin=LUNES, estado="APROBADA"):
        return Asignacion.objects.create(
            recurso=self.recurso, proyecto=proyecto, modo_asignacion="RANGO",
            fecha_inicio=inicio, fecha_fin=fin, dias_habiles=1,
            horas_totales=int(float(intensidad)) or 1,
            intensidad_diaria=Decimal(str(intensidad)),
            estado=estado, solicitada_por=self.pm,
        )

    def _evaluar(self, registro, dia):
        return sn.evaluar(registro, dia, Contexto([dia]))

    def _codigos(self, registro, dia):
        return {s.codigo for s in self._evaluar(registro, dia).senales}


class LosDosCasosRealesTests(BaseTriaje):
    def test_el_estudio_de_siete_horas_y_media(self):
        """7,5 h de 8,5 para escoger una certificación. Se aprobó sin aviso."""
        dia = self._dia()
        r = self._renglon(
            dia, "7.5",
            "Escoger certificacion y ver que ruta de estudio tomar por parte de learn microsoft",
            tipo=self.estudio,
        )
        evaluacion = self._evaluar(r, dia)
        self.assertIn("NO_FACTURABLE_MEDIA_JORNADA", {s.codigo for s in evaluacion.senales})
        self.assertEqual(evaluacion.banda, sn.REVISAR)
        # El motivo tiene que ser legible por si solo: quien firma lo lee sin
        # tener que abrir nada mas.
        texto = evaluacion.senales[0].texto
        self.assertIn("7.5 h de 8.5", texto)
        self.assertIn("88%", texto)

    def test_la_jornada_entera_en_actividades_departamentales(self):
        """8,5 de 8,5 en un proyecto interno, descrito como «muchas tareas»."""
        dia = self._dia()
        r = self._renglon(dia, "8.5", "muchas tareas", proyecto=self.interno)
        codigos = self._codigos(r, dia)
        self.assertIn("NO_FACTURABLE_MEDIA_JORNADA", codigos)
        self.assertIn("DETALLE_POBRE", codigos)


class ReglasTests(BaseTriaje):
    # ── lo que NO debe marcarse ─────────────────────────────────────────────

    def test_un_dia_normal_es_rutina(self):
        """Si esto marcara algo, la pantalla seria ruido y se ignoraria."""
        dia = self._dia()
        self._plan(self.cliente, "8.5")
        r = self._renglon(
            dia, "8.5", "Ajustes al conector de Oracle y pruebas del pipeline",
            proyecto=self.cliente,
        )
        self.assertEqual(self._evaluar(r, dia).banda, sn.RUTINA)

    def test_unas_horas_internas_sueltas_no_molestan(self):
        """Dos horas de estudio en una jornada de 8,5 son normales."""
        dia = self._dia()
        self._plan(self.cliente, "6.5")
        r = self._renglon(
            dia, "2", "Curso de Databricks, modulo de particionado", tipo=self.estudio,
        )
        self.assertEqual(self._codigos(r, dia), set())

    def test_media_hora_de_mas_no_es_pasarse_del_plan(self):
        dia = self._dia()
        self._plan(self.cliente, "4.0")
        r = self._renglon(
            dia, "4.5", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente,
        )
        self.assertNotIn("SOBRE_PLAN", self._codigos(r, dia))

    # ── plan ────────────────────────────────────────────────────────────────

    def test_declarar_bastante_mas_de_lo_planificado(self):
        dia = self._dia()
        self._plan(self.cliente, "4.3")
        r = self._renglon(
            dia, "8.5", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente,
        )
        evaluacion = self._evaluar(r, dia)
        self.assertIn("SOBRE_PLAN", {s.codigo for s in evaluacion.senales})
        self.assertIn("8.5 h", evaluacion.senales[0].texto)
        self.assertIn("4.3 h/día", evaluacion.senales[0].texto)

    def test_imputar_a_un_proyecto_sin_asignacion_es_atencion(self):
        """La pantalla no lo ofrece, asi que si aparece es que algo cambio detras."""
        dia = self._dia()
        r = self._renglon(
            dia, "4", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente,
        )
        evaluacion = self._evaluar(r, dia)
        self.assertIn("SIN_PLAN", {s.codigo for s in evaluacion.senales})
        self.assertEqual(evaluacion.banda, sn.ATENCION)

    def test_una_asignacion_solo_solicitada_no_cuenta_como_plan(self):
        dia = self._dia()
        self._plan(self.cliente, "4.3", estado="SOLICITADA")
        r = self._renglon(
            dia, "4", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente,
        )
        self.assertIn("SIN_PLAN", self._codigos(r, dia))

    def test_un_proyecto_interno_nunca_se_marca_por_falta_de_plan(self):
        """Nadie recibe una asignacion a «Departamentales» y aun asi todos
        pueden imputarle horas."""
        dia = self._dia()
        r = self._renglon(dia, "2", "Reunion de seguimiento del area", proyecto=self.interno)
        self.assertNotIn("SIN_PLAN", self._codigos(r, dia))

    def test_no_facturable_con_la_jornada_ya_planificada_es_atencion(self):
        dia = self._dia()
        self._plan(self.cliente, "8.5")
        r = self._renglon(
            dia, "2", "Curso de Databricks, modulo de particionado", tipo=self.estudio,
        )
        evaluacion = self._evaluar(r, dia)
        self.assertIn("NO_FACTURABLE_CON_PLAN_LLENO", {s.codigo for s in evaluacion.senales})
        self.assertEqual(evaluacion.banda, sn.ATENCION)

    # ── detalle ─────────────────────────────────────────────────────────────

    def test_un_detalle_de_una_palabra(self):
        dia = self._dia()
        self._plan(self.cliente, "8.5")
        r = self._renglon(dia, "8.5", "desarrollo", proyecto=self.cliente)
        self.assertIn("DETALLE_POBRE", self._codigos(r, dia))

    def test_un_detalle_suficiente_no_se_marca(self):
        dia = self._dia()
        self._plan(self.cliente, "8.5")
        r = self._renglon(
            dia, "8.5", "Notebooks de precarga y ajustes de pipeline", proyecto=self.cliente,
        )
        self.assertNotIn("DETALLE_POBRE", self._codigos(r, dia))

    def test_el_mismo_texto_copiado_de_otro_dia(self):
        anterior = self._dia(fecha=LUNES - timedelta(days=1))
        self._renglon(
            anterior, "8.5", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente,
        )
        dia = self._dia()
        self._plan(self.cliente, "8.5")
        r = self._renglon(
            dia, "8.5", "  Ajustes al CONECTOR de Oracle y pruebas ", proyecto=self.cliente,
        )
        evaluacion = self._evaluar(r, dia)
        self.assertIn("DETALLE_REPETIDO", {s.codigo for s in evaluacion.senales})
        self.assertIn("13/09", evaluacion.senales[-1].texto)

    def test_el_mismo_texto_en_dos_renglones_del_mismo_dia_no_cuenta(self):
        """Partir el dia entre dos proyectos con la misma tarea es legitimo."""
        dia = self._dia()
        self._plan(self.cliente, "8.5")
        self._renglon(dia, "4", "Ajustes al conector de Oracle", proyecto=self.cliente)
        r = self._renglon(dia, "4.5", "Ajustes al conector de Oracle", proyecto=self.cliente)
        self.assertNotIn("DETALLE_REPETIDO", self._codigos(r, dia))


class ClasificarLaColaTests(BaseTriaje):
    def _cola(self, usuario=None):
        dias = svc.dias_por_aprobar(usuario or self.pm)
        recuento = clasificar(dias)
        return dias, recuento

    def test_el_recuento_cuadra_con_los_renglones(self):
        dia = self._dia()
        self._plan(self.cliente, "4.0")
        self._renglon(dia, "4", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente)
        self._renglon(dia, "4.5", "muchas tareas", proyecto=self.interno)

        _, recuento = self._cola()
        self.assertEqual(recuento[sn.RUTINA], 1)
        self.assertEqual(recuento[sn.REVISAR], 1)
        self.assertEqual(recuento[sn.ATENCION], 0)

    def test_lo_urgente_se_pone_primero(self):
        tranquilo = self._dia(fecha=LUNES)
        self._plan(self.cliente, "8.5", inicio=LUNES, fin=LUNES)
        self._renglon(
            tranquilo, "8.5", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente,
        )
        urgente = self._dia(fecha=LUNES + timedelta(days=1))
        # Texto distinto a proposito: con el mismo saltaria DETALLE_REPETIDO en
        # los dos dias y el tranquilo dejaria de serlo.
        self._renglon(
            urgente, "8.5", "Pruebas end to end del simulador de campanas",
            proyecto=self.cliente,
        )

        dias, _ = self._cola()
        self.assertEqual(dias[0].fecha, urgente.fecha)
        self.assertEqual(dias[0].banda, sn.ATENCION)
        self.assertEqual(dias[1].banda, sn.RUTINA)

    def test_la_banda_del_dia_sale_de_lo_que_uno_puede_firmar(self):
        """Un renglon de otro PM no debe subir el dia al carril de atencion:
        quien mira no puede hacer nada con el."""
        otro_pm = User.objects.create_user("otro_pm", "otro@test.com", "x")
        ajeno = Proyecto.objects.create(
            codigo="V-25188809/Q", nombre="Otro", cliente="ANECOOP",
            fecha_inicio=date(2026, 1, 1), estado="ACTIVO", pm=otro_pm, facturable=True,
        )
        dia = self._dia()
        self._plan(self.cliente, "4.0")
        self._renglon(dia, "4", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente)
        self._renglon(dia, "4.5", "Trabajo en el otro proyecto de ANECOOP", proyecto=ajeno)

        dias, _ = self._cola()
        self.assertEqual(dias[0].banda, sn.RUTINA)
        # El renglon ajeno se evalua igual —se ve, sin botones— pero no manda.
        ajenos = [r.evaluacion.banda for r in dias[0].otros]
        self.assertEqual(ajenos, [sn.ATENCION])

    def test_las_consultas_no_crecen_con_la_cola(self):
        for i in range(8):
            recurso = Recurso.objects.create(
                nombre=f"Persona {i}", email=f"p{i}@test.com", banda="JR",
            )
            dia = DiaLegalizado.objects.create(
                recurso=recurso, fecha=LUNES, estado=DiaLegalizado.REGISTRADO,
                total_horas=Decimal("8.5"), jornada_esperada=Decimal("8.5"),
            )
            Asignacion.objects.create(
                recurso=recurso, proyecto=self.cliente, modo_asignacion="RANGO",
                fecha_inicio=LUNES, fecha_fin=LUNES, dias_habiles=1, horas_totales=9,
                intensidad_diaria=Decimal("8.5"), estado="APROBADA", solicitada_por=self.pm,
            )
            RegistroHoras.objects.create(
                dia=dia, tipo_actividad=self.de_proyecto, proyecto=self.cliente,
                horas=Decimal("8.5"), detalle="Ajustes al conector y pruebas del pipeline",
            )

        dias = svc.dias_por_aprobar(self.pm)
        with CaptureQueriesContext(connection) as consultas:
            clasificar(dias)
        self.assertLessEqual(
            len(consultas.captured_queries), 4,
            f"el triaje hizo {len(consultas.captured_queries)} consultas para 8 días; "
            "debe ser un número fijo, no uno por fila",
        )


class SePuedeApagarTests(BaseTriaje):
    def test_sin_el_modulo_la_cola_se_pinta_igual(self):
        """La propiedad que justifica que esto viva en una app aparte."""
        dia = self._dia()
        self._renglon(dia, "8.5", "muchas tareas", proyecto=self.interno)

        with self.modify_settings(INSTALLED_APPS={"remove": ["apps.revision"]}):
            dias = svc.dias_por_aprobar(self.pm)
            recuento = svc.triar(dias)

        self.assertEqual(recuento, {})
        self.assertEqual(len(dias), 1)
        self.assertFalse(hasattr(dias[0], "banda"))


class AprobarElDiaEnteroTests(BaseTriaje):
    """El botón de firmar un día interno de una vez.

    Lo pidió un caso concreto: alguien en bench que reparte su jornada en varias
    tareas internas, cada una descrita y acotada. Revisarlas de a una no aporta
    nada, y obligar a hacerlo es lo que lleva a firmar sin mirar.

    Casi todas estas pruebas comprueban cuándo **no** debe ofrecerse. Un botón
    de aprobación en bloque que aparece donde no toca es peor que no tenerlo:
    convierte el triaje en una aprobación automática de hecho.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        self.admin = User.objects.create_user("admin_bloque", "adm@test.com", "x")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])

    def _dia_interno_limpio(self):
        """Un dia de bench repartido en tareas atomicas.

        Ninguna llega a media jornada a proposito: ese es justo el corte que
        separa este caso del renglon de estudio de 7,5 h.
        """
        dia = self._dia()
        self._renglon(dia, "3", "Reunion de seguimiento del area y actas", proyecto=self.interno)
        self._renglon(dia, "3", "Documentacion del procedimiento de altas", proyecto=self.interno)
        self._renglon(dia, "2.5", "Revision de tickets de soporte interno", proyecto=self.interno)
        return dia

    def _cola(self, usuario):
        dias = svc.dias_por_aprobar(usuario)
        clasificar(dias, usuario)
        return dias

    # ── cuándo sí ───────────────────────────────────────────────────────────

    def test_se_ofrece_en_un_dia_interno_bien_descrito(self):
        self._dia_interno_limpio()
        dia = self._cola(self.admin)[0]
        self.assertTrue(dia.aprobable_en_bloque)

    def test_firma_todos_los_renglones_con_su_nombre(self):
        dia = self._dia_interno_limpio()
        cuantos = svc.aprobar_dia_completo(dia, self.admin)

        self.assertEqual(cuantos, 3)
        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)
        for registro in dia.registros.all():
            self.assertEqual(registro.estado, RegistroHoras.APROBADO)
            self.assertEqual(registro.aprobado_por, self.admin)
            self.assertIsNotNone(registro.aprobado_en)

    # ── cuándo no ───────────────────────────────────────────────────────────

    def test_no_se_ofrece_a_un_pm(self):
        """Un PM responde por su proyecto, no por la jornada de otro."""
        self._dia_interno_limpio()
        dia = self._cola(self.pm)[0]
        self.assertFalse(dia.aprobable_en_bloque)

    def test_un_pm_no_puede_aunque_lo_intente(self):
        dia = self._dia_interno_limpio()
        with self.assertRaises(ValidationError):
            svc.aprobar_dia_completo(dia, self.pm)
        self.assertEqual(
            dia.registros.filter(estado=RegistroHoras.APROBADO).count(), 0,
        )

    def test_no_se_ofrece_si_queda_algo_facturable(self):
        dia = self._dia()
        self._plan(self.cliente, "4.0")
        self._renglon(dia, "4", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente)
        self._renglon(dia, "4.5", "Documentacion del procedimiento de altas", proyecto=self.interno)

        self.assertFalse(self._cola(self.admin)[0].aprobable_en_bloque)

    def test_no_se_ofrece_si_algun_detalle_es_pobre(self):
        dia = self._dia()
        self._renglon(dia, "4", "Reunion de seguimiento del area y actas", proyecto=self.interno)
        self._renglon(dia, "4.5", "muchas tareas", proyecto=self.interno)

        self.assertFalse(self._cola(self.admin)[0].aprobable_en_bloque)

    def test_no_se_ofrece_con_un_solo_renglon(self):
        """Con uno, el boton de siempre hace exactamente lo mismo."""
        dia = self._dia()
        self._renglon(dia, "8.5", "Documentacion del procedimiento de altas", proyecto=self.interno)
        self.assertFalse(self._cola(self.admin)[0].aprobable_en_bloque)

    def test_no_se_ofrece_si_el_plan_ya_ocupaba_la_jornada(self):
        """Un dia planificado al completo que acaba en horas internas no es
        rutina: o el plan se corrio o desplazaron trabajo de cliente."""
        dia = self._dia()
        self._plan(self.cliente, "8.5")
        self._renglon(dia, "4", "Reunion de seguimiento del area y actas", proyecto=self.interno)
        self._renglon(dia, "4.5", "Documentacion del procedimiento de altas", proyecto=self.interno)

        self.assertFalse(self._cola(self.admin)[0].aprobable_en_bloque)

    def test_el_servicio_revalida_aunque_el_boton_se_hubiera_pintado(self):
        """Entre que se cargo la pantalla y llega el POST, el dia pudo cambiar."""
        dia = self._dia_interno_limpio()
        self.assertTrue(self._cola(self.admin)[0].aprobable_en_bloque)

        # Alguien empeora un detalle antes de que llegue el POST.
        renglon = dia.registros.first()
        renglon.detalle = "varias cosas"
        renglon.save(update_fields=["detalle"])

        with self.assertRaises(ValidationError):
            svc.aprobar_dia_completo(dia, self.admin)
        self.assertEqual(dia.registros.filter(estado=RegistroHoras.APROBADO).count(), 0)

    def test_sin_el_modulo_no_hay_aprobacion_por_dia(self):
        dia = self._dia_interno_limpio()
        with self.modify_settings(INSTALLED_APPS={"remove": ["apps.revision"]}):
            with self.assertRaises(ValidationError):
                svc.aprobar_dia_completo(dia, self.admin)

    def test_una_tarea_interna_de_media_jornada_bloquea_el_boton(self):
        """El renglon de estudio de 7,5 h no puede colarse por aqui.

        Es el corte que hace util el boton: un dia repartido en tareas acotadas
        se firma de una vez; uno donde una sola cosa se lleva medio dia se mira.
        """
        dia = self._dia()
        self._renglon(
            dia, "7.5",
            "Escoger certificacion y ver que ruta de estudio tomar por parte de learn microsoft",
            tipo=self.estudio,
        )
        self._renglon(dia, "1", "Reunion de seguimiento del area", proyecto=self.interno)

        self.assertFalse(self._cola(self.admin)[0].aprobable_en_bloque)
