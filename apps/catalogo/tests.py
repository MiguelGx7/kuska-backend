"""Pruebas del Paso 4: categorías (RN-17) y configuración."""

from datetime import date, timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.donaciones.models import Donacion, DonacionAlimento
from apps.usuarios.models import Rol
from apps.usuarios.tests import cliente_de, crear_admin, crear_usuario

from .models import Categoria, Configuracion


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BaseCatalogoTest(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = crear_admin()
        self.donante = crear_usuario("dona@k.com", Rol.DONANTE, "10", tipo_donante="Persona")
        self.cliente = cliente_de(self.admin)


class AdminCategoriaTests(BaseCatalogoTest):
    url = "/api/admin/categorias/"

    def crear(self, nombre="Congelados"):
        return self.cliente.post(self.url, {"nombre": nombre, "descripcion": "Helados"}, format="json")

    def test_solo_administradores(self):
        self.assertEqual(cliente_de(self.donante).get(self.url).status_code, 403)

    def test_lista_las_categorias_iniciales(self):
        r = self.cliente.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 8)

    def test_crear_quita_espacios_y_queda_activa(self):
        r = self.crear("  Congelados ")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["nombre"], "Congelados")
        self.assertTrue(r.data["activa"])

    def test_nombre_unico_sin_importar_mayusculas(self):
        r = self.crear("granos")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data, {"nombre": ["Ya existe una categoría con este nombre."]})

    def test_editar_a_un_nombre_repetido(self):
        cid = self.crear().data["id"]
        r = self.cliente.patch(f"{self.url}{cid}/", {"nombre": "Lácteos"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("nombre", r.data)

    def test_inactivar_conservando_su_nombre(self):
        cid = self.crear().data["id"]
        r = self.cliente.patch(f"{self.url}{cid}/", {"nombre": "Congelados", "activa": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["activa"])

    def test_filtro_y_busqueda(self):
        Categoria.objects.filter(nombre="Otros").update(activa=False)
        self.assertEqual(self.cliente.get(self.url, {"activa": "false"}).data["count"], 1)
        self.assertEqual(self.cliente.get(self.url, {"search": "gran"}).data["count"], 1)

    def test_borrar_categoria_sin_alimentos(self):
        cid = self.crear().data["id"]
        self.assertEqual(self.cliente.delete(f"{self.url}{cid}/").status_code, 204)
        self.assertFalse(Categoria.objects.filter(pk=cid).exists())

    def test_no_borra_categoria_en_uso(self):
        granos = Categoria.objects.get(nombre="Granos")
        donacion = Donacion.objects.create(donante=self.donante, titulo="x", modalidad_entrega="Domicilio")
        DonacionAlimento.objects.create(
            donacion=donacion, categoria=granos, nombre="Arroz", unidad_medida="kg",
            cantidad_total=1, cantidad_disponible=1,
            fecha_vencimiento=date.today() + timedelta(days=10), foto="kumora/alimentos/x.jpg",
        )
        r = self.cliente.delete(f"{self.url}{granos.pk}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Puedes inactivarla", str(r.data))
        self.assertTrue(Categoria.objects.filter(pk=granos.pk).exists())


class CategoriaPublicaTests(BaseCatalogoTest):
    url = "/api/categorias/"

    def test_exige_login(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_solo_activas_y_sin_paginar(self):
        Categoria.objects.filter(nombre="Otros").update(activa=False)
        r = cliente_de(self.donante).get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.data, list)
        self.assertEqual(len(r.data), 7)
        self.assertNotIn("Otros", [c["nombre"] for c in r.data])
        self.assertNotIn("activa", r.data[0])


class ConfiguracionTests(BaseCatalogoTest):
    url = "/api/admin/configuracion/"

    def patch(self, datos):
        return self.cliente.patch(self.url, datos, format="json")

    def test_solo_administradores(self):
        self.assertEqual(cliente_de(self.donante).get(self.url).status_code, 403)

    def test_ver_valores_iniciales(self):
        r = self.cliente.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["plazo_recogida_dias"], 3)
        self.assertEqual(r.data["margen_vencimiento_dias"], 1)
        self.assertEqual(r.data["max_alimentos_por_solicitud"], 10)
        self.assertEqual(r.data["porcentaje_max_por_alimento"], "30.00")
        self.assertEqual(r.data["max_solicitudes_activas"], 2)
        self.assertIsNone(r.data["actualizada_por"])

    def test_editar_guarda_quien_la_actualizo(self):
        r = self.patch({"plazo_recogida_dias": 5, "porcentaje_max_por_alimento": "25.5"})
        config = Configuracion.objects.get(pk=1)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(config.plazo_recogida_dias, 5)
        self.assertEqual(str(config.porcentaje_max_por_alimento), "25.50")
        self.assertEqual(config.actualizada_por, self.admin)
        self.assertEqual(r.data["actualizada_por"], "admin@k.com")

    def test_valida_todos_los_limites_y_no_guarda_nada(self):
        r = self.patch({
            "plazo_recogida_dias": 0,
            "margen_vencimiento_dias": -1,
            "max_alimentos_por_solicitud": 0,
            "max_solicitudes_activas": 0,
            "porcentaje_max_por_alimento": "100.01",
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(
            set(r.data),
            {"plazo_recogida_dias", "margen_vencimiento_dias", "max_alimentos_por_solicitud",
             "max_solicitudes_activas", "porcentaje_max_por_alimento"},
        )
        self.assertEqual(Configuracion.objects.get(pk=1).plazo_recogida_dias, 3)

    def test_porcentaje_cero_no_es_valido(self):
        self.assertEqual(self.patch({"porcentaje_max_por_alimento": "0"}).status_code, 400)

    def test_bordes_validos(self):
        r = self.patch({"porcentaje_max_por_alimento": "100", "margen_vencimiento_dias": 0})
        self.assertEqual(r.status_code, 200)

    def test_id_no_editable(self):
        r = self.patch({"id": 2})
        self.assertEqual(r.status_code, 400)
        self.assertIn("id", r.data)

    def test_put_no_permitido(self):
        self.assertEqual(self.cliente.put(self.url, {}, format="json").status_code, 405)
