"""Pruebas del Paso 12: dashboard (RN-60, RN-61)."""

from datetime import datetime, timedelta, timezone as dt_timezone

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.donaciones.models import Donacion, DonacionAlimento
from apps.solicitudes.models import Entrega, Solicitud
from apps.solicitudes.tests import BaseGestionTest
from apps.usuarios.models import Rol
from apps.usuarios.tests import cliente_de, crear_usuario

from . import services


def mes(desplazamiento=0):
    """'AAAA-MM' del mes actual (Bogotá) más `desplazamiento` meses."""
    hoy = timezone.localdate()
    i = hoy.year * 12 + hoy.month - 1 + desplazamiento
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


class BaseDashboardTest(BaseGestionTest):
    def setUp(self):
        super().setUp()
        DonacionAlimento.objects.filter(pk=self.leche.pk).update(categoria=self.lacteos)
        self.otro_bene = crear_usuario("bene2@k.com", Rol.BENEFICIARIO, "21")

    def entregada(self, donacion, items, beneficiario=None, codigo="KUM-20261012-0001"):
        s = self.crear_solicitud(donacion, items, estado="Entregada", codigo=codigo)
        if beneficiario:
            Solicitud.objects.filter(pk=s.pk).update(beneficiario=beneficiario)
        Entrega.objects.create(solicitud=s, registrada_por=self.admin)
        return s

    def ver_admin(self):
        r = self.cliente_admin.get("/api/dashboard/admin/")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def ver_donante(self, cliente=None):
        r = (cliente or self.cliente).get("/api/dashboard/donante/")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    @staticmethod
    def del_mes(datos, clave):
        return {m["mes"]: m for m in datos["por_mes"]}[clave]


class PermisosDashboardTests(BaseDashboardTest):
    def test_cada_dashboard_para_su_rol(self):
        self.assertEqual(self.cb.get("/api/dashboard/admin/").status_code, 403)
        self.assertEqual(self.cb.get("/api/dashboard/donante/").status_code, 403)
        self.assertEqual(self.cliente.get("/api/dashboard/admin/").status_code, 403)
        self.assertEqual(self.cliente_admin.get("/api/dashboard/donante/").status_code, 403)


class DashboardAdminTests(BaseDashboardTest):
    def test_totales_rn60(self):
        self.crear_en_bd()  # Pendiente: no cuenta
        self.crear_en_bd(estado="Rechazada")  # no cuenta
        Donacion.objects.filter(pk=self.d3.pk).update(estado="Finalizada")  # sí cuenta
        self.entregada(self.d1, [(self.arroz, "1")], codigo="KUM-20261012-0001")
        self.entregada(self.d2, [(self.panela, "1")], codigo="KUM-20261012-0002")  # mismo beneficiario
        self.entregada(self.d1, [(self.leche, "1")], beneficiario=self.otro_bene, codigo="KUM-20261012-0003")
        self.crear_solicitud(self.d3, [(self.frijol, "1")], codigo="KUM-20261012-0004")  # Pendiente
        rechazada = self.crear_solicitud(self.d2, [(self.panela, "1")], codigo="KUM-20261012-0005")
        Solicitud.objects.filter(pk=rechazada.pk).update(estado="Rechazada", motivo_rechazo="x")

        datos = self.ver_admin()
        self.assertEqual(datos["donaciones_recibidas"], 3)
        self.assertEqual(datos["solicitudes_entregadas"], 3)
        self.assertEqual(datos["solicitudes_pendientes"], 1)
        self.assertEqual(datos["personas_beneficiadas"], 2)  # beneficiarios distintos

    def test_serie_de_12_meses_con_ceros(self):
        datos = self.ver_admin()
        self.assertEqual([m["mes"] for m in datos["por_mes"]], [mes(i) for i in range(-11, 1)])
        actual = self.del_mes(datos, mes(0))
        self.assertEqual((actual["donaciones"], actual["alimentos_donados"]), (3, 4))
        self.assertEqual(self.del_mes(datos, mes(-5)),
                         {"mes": mes(-5), "donaciones": 0, "solicitudes": 0, "entregas": 0,
                          "alimentos_donados": 0, "alimentos_entregados": 0})

    def test_los_meses_se_agrupan_en_hora_de_bogota(self):
        hoy = timezone.localdate()
        # 1.º del mes a las 03:00 UTC = último día del mes anterior a las 22:00 en Bogotá.
        borde = datetime(hoy.year, hoy.month, 1, 3, 0, tzinfo=dt_timezone.utc)
        Donacion.objects.filter(pk=self.d3.pk).update(fecha_registro=borde)
        hace_13 = timezone.now() - timedelta(days=400)  # fuera de la serie
        Donacion.objects.filter(pk=self.d2.pk).update(fecha_registro=hace_13)

        datos = self.ver_admin()
        self.assertEqual(self.del_mes(datos, mes(-1))["donaciones"], 1)
        self.assertEqual(self.del_mes(datos, mes(0))["donaciones"], 1)
        self.assertEqual(sum(m["donaciones"] for m in datos["por_mes"]), 2)
        self.assertEqual(datos["donaciones_recibidas"], 3)  # el total sí es histórico

    def test_cuenta_alimentos_entregados_por_mes_y_categoria(self):
        s = self.entregada(self.d1, [(self.arroz, "2.5"), (self.leche, "2")])
        s.detalles.filter(donacion_alimento=self.leche).update(cancelado=True)  # no se entregó
        self.entregada(self.d2, [(self.panela, "1")], beneficiario=self.otro_bene, codigo="KUM-20261012-0002")

        datos = self.ver_admin()
        actual = self.del_mes(datos, mes(0))
        self.assertEqual((actual["solicitudes"], actual["entregas"], actual["alimentos_entregados"]), (2, 2, 2))
        self.assertEqual(
            datos["por_categoria"],
            [
                {"categoria_id": self.granos.pk, "categoria": "Granos",
                 "alimentos_donados": 3, "alimentos_entregados": 2},
                {"categoria_id": self.lacteos.pk, "categoria": "Lácteos",
                 "alimentos_donados": 1, "alimentos_entregados": 0},
            ],
        )

    def test_aplica_los_vencimientos_antes_de_calcular(self):
        vencida = self.crear_solicitud(self.d1, [(self.arroz, "1")], estado="Aprobada",
                                       limite=timezone.now() - timedelta(hours=1))
        self.vencer(self.frijol, 0)
        self.ver_admin()
        vencida.refresh_from_db()
        self.d3.refresh_from_db()
        self.assertEqual(vencida.estado, "No recogida")
        self.assertEqual(self.d3.estado, "Finalizada")

    def test_pocas_consultas_que_no_crecen_con_los_datos(self):
        def consultas():
            with CaptureQueriesContext(connection) as ctx:
                services.dashboard_admin()
            return len(ctx)

        antes = consultas()
        for i in range(3):
            self.entregada(self.d1, [(self.arroz, "1"), (self.leche, "1")], codigo=f"KUM-20261012-000{i}")
        self.assertEqual(consultas(), antes)
        self.assertLessEqual(antes, 15)


class DashboardDonanteTests(BaseDashboardTest):
    def test_solo_sus_datos_rn61(self):
        ajena = self.crear_en_bd(donante=self.otro_donante, estado="Validada")
        self.entregada(ajena, [(ajena.alimentos.get(), "1")], codigo="KUM-20261012-0009")
        self.crear_en_bd()  # su donación Pendiente
        self.crear_en_bd(estado="Rechazada")
        self.entregada(self.d1, [(self.arroz, "2.5"), (self.leche, "2")], codigo="KUM-20261012-0001")
        self.entregada(self.d2, [(self.panela, "1")], beneficiario=self.otro_bene, codigo="KUM-20261012-0002")
        cancelada = self.entregada(self.d3, [(self.frijol, "1")], codigo="KUM-20261012-0003")
        cancelada.detalles.update(cancelado=True)  # entrega sin alimentos vigentes: no suma alimentos

        datos = self.ver_donante()
        self.assertEqual(datos["donaciones_por_estado"],
                         {"Pendiente": 1, "Validada": 3, "Rechazada": 1, "Agotada": 0, "Finalizada": 0})
        self.assertEqual(datos["entregas"], 3)
        self.assertEqual(datos["alimentos_entregados"], 3)
        self.assertEqual(datos["personas_ayudadas"], 2)
        # Por unidad: los 2.5 kg de arroz y el 1 kg de panela se suman; la leche (unidad) va aparte.
        self.assertEqual(datos["cantidades_entregadas"], [
            {"unidad_medida": "kg", "alimentos": 2, "cantidad": "3.50"},
            {"unidad_medida": "unidad", "alimentos": 1, "cantidad": "2.00"},
        ])
        actual = self.del_mes(datos, mes(0))
        self.assertEqual(actual, {"mes": mes(0), "donaciones": 3, "entregas": 3,
                                  "alimentos_donados": 4, "alimentos_entregados": 3})
        self.assertEqual([c["categoria"] for c in datos["por_categoria"]], ["Granos", "Lácteos"])
        self.assertEqual(len(datos["por_mes"]), 12)

    def test_donante_sin_datos(self):
        nuevo = crear_usuario("nuevo@k.com", Rol.DONANTE, "40", tipo_donante="Persona")
        datos = self.ver_donante(cliente_de(nuevo))
        self.assertEqual(set(datos["donaciones_por_estado"].values()), {0})
        self.assertEqual((datos["entregas"], datos["alimentos_entregados"], datos["personas_ayudadas"]), (0, 0, 0))
        self.assertEqual((datos["cantidades_entregadas"], datos["por_categoria"]), ([], []))
        self.assertTrue(all(m["donaciones"] == 0 for m in datos["por_mes"]))

    def test_aplica_solo_sus_vencimientos(self):
        ajena = self.crear_en_bd(donante=self.otro_donante, estado="Validada")
        ajena_vencida = self.crear_solicitud(ajena, [(ajena.alimentos.get(), "1")], estado="Aprobada",
                                             limite=timezone.now() - timedelta(hours=1), codigo="KUM-20261012-0009")
        propia = self.crear_solicitud(self.d1, [(self.arroz, "1")], estado="Aprobada",
                                      limite=timezone.now() - timedelta(hours=1))
        self.ver_donante()
        propia.refresh_from_db()
        ajena_vencida.refresh_from_db()
        self.assertEqual((propia.estado, ajena_vencida.estado), ("No recogida", "Aprobada"))
