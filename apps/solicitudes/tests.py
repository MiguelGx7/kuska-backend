"""Pruebas del Paso 6: carrito y envío de solicitudes (RN-20 a RN-23, RN-29)."""

from decimal import Decimal

from django.utils import timezone

from apps.catalogo.models import Configuracion
from apps.donaciones.models import Donacion, DonacionAlimento, Novedad
from apps.donaciones.tests import BaseDonacionTest
from apps.notificaciones.models import Notificacion
from apps.usuarios.models import Rol, Usuario
from apps.usuarios.tests import cliente_de, crear_admin, crear_usuario

from .models import CarritoItem, DetalleSolicitud, Solicitud


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
