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


class RevisarHistoricoTests(BaseTriaje):
    """El informe retroactivo sobre horas ya registradas.

    Lo que hay que garantizar por encima de todo es que **no toca nada**: se
    ejecuta sobre datos de produccion ya firmados, y un comando de informe que
    escribiera seria un desastre silencioso.
    """

    def _ejecutar(self, **extra):
        from io import StringIO
        from django.core.management import call_command
        salida = StringIO()
        call_command("revisar_historico", stdout=salida, stderr=salida, **extra)
        return salida.getvalue()

    def _dia_aprobado_con_estudio(self):
        dia = self._dia()
        r = self._renglon(
            dia, "7.5",
            "Escoger certificacion y ver que ruta de estudio tomar por parte de learn microsoft",
            tipo=self.estudio,
        )
        self._renglon(dia, "1", "Reunion de seguimiento del area", proyecto=self.interno)
        dia.registros.update(estado=RegistroHoras.APROBADO, aprobado_por=self.pm)
        dia.estado = DiaLegalizado.APROBADO
        dia.save(update_fields=["estado"])
        return dia, r

    def test_no_cambia_ningun_estado(self):
        """La garantia principal: corre sobre lo ya firmado."""
        dia, _ = self._dia_aprobado_con_estudio()
        antes = list(dia.registros.values_list("id", "estado", "aprobado_por_id"))

        self._ejecutar(desde="2026-09-01", hasta="2026-09-30")

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)
        self.assertEqual(list(dia.registros.values_list("id", "estado", "aprobado_por_id")), antes)

    def test_encuentra_lo_que_se_aprobo_sin_aviso(self):
        self._dia_aprobado_con_estudio()
        salida = self._ejecutar(desde="2026-09-01", hasta="2026-09-30", detalle=True)

        self.assertIn("SE HABRÍAN MARCADO", salida)
        self.assertIn("NO_FACTURABLE_MEDIA_JORNADA", salida)
        self.assertIn("88% del día", salida)

    def test_dice_cuanta_cola_se_ahorra(self):
        self._dia_aprobado_con_estudio()
        salida = self._ejecutar(desde="2026-09-01", hasta="2026-09-30")
        self.assertIn("habrían salido en Rutina", salida)
        self.assertIn("POR BANDA", salida)

    def test_solo_aprobados_deja_fuera_lo_pendiente(self):
        self._dia_aprobado_con_estudio()
        pendiente = self._dia(fecha=LUNES + timedelta(days=1))
        self._renglon(pendiente, "8.5", "muchas tareas", proyecto=self.interno)

        con_todo = self._ejecutar(desde="2026-09-01", hasta="2026-09-30")
        solo = self._ejecutar(desde="2026-09-01", hasta="2026-09-30", solo_aprobados=True)
        self.assertIn("Días     2", con_todo)
        self.assertIn("Días     1", solo)

    def test_un_rango_vacio_no_revienta(self):
        salida = self._ejecutar(desde="2020-01-01", hasta="2020-01-31")
        self.assertIn("No hay días registrados", salida)


class ContextoConVariasPersonasTests(BaseTriaje):
    """El mismo día aparece una vez por persona en la cola.

    Lo encontro el informe retroactivo sobre datos reales, no una prueba: decia
    "el plan ya ocupaba 25,5 h" en una jornada de 8,5, porque la fecha se
    repetia tres veces y el plan se sumaba una vez por repeticion. Las pruebas
    no lo veian porque todas usaban una sola persona.
    """

    def test_el_plan_no_se_multiplica_por_cuanta_gente_registro_ese_dia(self):
        otros = [
            Recurso.objects.create(nombre=f"Otra Persona {i}", email=f"o{i}@test.com", banda="JR")
            for i in range(2)
        ]
        for recurso in [self.recurso, *otros]:
            DiaLegalizado.objects.create(
                recurso=recurso, fecha=LUNES, estado=DiaLegalizado.REGISTRADO,
                total_horas=Decimal("8.5"), jornada_esperada=Decimal("8.5"),
            )
        self._plan(self.cliente, "8.5")

        dias = list(DiaLegalizado.objects.filter(fecha=LUNES))
        ctx = Contexto(dias)

        self.assertEqual(ctx.plan_del_dia(self.recurso.pk, LUNES), 8.5)
        self.assertEqual(
            ctx.horas_planificadas(self.recurso.pk, self.cliente.pk, LUNES), 8.5,
        )

    def test_sobre_plan_sigue_saltando_con_varias_personas_en_la_cola(self):
        """El plan inflado apagaba la regla justo cuando debia saltar."""
        for i in range(2):
            recurso = Recurso.objects.create(
                nombre=f"Otra Persona {i}", email=f"o{i}@test.com", banda="JR",
            )
            DiaLegalizado.objects.create(
                recurso=recurso, fecha=LUNES, estado=DiaLegalizado.REGISTRADO,
                total_horas=Decimal("8.5"), jornada_esperada=Decimal("8.5"),
            )
        dia = self._dia()
        self._plan(self.cliente, "4.3")
        r = self._renglon(
            dia, "8.5", "Ajustes al conector de Oracle y pruebas", proyecto=self.cliente,
        )

        ctx = Contexto(list(DiaLegalizado.objects.filter(fecha=LUNES)))
        codigos = {s.codigo for s in sn.evaluar(r, dia, ctx).senales}
        self.assertIn("SOBRE_PLAN", codigos)


class ForzarElDiaEnteroTests(BaseTriaje):
    """El mismo botón sobre un día que sí trae avisos.

    Un aviso nunca fue un veto: muchos son trabajo normal que la regla marcó de
    más, y no poder firmarlos convierte el triaje en un estorbo. Lo que se cuida
    aquí es que forzar **no** relaje nada más —sigue siendo Admin, sigue siendo
    todo interno, sigue sin tocar horas de cliente— y que deje escrito qué aviso
    se anuló, que es lo único que después permite corregir la regla.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        self.admin = User.objects.create_user("admin_forzar", "adf@test.com", "x")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])

    def _dia_interno_con_aviso(self):
        """Interno, repartido y descrito salvo uno: el caso que pide el botón."""
        dia = self._dia()
        self._renglon(dia, "4", "Reunion de seguimiento del area y actas", proyecto=self.interno)
        self._renglon(dia, "4.5", "muchas tareas", proyecto=self.interno)
        return dia

    def _cola(self, usuario):
        dias = svc.dias_por_aprobar(usuario)
        clasificar(dias, usuario)
        return dias

    # ── cuándo se ofrece ────────────────────────────────────────────────────

    def test_se_ofrece_donde_el_limpio_no_llega(self):
        self._dia_interno_con_aviso()
        dia = self._cola(self.admin)[0]
        self.assertFalse(dia.aprobable_en_bloque)
        self.assertTrue(dia.forzable_en_bloque)

    def test_los_dos_botones_nunca_salen_a_la_vez(self):
        """Serian dos botones que hacen lo mismo, y uno pediria motivo de mas."""
        # Tres tareas por debajo de media jornada: si alguna llegara a 4,5 de
        # 8,5 saltaria NO_FACTURABLE_MEDIA_JORNADA y esto seria el otro caso.
        dia = self._dia()
        self._renglon(dia, "3", "Reunion de seguimiento del area y actas", proyecto=self.interno)
        self._renglon(dia, "3", "Documentacion del procedimiento de altas", proyecto=self.interno)
        self._renglon(dia, "2.5", "Revision de tickets de soporte interno", proyecto=self.interno)

        dia = self._cola(self.admin)[0]
        self.assertTrue(dia.aprobable_en_bloque)
        self.assertFalse(dia.forzable_en_bloque)

    def test_cuenta_cuantas_traen_aviso(self):
        self._dia_interno_con_aviso()
        dia = self._cola(self.admin)[0]
        self.assertEqual(dia.n_avisos, 1)
        self.assertEqual(len(dia.pendientes_mios), 2)

    # ── lo que forzar NO relaja ─────────────────────────────────────────────

    def test_no_se_ofrece_a_un_pm(self):
        self._dia_interno_con_aviso()
        self.assertFalse(self._cola(self.pm)[0].forzable_en_bloque)

    def test_un_pm_no_puede_forzar_aunque_lo_intente(self):
        dia = self._dia_interno_con_aviso()
        with self.assertRaises(ValidationError):
            svc.aprobar_dia_completo(dia, self.pm, forzado=True, motivo="es normal")
        self.assertEqual(dia.registros.filter(estado=RegistroHoras.APROBADO).count(), 0)

    def test_las_horas_de_cliente_no_se_fuerzan_ni_con_motivo(self):
        """Para esas estan las casillas, que se marcan una a una."""
        dia = self._dia()
        self._plan(self.cliente, "4.0")
        self._renglon(dia, "4", "trabajo", proyecto=self.cliente)
        self._renglon(dia, "4.5", "muchas tareas", proyecto=self.interno)

        self.assertFalse(self._cola(self.admin)[0].forzable_en_bloque)
        with self.assertRaises(ValidationError):
            svc.aprobar_dia_completo(dia, self.admin, forzado=True, motivo="es normal")
        self.assertEqual(dia.registros.filter(estado=RegistroHoras.APROBADO).count(), 0)

    def test_con_un_solo_renglon_no_se_ofrece(self):
        dia = self._dia()
        self._renglon(dia, "8.5", "muchas tareas", proyecto=self.interno)
        self.assertFalse(self._cola(self.admin)[0].forzable_en_bloque)

    def test_sin_motivo_no_se_firma(self):
        """El motivo es el precio de saltarse el aviso, no un adorno."""
        dia = self._dia_interno_con_aviso()
        with self.assertRaises(ValidationError):
            svc.aprobar_dia_completo(dia, self.admin, forzado=True, motivo="   ")
        self.assertEqual(dia.registros.filter(estado=RegistroHoras.APROBADO).count(), 0)

    def test_el_boton_limpio_no_firma_un_dia_con_avisos(self):
        """Sin `forzado`, un dia con avisos se rechaza igual que antes."""
        dia = self._dia_interno_con_aviso()
        with self.assertRaises(ValidationError):
            svc.aprobar_dia_completo(dia, self.admin)
        self.assertEqual(dia.registros.filter(estado=RegistroHoras.APROBADO).count(), 0)

    # ── el rastro ───────────────────────────────────────────────────────────

    def test_deja_escrito_que_aviso_se_anulo(self):
        """Es lo unico que despues permite saber que regla sobra."""
        dia = self._dia_interno_con_aviso()
        cuantos = svc.aprobar_dia_completo(
            dia, self.admin, forzado=True,
            motivo="Son tareas de bench, el detalle corto es correcto",
        )
        self.assertEqual(cuantos, 2)

        marcado = dia.registros.get(detalle="muchas tareas")
        self.assertTrue(marcado.aprobacion_forzada)
        self.assertIn("DETALLE_POBRE", marcado.senales_anuladas)
        self.assertIn("bench", marcado.motivo_aprobacion)

    def test_el_renglon_sin_avisos_no_queda_marcado_como_forzado(self):
        """Iba en el mismo envio, pero no se salto ninguna regla."""
        dia = self._dia_interno_con_aviso()
        svc.aprobar_dia_completo(dia, self.admin, forzado=True, motivo="es normal")

        limpio = dia.registros.get(detalle="Reunion de seguimiento del area y actas")
        self.assertFalse(limpio.aprobacion_forzada)
        self.assertEqual(limpio.senales_anuladas, [])
        self.assertEqual(limpio.motivo_aprobacion, "")

    def test_firma_igual_con_nombre_y_fecha(self):
        dia = self._dia_interno_con_aviso()
        svc.aprobar_dia_completo(dia, self.admin, forzado=True, motivo="es normal")

        dia.refresh_from_db()
        self.assertEqual(dia.estado, DiaLegalizado.APROBADO)
        for registro in dia.registros.all():
            self.assertEqual(registro.estado, RegistroHoras.APROBADO)
            self.assertEqual(registro.aprobado_por, self.admin)


class AprobarLoMarcadoTests(BaseTriaje):
    """Marcar varias actividades de la cola y firmarlas de una vez.

    A diferencia del día entero, aquí se mira cada renglón: marcar la casilla es
    el mismo acto que pulsar su botón, solo que treinta veces en un envío. Por
    eso no pide motivo aunque haya avisos.

    Lo que se cuida es que **un fallo no tumbe el resto**. Se marcan treinta y
    una ya la firmó otro: tirar las veintinueve buenas obliga a repetir el
    trabajo entero, y es la mejor forma de que nadie vuelva a usar el botón.
    """

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        self.admin = User.objects.create_user("admin_sel", "ads@test.com", "x")
        self.admin.groups.add(Group.objects.get_or_create(name="Admin")[0])

    def test_firma_lo_marcado_de_varios_dias(self):
        uno = self._dia()
        a = self._renglon(uno, "8.5", "Documentacion del procedimiento de altas", proyecto=self.interno)

        otro = self._dia(fecha=LUNES - timedelta(days=3))
        b = self._renglon(otro, "8.5", "Revision de tickets de soporte interno", proyecto=self.interno)

        aprobados, fallos = svc.aprobar_seleccion([a.pk, b.pk], self.admin)

        self.assertEqual(len(aprobados), 2)
        self.assertEqual(fallos, [])
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.estado, RegistroHoras.APROBADO)
        self.assertEqual(b.estado, RegistroHoras.APROBADO)

    def test_deja_en_paz_lo_que_no_se_marco(self):
        dia = self._dia()
        marcado = self._renglon(dia, "4", "Reunion de seguimiento del area y actas", proyecto=self.interno)
        suelto = self._renglon(dia, "4.5", "Documentacion del procedimiento de altas", proyecto=self.interno)

        svc.aprobar_seleccion([marcado.pk], self.admin)

        suelto.refresh_from_db()
        self.assertEqual(suelto.estado, RegistroHoras.PENDIENTE)

    def test_un_fallo_no_tumba_a_los_demas(self):
        """Lo que decide si el boton se usa dos veces o ninguna."""
        dia = self._dia()
        bueno = self._renglon(dia, "4", "Reunion de seguimiento del area y actas", proyecto=self.interno)
        ya_firmado = self._renglon(dia, "4.5", "Documentacion del procedimiento de altas", proyecto=self.interno)
        svc.aprobar_registro(ya_firmado, self.admin)

        aprobados, fallos = svc.aprobar_seleccion([bueno.pk, ya_firmado.pk], self.admin)

        self.assertEqual([r.pk for r in aprobados], [bueno.pk])
        self.assertEqual(len(fallos), 1)
        self.assertIn("ya está aprobada", fallos[0][1])
        bueno.refresh_from_db()
        self.assertEqual(bueno.estado, RegistroHoras.APROBADO)

    def test_lo_que_desaparecio_se_cuenta_no_se_calla(self):
        dia = self._dia()
        vivo = self._renglon(dia, "8.5", "Documentacion del procedimiento de altas", proyecto=self.interno)

        aprobados, fallos = svc.aprobar_seleccion([vivo.pk, 999999], self.admin)

        self.assertEqual(len(aprobados), 1)
        self.assertEqual(len(fallos), 1)
        self.assertIn("ya no existe", fallos[0][1])

    def test_un_pm_no_firma_lo_que_no_es_suyo(self):
        """La casilla se pinta, pero el permiso se comprueba renglon a renglon."""
        dia = self._dia()
        ajeno = self._renglon(dia, "8.5", "Escoger certificacion y ruta de estudio", tipo=self.estudio)

        aprobados, fallos = svc.aprobar_seleccion([ajeno.pk], self.pm)

        self.assertEqual(aprobados, [])
        self.assertEqual(len(fallos), 1)
        ajeno.refresh_from_db()
        self.assertEqual(ajeno.estado, RegistroHoras.PENDIENTE)

    def test_marcar_un_renglon_con_aviso_lo_firma_y_lo_deja_anotado(self):
        """No pide motivo —se miro uno a uno— pero el aviso anulado se guarda."""
        dia = self._dia()
        pobre = self._renglon(dia, "8.5", "muchas tareas", proyecto=self.interno)

        aprobados, _ = svc.aprobar_seleccion([pobre.pk], self.admin)

        self.assertEqual(len(aprobados), 1)
        pobre.refresh_from_db()
        self.assertEqual(pobre.estado, RegistroHoras.APROBADO)
        self.assertTrue(pobre.aprobacion_forzada)
        self.assertIn("DETALLE_POBRE", pobre.senales_anuladas)
        self.assertEqual(pobre.motivo_aprobacion, "")

    def test_una_actividad_limpia_no_queda_marcada_como_forzada(self):
        # 3 h y no 8,5: una sola tarea interna que se lleve media jornada ya
        # trae aviso, y entonces esto probaria justo lo contrario.
        dia = self._dia()
        limpio = self._renglon(dia, "3", "Documentacion del procedimiento de altas", proyecto=self.interno)
        self._renglon(dia, "5.5", "Revision de tickets de soporte interno", proyecto=self.interno)

        svc.aprobar_seleccion([limpio.pk], self.admin)

        limpio.refresh_from_db()
        self.assertFalse(limpio.aprobacion_forzada)
        self.assertEqual(limpio.senales_anuladas, [])

    def test_sin_el_modulo_de_triaje_se_firma_igual(self):
        """Marcar y firmar es la pantalla de siempre; el rastro es lo que falta."""
        dia = self._dia()
        renglon = self._renglon(dia, "8.5", "muchas tareas", proyecto=self.interno)

        with self.modify_settings(INSTALLED_APPS={"remove": ["apps.revision"]}):
            aprobados, fallos = svc.aprobar_seleccion([renglon.pk], self.admin)

        self.assertEqual(len(aprobados), 1)
        self.assertEqual(fallos, [])
        renglon.refresh_from_db()
        self.assertEqual(renglon.estado, RegistroHoras.APROBADO)
        self.assertFalse(renglon.aprobacion_forzada)
