"""Pruebas de las funciones internas de notificaciones (Paso 5)."""

from django.test import TestCase

from apps.usuarios.models import Rol, Usuario
from apps.usuarios.tests import crear_admin, crear_usuario

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
