"""Conexión a PostgreSQL con token de Entra.

Lo que se cuida aquí es **una sola cosa**: que nada de este camino pueda dejar a
la aplicación sin base de datos. La contraseña sigue activa en el servidor
precisamente para eso, y estas pruebas comprueban que se usa siempre que el
token no sirva —no haya identidad, falle el endpoint, o el servidor lo rechace.

La otra mitad es que fuera de Azure esto no exista: en el Docker de desarrollo y
en la propia suite de tests no hay `IDENTITY_ENDPOINT`, así que el backend tiene
que comportarse exactamente como el estándar. Si esa parte se rompe, se rompe
todo el proyecto a la vez, no solo producción.
"""

import time
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase

from . import base as backend
from . import token as tok

ENDPOINT = {"IDENTITY_ENDPOINT": "http://localhost:42356/msi/token",
            "IDENTITY_HEADER": "cabecera-de-prueba"}


class SinIdentidadTests(SimpleTestCase):
    """Fuera de Azure. Es el caso de desarrollo y el de la suite entera."""

    def setUp(self):
        tok.olvidar()
        backend._levantar_castigo()

    def test_no_hay_identidad_no_hay_token(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(tok.disponible())
            self.assertIsNone(tok.obtener("cualquiera"))

    def test_ni_siquiera_llama_al_endpoint(self):
        """Sin identidad no hay a quién llamar: si lo intentara, fallaría raro."""
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch.object(tok, "_pedir") as pedir:
                tok.obtener("cualquiera")
        pedir.assert_not_called()

    def test_el_backend_es_el_de_postgres_de_siempre(self):
        """Nuestro envoltorio ES un backend de PostgreSQL, no uno paralelo:
        el dialecto, las migraciones y el pool son los de Django sin tocar."""
        from django.db import connections
        from django.db.backends.postgresql import base as estandar

        envoltorio = connections["default"]
        self.assertIsInstance(envoltorio, backend.DatabaseWrapper)
        self.assertIsInstance(envoltorio, estandar.DatabaseWrapper)

    def test_la_suite_corre_sobre_este_backend(self):
        """Si esto falla, el ENGINE de settings no es el que se cree."""
        self.assertEqual(
            connection.settings_dict["ENGINE"], "config.db.entra",
        )


class ObtenerTokenTests(SimpleTestCase):
    def setUp(self):
        tok.olvidar()

    def test_pide_el_token_y_lo_devuelve(self):
        with mock.patch.dict("os.environ", ENDPOINT, clear=True):
            with mock.patch.object(tok, "_pedir", return_value=("tok-1", time.time() + 3600)):
                self.assertEqual(tok.obtener("cliente-1"), "tok-1")

    def test_lo_cachea_y_no_lo_vuelve_a_pedir(self):
        """Con CONN_MAX_AGE=60 esto se llama muy a menudo; pedirlo cada vez
        seria una llamada de red por conexion."""
        with mock.patch.dict("os.environ", ENDPOINT, clear=True):
            with mock.patch.object(
                tok, "_pedir", return_value=("tok-1", time.time() + 3600),
            ) as pedir:
                tok.obtener("cliente-1")
                tok.obtener("cliente-1")
                tok.obtener("cliente-1")
        self.assertEqual(pedir.call_count, 1)

    def test_lo_renueva_antes_de_que_caduque(self):
        """Un token que caduca mientras se abre la conexion da un fallo
        indistinguible de una contrasena mala."""
        caduca = time.time() + 3600
        with mock.patch.dict("os.environ", ENDPOINT, clear=True):
            with mock.patch.object(
                tok, "_pedir", side_effect=[("viejo", caduca), ("nuevo", caduca + 3600)],
            ):
                self.assertEqual(tok.obtener("c", ahora=caduca - 3600), "viejo")
                # Dentro del margen: todavia vale, pero ya toca renovarlo.
                dentro_del_margen = caduca - tok.MARGEN_SEGUNDOS + 1
                self.assertEqual(tok.obtener("c", ahora=dentro_del_margen), "nuevo")

    def test_si_el_endpoint_falla_devuelve_none_y_no_lanza(self):
        """Quien llama tiene que poder seguir con la contrasena."""
        with mock.patch.dict("os.environ", ENDPOINT, clear=True):
            with mock.patch.object(tok, "_pedir", side_effect=OSError("sin ruta")):
                self.assertIsNone(tok.obtener("cliente-1"))

    def test_una_respuesta_sin_token_tampoco_lanza(self):
        with mock.patch.dict("os.environ", ENDPOINT, clear=True):
            with mock.patch.object(tok, "_pedir", side_effect=KeyError("access_token")):
                self.assertIsNone(tok.obtener("cliente-1"))

    def test_el_client_id_viaja_en_la_consulta(self):
        """Sin el, el servicio busca una identidad de sistema que aqui no existe."""
        capturado = {}

        class RespuestaFalsa:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return b'{"access_token": "tok", "expires_on": "99999999999"}'

        def falsa_urlopen(peticion, timeout=None):
            capturado["url"] = peticion.full_url
            capturado["cabecera"] = peticion.get_header("X-identity-header")
            return RespuestaFalsa()

        with mock.patch.dict("os.environ", ENDPOINT, clear=True):
            with mock.patch("urllib.request.urlopen", falsa_urlopen):
                self.assertEqual(tok.obtener("cliente-42"), "tok")

        self.assertIn("client_id=cliente-42", capturado["url"])
        self.assertIn("ossrdbms-aad.database.windows.net", capturado["url"])
        self.assertEqual(capturado["cabecera"], "cabecera-de-prueba")


class CaidaALaContrasenaTests(SimpleTestCase):
    """El backend. Ninguna rama puede acabar sin conexión."""

    def setUp(self):
        tok.olvidar()
        backend._levantar_castigo()
        self.envoltorio = backend.DatabaseWrapper({
            **connection.settings_dict,
            "ENTRA": {"USER": "id-app", "CLIENT_ID": "cliente-1"},
        })
        self.params = {"user": "pgadmin", "password": "la-de-siempre", "dbname": "x"}

    def _padre(self, **kwargs):
        return mock.patch.object(
            backend.base.DatabaseWrapper, "get_new_connection", **kwargs,
        )

    def test_sin_token_usa_la_contrasena_tal_cual(self):
        with mock.patch.object(tok, "obtener", return_value=None):
            with self._padre(return_value="conexion") as padre:
                self.assertEqual(
                    self.envoltorio.get_new_connection(self.params), "conexion",
                )
        padre.assert_called_once_with(self.params)

    def test_con_token_cambia_usuario_y_credencial(self):
        with mock.patch.object(tok, "obtener", return_value="tok-1"):
            with self._padre(return_value="conexion") as padre:
                self.envoltorio.get_new_connection(self.params)
        usados = padre.call_args[0][0]
        self.assertEqual(usados["user"], "id-app")
        self.assertEqual(usados["password"], "tok-1")
        self.assertEqual(usados["dbname"], "x", "se perdio el resto de parametros")

    def test_no_muta_los_parametros_que_recibe(self):
        """Django reutiliza ese diccionario; ensuciarlo rompe la siguiente
        conexion, que es un fallo dificilisimo de encontrar."""
        with mock.patch.object(tok, "obtener", return_value="tok-1"):
            with self._padre(return_value="conexion"):
                self.envoltorio.get_new_connection(self.params)
        self.assertEqual(self.params["user"], "pgadmin")
        self.assertEqual(self.params["password"], "la-de-siempre")

    def test_si_el_servidor_rechaza_el_token_entra_con_la_contrasena(self):
        """El rol de la identidad puede no existir todavia. La aplicacion no
        puede quedarse fuera por eso."""
        intentos = []

        def registrar(params):
            intentos.append(params["password"])
            if params["password"] == "tok-1":
                raise backend.base.Database.Error("role does not exist")
            return "conexion"

        with mock.patch.object(tok, "obtener", return_value="tok-1"):
            with self._padre(side_effect=registrar):
                self.assertEqual(
                    self.envoltorio.get_new_connection(self.params), "conexion",
                )
        self.assertEqual(intentos, ["tok-1", "la-de-siempre"])

    def test_tras_un_rechazo_no_reintenta_el_token(self):
        """Sin esto, cada conexion pagaria dos intentos para siempre."""
        with mock.patch.object(tok, "obtener", return_value="tok-1") as obtener:
            with self._padre(side_effect=backend.base.Database.Error("no")):
                with self.assertRaises(backend.base.Database.Error):
                    # Falla tambien la contrasena: da igual, lo que se mide es
                    # que en la segunda llamada ya no se pide token.
                    self.envoltorio.get_new_connection(self.params)
            obtener.reset_mock()
            with self._padre(return_value="conexion"):
                self.envoltorio.get_new_connection(self.params)
        obtener.assert_not_called()

    def test_el_castigo_caduca(self):
        backend._castigar(time.time() - backend.ESPERA_TRAS_RECHAZO - 1)
        with mock.patch.object(tok, "obtener", return_value="tok-1") as obtener:
            with self._padre(return_value="conexion"):
                self.envoltorio.get_new_connection(self.params)
        obtener.assert_called_once()

    def test_sin_usuario_de_entra_configurado_ni_lo_intenta(self):
        """Es el estado del despliegue antes de crear el rol, y el de dev."""
        envoltorio = backend.DatabaseWrapper({
            **connection.settings_dict, "ENTRA": {"USER": "", "CLIENT_ID": ""},
        })
        with mock.patch.object(tok, "obtener") as obtener:
            with self._padre(return_value="conexion"):
                envoltorio.get_new_connection(self.params)
        obtener.assert_not_called()
