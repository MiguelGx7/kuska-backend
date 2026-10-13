"""
Pruebas del Paso 3 (autenticación) y del Paso 4 (administración de usuarios).

Corren sobre una base temporal (test_<DB_NAME>) que Django crea y borra.
"""

import base64
import importlib
import json
import re
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

import requests
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.sanciones.models import Sancion
from config.certificado import ruta_certificado
from config.correo import BREVO, CONSOLA, SMTP, elegir_backend, limpiar_clave, resumen_configuracion

from .apps import registrar_configuracion_correo
from .models import CodigoVerificacion, Rol, Usuario
from .permissions import EsAdministrador, EsBeneficiario, EsDonante
from .services import (
    MENSAJE_CODIGO_INVALIDO,
    MENSAJE_CODIGO_REGISTRO,
    MENSAJE_RECUPERACION,
    _enmascarar_correo,
    crear_usuario as crear_usuario_directo,
)

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

    def registrar(self, codigo=None, **datos):
        """Pide el código al correo (paso 1) y registra con él (paso 2)."""
        datos = {**self.base, **datos}
        if codigo is None:
            r = self.post("/api/auth/registro/solicitar-codigo/", {"correo": datos["correo"]})
            # Si el correo ya está registrado no hay código: se prueba la validación de datos.
            codigo = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1) if r.status_code == 200 else "000000"
        return self.post(self.url, {**datos, "codigo": codigo})

    def test_registra_donante_negocio_sin_devolver_contrasena(self):
        r = self.registrar(
            correo="Dona@Mail.com", rol="Donante", numero_documento="1",
            tipo_donante="Negocio", nombre_negocio="Tienda", nit="900",
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["usuario"]["rol"], "Donante")
        self.assertEqual(r.data["usuario"]["correo"], "dona@mail.com")
        self.assertNotIn("contrasena", r.data["usuario"])
        self.assertNotIn("password", r.data["usuario"])
        self.assertIn("access", r.data)  # RN-04d: el registro devuelve lo mismo que el login
        self.assertIn("refresh", r.data)

    def test_registra_beneficiario_sin_datos_de_donante(self):
        r = self.registrar(correo="b@mail.com", rol="Beneficiario", numero_documento="2")
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.data["usuario"]["tipo_donante"])
        self.assertIsNone(Usuario.objects.get(correo="b@mail.com").nit)

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
        return CodigoVerificacion.objects.filter(usuario=self.usuario, proposito="Recuperar contraseña").latest("id")

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
        CodigoVerificacion.objects.filter(usuario=self.usuario, proposito="Recuperar contraseña").update(
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


# =====================================================================
# Paso 14: certificado de Aiven por variable (despliegue en Railway)
# =====================================================================
class CertificadoAivenTests(SimpleTestCase):
    PEM = "-----BEGIN CERTIFICATE-----\nMIIabc\n-----END CERTIFICATE-----"

    def setUp(self):
        self.carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(self.carpeta.cleanup)
        parche = mock.patch("config.certificado.tempfile.gettempdir", return_value=self.carpeta.name)
        parche.start()
        self.addCleanup(parche.stop)

    def b64(self, texto):
        return base64.b64encode(texto.encode("utf-8")).decode("ascii")

    def leer(self, ruta):
        self.assertTrue(ruta.startswith(self.carpeta.name))
        return Path(ruta).read_text(encoding="utf-8")

    def test_en_local_usa_la_ruta_relativa_al_proyecto(self):
        self.assertEqual(ruta_certificado("/proyecto", "ca.pem"), str(Path("/proyecto") / "ca.pem"))

    def test_contenido_se_escribe_en_un_temporal(self):
        ruta = ruta_certificado("/proyecto", "ca.pem", contenido=self.PEM)  # el contenido manda sobre la ruta
        self.assertEqual(self.leer(ruta), self.PEM + "\n")

    def test_contenido_acepta_saltos_de_linea_escritos_como_barra_n(self):
        escrito = self.PEM.replace("\n", r"\n")  # una sola línea con \n literales (barra + n)
        self.assertNotIn("\n", escrito)
        self.assertEqual(self.leer(ruta_certificado("/p", contenido=escrito)), self.PEM + "\n")

    def test_base64_se_decodifica_y_se_escribe(self):
        ruta = ruta_certificado("/p", en_base64=self.b64(self.PEM + "\r\n"))
        self.assertEqual(self.leer(ruta).replace("\r", ""), self.PEM + "\n")

    def test_base64_acepta_espacios_y_saltos_si_se_pego_partido(self):
        valor = self.b64(self.PEM)
        partido = f"  {valor[:10]}\n{valor[10:30]} {valor[30:]}  "
        self.assertEqual(self.leer(ruta_certificado("/p", en_base64=partido)), self.PEM + "\n")

    def test_prioridad_base64_luego_contenido_luego_ruta(self):
        otro = self.PEM.replace("MIIabc", "MIIotro")
        ruta = ruta_certificado("/p", "ca.pem", contenido=otro, en_base64=self.b64(self.PEM))
        self.assertIn("MIIabc", self.leer(ruta))
        ruta = ruta_certificado("/p", "ca.pem", contenido=otro, en_base64="   ")  # base64 vacío: sigue
        self.assertIn("MIIotro", self.leer(ruta))

    def test_base64_invalido_o_que_no_es_pem(self):
        with self.assertRaisesMessage(ValueError, "DB_SSL_CA_BASE64 no es un base64 válido"):
            ruta_certificado("/p", en_base64="esto no es base64!!")
        with self.assertRaisesMessage(ValueError, "DB_SSL_CA_BASE64 no es un base64 válido"):
            ruta_certificado("/p", en_base64=base64.b64encode(b"\xff\xfe\xfa").decode())  # no es texto
        with self.assertRaisesMessage(ValueError, "DB_SSL_CA_BASE64 (ya decodificado) no contiene un certificado PEM"):
            ruta_certificado("/p", en_base64=self.b64("hola mundo"))

    def test_errores_claros_sin_certificado(self):
        with self.assertRaisesMessage(ValueError, "DB_SSL_CA_CONTENIDO no contiene un certificado PEM"):
            ruta_certificado("/p", contenido="texto cualquiera")
        with self.assertRaisesMessage(ValueError, "define DB_SSL_CA_BASE64 o DB_SSL_CA_CONTENIDO"):
            ruta_certificado("/p", contenido="  ")


# =====================================================================
# Paso 15: correo de recuperación (locmem en pruebas, Brevo en producción)
# =====================================================================
REMITENTE = "Kumora <no-responder@kumora.test>"
BREVO_FALSO = {
    "EMAIL_BACKEND": "anymail.backends.brevo.EmailBackend",
    "ANYMAIL": {"BREVO_API_KEY": "clave-falsa", "REQUESTS_TIMEOUT": 10},
    "DEFAULT_FROM_EMAIL": REMITENTE,
}


def respuesta_brevo(estado, cuerpo):
    """Respuesta HTTP simulada de la API de Brevo (no sale nada a internet)."""
    respuesta = requests.Response()
    respuesta.status_code = estado
    respuesta._content = json.dumps(cuerpo).encode()
    respuesta.headers["Content-Type"] = "application/json"
    return respuesta


@override_settings(DEFAULT_FROM_EMAIL=REMITENTE)
class CorreoRecuperacionTests(BaseAPITest):
    url = "/api/auth/recuperar/solicitar/"

    def setUp(self):
        super().setUp()
        self.usuario = crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")

    def test_asunto_y_texto_claros_en_espanol(self):
        # En las pruebas Django usa el backend en memoria (locmem): mail.outbox.
        r = self.post(self.url, {"correo": "dona@mail.com"})
        self.assertEqual(r.data, {"mensaje": MENSAJE_RECUPERACION})
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.subject, "Kumora: código para recuperar tu contraseña")
        self.assertEqual(correo.to, ["dona@mail.com"])
        self.assertEqual(correo.from_email, REMITENTE)
        self.assertIn("Hola, dona.", correo.body)
        self.assertRegex(correo.body, r"Tu código para recuperar la contraseña es: \d{6}\n")
        self.assertIn("Vence en 15 minutos.", correo.body)
        self.assertIn("Si no lo pediste, ignora este correo.", correo.body)

    @override_settings(**BREVO_FALSO)
    def test_con_brevo_se_envia_por_su_api_https(self):
        with mock.patch(
            "requests.Session.request", return_value=respuesta_brevo(201, {"messageId": "<abc@smtp-relay.brevo.com>"})
        ) as peticion:
            r = self.post(self.url, {"correo": "dona@mail.com"})
        self.assertEqual(r.data, {"mensaje": MENSAJE_RECUPERACION})
        peticion.assert_called_once()
        metodo, url = peticion.call_args.kwargs["method"], peticion.call_args.kwargs["url"]
        datos = json.loads(peticion.call_args.kwargs["data"])
        self.assertEqual((metodo.upper(), url), ("POST", "https://api.brevo.com/v3/smtp/email"))
        self.assertEqual(peticion.call_args.kwargs["headers"]["api-key"], "clave-falsa")
        self.assertEqual(peticion.call_args.kwargs["timeout"], 10)
        self.assertEqual(datos["sender"], {"email": "no-responder@kumora.test", "name": "Kumora"})
        self.assertEqual(datos["to"], [{"email": "dona@mail.com"}])
        self.assertEqual(datos["subject"], "Kumora: código para recuperar tu contraseña")
        self.assertIn("Vence en 15 minutos.", datos["textContent"])

    @override_settings(**BREVO_FALSO)
    def test_si_brevo_falla_la_respuesta_es_la_misma_y_queda_en_el_log(self):
        fallas = [
            requests.ConnectionError("sin conexión con Brevo"),
            respuesta_brevo(401, {"code": "unauthorized", "message": "Key not found"}),
        ]
        for falla in fallas:
            with self.subTest(falla=type(falla).__name__):
                efecto = {"side_effect": falla} if isinstance(falla, Exception) else {"return_value": falla}
                with mock.patch("requests.Session.request", **efecto), \
                        self.assertLogs("apps.usuarios.services", level="ERROR") as log:
                    r = self.post(self.url, {"correo": "dona@mail.com"})
                # RN-04b: misma respuesta; el fallo solo queda en el log.
                self.assertEqual((r.status_code, r.data), (200, {"mensaje": MENSAJE_RECUPERACION}))
                self.assertIn(f"No se pudo enviar el código de recuperación al usuario {self.usuario.pk}.", log.output[0])
        # El código sí se generó (el usuario puede pedir otro cuando el correo funcione).
        self.assertEqual(CodigoVerificacion.objects.filter(usuario=self.usuario, proposito="Recuperar contraseña").count(), 2)


class ElegirBackendCorreoTests(SimpleTestCase):
    def test_prioridad_brevo_luego_smtp_luego_consola(self):
        self.assertEqual(elegir_backend("xkeysib-123", "yo@gmail.com"), "anymail.backends.brevo.EmailBackend")
        self.assertEqual(elegir_backend("", "yo@gmail.com"), "django.core.mail.backends.smtp.EmailBackend")
        self.assertEqual(elegir_backend("  ", ""), "django.core.mail.backends.console.EmailBackend")
        self.assertEqual(elegir_backend(None, None), "django.core.mail.backends.console.EmailBackend")


# =====================================================================
# Diagnóstico del correo en producción: logs sin datos sensibles
# =====================================================================
@override_settings(DEFAULT_FROM_EMAIL=REMITENTE)
class LogsRecuperacionTests(BaseAPITest):
    url = "/api/auth/recuperar/solicitar/"

    def setUp(self):
        super().setUp()
        self.usuario = crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")

    def test_info_al_enviar_solo_con_el_id_y_sin_el_codigo(self):
        with self.assertLogs("apps.usuarios.services", level="INFO") as log:
            self.post(self.url, {"correo": "dona@mail.com"})
        codigo = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        texto = "\n".join(log.output)
        self.assertIn(f"INFO:apps.usuarios.services:Código de recuperación enviado al usuario {self.usuario.pk} (", texto)
        self.assertNotIn(codigo, texto)
        self.assertNotIn("dona@mail.com", texto)

    def test_warning_si_el_correo_no_es_de_ningun_usuario_sin_mostrarlo(self):
        with self.assertLogs("apps.usuarios.services", level="WARNING") as log:
            r = self.post(self.url, {"correo": "Nadie.Aqui@correo.com"})
        self.assertEqual(r.data, {"mensaje": MENSAJE_RECUPERACION})  # RN-04b: misma respuesta
        self.assertEqual(mail.outbox, [])
        self.assertEqual(
            log.output,
            ["WARNING:apps.usuarios.services:Recuperación pedida para un correo que no es de ningún usuario: "
             "Na***@correo.com"],
        )

    @override_settings(**BREVO_FALSO)
    def test_el_error_de_envio_muestra_el_id_y_no_el_correo(self):
        with mock.patch("requests.Session.request", side_effect=requests.ConnectionError("sin red")), \
                self.assertLogs("apps.usuarios.services", level="ERROR") as log:
            self.post(self.url, {"correo": "dona@mail.com"})
        self.assertIn(f"No se pudo enviar el código de recuperación al usuario {self.usuario.pk}.", log.output[0])
        self.assertNotIn("dona@mail.com", log.output[0])

    def test_enmascarar_correo(self):
        self.assertEqual(_enmascarar_correo("dona@mail.com"), "do***@mail.com")
        self.assertEqual(_enmascarar_correo("a@x.co"), "a***@x.co")
        self.assertEqual(_enmascarar_correo("sin-arroba"), "***")


class ConfiguracionCorreoTests(SimpleTestCase):
    CLAVE = "xkeysib-0123456789secreta"

    def test_limpia_espacios_y_comillas_de_la_clave(self):
        self.assertEqual(limpiar_clave(f'  "{self.CLAVE}" '), self.CLAVE)
        self.assertEqual(limpiar_clave(f"'{self.CLAVE}'"), self.CLAVE)
        self.assertEqual(limpiar_clave(None), "")
        # Unas comillas vacías no cuentan como clave: no se elige Brevo.
        self.assertEqual(elegir_backend('""', ""), CONSOLA)

    def test_resumen_con_brevo_bien_configurado_no_muestra_la_clave(self):
        mensajes = resumen_configuracion(BREVO, self.CLAVE, REMITENTE, debug=False)
        self.assertEqual(mensajes, [("INFO", f"Correo: se usa Brevo (API HTTPS). Remitente: {REMITENTE}.")])

    def test_avisa_si_la_clave_es_smtp_sin_mostrarla(self):
        mensajes = resumen_configuracion(BREVO, "xsmtpsib-abcdef123456", REMITENTE, debug=False)
        self.assertEqual(mensajes[1][0], "WARNING")
        self.assertIn("empieza por 'xsmtpsib-'", mensajes[1][1])
        self.assertNotIn("abcdef123456", str(mensajes))

    def test_avisa_si_falta_el_remitente_o_si_produccion_queda_en_consola(self):
        self.assertIn("DEFAULT_FROM_EMAIL", resumen_configuracion(BREVO, self.CLAVE, "", debug=False)[1][1])
        self.assertIn("no se envían", resumen_configuracion(CONSOLA, "", "", debug=False)[1][1])
        self.assertEqual(len(resumen_configuracion(CONSOLA, "", "", debug=True)), 1)  # en desarrollo es normal
        self.assertIn("Correo: se usa SMTP.", resumen_configuracion(SMTP, "", REMITENTE, debug=False)[0][1])

    @override_settings(EMAIL_BACKEND=BREVO, BREVO_API_KEY=CLAVE, DEFAULT_FROM_EMAIL=REMITENTE, DEBUG=False)
    def test_al_arrancar_queda_en_el_log_que_backend_se_eligio(self):
        with self.assertLogs("apps.usuarios.apps", level="INFO") as log:
            registrar_configuracion_correo()
        self.assertEqual(
            log.output, [f"INFO:apps.usuarios.apps:Correo: se usa Brevo (API HTTPS). Remitente: {REMITENTE}."]
        )
        self.assertNotIn(self.CLAVE, "\n".join(log.output))


# =====================================================================
# Paso 16: registro con código de verificación por correo (RN-04d)
# =====================================================================
class RegistroConCodigoTests(BaseAPITest):
    solicitar = "/api/auth/registro/solicitar-codigo/"
    url = "/api/auth/registro/"
    datos = {
        "correo": "nueva@mail.com", "rol": "Beneficiario", "nombres": "Nueva", "apellidos": "Persona",
        "tipo_documento": "CC", "numero_documento": "555", "telefono": "3001112233", "contrasena": CLAVE,
    }

    def pedir_codigo(self, correo="nueva@mail.com"):
        r = self.post(self.solicitar, {"correo": correo})
        self.assertEqual(r.status_code, 200, r.data)
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def registrar(self, codigo, **cambios):
        return self.post(self.url, {**self.datos, **cambios, "codigo": codigo})

    def codigos_registro(self, correo="nueva@mail.com"):
        return CodigoVerificacion.objects.filter(correo=correo, proposito="Registro")

    def assertNoSeCreoNada(self, r, correo="nueva@mail.com"):
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data, {"codigo": [MENSAJE_CODIGO_INVALIDO]})
        self.assertFalse(Usuario.objects.filter(correo=correo).exists())

    # --- Paso 1: pedir el código ---
    def test_solicitar_codigo_con_correo_libre(self):
        r = self.post(self.solicitar, {"correo": "  Nueva@Mail.com "})
        self.assertEqual((r.status_code, r.data), (200, {"mensaje": MENSAJE_CODIGO_REGISTRO}))
        correo = mail.outbox[0]
        self.assertEqual(correo.subject, "Kumora: tu código para crear la cuenta")
        self.assertEqual(correo.to, ["nueva@mail.com"])
        self.assertRegex(correo.body, r"Tu código para crear tu cuenta en Kumora es: \d{6}\n")
        self.assertIn("Vence en 15 minutos.", correo.body)
        codigo = re.search(r"\b(\d{6})\b", correo.body).group(1)
        guardado = self.codigos_registro().get()
        self.assertIsNone(guardado.usuario)  # el usuario aún no existe
        self.assertNotEqual(guardado.codigo_hash, codigo)  # se guarda con hash
        self.assertAlmostEqual(
            (guardado.fecha_expiracion - timezone.now()).total_seconds(), 15 * 60, delta=60
        )
        self.assertFalse(Usuario.objects.filter(correo="nueva@mail.com").exists())

    def test_solicitar_codigo_con_correo_registrado(self):
        crear_usuario("ya@mail.com", Rol.BENEFICIARIO, "1")
        r = self.post(self.solicitar, {"correo": "YA@mail.com"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data, {"correo": ["Ya existe una cuenta con este correo."]})  # el mismo de RN-02
        self.assertEqual(mail.outbox, [])
        self.assertFalse(CodigoVerificacion.objects.exists())

    def test_solicitar_codigo_valida_el_formato(self):
        r = self.post(self.solicitar, {"correo": "no-es-correo"})
        self.assertEqual(r.data, {"correo": ["Escribe un correo válido."]})

    def test_si_el_correo_no_se_envia_responde_503_y_no_queda_codigo(self):
        with mock.patch("apps.usuarios.services.send_mail", side_effect=OSError("sin red")), \
                self.assertLogs("apps.usuarios.services", level="ERROR"):
            r = self.post(self.solicitar, {"correo": "nueva@mail.com"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("No pudimos enviar el correo", str(r.data["detail"]))
        self.assertFalse(CodigoVerificacion.objects.exists())

    def test_pedir_otro_codigo_invalida_el_anterior(self):
        primero = self.pedir_codigo()
        segundo = self.pedir_codigo()
        if primero != segundo:
            self.assertNoSeCreoNada(self.registrar(primero))
        self.assertEqual(self.registrar(segundo).status_code, 201)

    def test_limite_de_5_por_minuto(self):
        codigos = [self.api.post(self.solicitar, {"correo": f"x{i}@mail.com"}, format="json").status_code
                   for i in range(6)]
        self.assertEqual(codigos, [200] * 5 + [429])

    # --- Paso 2: registrar con el código ---
    def test_registro_con_codigo_valido_crea_la_cuenta_y_entra(self):
        r = self.registrar(self.pedir_codigo())
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(set(r.data), {"access", "refresh", "usuario"})
        self.assertEqual(r.data["usuario"]["correo"], "nueva@mail.com")
        self.assertTrue(self.codigos_registro().get().usado)
        # El access sirve de inmediato.
        cliente = APIClient()
        cliente.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access']}")
        self.assertEqual(cliente.get("/api/auth/perfil/").status_code, 200)

    def test_codigo_incorrecto_no_crea_nada_y_suma_un_intento(self):
        codigo = self.pedir_codigo()
        self.assertNoSeCreoNada(self.registrar("000000" if codigo != "000000" else "111111"))
        self.assertEqual(self.codigos_registro().get().intentos, 1)

    def test_codigo_vencido(self):
        codigo = self.pedir_codigo()
        self.codigos_registro().update(fecha_expiracion=timezone.now() - timedelta(seconds=1))
        self.assertNoSeCreoNada(self.registrar(codigo))

    def test_cinco_intentos_y_ya_no_sirve_ni_el_correcto(self):
        codigo = self.pedir_codigo()
        malo = "000000" if codigo != "000000" else "111111"
        for _ in range(5):
            self.registrar(malo)
        self.assertNoSeCreoNada(self.registrar(codigo))

    def test_codigo_ya_usado_no_sirve_otra_vez(self):
        codigo = self.pedir_codigo()
        self.assertEqual(self.registrar(codigo).status_code, 201)
        Usuario.objects.filter(correo="nueva@mail.com").delete()  # aunque la cuenta ya no exista
        self.assertNoSeCreoNada(self.registrar(codigo, numero_documento="556"))

    def test_codigo_de_otro_correo_no_sirve(self):
        codigo = self.pedir_codigo("otra@mail.com")
        self.assertNoSeCreoNada(self.registrar(codigo))
        self.assertFalse(Usuario.objects.filter(correo="otra@mail.com").exists())

    def test_codigo_de_recuperacion_no_sirve_para_registrarse(self):
        # Un usuario pide recuperar su contraseña y luego el admin le cambia el correo (RN-04c):
        # el código de recuperación sigue ligado a "nueva@mail.com", pero no es de registro.
        usuario = crear_usuario("nueva@mail.com", Rol.BENEFICIARIO, "1")
        self.post("/api/auth/recuperar/solicitar/", {"correo": "nueva@mail.com"})
        codigo = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        Usuario.objects.filter(pk=usuario.pk).update(correo="cambiado@mail.com")
        self.assertNoSeCreoNada(self.registrar(codigo))

    def test_codigo_de_registro_no_sirve_para_recuperar(self):
        codigo = self.pedir_codigo()
        crear_usuario("nueva@mail.com", Rol.BENEFICIARIO, "1")  # existe, pero sin código de recuperación
        r = self.post("/api/auth/recuperar/confirmar/",
                      {"correo": "nueva@mail.com", "codigo": codigo, "contrasena_nueva": CLAVE_NUEVA})
        self.assertEqual(r.data, {"codigo": [MENSAJE_CODIGO_INVALIDO]})
        self.assertTrue(Usuario.objects.get(correo="nueva@mail.com").check_password(CLAVE))

    def test_los_datos_se_validan_antes_y_no_gastan_el_codigo(self):
        codigo = self.pedir_codigo()
        r = self.registrar(codigo, rol="Administrador")  # RN-01
        self.assertEqual(r.data, {"rol": ["Solo puedes registrarte como Donante o Beneficiario."]})
        r = self.registrar(codigo, contrasena="123")  # contraseña débil
        self.assertIn("contrasena", r.data)
        guardado = self.codigos_registro().get()
        self.assertEqual((guardado.intentos, guardado.usado), (0, False))
        self.assertEqual(self.registrar(codigo).status_code, 201)

    def test_el_codigo_es_obligatorio(self):
        r = self.post(self.url, self.datos)
        self.assertEqual(r.data, {"codigo": ["Escribe el código que te enviamos al correo."]})

    def test_crear_usuario_directo_no_pide_codigo(self):
        # cargar_demo (y createsuperuser) crean usuarios sin pasar por el registro público.
        datos = {k: v for k, v in self.datos.items()}
        usuario = crear_usuario_directo(datos)
        self.assertEqual(usuario.correo, "nueva@mail.com")
        self.assertFalse(CodigoVerificacion.objects.exists())

    def test_la_recuperacion_sigue_funcionando(self):
        usuario = crear_usuario("dona@mail.com", Rol.DONANTE, "10", tipo_donante="Persona")
        self.post("/api/auth/recuperar/solicitar/", {"correo": "dona@mail.com"})
        self.assertEqual(mail.outbox[-1].subject, "Kumora: código para recuperar tu contraseña")
        codigo = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        guardado = CodigoVerificacion.objects.get(usuario=usuario)
        self.assertEqual((guardado.proposito, guardado.correo), ("Recuperar contraseña", "dona@mail.com"))
        r = self.post("/api/auth/recuperar/confirmar/",
                      {"correo": "dona@mail.com", "codigo": codigo, "contrasena_nueva": CLAVE_NUEVA})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.login("dona@mail.com", CLAVE_NUEVA).status_code, 200)


class MigracionCodigoVerificacionTests(TestCase):
    def test_los_codigos_existentes_reciben_el_correo_de_su_usuario(self):
        # Simula un registro de la antigua recuperacion_contrasena: con usuario y sin correo.
        from django.apps import apps as registro_apps

        migracion = importlib.import_module("apps.usuarios.migrations.0003_codigo_verificacion")
        usuario = crear_usuario("vieja@mail.com", Rol.BENEFICIARIO, "1")
        codigo = CodigoVerificacion.objects.create(
            usuario=usuario, proposito="Recuperar contraseña", correo="", codigo_hash="x",
            fecha_expiracion=timezone.now(),
        )
        migracion.completar_correo(registro_apps, None)
        codigo.refresh_from_db()
        self.assertEqual(codigo.correo, "vieja@mail.com")
