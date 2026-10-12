"""
Pruebas del Paso 3 (autenticación) y del Paso 4 (administración de usuarios).

Corren sobre una base temporal (test_<DB_NAME>) que Django crea y borra.
"""

import re
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.sanciones.models import Sancion

from .models import RecuperacionContrasena, Rol, Usuario
from .permissions import EsAdministrador, EsBeneficiario, EsDonante
from .services import MENSAJE_CODIGO_INVALIDO

CLAVE = "Kumora.2026x"
CLAVE_NUEVA = "Otra.Clave2026"


def crear_usuario(correo, rol, documento, **extra):
    return Usuario.objects.create_user(
        correo,
        CLAVE,
        rol=Rol.objects.get(nombre=rol),
        nombres=correo.split("@")[0],
        apellidos="Prueba",
        tipo_documento="CC",
        numero_documento=documento,
        telefono="3000000000",
        **extra,
    )


def crear_admin(correo="admin@k.com", documento="1"):
    return Usuario.objects.create_superuser(
        correo,
        CLAVE,
        nombres="Admin",
        apellidos="Kumora",
        tipo_documento="CC",
        numero_documento=documento,
        telefono="3000000000",
    )


def cliente_de(usuario):
    cliente = APIClient()
    token = RefreshToken.for_user(usuario).access_token
    cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return cliente


# Hash rápido solo para las pruebas (el real es PBKDF2, más lento a propósito).
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BaseAPITest(TestCase):
    def setUp(self):
        cache.clear()  # reinicia el contador de throttling
        self.api = APIClient()

    def post(self, url, datos, cliente=None):
        """POST sin que el límite de 5/min interfiera entre pasos de una prueba."""
        cache.clear()
        return (cliente or self.api).post(url, datos, format="json")

    def login(self, correo, contrasena=CLAVE):
        return self.post("/api/auth/login/", {"correo": correo, "contrasena": contrasena})


# =====================================================================
# Paso 3: autenticación
# =====================================================================
class RegistroTests(BaseAPITest):
    url = "/api/auth/registro/"
    base = {
        "nombres": "Ana",
        "apellidos": "Paz",
        "tipo_documento": "CC",
        "telefono": "3001234567",
        "contrasena": CLAVE,
    }

    def registrar(self, **datos):
        return self.post(self.url, {**self.base, **datos})

    def test_registra_donante_negocio_sin_devolver_contrasena(self):
        r = self.registrar(
            correo="Dona@Mail.com", rol="Donante", numero_documento="1",
            tipo_donante="Negocio", nombre_negocio="Tienda", nit="900",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["rol"], "Donante")
        self.assertEqual(r.data["correo"], "dona@mail.com")
        self.assertNotIn("contrasena", r.data)
        self.assertNotIn("password", r.data)
        self.assertNotIn("access", r.data)  # el registro no inicia sesión

    def test_registra_beneficiario_sin_datos_de_donante(self):
        r = self.registrar(correo="b@mail.com", rol="Beneficiario", numero_documento="2")
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.data["tipo_donante"])
        self.assertIsNone(r.data["nit"])

    def test_rechaza_rol_administrador(self):
        r = self.registrar(correo="x@mail.com", rol="Administrador", numero_documento="3")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["rol"], ["Solo puedes registrarte como Donante o Beneficiario."])

    def test_rn01_el_administrador_se_crea_por_consola_con_su_rol(self):
        # createsuperuser usa create_superuser: asigna el rol Administrador (rol_id es obligatorio).
        admin = crear_admin("consola@mail.com", "77")
        self.assertEqual(admin.rol.nombre, Rol.ADMINISTRADOR)
        self.assertTrue(admin.is_superuser and admin.is_staff)
        self.assertEqual(admin.estado, "Activo")

    def test_negocio_exige_nombre_y_nit(self):
        r = self.registrar(correo="y@mail.com", rol="Donante", numero_documento="4", tipo_donante="Negocio")
        self.assertEqual(r.status_code, 400)
        self.assertIn("nit", r.data)
        self.assertIn("nombre_negocio", r.data)

    def test_donante_exige_tipo_donante(self):
        r = self.registrar(correo="y@mail.com", rol="Donante", numero_documento="4")
        self.assertEqual(r.status_code, 400)
        self.assertIn("tipo_donante", r.data)

    def test_persona_no_lleva_nit(self):
        r = self.registrar(
            correo="y@mail.com", rol="Donante", numero_documento="4", tipo_donante="Persona", nit="1"
        )
        self.assertEqual(r.status_code, 400)

    def test_beneficiario_no_lleva_datos_de_donante(self):
        r = self.registrar(correo="y@mail.com", rol="Beneficiario", numero_documento="4", nit="1")
        self.assertEqual(r.status_code, 400)

    def test_correo_y_documento_unicos(self):
        self.registrar(correo="dona@mail.com", rol="Beneficiario", numero_documento="1")
        r = self.registrar(correo="DONA@mail.com", rol="Beneficiario", numero_documento="1")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["correo"], ["Ya existe una cuenta con este correo."])
        self.assertEqual(r.data["numero_documento"], ["Ya existe una cuenta con este número de documento."])

    def test_aplica_validaciones_de_contrasena(self):
        r = self.registrar(correo="z@mail.com", rol="Beneficiario", numero_documento="9", contrasena="123")
        self.assertEqual(r.status_code, 400)
        self.assertIn("contrasena", r.data)


class LoginTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.admin = crear_admin()
        self.donante = crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")

    def bloquear(self, hasta):
        Usuario.objects.filter(pk=self.donante.pk).update(estado="Bloqueado", bloqueado_hasta=hasta)

    def test_credenciales_incorrectas_responde_401(self):
        r = self.login("dona@mail.com", "mala")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(str(r.data["detail"]), "Correo o contraseña incorrectos.")

    def test_login_devuelve_tokens_y_rol(self):
        r = self.login("DONA@mail.com")
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.data)
        self.assertIn("refresh", r.data)
        self.assertEqual(r.data["usuario"]["rol"], "Donante")

    def test_bloqueo_vigente_dice_hasta_cuando(self):
        self.bloquear(timezone.now() + timedelta(days=7))
        r = self.login("dona@mail.com")
        self.assertEqual(r.status_code, 403)
        self.assertIn("bloqueada hasta el", str(r.data["detail"]))

    def test_bloqueo_vencido_se_levanta_al_iniciar_sesion(self):
        pasado = timezone.now() - timedelta(minutes=1)
        self.bloquear(pasado)
        sancion = Sancion.objects.create(
            usuario=self.donante, tipo="Bloqueo temporal", motivo="x",
            creada_por=self.admin, fecha_fin=pasado,
        )
        r = self.login("dona@mail.com")
        self.donante.refresh_from_db()
        sancion.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.donante.estado, "Activo")
        self.assertIsNone(self.donante.bloqueado_hasta)
        self.assertFalse(sancion.vigente)

    def test_bloqueo_permanente_no_entra(self):
        self.bloquear(None)
        r = self.login("dona@mail.com")
        self.assertEqual(r.status_code, 403)
        self.assertIn("permanente", str(r.data["detail"]))

    def test_inactivo_no_entra(self):
        Usuario.objects.filter(pk=self.donante.pk).update(estado="Inactivo")
        r = self.login("dona@mail.com")
        self.assertEqual(r.status_code, 403)
        self.assertIn("inactiva", str(r.data["detail"]))


class RefreshTests(BaseAPITest):
    url = "/api/auth/refresh/"

    def setUp(self):
        super().setUp()
        self.donante = crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")
        self.refresh = self.login("dona@mail.com").data["refresh"]

    def test_rota_y_pone_el_anterior_en_lista_negra(self):
        r = self.post(self.url, {"refresh": self.refresh})
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.data["refresh"], self.refresh)
        self.assertEqual(self.post(self.url, {"refresh": self.refresh}).status_code, 401)

    def test_rechaza_usuario_bloqueado(self):
        Usuario.objects.filter(pk=self.donante.pk).update(
            estado="Bloqueado", bloqueado_hasta=timezone.now() + timedelta(days=1)
        )
        r = self.post(self.url, {"refresh": self.refresh})
        self.assertEqual(r.status_code, 401)
        self.assertIn("bloqueada", str(r.data["detail"]))


class PermisosTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.donante = crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")

    def test_ruta_protegida_sin_token_responde_401(self):
        self.assertEqual(self.api.get("/api/auth/perfil/").status_code, 401)

    def test_es_usuario_activo_rechaza_bloqueado_con_token_valido(self):
        cliente = cliente_de(self.donante)
        Usuario.objects.filter(pk=self.donante.pk).update(
            estado="Bloqueado", bloqueado_hasta=timezone.now() + timedelta(days=1)
        )
        r = cliente.get("/api/auth/perfil/")
        self.assertEqual(r.status_code, 403)
        self.assertIn("bloqueada hasta", str(r.data["detail"]))

    def test_permisos_por_rol(self):
        peticion = APIRequestFactory().get("/")
        peticion.user = self.donante
        self.assertTrue(EsDonante().has_permission(peticion, None))
        self.assertFalse(EsBeneficiario().has_permission(peticion, None))
        self.assertFalse(EsAdministrador().has_permission(peticion, None))


class PerfilTests(BaseAPITest):
    url = "/api/auth/perfil/"

    def setUp(self):
        super().setUp()
        self.donante = crear_usuario(
            "dona@mail.com", Rol.DONANTE, "10",
            tipo_donante="Negocio", nombre_negocio="Tienda", nit="900",
        )
        self.cliente = cliente_de(self.donante)

    def test_ver_perfil(self):
        r = self.cliente.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["nit"], "900")
        self.assertNotIn("password", r.data)

    def test_editar_campos_permitidos(self):
        r = self.cliente.patch(self.url, {"nombres": "Ana María", "direccion": ""}, format="json")
        self.donante.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.donante.nombres, "Ana María")
        self.assertIsNone(self.donante.direccion)

    def test_rechaza_campos_no_editables_sin_guardar_nada(self):
        r = self.cliente.patch(
            self.url,
            {"nombres": "Otro", "correo": "h@mail.com", "nit": "1", "rol": "Administrador"},
            format="json",
        )
        self.donante.refresh_from_db()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.data), {"correo", "nit", "rol"})
        self.assertEqual(r.data["nit"], ["Este dato no se puede editar desde el perfil."])
        self.assertNotEqual(self.donante.nombres, "Otro")

    def test_cada_dato_de_identidad_es_no_editable(self):
        for campo in ("tipo_documento", "numero_documento", "tipo_donante", "nombre_negocio"):
            with self.subTest(campo=campo):
                r = self.cliente.patch(self.url, {campo: "x"}, format="json")
                self.assertEqual(r.status_code, 400)
                self.assertIn(campo, r.data)

    def test_put_tambien_rechaza_no_editables(self):
        datos = {"nombres": "A", "apellidos": "B", "telefono": "1", "direccion": "C", "correo": "h@m.com"}
        r = self.cliente.put(self.url, datos, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("correo", r.data)


class CambiarContrasenaTests(BaseAPITest):
    url = "/api/auth/perfil/cambiar-contrasena/"

    def setUp(self):
        super().setUp()
        crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")
        datos = self.login("dona@mail.com").data
        self.refresh = datos["refresh"]
        self.cliente = APIClient()
        self.cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {datos['access']}")

    def test_contrasena_actual_incorrecta(self):
        r = self.post(self.url, {"contrasena_actual": "mala", "contrasena_nueva": CLAVE_NUEVA}, self.cliente)
        self.assertEqual(r.status_code, 400)
        self.assertIn("contrasena_actual", r.data)

    def test_cambia_e_invalida_refresh_tokens(self):
        r = self.post(self.url, {"contrasena_actual": CLAVE, "contrasena_nueva": CLAVE_NUEVA}, self.cliente)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.post("/api/auth/refresh/", {"refresh": self.refresh}).status_code, 401)
        self.assertEqual(self.login("dona@mail.com", CLAVE).status_code, 401)
        self.assertEqual(self.login("dona@mail.com", CLAVE_NUEVA).status_code, 200)


class RecuperarContrasenaTests(BaseAPITest):
    solicitar = "/api/auth/recuperar/solicitar/"
    confirmar = "/api/auth/recuperar/confirmar/"

    def setUp(self):
        super().setUp()
        self.usuario = crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")

    def pedir_codigo(self):
        self.post(self.solicitar, {"correo": "dona@mail.com"})
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def confirmar_con(self, codigo, correo="dona@mail.com", contrasena=CLAVE_NUEVA):
        return self.post(
            self.confirmar, {"correo": correo, "codigo": codigo, "contrasena_nueva": contrasena}
        )

    def ultima_recuperacion(self):
        return RecuperacionContrasena.objects.filter(usuario=self.usuario).latest("id")

    def assertMensajeGenerico(self, respuesta):
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.data, {"codigo": [MENSAJE_CODIGO_INVALIDO]})

    def test_respuesta_igual_exista_o_no_el_correo(self):
        r1 = self.post(self.solicitar, {"correo": "noexiste@mail.com"})
        r2 = self.post(self.solicitar, {"correo": "dona@mail.com"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.data, r2.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_codigo_con_hash_y_15_minutos(self):
        codigo = self.pedir_codigo()
        rec = self.ultima_recuperacion()
        self.assertNotEqual(rec.codigo_hash, codigo)
        minutos = (rec.fecha_expiracion - rec.fecha_creacion).total_seconds() / 60
        self.assertEqual(round(minutos), 15)

    def test_flujo_correcto_cambia_contrasena_e_invalida_refresh(self):
        refresh = self.login("dona@mail.com").data["refresh"]
        r = self.confirmar_con(self.pedir_codigo())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.post("/api/auth/refresh/", {"refresh": refresh}).status_code, 401)
        self.assertEqual(self.login("dona@mail.com", CLAVE_NUEVA).status_code, 200)

    def test_pedir_otro_codigo_invalida_el_anterior(self):
        viejo = self.pedir_codigo()
        nuevo = self.pedir_codigo()
        if viejo != nuevo:
            self.assertMensajeGenerico(self.confirmar_con(viejo))
        self.assertEqual(self.confirmar_con(nuevo).status_code, 200)

    def test_correo_inexistente_mismo_mensaje(self):
        self.assertMensajeGenerico(self.confirmar_con("123456", correo="noexiste@mail.com"))

    def test_codigo_vencido_mismo_mensaje(self):
        codigo = self.pedir_codigo()
        RecuperacionContrasena.objects.filter(usuario=self.usuario).update(
            fecha_expiracion=timezone.now() - timedelta(seconds=1)
        )
        self.assertMensajeGenerico(self.confirmar_con(codigo))

    def test_codigo_usado_mismo_mensaje(self):
        codigo = self.pedir_codigo()
        self.assertEqual(self.confirmar_con(codigo).status_code, 200)
        self.assertMensajeGenerico(self.confirmar_con(codigo, contrasena="Tercera.Clave2026"))

    def test_cinco_intentos_y_no_revela_cuantos_quedan(self):
        codigo = self.pedir_codigo()
        malo = "000000" if codigo != "000000" else "111111"
        for _ in range(5):
            r = self.confirmar_con(malo)
            self.assertMensajeGenerico(r)
            self.assertNotIn("intento", str(r.data))
        self.assertEqual(self.ultima_recuperacion().intentos, 5)
        # Sin intentos, ni el código correcto sirve.
        self.assertMensajeGenerico(self.confirmar_con(codigo))

    def test_contrasena_debil_igual_exista_o_no_y_no_gasta_intento(self):
        self.pedir_codigo()
        ra = self.confirmar_con("123456", correo="noexiste@mail.com", contrasena="123")
        rb = self.confirmar_con("123456", contrasena="123")
        self.assertEqual(ra.status_code, 400)
        self.assertEqual(ra.data, rb.data)
        self.assertIn("contrasena_nueva", ra.data)
        self.assertEqual(self.ultima_recuperacion().intentos, 0)


class ThrottleTests(BaseAPITest):
    def test_sexta_peticion_del_minuto_responde_429(self):
        datos = {"correo": "q@mail.com", "contrasena": "x"}
        codigos = [self.api.post("/api/auth/login/", datos, format="json").status_code for _ in range(6)]
        self.assertEqual(codigos, [401] * 5 + [429])

    def test_el_limite_es_por_endpoint(self):
        for _ in range(5):
            self.api.post("/api/auth/login/", {"correo": "q@mail.com", "contrasena": "x"}, format="json")
        r = self.api.post("/api/auth/recuperar/solicitar/", {"correo": "q@mail.com"}, format="json")
        self.assertEqual(r.status_code, 200)


# =====================================================================
# Paso 4: administración de usuarios
# =====================================================================
class AdminUsuarioTests(BaseAPITest):
    url = "/api/admin/usuarios/"

    def setUp(self):
        super().setUp()
        self.admin = crear_admin()
        self.donante = crear_usuario("dona@k.com", Rol.DONANTE, "10", tipo_donante="Persona")
        self.beneficiario = crear_usuario("bene@k.com", Rol.BENEFICIARIO, "11")
        self.cliente = cliente_de(self.admin)

    def test_solo_administradores(self):
        self.assertEqual(cliente_de(self.beneficiario).get(self.url).status_code, 403)
        self.assertEqual(cliente_de(self.donante).get(self.url).status_code, 403)

    def test_listar_sin_contrasena(self):
        r = self.cliente.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 3)
        self.assertNotIn("password", r.data["results"][0])
        self.assertNotIn("contrasena", r.data["results"][0])

    def test_filtros_y_busqueda(self):
        self.assertEqual(self.cliente.get(self.url, {"rol": "Donante"}).data["count"], 1)
        self.assertEqual(self.cliente.get(self.url, {"estado": "Activo"}).data["count"], 3)
        self.assertEqual(self.cliente.get(self.url, {"search": "bene"}).data["count"], 1)
        self.assertEqual(self.cliente.get(self.url, {"search": "11"}).data["count"], 1)

    def test_detalle(self):
        r = self.cliente.get(f"{self.url}{self.donante.pk}/")
        self.assertEqual(r.data["correo"], "dona@k.com")

    def test_corregir_correo_y_telefono(self):
        r = self.cliente.patch(
            f"{self.url}{self.donante.pk}/", {"correo": "Nuevo@K.com", "telefono": "311"}, format="json"
        )
        self.donante.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.donante.correo, "nuevo@k.com")
        self.assertEqual(self.donante.telefono, "311")

    def test_correo_debe_seguir_siendo_unico(self):
        r = self.cliente.patch(f"{self.url}{self.donante.pk}/", {"correo": "BENE@k.com"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data, {"correo": ["Ya existe una cuenta con este correo."]})

    def test_puede_dejar_su_mismo_correo(self):
        r = self.cliente.patch(f"{self.url}{self.donante.pk}/", {"correo": "dona@k.com"}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_solo_correo_y_telefono_son_editables(self):
        r = self.cliente.patch(
            f"{self.url}{self.donante.pk}/", {"nombres": "X", "estado": "Inactivo"}, format="json"
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.data), {"nombres", "estado"})

    def test_put_y_delete_no_permitidos(self):
        self.assertEqual(self.cliente.put(f"{self.url}{self.donante.pk}/", {}, format="json").status_code, 405)
        self.assertEqual(self.cliente.delete(f"{self.url}{self.donante.pk}/").status_code, 405)


class CambiarEstadoTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.admin = crear_admin()
        self.beneficiario = crear_usuario("bene@k.com", Rol.BENEFICIARIO, "11")
        self.cliente = cliente_de(self.admin)

    def cambiar(self, usuario, estado):
        return self.cliente.post(
            f"/api/admin/usuarios/{usuario.pk}/cambiar-estado/", {"estado": estado}, format="json"
        )

    def sancionar(self, tipo, fecha_fin=None):
        return Sancion.objects.create(
            usuario=self.beneficiario, tipo=tipo, motivo="x", creada_por=self.admin, fecha_fin=fecha_fin
        )

    def test_no_se_aplica_a_otro_administrador(self):
        otro = crear_admin("admin2@k.com", "2")
        r = self.cambiar(otro, "Inactivo")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(str(r.data["detail"]), "No se puede cambiar el estado de un Administrador.")

    def test_no_se_aplica_a_uno_mismo(self):
        r = self.cambiar(self.admin, "Inactivo")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(str(r.data["detail"]), "No puedes cambiar tu propio estado.")

    def test_solo_activo_o_inactivo(self):
        self.assertEqual(self.cambiar(self.beneficiario, "Bloqueado").status_code, 400)

    def test_ya_activo(self):
        r = self.cambiar(self.beneficiario, "Activo")
        self.assertEqual(r.status_code, 400)
        self.assertIn("ya está activo", str(r.data))

    def test_inactivar_invalida_refresh_y_corta_el_acceso(self):
        refresh = RefreshToken.for_user(self.beneficiario)
        cliente_beneficiario = cliente_de(self.beneficiario)
        r = self.cambiar(self.beneficiario, "Inactivo")
        self.beneficiario.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.beneficiario.estado, "Inactivo")
        self.assertEqual(self.post("/api/auth/refresh/", {"refresh": str(refresh)}).status_code, 401)
        self.assertEqual(cliente_beneficiario.get("/api/auth/perfil/").status_code, 401)

    def test_reactivar_sin_bloqueo_vuelve_a_activo(self):
        self.cambiar(self.beneficiario, "Inactivo")
        self.cambiar(self.beneficiario, "Activo")
        self.beneficiario.refresh_from_db()
        self.assertEqual(self.beneficiario.estado, "Activo")

    def test_bloqueado_no_se_activa_desde_aqui(self):
        hasta = timezone.now() + timedelta(days=3)
        self.sancionar("Bloqueo temporal", hasta)
        Usuario.objects.filter(pk=self.beneficiario.pk).update(estado="Bloqueado", bloqueado_hasta=hasta)
        r = self.cambiar(self.beneficiario, "Activo")
        self.assertEqual(r.status_code, 400)
        self.assertIn("vence su sanción", str(r.data))

    def test_rn75_vuelve_a_bloqueado_si_el_temporal_sigue_vigente(self):
        hasta = timezone.now() + timedelta(days=3)
        self.sancionar("Bloqueo temporal", hasta)
        Usuario.objects.filter(pk=self.beneficiario.pk).update(estado="Bloqueado", bloqueado_hasta=hasta)
        self.assertEqual(self.cambiar(self.beneficiario, "Inactivo").status_code, 200)
        self.cambiar(self.beneficiario, "Activo")
        self.beneficiario.refresh_from_db()
        self.assertEqual(self.beneficiario.estado, "Bloqueado")
        self.assertAlmostEqual(
            self.beneficiario.bloqueado_hasta.timestamp(), hasta.timestamp(), delta=1
        )

    def test_rn75_bloqueo_vencido_vuelve_a_activo_y_cierra_la_sancion(self):
        self.sancionar("Bloqueo temporal", timezone.now() - timedelta(minutes=1))
        self.cambiar(self.beneficiario, "Inactivo")
        self.cambiar(self.beneficiario, "Activo")
        self.beneficiario.refresh_from_db()
        self.assertEqual(self.beneficiario.estado, "Activo")
        self.assertIsNone(self.beneficiario.bloqueado_hasta)
        self.assertFalse(Sancion.objects.filter(usuario=self.beneficiario, vigente=True).exists())

    def test_rn75_bloqueo_permanente_vuelve_a_bloqueado_sin_fecha(self):
        self.sancionar("Bloqueo permanente")
        self.cambiar(self.beneficiario, "Inactivo")
        self.cambiar(self.beneficiario, "Activo")
        self.beneficiario.refresh_from_db()
        self.assertEqual(self.beneficiario.estado, "Bloqueado")
        self.assertIsNone(self.beneficiario.bloqueado_hasta)


# =====================================================================
# Paso 13: contrato de la API (Swagger)
# =====================================================================
class ContratoApiTests(TestCase):
    def test_todos_los_endpoints_tienen_descripcion_y_errores(self):
        from drf_spectacular.generators import SchemaGenerator

        esquema = SchemaGenerator().get_schema(request=None, public=True)
        faltan = [
            f"{metodo.upper()} {ruta}"
            for ruta, operaciones in esquema["paths"].items()
            for metodo, operacion in operaciones.items()
            if not operacion.get("description") or not any(c.startswith("4") for c in operacion["responses"])
        ]
        self.assertEqual(faltan, [])
        login = esquema["paths"]["/api/auth/login/"]["post"]
        self.assertEqual(set(login["responses"]), {"200", "400", "401", "403", "429"})
        self.assertIn("examples", login["requestBody"]["content"]["application/json"])
