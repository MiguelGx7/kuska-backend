"""Pruebas de notificaciones: funciones internas (Paso 5) y endpoints (Paso 10)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.usuarios.models import Rol, Usuario
from apps.usuarios.tests import cliente_de, crear_admin, crear_usuario

from .models import Notificacion
from .services import crear_notificacion, notificar_administradores


class NotificacionServiceTests(TestCase):
    def setUp(self):
        self.admin1 = crear_admin("admin1@k.com", "1")
        self.admin2 = crear_admin("admin2@k.com", "2")
        self.donante = crear_usuario("dona@k.com", Rol.DONANTE, "10", tipo_donante="Persona")

    def test_crear_notificacion(self):
        n = crear_notificacion(self.donante, Notificacion.Tipo.DONACION_VALIDADA, "Título", "Mensaje")
        self.assertEqual(n.usuario, self.donante)
        self.assertEqual(n.tipo, "donacion_validada")
        self.assertFalse(n.leida)

    def test_recorta_textos_largos(self):
        n = crear_notificacion(self.donante, Notificacion.Tipo.SANCION, "t" * 200, "m" * 900)
        self.assertEqual(len(n.titulo), 120)
        self.assertEqual(len(n.mensaje), 500)
        self.assertTrue(n.mensaje.endswith("…"))

    def test_notifica_a_cada_administrador_activo(self):
        Usuario.objects.filter(pk=self.admin2.pk).update(estado="Inactivo")
        notificar_administradores(Notificacion.Tipo.NUEVA_DONACION, "Nueva", "Mensaje")
        destinatarios = set(Notificacion.objects.values_list("usuario__correo", flat=True))
        self.assertEqual(destinatarios, {"admin1@k.com"})


# =====================================================================
# Paso 10: endpoints (RN-41)
# =====================================================================
class NotificacionApiTests(TestCase):
    def setUp(self):
        self.admin = crear_admin()
        self.donante = crear_usuario("dona@k.com", Rol.DONANTE, "10", tipo_donante="Persona")
        self.otro = crear_usuario("bene@k.com", Rol.BENEFICIARIO, "11")
        self.cliente = cliente_de(self.donante)
        self.vieja = crear_notificacion(self.donante, Notificacion.Tipo.DONACION_VALIDADA, "Vieja", "m")
        self.nueva = crear_notificacion(self.donante, Notificacion.Tipo.NOVEDAD_RECHAZADA, "Nueva", "m")
        self.leida = crear_notificacion(self.donante, Notificacion.Tipo.DONACION_VALIDADA, "Leída", "m")
        Notificacion.objects.filter(pk=self.leida.pk).update(leida=True)
        Notificacion.objects.filter(pk=self.vieja.pk).update(fecha=timezone.now() - timedelta(days=1))
        self.ajena = crear_notificacion(self.otro, Notificacion.Tipo.SOLICITUD_APROBADA, "Ajena", "m")

    def ids(self, **filtros):
        r = self.cliente.get("/api/notificaciones/", filtros)
        self.assertEqual(r.status_code, 200, r.data)
        return [n["id"] for n in r.data["results"]], r.data

    def test_solo_las_propias_de_la_mas_nueva_a_la_mas_vieja(self):
        ids, datos = self.ids()
        self.assertEqual(ids, [self.leida.pk, self.nueva.pk, self.vieja.pk])
        self.assertEqual(datos["no_leidas"], 2)
        self.assertEqual(datos["count"], 3)

    def test_filtros_leida_y_tipo_sin_cambiar_el_contador(self):
        ids, datos = self.ids(leida="false")
        self.assertEqual(ids, [self.nueva.pk, self.vieja.pk])
        ids, datos = self.ids(tipo="donacion_validada", leida="true")
        self.assertEqual(ids, [self.leida.pk])
        self.assertEqual(datos["no_leidas"], 2)  # total del usuario, no del filtro

    def test_leer_una(self):
        r = self.cliente.post(f"/api/notificaciones/{self.nueva.pk}/leer/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["leida"])
        self.assertEqual(self.ids()[1]["no_leidas"], 1)
        # Leerla otra vez no falla.
        self.assertEqual(self.cliente.post(f"/api/notificaciones/{self.nueva.pk}/leer/").status_code, 200)

    def test_leer_una_ajena_o_inexistente_responde_404(self):
        r = self.cliente.post(f"/api/notificaciones/{self.ajena.pk}/leer/")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(str(r.data["detail"]), "No encontramos esa notificación.")
        self.ajena.refresh_from_db()
        self.assertFalse(self.ajena.leida)
        self.assertEqual(self.cliente.post("/api/notificaciones/999999/leer/").status_code, 404)
        self.assertEqual(self.cliente.post("/api/notificaciones/abc/leer/").status_code, 404)

    def test_leer_todas_devuelve_cuantas_cambio(self):
        r = self.cliente.post("/api/notificaciones/leer-todas/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, {"marcadas": 2, "no_leidas": 0})
        self.assertEqual(self.cliente.post("/api/notificaciones/leer-todas/").data["marcadas"], 0)
        self.ajena.refresh_from_db()
        self.assertFalse(self.ajena.leida)  # las de otros no se tocan

    def test_requiere_login_y_cuenta_activa_y_sirve_al_administrador(self):
        self.assertEqual(APIClient().get("/api/notificaciones/").status_code, 401)
        crear_notificacion(self.admin, Notificacion.Tipo.NUEVA_DONACION, "Para el admin", "m")
        self.assertEqual(cliente_de(self.admin).get("/api/notificaciones/").data["count"], 1)
        # Bloqueado: EsUsuarioActivo responde 403. Inactivo: el token deja de valer (401).
        Usuario.objects.filter(pk=self.donante.pk).update(estado="Bloqueado")
        self.assertEqual(self.cliente.get("/api/notificaciones/").status_code, 403)
        Usuario.objects.filter(pk=self.donante.pk).update(estado="Inactivo")
        self.assertEqual(self.cliente.get("/api/notificaciones/").status_code, 401)
