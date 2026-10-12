"""Pruebas de los Pasos 6 a 8 y 11: carrito, solicitudes, entregas, novedades y vencimientos."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.files.storage import default_storage
from django.core.management import CommandError, call_command
from django.db.models import F
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.catalogo.models import Configuracion
from apps.donaciones.models import Donacion, DonacionAlimento, Novedad
from apps.donaciones.services import procesar_donaciones_desactualizadas
from apps.donaciones.tests import ALMACENAMIENTO_FALSO, BaseDonacionTest
from apps.notificaciones.models import Notificacion
from apps.sanciones.models import Sancion
from apps.usuarios.models import Rol, Usuario
from apps.usuarios.tests import cliente_de, crear_admin, crear_usuario

from . import services as servicios
from .models import CarritoItem, DetalleSolicitud, Entrega, Solicitud
from .services import (
    MOTIVO_PENDIENTE_VENCIDA,
    cancelar_si_pendiente_vencida,
    fin_del_dia,
    marcar_no_recogida_si_vencida,
)


class BaseCarritoTest(BaseDonacionTest):
    def setUp(self):
        super().setUp()
        Usuario.objects.filter(pk=self.beneficiario.pk).update(direccion="Carrera 7 # 1-1")
        self.beneficiario.refresh_from_db()
        self.cb = cliente_de(self.beneficiario)
        # d1: Ambas, con arroz (kg) y leche (unidad); d2: Recoger; d3: Domicilio.
        self.d1 = self.validada("Ambas", alimentos=2)
        self.arroz, self.leche = self.d1.alimentos.order_by("id")
        DonacionAlimento.objects.filter(pk=self.leche.pk).update(nombre="Leche", unidad_medida="unidad")
        self.leche.refresh_from_db()
        self.d2 = self.validada("Recoger")
        self.panela = self.d2.alimentos.get()
        self.d3 = self.validada("Domicilio")
        self.frijol = self.d3.alimentos.get()

    def validada(self, modalidad, alimentos=1, **extra):
        datos = {
            "cantidad_total": Decimal("10"),
            "cantidad_disponible": Decimal("10"),
            "max_por_solicitud": Decimal("3"),
            **extra,
        }
        return self.crear_en_bd(estado="Validada", modalidad=modalidad, alimentos=alimentos, **datos)

    def agregar(self, alimento, cantidad, cliente=None):
        return (cliente or self.cb).post(
            "/api/carrito/", {"donacion_alimento": alimento.pk, "cantidad": str(cantidad)}, format="json"
        )

    def en_carrito(self, alimento, cantidad):
        return CarritoItem.objects.create(
            beneficiario=self.beneficiario, donacion_alimento=alimento, cantidad=Decimal(cantidad)
        )

    def enviar(self, donaciones=None):
        return self.cb.post("/api/carrito/enviar/", {"donaciones": donaciones or []}, format="json")

    def solicitud_en_curso(self, donacion, estado="Pendiente", beneficiario=None):
        return Solicitud.objects.create(
            codigo_pedido="KUM-20260101-0001",
            beneficiario=beneficiario or self.beneficiario,
            donacion=donacion,
            modalidad_entrega="Recoger",
            estado=estado,
        )

    def disponible(self, alimento):
        alimento.refresh_from_db()
        return alimento.cantidad_disponible


# =====================================================================
# Carrito (RN-20)
# =====================================================================
class AgregarAlCarritoTests(BaseCarritoTest):
    def test_agrega_sin_reservar(self):
        r = self.agregar(self.arroz, "2.5")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["cantidad"], "2.50")
        self.assertEqual(self.disponible(self.arroz), Decimal("10"))

    def test_si_ya_esta_actualiza_la_cantidad(self):
        self.agregar(self.arroz, 1)
        r = self.agregar(self.arroz, 3)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CarritoItem.objects.filter(beneficiario=self.beneficiario).count(), 1)
        self.assertEqual(CarritoItem.objects.get().cantidad, Decimal("3"))

    def test_solo_alimentos_visibles(self):
        pendiente = self.crear_en_bd().alimentos.get()
        r = self.agregar(pendiente, 1)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["donacion_alimento"], ["Esta donación ya no está disponible."])
        Novedad.objects.create(donante=self.donante, donacion=self.d1, descripcion="x")
        r = self.agregar(self.arroz, 1)
        self.assertIn("novedad", str(r.data["donacion_alimento"]))

    def test_tope_por_solicitud_y_disponible(self):
        r = self.agregar(self.arroz, 4)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["cantidad"], ["Puedes pedir máximo 3 kg de este alimento."])
        DonacionAlimento.objects.filter(pk=self.arroz.pk).update(cantidad_disponible=Decimal("1.5"))
        r = self.agregar(self.arroz, 2)
        self.assertEqual(r.data["cantidad"], ["Solo quedan 1.5 kg disponibles."])

    def test_enteros_para_unidad(self):
        r = self.agregar(self.leche, "1.5")
        self.assertEqual(r.status_code, 400)
        self.assertIn("entero", str(r.data["cantidad"]))

    def test_no_agrega_de_una_donacion_con_solicitud_en_curso(self):
        self.solicitud_en_curso(self.d1, "Aprobada")
        r = self.agregar(self.arroz, 1)
        self.assertEqual(r.status_code, 400)
        self.assertIn("solicitud en curso", str(r.data))
        # Con la solicitud ya terminada sí puede volver a pedir.
        Solicitud.objects.update(estado="Entregada")
        self.assertEqual(self.agregar(self.arroz, 1).status_code, 201)

    def test_alimento_inexistente(self):
        r = self.cb.post("/api/carrito/", {"donacion_alimento": 999999, "cantidad": "1"}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_cambiar_cantidad_y_quitar(self):
        item = self.en_carrito(self.arroz, "1")
        r = self.cb.patch(f"/api/carrito/{item.pk}/", {"cantidad": "2"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["cantidad"], "2.00")
        r = self.cb.patch(f"/api/carrito/{item.pk}/", {"cantidad": "9"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.cb.delete(f"/api/carrito/{item.pk}/").status_code, 204)
        self.assertFalse(CarritoItem.objects.exists())

    def test_no_toca_carritos_ajenos(self):
        otro = crear_usuario("otro_b@k.com", Rol.BENEFICIARIO, "30")
        item = CarritoItem.objects.create(beneficiario=otro, donacion_alimento=self.arroz, cantidad=1)
        self.assertEqual(self.cb.delete(f"/api/carrito/{item.pk}/").status_code, 404)
        r = self.cb.patch(f"/api/carrito/{item.pk}/", {"cantidad": "1"}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_solo_beneficiarios(self):
        self.assertEqual(self.cliente.get("/api/carrito/").status_code, 403)
        self.assertEqual(self.agregar(self.arroz, 1, cliente=self.cliente).status_code, 403)


# =====================================================================
# Ver carrito (RN-21)
# =====================================================================
class VerCarritoTests(BaseCarritoTest):
    def ver(self):
        r = self.cb.get("/api/carrito/")
        self.assertEqual(r.status_code, 200)
        return r.data

    def items(self, datos):
        return {i["alimento"]["id"]: i for g in datos["donaciones"] for i in g["items"]}

    def test_agrupa_por_donacion_y_puede_enviar(self):
        self.en_carrito(self.arroz, "1")
        self.en_carrito(self.leche, "2")
        self.en_carrito(self.panela, "1")
        datos = self.ver()
        self.assertEqual([g["donacion"] for g in datos["donaciones"]], sorted([self.d1.pk, self.d2.pk]))
        self.assertEqual(len(datos["donaciones"][0]["items"]), 2)
        self.assertTrue(all(i["valido"] and i["motivos"] == [] for i in self.items(datos).values()))
        self.assertEqual(datos["avisos"], [])
        self.assertTrue(datos["puede_enviar"])

    def test_carrito_vacio_no_se_puede_enviar(self):
        datos = self.ver()
        self.assertEqual(datos["donaciones"], [])
        self.assertFalse(datos["puede_enviar"])

    def test_marca_items_que_dejaron_de_ser_validos(self):
        self.en_carrito(self.arroz, "1")
        self.en_carrito(self.leche, "3")
        self.en_carrito(self.panela, "1")
        DonacionAlimento.objects.filter(pk=self.arroz.pk).update(cantidad_disponible=0)
        DonacionAlimento.objects.filter(pk=self.leche.pk).update(cantidad_disponible=2)
        Novedad.objects.create(
            donante=self.donante, donacion=self.d2, donacion_alimento=self.panela, descripcion="x"
        )
        datos = self.ver()
        items = self.items(datos)
        self.assertEqual(items[self.arroz.pk]["motivos"], ["Este alimento se agotó."])
        self.assertEqual(items[self.leche.pk]["motivos"], ["Solo quedan 2 unidad disponibles."])
        self.assertIn("novedad", items[self.panela.pk]["motivos"][0])
        self.assertFalse(any(i["valido"] for i in items.values()))
        self.assertFalse(datos["puede_enviar"])

    def test_marca_items_de_donacion_con_solicitud_en_curso(self):
        self.en_carrito(self.arroz, "1")
        self.solicitud_en_curso(self.d1)
        item = self.items(self.ver())[self.arroz.pk]
        self.assertFalse(item["valido"])
        self.assertIn("solicitud en curso", item["motivos"][0])

    def test_avisa_si_supera_las_solicitudes_activas(self):
        self.en_carrito(self.arroz, "1")
        self.en_carrito(self.panela, "1")
        self.en_carrito(self.frijol, "1")
        datos = self.ver()
        self.assertEqual(len(datos["avisos"]), 1)
        self.assertIn("3 donaciones", datos["avisos"][0])
        self.assertFalse(datos["puede_enviar"])

    def test_cuenta_las_solicitudes_que_ya_tiene(self):
        self.en_carrito(self.arroz, "1")
        self.en_carrito(self.panela, "1")
        self.solicitud_en_curso(self.validada("Recoger"))
        self.assertIn("ya tienes 1", self.ver()["avisos"][0])

    def test_avisa_si_supera_alimentos_por_solicitud(self):
        Configuracion.objects.filter(pk=1).update(max_alimentos_por_solicitud=1)
        self.en_carrito(self.arroz, "1")
        self.en_carrito(self.leche, "1")
        self.assertIn("2 alimentos distintos", self.ver()["avisos"][0])


# =====================================================================
# Enviar (RN-22)
# =====================================================================
class EnviarCarritoTests(BaseCarritoTest):
    def setUp(self):
        super().setUp()
        self.admin2 = crear_admin("admin2@k.com", "2")

    def test_crea_una_solicitud_por_donacion_con_el_mismo_codigo(self):
        self.en_carrito(self.arroz, "2.5")
        self.en_carrito(self.leche, "3")
        self.en_carrito(self.panela, "1")
        r = self.enviar([{"donacion": self.d1.pk, "modalidad_entrega": "Domicilio"}])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["codigo_pedido"], f"KUM-{timezone.localdate():%Y%m%d}-0001")
        self.assertEqual(len(r.data["solicitudes"]), 2)

        s1 = Solicitud.objects.get(donacion=self.d1)
        s2 = Solicitud.objects.get(donacion=self.d2)
        self.assertEqual({s1.codigo_pedido, s2.codigo_pedido}, {r.data["codigo_pedido"]})
        self.assertEqual(
            (s1.estado, s1.modalidad_entrega, s1.direccion_entrega),
            ("Pendiente", "Domicilio", "Carrera 7 # 1-1"),
        )
        self.assertEqual((s2.modalidad_entrega, s2.direccion_entrega), ("Recoger", None))
        self.assertEqual(
            dict(s1.detalles.values_list("donacion_alimento_id", "cantidad")),
            {self.arroz.pk: Decimal("2.5"), self.leche.pk: Decimal("3")},
        )
        # Reserva inmediata y carrito vacío.
        self.assertEqual(self.disponible(self.arroz), Decimal("7.5"))
        self.assertEqual(self.disponible(self.leche), Decimal("7"))
        self.assertEqual(self.disponible(self.panela), Decimal("9"))
        self.assertFalse(CarritoItem.objects.filter(beneficiario=self.beneficiario).exists())

    def test_una_notificacion_por_pedido_a_cada_admin(self):
        self.en_carrito(self.arroz, "1")
        self.en_carrito(self.panela, "1")
        r = self.enviar([{"donacion": self.d1.pk, "modalidad_entrega": "Recoger"}])
        notificaciones = Notificacion.objects.filter(tipo="nueva_solicitud")
        self.assertEqual(
            sorted(n.usuario_id for n in notificaciones), sorted([self.admin.pk, self.admin2.pk])
        )
        self.assertIn(r.data["codigo_pedido"], notificaciones.first().mensaje)

    def test_consecutivo_del_dia(self):
        self.en_carrito(self.panela, "1")
        primero = self.enviar().data["codigo_pedido"]
        Solicitud.objects.update(estado="Entregada")
        self.en_carrito(self.panela, "1")
        segundo = self.enviar().data["codigo_pedido"]
        self.assertTrue(primero.endswith("-0001"))
        self.assertTrue(segundo.endswith("-0002"))
        self.assertRegex(segundo, r"^KUM-\d{8}-\d{4}$")

    def test_direccion_explicita_para_domicilio(self):
        self.en_carrito(self.frijol, "1")
        self.enviar([{"donacion": self.d3.pk, "direccion_entrega": "Otra dirección 123"}])
        self.assertEqual(Solicitud.objects.get().direccion_entrega, "Otra dirección 123")

    def test_domicilio_sin_direccion_en_ningun_lado(self):
        Usuario.objects.filter(pk=self.beneficiario.pk).update(direccion=None)
        self.en_carrito(self.frijol, "1")
        r = self.enviar()
        self.assertEqual(r.status_code, 400)
        self.assertIn("dirección de entrega", str(r.data["donaciones"][str(self.d3.pk)]))

    def test_ambas_exige_elegir_modalidad(self):
        self.en_carrito(self.arroz, "1")
        r = self.enviar()
        self.assertEqual(r.status_code, 400)
        self.assertIn("Elige cómo", str(r.data["donaciones"][str(self.d1.pk)]))

    def test_modalidad_distinta_a_la_permitida(self):
        self.en_carrito(self.panela, "1")
        r = self.enviar([{"donacion": self.d2.pk, "modalidad_entrega": "Domicilio"}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("solo permite Recoger", str(r.data["donaciones"][str(self.d2.pk)]))

    def test_eleccion_de_donacion_que_no_esta_en_el_carrito(self):
        self.en_carrito(self.panela, "1")
        r = self.enviar([{"donacion": self.d3.pk, "modalidad_entrega": "Domicilio"}])
        self.assertEqual(r.status_code, 400)
        self.assertIn(str(self.d3.pk), r.data["donaciones"])

    def test_carrito_vacio(self):
        r = self.enviar()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["carrito"], ["Tu carrito está vacío."])

    def test_si_algo_falla_responde_todos_los_errores_y_no_crea_nada(self):
        a = self.en_carrito(self.arroz, "1")
        self.en_carrito(self.panela, "1")
        self.en_carrito(self.frijol, "1")
        DonacionAlimento.objects.filter(pk=self.arroz.pk).update(cantidad_disponible=Decimal("0.5"))
        r = self.enviar()  # además, no elige modalidad para d1 (Ambas)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["items"][str(a.pk)], ["Solo quedan 0.5 kg disponibles."])
        self.assertIn(str(self.d1.pk), r.data["donaciones"])
        self.assertIn("el máximo es 2", r.data["carrito"][0])
        self.assertFalse(Solicitud.objects.exists())
        self.assertEqual(CarritoItem.objects.count(), 3)
        self.assertEqual(self.disponible(self.panela), Decimal("10"))

    def test_revalida_visibilidad_y_tope_al_enviar(self):
        a = self.en_carrito(self.arroz, "5")  # supera max_por_solicitud = 3
        p = self.en_carrito(self.panela, "1")
        Donacion.objects.filter(pk=self.d2.pk).update(estado="Finalizada")
        r = self.enviar([{"donacion": self.d1.pk, "modalidad_entrega": "Recoger"}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("máximo 3 kg", r.data["items"][str(a.pk)][0])
        self.assertEqual(r.data["items"][str(p.pk)], ["Esta donación ya no está disponible."])

    def test_una_solicitud_activa_por_donacion(self):
        i = self.en_carrito(self.panela, "1")
        self.solicitud_en_curso(self.d2)
        r = self.enviar()
        self.assertEqual(r.status_code, 400)
        self.assertIn("solicitud en curso", r.data["items"][str(i.pk)][0])

    def test_maximo_de_alimentos_distintos_por_solicitud(self):
        Configuracion.objects.filter(pk=1).update(max_alimentos_por_solicitud=1)
        self.en_carrito(self.arroz, "1")
        self.en_carrito(self.leche, "1")
        r = self.enviar([{"donacion": self.d1.pk, "modalidad_entrega": "Recoger"}])
        self.assertEqual(r.status_code, 400)
        self.assertIn("Máximo 1 alimentos distintos", str(r.data["donaciones"][str(self.d1.pk)]))

    def test_recalcula_la_donacion_si_se_agota(self):
        DonacionAlimento.objects.filter(pk=self.panela.pk).update(cantidad_disponible=Decimal("2"))
        self.en_carrito(self.panela, "2")
        self.assertEqual(self.enviar().status_code, 201)
        self.d2.refresh_from_db()
        self.assertEqual(self.d2.estado, "Agotada")


# =====================================================================
# Mis solicitudes (RN-23, RN-29)
# =====================================================================
class MisSolicitudesTests(BaseCarritoTest):
    def setUp(self):
        super().setUp()
        Usuario.objects.filter(pk=self.donante.pk).update(telefono="3109998877")
        self.en_carrito(self.panela, "2")
        self.enviar()
        self.solicitud = Solicitud.objects.get()

    def test_lista_solo_las_propias_con_filtro(self):
        otro = crear_usuario("otro_b@k.com", Rol.BENEFICIARIO, "30")
        ajena = self.solicitud_en_curso(self.d1, beneficiario=otro)
        self.assertEqual(self.cb.get("/api/mis-solicitudes/").data["count"], 1)
        self.assertEqual(self.cb.get("/api/mis-solicitudes/", {"estado": "Aprobada"}).data["count"], 0)
        self.assertEqual(self.cb.get(f"/api/mis-solicitudes/{ajena.pk}/").status_code, 404)

    def test_rn29_en_pendiente_solo_el_nombre_del_donante(self):
        r = self.cb.get(f"/api/mis-solicitudes/{self.solicitud.pk}/")
        self.assertEqual(
            r.data["donante"],
            {"nombre": self.donante.nombre_para_mostrar, "telefono": None, "direccion_recogida": None},
        )
        self.assertEqual(r.data["detalles"][0]["cantidad"], "2.00")

    def test_rn29_desde_aprobada_telefono_y_direccion(self):
        for estado in ("Aprobada", "Entregada", "No recogida"):
            with self.subTest(estado=estado):
                Solicitud.objects.filter(pk=self.solicitud.pk).update(estado=estado)
                donante = self.cb.get(f"/api/mis-solicitudes/{self.solicitud.pk}/").data["donante"]
                self.assertEqual(donante["telefono"], "3109998877")
                self.assertEqual(donante["direccion_recogida"], "Calle 1")

    def test_cancelar_devuelve_cantidades_y_recalcula(self):
        DonacionAlimento.objects.filter(pk=self.panela.pk).update(cantidad_disponible=0)
        Donacion.objects.filter(pk=self.d2.pk).update(estado="Agotada")
        r = self.cb.post(f"/api/mis-solicitudes/{self.solicitud.pk}/cancelar/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["estado"], "Cancelada")
        self.assertEqual(r.data["motivo_cancelacion"], "Cancelada por el beneficiario.")
        self.assertEqual(self.disponible(self.panela), Decimal("2"))
        self.d2.refresh_from_db()
        self.assertEqual(self.d2.estado, "Validada")

    def test_no_devuelve_detalles_cancelados(self):
        DetalleSolicitud.objects.filter(solicitud=self.solicitud).update(cancelado=True)
        self.cb.post(f"/api/mis-solicitudes/{self.solicitud.pk}/cancelar/")
        self.assertEqual(self.disponible(self.panela), Decimal("8"))

    def test_solo_cancela_pendientes(self):
        Solicitud.objects.filter(pk=self.solicitud.pk).update(estado="Aprobada")
        r = self.cb.post(f"/api/mis-solicitudes/{self.solicitud.pk}/cancelar/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Solo puedes cancelar solicitudes pendientes", str(r.data))
        self.assertEqual(self.disponible(self.panela), Decimal("8"))

    def test_puede_volver_a_pedir_despues_de_cancelar(self):
        self.cb.post(f"/api/mis-solicitudes/{self.solicitud.pk}/cancelar/")
        self.assertEqual(self.agregar(self.panela, 1).status_code, 201)


# =====================================================================
# Paso 7: gestión de solicitudes y entregas (RN-24 a RN-28, RN-31, RN-15)
# =====================================================================
class BaseGestionTest(BaseCarritoTest):
    def crear_solicitud(self, donacion, items, estado="Pendiente", modalidad="Recoger",
                        direccion=None, limite=None, codigo="KUM-20261012-0001"):
        """Crea la solicitud con sus detalles y descuenta las cantidades (como al enviar)."""
        solicitud = Solicitud.objects.create(
            codigo_pedido=codigo, beneficiario=self.beneficiario, donacion=donacion,
            modalidad_entrega=modalidad, direccion_entrega=direccion, estado=estado,
            fecha_limite_recogida=limite,
        )
        for alimento, cantidad in items:
            DetalleSolicitud.objects.create(
                solicitud=solicitud, donacion_alimento=alimento, cantidad=Decimal(cantidad)
            )
            DonacionAlimento.objects.filter(pk=alimento.pk).update(
                cantidad_disponible=F("cantidad_disponible") - Decimal(cantidad)
            )
        return solicitud

    def accion(self, solicitud, accion, datos=None, cliente=None):
        return (cliente or self.cliente_admin).post(
            f"/api/admin/solicitudes/{solicitud.pk}/{accion}/", datos or {}, format="json"
        )

    def vencer(self, alimento, dias=0):
        DonacionAlimento.objects.filter(pk=alimento.pk).update(
            fecha_vencimiento=timezone.localdate() + timedelta(days=dias)
        )

    def notificaciones(self, tipo, usuario):
        return Notificacion.objects.filter(tipo=tipo, usuario=usuario)


class AdminSolicitudListadoTests(BaseGestionTest):
    def test_solo_administradores(self):
        self.assertEqual(self.cb.get("/api/admin/solicitudes/").status_code, 403)
        self.assertEqual(self.cliente.get("/api/admin/solicitudes/").status_code, 403)

    def test_filtros_y_detalle(self):
        s1 = self.crear_solicitud(self.d1, [(self.arroz, "1")], codigo="KUM-20261012-0001")
        self.crear_solicitud(self.d2, [(self.panela, "1")], estado="Aprobada", codigo="KUM-20261012-0002")
        url = "/api/admin/solicitudes/"
        self.assertEqual(self.cliente_admin.get(url).data["count"], 2)
        self.assertEqual(self.cliente_admin.get(url, {"estado": "Aprobada"}).data["count"], 1)
        self.assertEqual(self.cliente_admin.get(url, {"codigo_pedido": "kum-20261012-0001"}).data["count"], 1)
        self.assertEqual(self.cliente_admin.get(url, {"beneficiario": self.beneficiario.pk}).data["count"], 2)
        self.assertEqual(self.cliente_admin.get(url, {"donacion": self.d2.pk}).data["count"], 1)
        r = self.cliente_admin.get(f"{url}{s1.pk}/")
        self.assertEqual(r.data["beneficiario"]["correo"], "bene@k.com")
        self.assertEqual(r.data["donante"]["correo"], "dona@k.com")
        self.assertIsNone(r.data["entrega"])
        self.assertEqual(len(r.data["detalles"]), 1)


class AprobarSolicitudTests(BaseGestionTest):
    def test_aprueba_con_fecha_limite_por_plazo_y_notifica(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "2")])
        r = self.accion(s, "aprobar")
        s.refresh_from_db()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(s.estado, "Aprobada")
        self.assertEqual(s.gestionada_por, self.admin)
        # Vence en 30 días: manda el plazo de 3 días.
        self.assertEqual(s.fecha_limite_recogida, s.fecha_gestion + timedelta(days=3))
        mensaje = self.notificaciones("solicitud_aprobada", self.beneficiario).get().mensaje
        self.assertIn("Recoger en Calle 1", mensaje)
        self.assertIn("Fecha límite", mensaje)
        self.assertTrue(self.notificaciones("solicitud_aprobada", self.donante).exists())

    def test_fecha_limite_por_vencimiento_al_final_del_dia(self):
        self.vencer(self.panela, 3)  # 3 - margen 1 = pasado mañana, antes que hoy + 3 días
        s = self.crear_solicitud(self.d2, [(self.panela, "1")])
        self.accion(s, "aprobar")
        s.refresh_from_db()
        esperado = fin_del_dia(timezone.localdate() + timedelta(days=2))
        self.assertEqual(s.fecha_limite_recogida, esperado)
        self.assertEqual(timezone.localtime(s.fecha_limite_recogida).strftime("%H:%M"), "23:59")

    def test_notificacion_de_domicilio(self):
        s = self.crear_solicitud(self.d3, [(self.frijol, "1")], modalidad="Domicilio", direccion="Carrera 9")
        self.accion(s, "aprobar")
        self.assertIn("Domicilio a Carrera 9", self.notificaciones("solicitud_aprobada", self.beneficiario).get().mensaje)

    def test_solo_pendientes(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")], estado="Aprobada",
                                 limite=timezone.now() + timedelta(days=1))
        r = self.accion(s, "aprobar")
        self.assertEqual(r.status_code, 400)
        self.assertIn("debe estar Pendiente", str(r.data))

    def test_rn28_pendiente_vencida_se_cancela_antes_de_aprobar(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "2")])
        self.vencer(self.panela, 0)  # vence hoy: hoy − 1 ya pasó
        r = self.accion(s, "aprobar")
        s.refresh_from_db()
        self.assertEqual(r.status_code, 400)
        self.assertIn(MOTIVO_PENDIENTE_VENCIDA, str(r.data))
        self.assertEqual((s.estado, s.motivo_cancelacion), ("Cancelada", MOTIVO_PENDIENTE_VENCIDA))
        self.assertEqual(self.disponible(self.panela), Decimal("10"))
        self.assertTrue(self.notificaciones("solicitud_cancelada", self.beneficiario).exists())
        self.assertTrue(self.notificaciones("solicitud_cancelada", self.donante).exists())
        self.d2.refresh_from_db()
        self.assertEqual(self.d2.estado, "Finalizada")  # sin disponibilidad ni solicitudes en curso

    def test_rn31_no_aprueba_con_novedad_pendiente(self):
        s = self.crear_solicitud(self.d1, [(self.arroz, "1")])
        novedad = Novedad.objects.create(donante=self.donante, donacion=self.d1, donacion_alimento=self.arroz, descripcion="x")
        r = self.accion(s, "aprobar")
        self.assertEqual(r.status_code, 400)
        self.assertIn("novedad pendiente", str(r.data))
        # Una novedad sobre toda la donación también lo impide.
        novedad.donacion_alimento = None
        novedad.save()
        self.assertEqual(self.accion(s, "aprobar").status_code, 400)

    def test_rn31_ignora_novedades_de_alimentos_cancelados_o_de_otros(self):
        s = self.crear_solicitud(self.d1, [(self.arroz, "1"), (self.leche, "1")])
        DetalleSolicitud.objects.filter(solicitud=s, donacion_alimento=self.leche).update(cancelado=True)
        Novedad.objects.create(donante=self.donante, donacion=self.d1, donacion_alimento=self.leche, descripcion="x")
        self.assertEqual(self.accion(s, "aprobar").status_code, 200)


class RechazarSolicitudTests(BaseGestionTest):
    def test_exige_motivo(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")])
        for datos in ({}, {"motivo_rechazo": " "}):
            r = self.accion(s, "rechazar", datos)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.data["motivo_rechazo"], ["Debes indicar el motivo del rechazo."])

    def test_rechaza_devuelve_cantidades_recalcula_y_notifica(self):
        DonacionAlimento.objects.filter(pk=self.panela.pk).update(cantidad_disponible=Decimal("2"))
        s = self.crear_solicitud(self.d2, [(self.panela, "2")])
        Donacion.objects.filter(pk=self.d2.pk).update(estado="Agotada")
        r = self.accion(s, "rechazar", {"motivo_rechazo": "Ya tienes muchas solicitudes"})
        s.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual((s.estado, s.motivo_rechazo, s.gestionada_por), ("Rechazada", "Ya tienes muchas solicitudes", self.admin))
        self.assertEqual(self.disponible(self.panela), Decimal("2"))
        self.d2.refresh_from_db()
        self.assertEqual(self.d2.estado, "Validada")
        self.assertIn("Ya tienes muchas", self.notificaciones("solicitud_rechazada", self.beneficiario).get().mensaje)

    def test_rn50_aplica_vencimientos_antes_de_rechazar(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")])
        self.vencer(self.panela, 0)  # RN-28: ya no se puede aprobar ni rechazar; se cancela sola
        r = self.accion(s, "rechazar", {"motivo_rechazo": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Cancelada", str(r.data))
        s.refresh_from_db()
        self.assertEqual((s.estado, s.motivo_cancelacion), ("Cancelada", MOTIVO_PENDIENTE_VENCIDA))

    def test_solo_pendientes(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")], estado="Entregada")
        self.assertEqual(self.accion(s, "rechazar", {"motivo_rechazo": "x"}).status_code, 400)


class EntregarSolicitudTests(BaseGestionTest):
    def aprobada(self, donacion=None, items=None, limite=None):
        return self.crear_solicitud(
            donacion or self.d2, items or [(self.panela, "1")], estado="Aprobada",
            limite=limite or timezone.now() + timedelta(days=2),
        )

    def test_admin_registra_la_entrega(self):
        DonacionAlimento.objects.filter(pk=self.panela.pk).update(cantidad_disponible=Decimal("1"))
        s = self.aprobada()
        r = self.accion(s, "entregar", {"observacion": "Faltó una panela"})
        s.refresh_from_db()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(s.estado, "Entregada")
        self.assertEqual(r.data["entrega"]["observacion"], "Faltó una panela")
        self.assertEqual(s.entrega.registrada_por, self.admin)
        self.assertTrue(self.notificaciones("solicitud_entregada", self.beneficiario).exists())
        # Sin disponibilidad ni solicitudes en curso: la donación queda Finalizada (RN-16).
        self.d2.refresh_from_db()
        self.assertEqual(self.d2.estado, "Finalizada")

    def test_observacion_opcional(self):
        s = self.aprobada()
        self.assertEqual(self.accion(s, "entregar").status_code, 200)
        self.assertIsNone(Entrega.objects.get(solicitud=s).observacion)

    def test_solo_aprobadas_y_una_vez(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")])
        self.assertEqual(self.accion(s, "entregar").status_code, 400)
        s2 = self.aprobada(self.d1, [(self.arroz, "1")])
        self.assertEqual(self.accion(s2, "entregar").status_code, 200)
        r = self.accion(s2, "entregar")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Entrega.objects.filter(solicitud=s2).count(), 1)

    def test_donante_dueno_registra_la_entrega(self):
        s = self.aprobada()
        r = self.cliente.post(f"/api/solicitudes/{s.pk}/entregar/", {"observacion": "ok"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["registrada_por"], "dona@k.com")
        s.refresh_from_db()
        self.assertEqual(s.estado, "Entregada")

    def test_otro_donante_o_beneficiario_no_pueden(self):
        s = self.aprobada()
        r = cliente_de(self.otro_donante).post(f"/api/solicitudes/{s.pk}/entregar/", {}, format="json")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.cb.post(f"/api/solicitudes/{s.pk}/entregar/", {}, format="json").status_code, 403)

    def test_rn31_no_entrega_con_novedad_pendiente(self):
        s = self.aprobada()
        Novedad.objects.create(donante=self.donante, donacion=self.d2, descripcion="toda")
        r = self.accion(s, "entregar")
        self.assertEqual(r.status_code, 400)
        self.assertIn("novedad pendiente", str(r.data))

    def test_rn27_vencida_pasa_a_no_recogida_antes_de_entregar(self):
        s = self.aprobada(limite=timezone.now() - timedelta(minutes=1))
        r = self.accion(s, "entregar")
        s.refresh_from_db()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(s.estado, "No recogida")
        self.assertEqual(self.disponible(self.panela), Decimal("10"))
        self.assertTrue(self.notificaciones("solicitud_no_recogida", self.beneficiario).exists())
        self.assertTrue(self.notificaciones("solicitud_no_recogida", self.donante).exists())

    def test_rn31_con_novedad_pendiente_no_se_marca_no_recogida(self):
        s = self.aprobada(limite=timezone.now() - timedelta(minutes=1))
        Novedad.objects.create(donante=self.donante, donacion=self.d2, donacion_alimento=self.panela, descripcion="x")
        self.accion(s, "entregar")
        s.refresh_from_db()
        self.assertEqual(s.estado, "Aprobada")
        self.assertEqual(self.disponible(self.panela), Decimal("9"))


class VencimientosTests(BaseGestionTest):
    """Las funciones de RN-27 y RN-28 directamente (el comando llega en el Paso 11)."""

    def test_rn27_no_cambia_si_la_fecha_limite_no_ha_pasado(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")], estado="Aprobada",
                                 limite=timezone.now() + timedelta(hours=1))
        self.assertFalse(marcar_no_recogida_si_vencida(s))
        self.assertEqual(Solicitud.objects.get(pk=s.pk).estado, "Aprobada")

    def test_rn27_solo_aprobadas(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")], limite=timezone.now() - timedelta(days=1))
        self.assertFalse(marcar_no_recogida_si_vencida(s))

    def test_rn27_no_devuelve_detalles_cancelados(self):
        s = self.crear_solicitud(self.d1, [(self.arroz, "1"), (self.leche, "2")], estado="Aprobada",
                                 limite=timezone.now() - timedelta(minutes=1))
        DetalleSolicitud.objects.filter(solicitud=s, donacion_alimento=self.leche).update(cancelado=True)
        self.assertTrue(marcar_no_recogida_si_vencida(s))
        self.assertEqual(self.disponible(self.arroz), Decimal("10"))
        self.assertEqual(self.disponible(self.leche), Decimal("8"))

    def test_rn28_solo_si_el_vencimiento_menos_margen_ya_paso(self):
        s = self.crear_solicitud(self.d2, [(self.panela, "1")])
        self.vencer(self.panela, 1)  # hoy + 1 − margen 1 = hoy: vence al final de hoy, aún no
        self.assertFalse(cancelar_si_pendiente_vencida(s))
        self.vencer(self.panela, 0)
        self.assertTrue(cancelar_si_pendiente_vencida(s))

    def test_rn28_ignora_alimentos_cancelados(self):
        s = self.crear_solicitud(self.d1, [(self.arroz, "1"), (self.leche, "1")])
        DetalleSolicitud.objects.filter(solicitud=s, donacion_alimento=self.leche).update(cancelado=True)
        self.vencer(self.leche, 0)
        self.assertFalse(cancelar_si_pendiente_vencida(s))


class SolicitudesRecibidasTests(BaseGestionTest):
    def url(self, donacion):
        return f"/api/donaciones/{donacion.pk}/solicitudes-recibidas/"

    def test_por_alimento_con_contacto_solo_desde_aprobada(self):
        Usuario.objects.filter(pk=self.beneficiario.pk).update(telefono="3201112233")
        otro = crear_usuario("otro_b@k.com", Rol.BENEFICIARIO, "30")
        pendiente = self.crear_solicitud(self.d1, [(self.arroz, "1")], codigo="KUM-20261012-0001")
        aprobada = Solicitud.objects.create(
            codigo_pedido="KUM-20261012-0002", beneficiario=otro, donacion=self.d1,
            modalidad_entrega="Domicilio", direccion_entrega="Calle 99", estado="Aprobada",
        )
        DetalleSolicitud.objects.create(solicitud=aprobada, donacion_alimento=self.arroz, cantidad=2)

        r = self.cliente.get(self.url(self.d1))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([a["alimento"] for a in r.data], [self.arroz.pk, self.leche.pk])
        arroz, leche = r.data
        self.assertEqual(leche["solicitudes"], [])
        por_solicitud = {x["solicitud"]: x for x in arroz["solicitudes"]}
        self.assertEqual(por_solicitud[pendiente.pk]["cantidad"], "1.00")
        self.assertEqual(por_solicitud[pendiente.pk]["beneficiario"],
                         {"nombre": self.beneficiario.nombre_para_mostrar, "telefono": None, "direccion": None})
        self.assertEqual(por_solicitud[aprobada.pk]["estado"], "Aprobada")
        self.assertEqual(por_solicitud[aprobada.pk]["beneficiario"]["telefono"], "3000000000")
        self.assertEqual(por_solicitud[aprobada.pk]["beneficiario"]["direccion"], "Calle 99")

    def test_recoger_muestra_la_direccion_del_perfil(self):
        self.crear_solicitud(self.d2, [(self.panela, "1")], estado="Entregada")
        beneficiario = self.cliente.get(self.url(self.d2)).data[0]["solicitudes"][0]["beneficiario"]
        self.assertEqual(beneficiario["direccion"], "Carrera 7 # 1-1")

    def test_solo_el_donante_dueno(self):
        self.assertEqual(cliente_de(self.otro_donante).get(self.url(self.d1)).status_code, 404)
        self.assertEqual(self.cb.get(self.url(self.d1)).status_code, 403)


# =====================================================================
# Paso 8: efectos de aceptar y rechazar novedades (RN-31 a RN-33)
# =====================================================================
class BaseNovedadTest(BaseGestionTest):
    def novedad(self, alimento=None, donacion=None):
        return Novedad.objects.create(
            donante=self.donante, donacion=donacion or self.d1, donacion_alimento=alimento,
            descripcion="Se dañó en la nevera",
        )

    def aprobada(self, items, gestion, limite, codigo):
        s = self.crear_solicitud(self.d1, items, estado="Aprobada", limite=limite, codigo=codigo)
        Solicitud.objects.filter(pk=s.pk).update(fecha_gestion=gestion)
        s.refresh_from_db()
        return s

    def aceptar(self, novedad, datos=None):
        return self.cliente_admin.post(f"/api/admin/novedades/{novedad.pk}/aceptar/", datos or {}, format="json")

    def rechazar(self, novedad, respuesta="Revisamos las fotos y el alimento está bien"):
        return self.cliente_admin.post(
            f"/api/admin/novedades/{novedad.pk}/rechazar/", {"respuesta_admin": respuesta}, format="json"
        )


class AceptarNovedadTests(BaseNovedadTest):
    def test_retira_el_alimento_y_ajusta_las_solicitudes(self):
        parcial = self.crear_solicitud(self.d1, [(self.arroz, "1"), (self.leche, "2")], codigo="KUM-20261012-0001")
        total = self.crear_solicitud(self.d1, [(self.arroz, "2")], codigo="KUM-20261012-0002")
        terminada = self.crear_solicitud(self.d1, [(self.arroz, "1")], estado="Entregada", codigo="KUM-20261012-0003")
        n = self.novedad(self.arroz)
        self.assertEqual(self.cliente_admin.get(f"/api/admin/novedades/{n.pk}/").data["solicitudes_afectadas"], 2)

        r = self.aceptar(n, {"respuesta_admin": "Gracias por avisar"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["estado"], "Aceptada")
        self.assertEqual(r.data["respuesta_admin"], "Gracias por avisar")

        self.arroz.refresh_from_db()
        self.assertEqual((self.arroz.activo, self.arroz.cantidad_disponible), (False, Decimal("0")))
        self.assertEqual(self.disponible(self.leche), Decimal("8"))  # no se toca

        # Parcial: solo el detalle del arroz queda cancelado; sigue Pendiente.
        parcial.refresh_from_db()
        self.assertEqual(parcial.estado, "Pendiente")
        self.assertEqual(
            dict(parcial.detalles.values_list("donacion_alimento_id", "cancelado")),
            {self.arroz.pk: True, self.leche.pk: False},
        )
        # Total: todos sus detalles cancelados -> Cancelada con motivo.
        total.refresh_from_db()
        self.assertEqual(total.estado, "Cancelada")
        self.assertIn("novedad del donante", total.motivo_cancelacion)
        # Las ya terminadas no se tocan.
        self.assertFalse(terminada.detalles.get().cancelado)

        aviso = self.notificaciones("alimento_cancelado", self.beneficiario).get()
        self.assertIn("Arroz", aviso.mensaje)
        self.assertIn("Se dañó en la nevera", aviso.mensaje)
        self.assertTrue(self.notificaciones("solicitud_cancelada", self.beneficiario).filter(solicitud=total).exists())
        self.assertTrue(self.notificaciones("novedad_aceptada", self.donante).exists())
        self.d1.refresh_from_db()
        self.assertEqual(self.d1.estado, "Validada")  # la leche sigue disponible

    def test_recalcula_la_fecha_limite_sin_los_cancelados(self):
        ahora = timezone.now()
        self.vencer(self.leche, 3)  # la leche era la que acortaba la fecha límite
        s = self.aprobada([(self.arroz, "1"), (self.leche, "1")], gestion=ahora - timedelta(days=1),
                          limite=fin_del_dia(timezone.localdate() + timedelta(days=2)), codigo="KUM-20261012-0001")
        self.aceptar(self.novedad(self.leche))
        s.refresh_from_db()
        self.assertEqual(s.estado, "Aprobada")
        # Sin la leche manda el plazo: fecha_gestion + 3 días.
        self.assertEqual(s.fecha_limite_recogida, s.fecha_gestion + timedelta(days=3))
        self.assertIn("Nueva fecha límite", self.notificaciones("alimento_cancelado", self.beneficiario).get().mensaje)

    def test_rn31_si_la_fecha_ya_paso_la_base_es_la_revision(self):
        ahora = timezone.now()
        # Aprobada hace 5 días; su límite venció mientras la novedad estaba Pendiente.
        s = self.aprobada([(self.arroz, "1"), (self.leche, "1")], gestion=ahora - timedelta(days=5),
                          limite=ahora - timedelta(days=1), codigo="KUM-20261012-0001")
        n = self.novedad(self.leche)
        self.aceptar(n)
        s.refresh_from_db()
        n.refresh_from_db()
        self.assertEqual(s.estado, "Aprobada")
        self.assertEqual(s.fecha_limite_recogida, n.fecha_revision + timedelta(days=3))

    def test_novedad_general_retira_todo_y_finaliza_la_donacion(self):
        s = self.crear_solicitud(self.d1, [(self.arroz, "1"), (self.leche, "1")])
        self.aceptar(self.novedad())
        self.assertFalse(DonacionAlimento.objects.filter(donacion=self.d1, activo=True).exists())
        s.refresh_from_db()
        self.assertEqual(s.estado, "Cancelada")
        self.d1.refresh_from_db()
        self.assertEqual(self.d1.estado, "Finalizada")  # sin disponibilidad ni solicitudes en curso

    def test_rn21_el_carrito_marca_el_alimento_retirado(self):
        self.en_carrito(self.arroz, "1")
        self.aceptar(self.novedad(self.arroz))
        item = self.cb.get("/api/carrito/").data["donaciones"][0]["items"][0]
        self.assertFalse(item["valido"])
        self.assertEqual(item["motivos"], ["Este alimento fue retirado de la donación."])

    def test_no_se_puede_reportar_otra_sobre_un_alimento_retirado(self):
        self.aceptar(self.novedad(self.arroz))
        r = self.cliente.post("/api/novedades/", {"donacion": self.d1.pk, "donacion_alimento": self.arroz.pk,
                                                  "descripcion": "x"}, format="json")
        self.assertEqual(r.status_code, 400)


class RechazarNovedadTests(BaseNovedadTest):
    def test_lo_afectado_vuelve_a_mostrarse_y_se_notifica_al_donante(self):
        self.en_carrito(self.arroz, "1")
        n = self.novedad(self.arroz)
        visibles = lambda: {a["id"] for a in self.cb.get("/api/alimentos-disponibles/").data["results"]}
        self.assertNotIn(self.arroz.pk, visibles())
        self.assertFalse(self.cb.get("/api/carrito/").data["donaciones"][0]["items"][0]["valido"])

        r = self.rechazar(n)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["estado"], "Rechazada")
        self.assertIn(self.arroz.pk, visibles())
        self.assertTrue(self.cb.get("/api/carrito/").data["donaciones"][0]["items"][0]["valido"])
        self.assertIn("Revisamos las fotos", self.notificaciones("novedad_rechazada", self.donante).get().mensaje)
        self.arroz.refresh_from_db()
        self.assertTrue(self.arroz.activo)

    def test_rn31_recalcula_la_fecha_limite_si_ya_paso(self):
        ahora = timezone.now()
        vencida = self.aprobada([(self.arroz, "1")], gestion=ahora - timedelta(days=5),
                                limite=ahora - timedelta(days=1), codigo="KUM-20261012-0001")
        vigente_limite = ahora + timedelta(days=1)
        vigente = self.aprobada([(self.leche, "1")], gestion=ahora, limite=vigente_limite, codigo="KUM-20261012-0002")
        n = self.novedad()  # sobre toda la donación: afecta a ambas
        self.rechazar(n)
        n.refresh_from_db()
        vencida.refresh_from_db()
        vigente.refresh_from_db()
        self.assertEqual(vencida.fecha_limite_recogida, n.fecha_revision + timedelta(days=3))
        self.assertEqual(vigente.fecha_limite_recogida, vigente_limite)
        # Ajuste del Paso 9: solo se avisa al beneficiario cuya fecha cambió, con la nueva fecha.
        avisos = self.notificaciones("solicitud_aprobada", self.beneficiario)
        self.assertEqual(list(avisos.values_list("solicitud_id", flat=True)), [vencida.pk])
        self.assertIn("Nueva fecha límite", avisos.get().mensaje)

    def test_no_toca_solicitudes_de_otros_alimentos(self):
        ahora = timezone.now()
        otra = self.aprobada([(self.leche, "1")], gestion=ahora - timedelta(days=5),
                             limite=ahora - timedelta(days=1), codigo="KUM-20261012-0001")
        self.rechazar(self.novedad(self.arroz))
        otra.refresh_from_db()
        self.assertEqual(otra.fecha_limite_recogida, ahora - timedelta(days=1))

    def test_despues_de_rechazarla_la_solicitud_ya_se_puede_aprobar(self):
        s = self.crear_solicitud(self.d1, [(self.arroz, "1")])
        n = self.novedad(self.arroz)
        self.assertEqual(self.accion(s, "aprobar").status_code, 400)
        self.rechazar(n)
        self.assertEqual(self.accion(s, "aprobar").status_code, 200)


# =====================================================================
# Paso 11: vencimientos automáticos (RN-50)
# =====================================================================
class BaseVencimientoTest(BaseGestionTest):
    def aprobada_vencida(self, donacion, alimento, codigo="KUM-20261012-0001"):
        return self.crear_solicitud(donacion, [(alimento, "1")], estado="Aprobada",
                                    limite=timezone.now() - timedelta(hours=1), codigo=codigo)

    def pendiente_vencida(self, donacion, alimento, codigo="KUM-20261012-0002"):
        s = self.crear_solicitud(donacion, [(alimento, "1")], codigo=codigo)
        self.vencer(alimento, 0)  # vence hoy: hoy − margen ya pasó
        return s

    def comando(self):
        salida = StringIO()
        call_command("procesar_vencimientos", stdout=salida, stderr=StringIO())
        return salida.getvalue()

    def estado(self, obj):
        obj.refresh_from_db()
        return obj.estado


class ProcesarVencimientosComandoTests(BaseVencimientoTest):
    def test_aplica_todo_e_imprime_el_resumen(self):
        no_recogida = self.aprobada_vencida(self.d1, self.arroz)
        cancelada = self.pendiente_vencida(self.d2, self.panela)
        vigente = self.crear_solicitud(self.d1, [(self.leche, "1")], estado="Aprobada",
                                       limite=timezone.now() + timedelta(days=1), codigo="KUM-20261012-0003")
        self.vencer(self.frijol, 0)  # d3 sin solicitudes y con su único alimento vencido

        salida = self.comando()
        self.assertEqual(self.estado(no_recogida), "No recogida")
        self.assertEqual(self.estado(cancelada), "Cancelada")
        self.assertEqual(self.estado(vigente), "Aprobada")
        self.assertEqual(self.estado(self.d3), "Finalizada")
        self.assertEqual(self.estado(self.d1), "Validada")
        self.assertIn("Aprobada -> No recogida (RN-27): 1", salida)
        self.assertIn("Pendiente -> Cancelada (RN-28): 1", salida)
        self.assertIn("Errores: 0", salida)
        self.assertTrue(self.notificaciones("solicitud_no_recogida", self.beneficiario).exists())

    def test_se_puede_ejecutar_varias_veces_sin_efectos_dobles(self):
        self.aprobada_vencida(self.d1, self.arroz)
        self.comando()
        disponible = self.disponible(self.arroz)
        avisos = Notificacion.objects.count()
        salida = self.comando()
        self.assertIn("No recogida (RN-27): 0", salida)
        self.assertIn("Finalizada (RN-16): 0", salida)
        self.assertEqual(self.disponible(self.arroz), disponible)
        self.assertEqual(Notificacion.objects.count(), avisos)

    def test_rn31_con_novedad_pendiente_no_se_marca_no_recogida(self):
        s = self.aprobada_vencida(self.d1, self.arroz)
        Novedad.objects.create(donante=self.donante, donacion=self.d1, descripcion="x")
        self.comando()
        self.assertEqual(self.estado(s), "Aprobada")

    def test_un_error_en_un_registro_no_detiene_los_demas(self):
        primera = self.aprobada_vencida(self.d1, self.arroz)
        segunda = self.aprobada_vencida(self.d2, self.panela, codigo="KUM-20261012-0002")
        original = servicios.aplicar_vencimientos

        def falla_en_la_primera(solicitud_id):
            if solicitud_id == primera.pk:
                raise RuntimeError("falla simulada")
            return original(solicitud_id)

        with mock.patch("apps.solicitudes.services.aplicar_vencimientos", side_effect=falla_en_la_primera):
            with self.assertLogs("apps.solicitudes.services", level="ERROR"):
                salida = self.comando()
        self.assertEqual(self.estado(primera), "Aprobada")
        self.assertEqual(self.estado(segunda), "No recogida")
        self.assertIn("Errores: 1", salida)

    def test_rn16_agotada_sin_solicitudes_en_curso_pasa_a_finalizada(self):
        DonacionAlimento.objects.filter(donacion=self.d2).update(cantidad_disponible=0)
        Donacion.objects.filter(pk=self.d2.pk).update(estado="Agotada")
        salida = self.comando()
        self.assertEqual(self.estado(self.d2), "Finalizada")
        self.assertIn("Donaciones -> Finalizada (RN-16): 1", salida)


class VencimientosConsultaEficienteTests(BaseVencimientoTest):
    def test_solo_encuentra_las_realmente_vencidas(self):
        vencida = self.aprobada_vencida(self.d1, self.arroz)
        pendiente_vencida = self.pendiente_vencida(self.d2, self.panela)
        self.crear_solicitud(self.d3, [(self.frijol, "1")], codigo="KUM-20261012-0003")  # vigente
        self.crear_solicitud(self.d1, [(self.leche, "1")], estado="Entregada", codigo="KUM-20261012-0004")
        # Un detalle cancelado no cuenta (RN-28 solo mira los no cancelados).
        self.vencer(self.leche, 0)
        con_cancelado = self.crear_solicitud(self.d1, [(self.leche, "1")], codigo="KUM-20261012-0005")
        con_cancelado.detalles.update(cancelado=True)
        self.assertEqual(
            servicios.ids_solicitudes_vencidas(Solicitud.objects.all()),
            sorted([vencida.pk, pendiente_vencida.pk]),
        )

    def test_sin_vencidas_no_procesa_nada(self):
        self.crear_solicitud(self.d1, [(self.arroz, "1")])
        # Una consulta para la configuración y otra para buscar las vencidas.
        with self.assertNumQueries(2):
            self.assertEqual(servicios.procesar_solicitudes_vencidas(Solicitud.objects.all())["no_recogidas"], 0)
        with self.assertNumQueries(2):
            procesar_donaciones_desactualizadas(Donacion.objects.all())


class VencimientosAlListarTests(BaseVencimientoTest):
    def test_mis_solicitudes(self):
        s = self.aprobada_vencida(self.d1, self.arroz)
        r = self.cb.get("/api/mis-solicitudes/")
        self.assertEqual(r.data["results"][0]["estado"], "No recogida")
        self.assertEqual(self.estado(s), "No recogida")

    def test_mis_solicitudes_no_toca_las_de_otros(self):
        otro = crear_usuario("otro_b@k.com", Rol.BENEFICIARIO, "30")
        s = self.aprobada_vencida(self.d1, self.arroz)
        Solicitud.objects.filter(pk=s.pk).update(beneficiario=otro)
        self.cb.get("/api/mis-solicitudes/")
        self.assertEqual(self.estado(s), "Aprobada")

    def test_admin_solicitudes_con_filtro_de_estado(self):
        s = self.aprobada_vencida(self.d1, self.arroz)
        r = self.cliente_admin.get("/api/admin/solicitudes/", {"estado": "Aprobada"})
        self.assertEqual(r.data["count"], 0)  # ya pasó a No recogida antes de filtrar
        r = self.cliente_admin.get("/api/admin/solicitudes/", {"estado": "No recogida"})
        self.assertEqual([x["id"] for x in r.data["results"]], [s.pk])

    def test_donaciones_del_donante_y_admin(self):
        self.vencer(self.frijol, 0)
        r = self.cliente.get("/api/donaciones/", {"estado": "Finalizada"})
        self.assertEqual([d["id"] for d in r.data["results"]], [self.d3.pk])
        self.vencer(self.panela, 0)
        r = self.cliente_admin.get("/api/admin/donaciones/", {"estado": "Finalizada"})
        self.assertEqual(r.data["count"], 2)

    def test_solicitudes_recibidas(self):
        s = self.pendiente_vencida(self.d2, self.panela)
        r = self.cliente.get(f"/api/donaciones/{self.d2.pk}/solicitudes-recibidas/")
        self.assertEqual(r.data[0]["solicitudes"][0]["estado"], "Cancelada")
        self.assertEqual(self.estado(s), "Cancelada")
        self.assertEqual(self.estado(self.d2), "Finalizada")

    def test_enviar_carrito_libera_el_cupo_de_una_aprobada_vencida(self):
        # max_solicitudes_activas = 2: una Aprobada vencida ya no debe ocupar cupo.
        self.aprobada_vencida(self.d1, self.arroz)
        self.crear_solicitud(self.d3, [(self.frijol, "1")], codigo="KUM-20261012-0002")
        self.en_carrito(self.panela, "1")
        r = self.enviar()
        self.assertEqual(r.status_code, 201, r.data)


# =====================================================================
# Paso 13: datos de demostración (cargar_demo)
# =====================================================================
@override_settings(
    DEBUG=True,
    STORAGES=ALMACENAMIENTO_FALSO,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class CargarDemoTests(TestCase):
    def setUp(self):
        self.admin = crear_admin()
        self.real = crear_usuario("real@k.com", Rol.BENEFICIARIO, "55")

    def comando(self, *args):
        salida = StringIO()
        call_command("cargar_demo", *args, stdout=salida)
        return salida.getvalue()

    def demo(self):
        return Usuario.objects.filter(correo__endswith="@demo.kumora")

    def test_solo_con_debug(self):
        with override_settings(DEBUG=False):
            with self.assertRaisesMessage(CommandError, "DEBUG = True"):
                call_command("cargar_demo", stdout=StringIO())
        self.assertFalse(self.demo().exists())

    def test_carga_todos_los_estados_sin_duplicar_y_se_puede_borrar(self):
        salida = self.comando()
        self.assertIn("Demo.Kumora2026", salida)
        self.assertEqual(self.demo().count(), 5)
        self.assertEqual(
            set(self.demo().values_list("tipo_donante", flat=True)), {"Persona", "Negocio", None}
        )
        donaciones = Donacion.objects.filter(donante__in=self.demo())
        self.assertEqual(set(donaciones.values_list("estado", flat=True)),
                         {"Pendiente", "Validada", "Agotada", "Finalizada", "Rechazada"})
        solicitudes = Solicitud.objects.filter(beneficiario__in=self.demo())
        self.assertEqual(set(solicitudes.values_list("estado", flat=True)),
                         {"Pendiente", "Aprobada", "Entregada", "No recogida", "Cancelada", "Rechazada"})
        self.assertTrue(Novedad.objects.filter(donante__in=self.demo(), estado="Pendiente").exists())
        self.assertTrue(Sancion.objects.filter(usuario__in=self.demo()).exists())
        # Fechas repartidas en varios meses para el dashboard.
        meses = {timezone.localtime(f).strftime("%Y-%m")
                 for f in donaciones.values_list("fecha_registro", flat=True)}
        self.assertGreaterEqual(len(meses), 3)
        # Una foto por alimento.
        self.assertTrue(all(DonacionAlimento.objects.filter(donacion__in=donaciones).values_list("foto", flat=True)))
        # Datos coherentes: el comando de vencimientos no encuentra nada que cambiar.
        vencimientos = StringIO()
        call_command("procesar_vencimientos", stdout=vencimientos, stderr=StringIO())
        self.assertIn("No recogida (RN-27): 0", vencimientos.getvalue())
        self.assertIn("Finalizada (RN-16): 0", vencimientos.getvalue())

        # Correrlo otra vez no duplica nada.
        conteo = (Usuario.objects.count(), Donacion.objects.count(), Solicitud.objects.count())
        self.assertIn("ya están cargados", self.comando())
        self.assertEqual((Usuario.objects.count(), Donacion.objects.count(), Solicitud.objects.count()), conteo)

        # --borrar quita todo lo demo (y sus fotos) sin tocar lo real.
        fotos = list(DonacionAlimento.objects.values_list("foto", flat=True))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertIn("eliminados", self.comando("--borrar"))
        self.assertFalse(self.demo().exists())
        self.assertFalse(Donacion.objects.exists())
        self.assertFalse(Solicitud.objects.exists())
        self.assertFalse(Notificacion.objects.exists())  # tampoco las de los administradores sobre lo demo
        self.assertTrue(all(not default_storage.exists(f) for f in fotos))
        self.assertTrue(Usuario.objects.filter(pk__in=[self.admin.pk, self.real.pk]).count() == 2)

        # Y se puede volver a cargar.
        self.comando()
        self.assertEqual(self.demo().count(), 5)

    def test_sin_administrador_no_carga(self):
        Usuario.objects.filter(pk=self.admin.pk).update(estado="Inactivo")
        with self.assertRaisesMessage(CommandError, "createsuperuser"):
            call_command("cargar_demo", stdout=StringIO())
