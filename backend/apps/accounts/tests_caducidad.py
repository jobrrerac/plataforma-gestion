"""Tests del aviso de caducidad del secreto del SSO.

El aviso existe porque Azure no notifica la caducidad por ningun canal: el dia
que ocurre, el boton de Microsoft deja de funcionar sin mensaje ni correo. Lo
que se prueba aqui es que el aviso aparece a tiempo, solo a quien puede
resolverlo, y que una fecha ilegible no lo rompe: un aviso roto es peor que
ninguno, porque ensena a la gente a ignorar la barra amarilla.
"""

from datetime import datetime, timedelta, timezone as tz

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts import roles


def en_dias(dias):
    """Fecha ISO en UTC dentro de N dias, como la devuelve Entra."""
    return (datetime.now(tz.utc) + timedelta(days=dias)).isoformat()


class AvisoCaducidadSSOTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for nombre in (roles.ADMIN, roles.PM, roles.INGENIERO):
            Group.objects.get_or_create(name=nombre)

    def setUp(self):
        self.admin = User.objects.create_user(username="admin1", password="Clave2026!")
        self.admin.groups.add(Group.objects.get(name=roles.ADMIN))

        self.ing = User.objects.create_user(username="ing1", password="Clave2026!")
        self.ing.groups.add(Group.objects.get(name=roles.INGENIERO))

    def _html(self, usuario):
        self.client.force_login(usuario)
        return self.client.get(reverse("dashboard")).content.decode()

    @override_settings(OIDC_SECRETO_CADUCA=en_dias(400), OIDC_DIAS_AVISO_CADUCIDAD=60)
    def test_lejos_de_caducar_no_avisa(self):
        self.assertNotIn("dejará de funcionar", self._html(self.admin))

    @override_settings(OIDC_SECRETO_CADUCA=en_dias(30), OIDC_DIAS_AVISO_CADUCIDAD=60)
    def test_dentro_de_la_ventana_avisa_al_admin(self):
        html = self._html(self.admin)
        self.assertIn("dejará de funcionar", html)
        self.assertIn("30 días", html)

    @override_settings(OIDC_SECRETO_CADUCA=en_dias(30), OIDC_DIAS_AVISO_CADUCIDAD=60)
    def test_no_se_le_muestra_a_quien_no_puede_resolverlo(self):
        # Llenar la pantalla de todo el mundo con algo que no pueden arreglar
        # solo educa a ignorar avisos.
        self.assertNotIn("dejará de funcionar", self._html(self.ing))

    @override_settings(OIDC_SECRETO_CADUCA=en_dias(-5), OIDC_DIAS_AVISO_CADUCIDAD=60)
    def test_ya_caducado_avisa_en_positivo_y_en_rojo(self):
        html = self._html(self.admin)
        self.assertIn("está caído", html)
        self.assertIn("hace 5 días", html)
        self.assertNotIn("hace -5", html)

    @override_settings(OIDC_SECRETO_CADUCA="", OIDC_DIAS_AVISO_CADUCIDAD=60)
    def test_sin_configurar_no_avisa(self):
        # Desarrollo local y entornos sin SSO.
        self.assertNotIn("dejará de funcionar", self._html(self.admin))

    @override_settings(OIDC_SECRETO_CADUCA="no-es-una-fecha", OIDC_DIAS_AVISO_CADUCIDAD=60)
    def test_una_fecha_ilegible_no_rompe_la_pagina(self):
        html = self._html(self.admin)
        self.assertNotIn("dejará de funcionar", html)

    @override_settings(OIDC_SECRETO_CADUCA=en_dias(10).replace("+00:00", "Z"))
    def test_acepta_el_formato_con_Z_de_entra(self):
        self.assertIn("dejará de funcionar", self._html(self.admin))

    @override_settings(OIDC_SECRETO_CADUCA=en_dias(30), OIDC_DIAS_AVISO_CADUCIDAD=60)
    def test_el_aviso_dice_como_rotarlo(self):
        # Quien lo vea en 2028 no tiene por que saber que existe Terraform.
        self.assertIn("terraform apply -replace", self._html(self.admin))
