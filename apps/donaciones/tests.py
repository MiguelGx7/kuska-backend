"""
Pruebas del Paso 5: donaciones (RN-10 a RN-14, RN-11a, RN-17).

Las fotos van a un almacenamiento en memoria (InMemoryStorage): no se
sube nada a Cloudinary.
"""

import io
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from apps.catalogo.models import Categoria, Configuracion
from apps.notificaciones.models import Notificacion
from apps.usuarios.models import Rol, Usuario
from apps.usuarios.tests import cliente_de, crear_admin, crear_usuario

from .models import Donacion, DonacionAlimento, Novedad
from .services import calcular_max_por_solicitud

ALMACENAMIENTO_FALSO = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def imagen(nombre="foto.jpg", formato="JPEG", tamano=(10, 10)):
    contenido = io.BytesIO()
    Image.new("RGB", tamano, "orange").save(contenido, formato)
    return SimpleUploadedFile(nombre, contenido.getvalue(), content_type=f"image/{formato.lower()}")


def en_dias(dias):
    return (timezone.localdate() + timedelta(days=dias)).isoformat()


@override_settings(
    STORAGES=ALMACENAMIENTO_FALSO,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class BaseDonacionTest(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = crear_admin()
        self.donante = crear_usuario("dona@k.com", Rol.DONANTE, "10", tipo_donante="Persona")
        self.otro_donante = crear_usuario("otro@k.com", Rol.DONANTE, "12", tipo_donante="Persona")
        self.beneficiario = crear_usuario("bene@k.com", Rol.BENEFICIARIO, "11")
        self.cliente = cliente_de(self.donante)
        self.cliente_admin = cliente_de(self.admin)
        self.granos = Categoria.objects.get(nombre="Granos")
        self.lacteos = Categoria.objects.get(nombre="Lácteos")

    def alimento(self, **cambios):
        datos = {
            "categoria": self.granos.pk, "nombre": "Arroz", "unidad_medida": "kg",
            "cantidad_total": "5", "fecha_vencimiento": en_dias(30),
        }
        return {**datos, **cambios}

    def datos(self, alimentos=None, **cambios):
        datos = {
            "titulo": "Mercado", "direccion_recogida": "Calle 1", "modalidad_entrega": "Ambas",
            "alimentos": alimentos if alimentos is not None else [self.alimento()],
        }
        return {**datos, **cambios}

    def crear(self, datos=None, fotos=None, cliente=None):
        datos = datos or self.datos()
        if fotos is None:
            fotos = {f"foto_{i}": imagen() for i in range(len(datos["alimentos"]))}
        return (cliente or self.cliente).post(
            "/api/donaciones/", {"datos": json.dumps(datos), **fotos}, format="multipart"
        )

    def crear_en_bd(self, donante=None, estado="Pendiente", modalidad="Ambas", alimentos=1, **alimento):
        donacion = Donacion.objects.create(
            donante=donante or self.donante, titulo="Mercado", direccion_recogida="Calle 1",
            modalidad_entrega=modalidad, estado=estado,
            motivo_rechazo="x" if estado == "Rechazada" else None,
        )
        for i in range(alimentos):
            datos = {
                "categoria": self.granos, "nombre": f"Arroz {i}", "unidad_medida": "kg",
                "cantidad_total": Decimal("5"), "cantidad_disponible": Decimal("5"),
                "fecha_vencimiento": timezone.localdate() + timedelta(days=30),
                "max_por_solicitud": Decimal("1.5") if estado != "Pendiente" else None,
                **alimento,
            }
            a = DonacionAlimento(donacion=donacion, **datos)
            a.foto.save(f"prueba_{donacion.pk}_{i}.jpg", imagen(), save=False)
            a.save()
        return donacion

    def fotos_guardadas(self):
        # El almacenamiento en memoria se comparte entre pruebas de la clase.
        try:
            return sorted(default_storage.listdir("kumora/alimentos")[1])
        except FileNotFoundError:
            return []


# =====================================================================
# Donante: crear
# =====================================================================
class CrearDonacionTests(BaseDonacionTest):
    def test_crea_pendiente_con_disponible_igual_al_total_y_fotos(self):
        alimentos = [self.alimento(), self.alimento(nombre="Leche", categoria=self.lacteos.pk,
                                                    unidad_medida="unidad", cantidad_total="12")]
        r = self.crear(self.datos(alimentos), {"foto_0": imagen(), "foto_1": imagen("b.png", "PNG")})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["estado"], "Pendiente")
        self.assertEqual(len(r.data["alimentos"]), 2)
        for a in r.data["alimentos"]:
            self.assertEqual(a["cantidad_disponible"], a["cantidad_total"])
            self.assertIsNone(a["max_por_solicitud"])
            self.assertIn("kumora/alimentos/", a["foto_url"])
        nombres = [a["foto_url"].rsplit("/", 1)[1] for a in r.data["alimentos"]]
        self.assertTrue(all(n in self.fotos_guardadas() for n in nombres))
        self.assertTrue(nombres[1].endswith(".png"))

    def test_acepta_mas_alimentos_que_los_campos_de_swagger(self):
        # Swagger documenta foto_0 a foto_3, pero el backend lee cualquier foto_N.
        alimentos = [self.alimento(nombre=f"Alimento {i}") for i in range(6)]
        r = self.crear(self.datos(alimentos))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(r.data["alimentos"]), 6)
        self.assertTrue(all(a["foto_url"] for a in r.data["alimentos"]))

    def test_falta_una_foto_intermedia(self):
        alimentos = [self.alimento(nombre=f"Alimento {i}") for i in range(6)]
        fotos = {f"foto_{i}": imagen() for i in range(6) if i != 4}
        r = self.crear(self.datos(alimentos), fotos)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["foto_4"], ["La foto del alimento es obligatoria."])

    def test_notifica_a_los_administradores(self):
        otro_admin = crear_admin("admin2@k.com", "2")
        r = self.crear()
        notificaciones = Notificacion.objects.filter(tipo="nueva_donacion", donacion_id=r.data["id"])
        self.assertEqual({n.usuario_id for n in notificaciones}, {self.admin.pk, otro_admin.pk})

    def test_exige_al_menos_un_alimento(self):
        r = self.crear(self.datos(alimentos=[]), fotos={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("al menos un alimento", str(r.data["alimentos"]))

    def test_datos_debe_ser_json_valido(self):
        r = self.cliente.post("/api/donaciones/", {"datos": "{no es json", "foto_0": imagen()}, format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("datos", r.data)

    def test_foto_obligatoria(self):
        r = self.crear(fotos={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["foto_0"], ["La foto del alimento es obligatoria."])

    def test_foto_debe_ser_imagen_real(self):
        falso = SimpleUploadedFile("foto.jpg", b"esto no es una imagen", content_type="image/jpeg")
        r = self.crear(fotos={"foto_0": falso})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["foto_0"], ["El archivo no es una imagen válida."])

    def test_foto_solo_jpg_png_webp(self):
        r = self.crear(fotos={"foto_0": imagen("foto.gif", "GIF")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["foto_0"], ["La foto debe ser JPG, PNG o WEBP."])
        self.assertEqual(self.crear(fotos={"foto_0": imagen("f.webp", "WEBP")}).status_code, 201)

    def test_foto_maximo_5_mb(self):
        grande = SimpleUploadedFile("foto.jpg", b"0" * (5 * 1024 * 1024 + 1), content_type="image/jpeg")
        r = self.crear(fotos={"foto_0": grande})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["foto_0"], ["La foto no puede pesar más de 5 MB."])

    def test_mas_fotos_que_alimentos(self):
        r = self.crear(fotos={"foto_0": imagen(), "foto_1": imagen()})
        self.assertEqual(r.status_code, 400)
        self.assertIn("fotos", r.data)

    def test_unidad_y_paquete_exigen_enteros(self):
        for unidad in ("unidad", "paquete"):
            with self.subTest(unidad=unidad):
                r = self.crear(self.datos([self.alimento(unidad_medida=unidad, cantidad_total="2.5")]))
                self.assertEqual(r.status_code, 400)
                self.assertIn("entero", str(r.data["alimentos"][0]["cantidad_total"]))

    def test_maximo_dos_decimales(self):
        r = self.crear(self.datos([self.alimento(cantidad_total="1.555")]))
        self.assertEqual(r.status_code, 400)
        self.assertIn("cantidad_total", r.data["alimentos"][0])
        self.assertEqual(self.crear(self.datos([self.alimento(cantidad_total="1.55")])).status_code, 201)

    def test_vencimiento_posterior_a_hoy_mas_margen(self):
        # margen = 1: hoy + 1 no sirve, hoy + 2 sí.
        r = self.crear(self.datos([self.alimento(fecha_vencimiento=en_dias(1))]))
        self.assertEqual(r.status_code, 400)
        self.assertIn("posterior al", str(r.data["alimentos"][0]["fecha_vencimiento"]))
        self.assertEqual(self.crear(self.datos([self.alimento(fecha_vencimiento=en_dias(2))])).status_code, 201)

    def test_solo_categorias_activas(self):
        Categoria.objects.filter(pk=self.granos.pk).update(activa=False)
        r = self.crear()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["alimentos"][0]["categoria"], ["La categoría no existe o está inactiva."])

    def test_direccion_obligatoria_salvo_domicilio(self):
        r = self.crear(self.datos(modalidad_entrega="Recoger", direccion_recogida=""))
        self.assertEqual(r.status_code, 400)
        self.assertIn("direccion_recogida", r.data)
        r = self.crear(self.datos(modalidad_entrega="Domicilio", direccion_recogida=""))
        self.assertEqual(r.status_code, 201)

    def test_errores_de_varios_alimentos_juntos_y_no_sube_nada(self):
        antes = self.fotos_guardadas()
        alimentos = [self.alimento(), self.alimento(unidad_medida="unidad", cantidad_total="1.5")]
        r = self.crear(self.datos(alimentos), {"foto_0": imagen()})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["alimentos"][0], {})
        self.assertIn("cantidad_total", r.data["alimentos"][1])
        self.assertIn("foto_1", r.data)
        self.assertEqual(self.fotos_guardadas(), antes)

    def test_si_falla_despues_de_subir_no_queda_nada(self):
        antes = self.fotos_guardadas()
        alimentos = [self.alimento(), self.alimento(nombre="Frijol")]
        with mock.patch(
            "apps.donaciones.services.notificar_administradores", side_effect=RuntimeError("falla")
        ):
            with self.assertRaises(RuntimeError):
                self.crear(self.datos(alimentos))
        self.assertFalse(Donacion.objects.exists())
        self.assertFalse(DonacionAlimento.objects.exists())
        self.assertEqual(self.fotos_guardadas(), antes)

    def test_solo_donantes(self):
        r = self.crear(cliente=cliente_de(self.beneficiario))
        self.assertEqual(r.status_code, 403)


# =====================================================================
# Donante: ver, editar y eliminar (RN-12)
# =====================================================================
class GestionDonacionTests(BaseDonacionTest):
    def test_lista_solo_las_propias_con_filtro_por_estado(self):
        self.crear_en_bd()
        self.crear_en_bd(estado="Validada")
        self.crear_en_bd(donante=self.otro_donante)
        r = self.cliente.get("/api/donaciones/")
        self.assertEqual(r.data["count"], 2)
        r = self.cliente.get("/api/donaciones/", {"estado": "Validada"})
        self.assertEqual(r.data["count"], 1)

    def test_no_ve_donaciones_ajenas(self):
        ajena = self.crear_en_bd(donante=self.otro_donante)
        self.assertEqual(self.cliente.get(f"/api/donaciones/{ajena.pk}/").status_code, 404)
        self.assertEqual(self.cliente.delete(f"/api/donaciones/{ajena.pk}/").status_code, 404)

    def test_editar_pendiente(self):
        d = self.crear_en_bd()
        r = self.cliente.patch(f"/api/donaciones/{d.pk}/", {"titulo": "Nuevo", "modalidad_entrega": "Domicilio",
                                                          "direccion_recogida": ""}, format="json")
        d.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(d.titulo, "Nuevo")
        self.assertIsNone(d.direccion_recogida)

    def test_editar_valida_direccion(self):
        d = self.crear_en_bd(modalidad="Domicilio")
        Donacion.objects.filter(pk=d.pk).update(direccion_recogida=None)
        r = self.cliente.patch(f"/api/donaciones/{d.pk}/", {"modalidad_entrega": "Recoger"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("direccion_recogida", r.data)

    def test_editar_rechaza_campos_no_editables(self):
        d = self.crear_en_bd()
        r = self.cliente.patch(f"/api/donaciones/{d.pk}/", {"estado": "Validada"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("estado", r.data)

    def test_revisada_no_se_edita_ni_borra(self):
        d = self.crear_en_bd(estado="Validada")
        r = self.cliente.patch(f"/api/donaciones/{d.pk}/", {"titulo": "X"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("novedad", str(r.data))
        self.assertEqual(self.cliente.delete(f"/api/donaciones/{d.pk}/").status_code, 400)
        r = self.cliente.post(f"/api/donaciones/{d.pk}/alimentos/", {**self.alimento(), "foto": imagen()}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_rechazada_mensaje_propio(self):
        d = self.crear_en_bd(estado="Rechazada")
        r = self.cliente.patch(f"/api/donaciones/{d.pk}/", {"titulo": "X"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("crea una donación nueva", str(r.data))

    def test_agregar_alimento(self):
        d = self.crear_en_bd()
        r = self.cliente.post(f"/api/donaciones/{d.pk}/alimentos/", {**self.alimento(nombre="Frijol"), "foto": imagen()}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["cantidad_disponible"], "5.00")
        self.assertEqual(d.alimentos.count(), 2)

    def test_agregar_alimento_valida_foto_y_reglas(self):
        d = self.crear_en_bd()
        r = self.cliente.post(f"/api/donaciones/{d.pk}/alimentos/", self.alimento(unidad_medida="paquete", cantidad_total="1.5"), format="multipart")
        self.assertEqual(r.status_code, 400)
        self.assertIn("foto", r.data)
        self.assertIn("cantidad_total", r.data)

    def test_quitar_alimento_borra_su_foto(self):
        d = self.crear_en_bd(alimentos=2)
        alimento = d.alimentos.first()
        foto = alimento.foto.name
        with self.captureOnCommitCallbacks(execute=True):
            r = self.cliente.delete(f"/api/donaciones/{d.pk}/alimentos/{alimento.pk}/")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(d.alimentos.count(), 1)
        self.assertFalse(default_storage.exists(foto))

    def test_no_se_quita_el_ultimo_alimento(self):
        d = self.crear_en_bd()
        r = self.cliente.delete(f"/api/donaciones/{d.pk}/alimentos/{d.alimentos.first().pk}/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("al menos un alimento", str(r.data))

    def test_quitar_alimento_de_otra_donacion(self):
        d = self.crear_en_bd(alimentos=2)
        otra = self.crear_en_bd()
        r = self.cliente.delete(f"/api/donaciones/{d.pk}/alimentos/{otra.alimentos.first().pk}/")
        self.assertEqual(r.status_code, 404)

    def test_eliminar_borra_fotos_y_conserva_notificaciones(self):
        r = self.crear(self.datos([self.alimento(), self.alimento(nombre="Frijol")]))
        d = Donacion.objects.get(pk=r.data["id"])
        fotos = [a.foto.name for a in d.alimentos.all()]
        with self.captureOnCommitCallbacks(execute=True):
            r = self.cliente.delete(f"/api/donaciones/{d.pk}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Donacion.objects.filter(pk=d.pk).exists())
        self.assertFalse(any(default_storage.exists(f) for f in fotos))
        # La notificación nueva_donacion queda como historial (donacion = NULL).
        self.assertTrue(Notificacion.objects.filter(tipo="nueva_donacion", donacion__isnull=True).exists())


# =====================================================================
# Administrador: validar y rechazar (RN-13)
# =====================================================================
class AdminDonacionTests(BaseDonacionTest):
    def validar(self, donacion):
        return self.cliente_admin.post(f"/api/admin/donaciones/{donacion.pk}/validar/")

    def rechazar(self, donacion, datos):
        return self.cliente_admin.post(f"/api/admin/donaciones/{donacion.pk}/rechazar/", datos, format="json")

    def test_solo_administradores(self):
        self.assertEqual(self.cliente.get("/api/admin/donaciones/").status_code, 403)

    def test_listar_con_filtros_y_detalle_con_fotos(self):
        d = self.crear_en_bd()
        self.crear_en_bd(donante=self.otro_donante, estado="Validada")
        self.assertEqual(self.cliente_admin.get("/api/admin/donaciones/").data["count"], 2)
        self.assertEqual(self.cliente_admin.get("/api/admin/donaciones/", {"estado": "Pendiente"}).data["count"], 1)
        self.assertEqual(self.cliente_admin.get("/api/admin/donaciones/", {"donante": self.otro_donante.pk}).data["count"], 1)
        r = self.cliente_admin.get(f"/api/admin/donaciones/{d.pk}/")
        self.assertEqual(r.data["donante"]["correo"], "dona@k.com")
        self.assertIn("kumora/alimentos/", r.data["alimentos"][0]["foto_url"])

    def test_validar_calcula_maximo_y_notifica(self):
        d = self.crear_en_bd(alimentos=0)
        hoy = timezone.localdate()
        for nombre, unidad, total in [("A", "unidad", "10"), ("B", "kg", "2.5")]:
            a = DonacionAlimento(donacion=d, categoria=self.granos, nombre=nombre, unidad_medida=unidad,
                                 cantidad_total=Decimal(total), cantidad_disponible=Decimal(total),
                                 fecha_vencimiento=hoy + timedelta(days=30))
            a.foto.save("x.jpg", imagen(), save=False)
            a.save()
        r = self.validar(d)
        d.refresh_from_db()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(d.estado, "Validada")
        self.assertEqual(d.revisada_por, self.admin)
        self.assertIsNotNone(d.fecha_revision)
        maximos = dict(d.alimentos.values_list("nombre", "max_por_solicitud"))
        self.assertEqual(maximos, {"A": Decimal("3.00"), "B": Decimal("0.75")})
        self.assertTrue(Notificacion.objects.filter(usuario=self.donante, tipo="donacion_validada", donacion=d).exists())

    def test_reglas_de_redondeo(self):
        casos = [
            ("unidad", "10", "30", "3"),     # 3 exacto
            ("paquete", "3", "30", "1"),     # 0,9 -> 0 -> mínimo 1
            ("unidad", "7", "30", "2"),      # 2,1 -> 2
            ("kg", "1.11", "30", "0.33"),    # 0,333 -> 0,33
            ("g", "0.03", "30", "0.01"),     # 0,009 -> 0 -> mínimo 0,01
            ("L", "2.99", "100", "2.99"),
        ]
        for unidad, total, porcentaje, esperado in casos:
            with self.subTest(unidad=unidad, total=total):
                alimento = DonacionAlimento(unidad_medida=unidad, cantidad_total=Decimal(total))
                self.assertEqual(calcular_max_por_solicitud(alimento, Decimal(porcentaje)), Decimal(esperado))

    def test_no_valida_si_un_alimento_ya_no_cumple_el_vencimiento(self):
        d = self.crear_en_bd(fecha_vencimiento=timezone.localdate() + timedelta(days=1))
        r = self.validar(d)
        d.refresh_from_db()
        self.assertEqual(r.status_code, 400)
        self.assertIn("Rechaza la donación", str(r.data))
        self.assertEqual(d.estado, "Pendiente")

    def test_solo_se_revisan_pendientes(self):
        d = self.crear_en_bd(estado="Validada")
        self.assertEqual(self.validar(d).status_code, 400)
        self.assertEqual(self.rechazar(d, {"motivo_rechazo": "x"}).status_code, 400)

    def test_rechazar_exige_motivo(self):
        d = self.crear_en_bd()
        for datos in ({}, {"motivo_rechazo": "  "}):
            r = self.rechazar(d, datos)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.data["motivo_rechazo"], ["Debes indicar el motivo del rechazo."])

    def test_rechazar_notifica_y_conserva_fotos(self):
        d = self.crear_en_bd()
        foto = d.alimentos.first().foto.name
        r = self.rechazar(d, {"motivo_rechazo": "No es comida"})
        d.refresh_from_db()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(d.estado, "Rechazada")
        self.assertEqual(d.motivo_rechazo, "No es comida")
        self.assertEqual(d.revisada_por, self.admin)
        self.assertTrue(default_storage.exists(foto))
        n = Notificacion.objects.get(usuario=self.donante, tipo="donacion_rechazada")
        self.assertIn("No es comida", n.mensaje)


# =====================================================================
# Beneficiario: alimentos disponibles (RN-14)
# =====================================================================
class AlimentosDisponiblesTests(BaseDonacionTest):
    url = "/api/alimentos-disponibles/"

    def setUp(self):
        super().setUp()
        self.cliente_beneficiario = cliente_de(self.beneficiario)
        self.visible = self.crear_en_bd(estado="Validada", modalidad="Recoger").alimentos.get()

    def ids(self, **filtros):
        r = self.cliente_beneficiario.get(self.url, filtros)
        self.assertEqual(r.status_code, 200)
        return {a["id"] for a in r.data["results"]}

    def test_solo_beneficiarios(self):
        self.assertEqual(self.cliente.get(self.url).status_code, 403)

    def test_muestra_alimento_visible_con_url_de_foto_y_sin_datos_de_contacto(self):
        r = self.cliente_beneficiario.get(self.url)
        a = r.data["results"][0]
        self.assertEqual(a["id"], self.visible.pk)
        self.assertIn("kumora/alimentos/", a["foto_url"])
        self.assertEqual(a["donante_nombre"], self.donante.nombre_para_mostrar)
        self.assertNotIn("direccion_recogida", a)
        self.assertNotIn("telefono", a)

    def test_oculta_lo_que_no_cumple_rn14(self):
        hoy = timezone.localdate()
        ocultos = [
            self.crear_en_bd(estado="Pendiente"),
            self.crear_en_bd(estado="Rechazada"),
            self.crear_en_bd(estado="Validada", activo=False),
            self.crear_en_bd(estado="Validada", cantidad_disponible=Decimal("0")),
            self.crear_en_bd(estado="Validada", fecha_vencimiento=hoy + timedelta(days=1)),  # = hoy + margen
        ]
        bloqueado = crear_usuario("bloq@k.com", Rol.DONANTE, "20", tipo_donante="Persona")
        ocultos.append(self.crear_en_bd(donante=bloqueado, estado="Validada"))
        Usuario.objects.filter(pk=bloqueado.pk).update(estado="Bloqueado", bloqueado_hasta=timezone.now() + timedelta(days=1))
        visibles = self.ids()
        self.assertEqual(visibles, {self.visible.pk})
        self.assertFalse(visibles & {d.alimentos.get().pk for d in ocultos})

    def test_vencimiento_un_dia_despues_del_margen_si_se_ve(self):
        a = self.crear_en_bd(estado="Validada", fecha_vencimiento=timezone.localdate() + timedelta(days=2)).alimentos.get()
        self.assertIn(a.pk, self.ids())

    def test_novedad_pendiente_sobre_el_alimento_o_toda_la_donacion(self):
        d1 = self.crear_en_bd(estado="Validada", alimentos=2)
        a1, a2 = d1.alimentos.order_by("id")
        Novedad.objects.create(donante=self.donante, donacion=d1, donacion_alimento=a1, descripcion="x")
        self.assertEqual(self.ids() & {a1.pk, a2.pk}, {a2.pk})

        d2 = self.crear_en_bd(estado="Validada", alimentos=2)
        Novedad.objects.create(donante=self.donante, donacion=d2, descripcion="toda")
        self.assertFalse(self.ids() & set(d2.alimentos.values_list("id", flat=True)))

        # Una novedad ya resuelta no oculta nada.
        Novedad.objects.filter(donacion=d2).update(estado="Rechazada")
        self.assertTrue(set(d2.alimentos.values_list("id", flat=True)) <= self.ids())

    def test_filtros(self):
        leche = self.crear_en_bd(estado="Validada", modalidad="Domicilio", categoria=self.lacteos, nombre="Leche").alimentos.get()
        ambas = self.crear_en_bd(estado="Validada", modalidad="Ambas", nombre="Panela").alimentos.get()
        self.assertEqual(self.ids(categoria=self.lacteos.pk), {leche.pk})
        self.assertEqual(self.ids(modalidad="Recoger"), {self.visible.pk, ambas.pk})
        self.assertEqual(self.ids(modalidad="Domicilio"), {leche.pk, ambas.pk})
        self.assertEqual(self.ids(modalidad="Ambas"), {ambas.pk})
        self.assertEqual(self.ids(search="lech"), {leche.pk})

    def test_margen_de_la_configuracion(self):
        Configuracion.objects.filter(pk=1).update(margen_vencimiento_dias=40)
        self.assertEqual(self.ids(), set())


# =====================================================================
# Estado de la donación (RN-16), Paso 6
# =====================================================================
class RecalcularEstadoTests(BaseDonacionTest):
    def solicitud(self, donacion, estado="Pendiente"):
        from apps.solicitudes.models import Solicitud

        return Solicitud.objects.create(
            codigo_pedido="KUM-20260101-0001", beneficiario=self.beneficiario, donacion=donacion,
            modalidad_entrega="Recoger", estado=estado,
            motivo_rechazo="x" if estado == "Rechazada" else None,
        )

    def recalcular(self, donacion):
        from .services import recalcular_estado_donacion

        return recalcular_estado_donacion(donacion).estado

    def test_con_disponibilidad_queda_validada(self):
        d = self.crear_en_bd(estado="Agotada")
        self.assertEqual(self.recalcular(d), "Validada")

    def test_sin_disponibilidad_y_con_solicitudes_en_curso_queda_agotada(self):
        for estado in ("Pendiente", "Aprobada"):
            with self.subTest(estado=estado):
                d = self.crear_en_bd(estado="Validada", cantidad_disponible=Decimal("0"))
                self.solicitud(d, estado)
                self.assertEqual(self.recalcular(d), "Agotada")

    def test_sin_disponibilidad_ni_solicitudes_en_curso_queda_finalizada(self):
        d = self.crear_en_bd(estado="Agotada", cantidad_disponible=Decimal("0"))
        self.solicitud(d, "Entregada")
        self.assertEqual(self.recalcular(d), "Finalizada")

    def test_inactivo_o_vencido_cuenta_como_sin_disponibilidad(self):
        d1 = self.crear_en_bd(estado="Validada", activo=False)
        d2 = self.crear_en_bd(estado="Validada", fecha_vencimiento=timezone.localdate() + timedelta(days=1))
        self.assertEqual(self.recalcular(d1), "Finalizada")
        self.assertEqual(self.recalcular(d2), "Finalizada")

    def test_finalizada_es_final(self):
        d = self.crear_en_bd(estado="Validada", cantidad_disponible=Decimal("0"))
        self.assertEqual(self.recalcular(d), "Finalizada")
        DonacionAlimento.objects.filter(donacion=d).update(cantidad_disponible=Decimal("5"))
        self.assertEqual(self.recalcular(d), "Finalizada")

    def test_pendiente_y_rechazada_no_cambian(self):
        self.assertEqual(self.recalcular(self.crear_en_bd(cantidad_disponible=Decimal("0"))), "Pendiente")
        self.assertEqual(self.recalcular(self.crear_en_bd(estado="Rechazada")), "Rechazada")

    def test_condiciones_temporales_no_cambian_el_estado(self):
        d = self.crear_en_bd(estado="Validada")
        Novedad.objects.create(donante=self.donante, donacion=d, descripcion="toda")
        Usuario.objects.filter(pk=self.donante.pk).update(
            estado="Bloqueado", bloqueado_hasta=timezone.now() + timedelta(days=1)
        )
        self.assertEqual(self.recalcular(d), "Validada")


# =====================================================================
# Novedades: crear, listar y revisar (RN-30), Paso 8
# Los efectos de aceptar/rechazar sobre solicitudes están en solicitudes/tests.py.
# =====================================================================
class NovedadDonanteTests(BaseDonacionTest):
    url = "/api/novedades/"

    def setUp(self):
        super().setUp()
        self.d = self.crear_en_bd(estado="Validada", alimentos=2)
        self.a1, self.a2 = self.d.alimentos.order_by("id")

    def crear_novedad(self, donacion=None, alimento=None, descripcion="Se dañó por la lluvia", cliente=None):
        datos = {"donacion": (donacion or self.d).pk, "descripcion": descripcion}
        if alimento is not None:
            datos["donacion_alimento"] = alimento.pk
        return (cliente or self.cliente).post(self.url, datos, format="json")

    def test_crea_sobre_un_alimento_y_notifica_a_los_administradores(self):
        otro_admin = crear_admin("admin2@k.com", "2")
        r = self.crear_novedad(alimento=self.a1)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["estado"], r.data["donacion_alimento"]), ("Pendiente", self.a1.pk))
        self.assertEqual(r.data["alimento_nombre"], self.a1.nombre)
        notificaciones = Notificacion.objects.filter(tipo="novedad_creada")
        self.assertEqual({n.usuario_id for n in notificaciones}, {self.admin.pk, otro_admin.pk})
        self.assertIn("Se dañó por la lluvia", notificaciones.first().mensaje)

    def test_crea_sobre_toda_la_donacion(self):
        r = self.crear_novedad()
        self.assertEqual(r.status_code, 201)
        self.assertIsNone(r.data["donacion_alimento"])
        self.assertIsNone(r.data["alimento_nombre"])

    def test_tambien_sobre_donacion_agotada(self):
        Donacion.objects.filter(pk=self.d.pk).update(estado="Agotada")
        self.assertEqual(self.crear_novedad().status_code, 201)

    def test_solo_validada_o_agotada(self):
        for estado in ("Pendiente", "Rechazada", "Finalizada"):
            with self.subTest(estado=estado):
                d = self.crear_en_bd(estado=estado)
                r = self.crear_novedad(donacion=d)
                self.assertEqual(r.status_code, 400)
                self.assertIn("donacion", r.data)

    def test_solo_sobre_donaciones_propias(self):
        ajena = self.crear_en_bd(donante=self.otro_donante, estado="Validada")
        self.assertEqual(self.crear_novedad(donacion=ajena).status_code, 404)

    def test_el_alimento_debe_ser_de_esa_donacion_y_estar_activo(self):
        otra = self.crear_en_bd(estado="Validada")
        r = self.crear_novedad(alimento=otra.alimentos.get())
        self.assertEqual(r.status_code, 400)
        self.assertIn("no pertenece", str(r.data))
        DonacionAlimento.objects.filter(pk=self.a2.pk).update(activo=False)
        r = self.crear_novedad(alimento=self.a2)
        self.assertIn("ya fue retirado", str(r.data))

    def test_maximo_una_pendiente_por_alimento_y_una_general(self):
        self.assertEqual(self.crear_novedad(alimento=self.a1).status_code, 201)
        r = self.crear_novedad(alimento=self.a1)
        self.assertEqual(r.status_code, 400)
        self.assertIn("este alimento", str(r.data))
        # Otro alimento y una general sí se permiten.
        self.assertEqual(self.crear_novedad(alimento=self.a2).status_code, 201)
        self.assertEqual(self.crear_novedad().status_code, 201)
        r = self.crear_novedad()
        self.assertIn("toda la donación", str(r.data))
        # Resuelta la anterior, puede reportar otra sobre el mismo alimento.
        Novedad.objects.filter(donacion_alimento=self.a1).update(estado="Rechazada")
        self.assertEqual(self.crear_novedad(alimento=self.a1).status_code, 201)

    def test_descripcion_obligatoria(self):
        r = self.crear_novedad(descripcion="  ")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["descripcion"], ["Describe qué pasó con los alimentos."])

    def test_lista_solo_las_propias(self):
        self.crear_novedad()
        ajena = self.crear_en_bd(donante=self.otro_donante, estado="Validada")
        Novedad.objects.create(donante=self.otro_donante, donacion=ajena, descripcion="x")
        r = self.cliente.get(self.url)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(self.cliente.get(self.url, {"estado": "Aceptada"}).data["count"], 0)

    def test_solo_donantes(self):
        self.assertEqual(self.crear_novedad(cliente=cliente_de(self.beneficiario)).status_code, 403)

    def test_rn14_lo_afectado_deja_de_mostrarse(self):
        cliente_b = cliente_de(self.beneficiario)
        visibles = lambda: {a["id"] for a in cliente_b.get("/api/alimentos-disponibles/").data["results"]}
        self.assertEqual(visibles(), {self.a1.pk, self.a2.pk})
        self.crear_novedad(alimento=self.a1)
        self.assertEqual(visibles(), {self.a2.pk})
        self.crear_novedad()
        self.assertEqual(visibles(), set())


class AdminNovedadTests(BaseDonacionTest):
    url = "/api/admin/novedades/"

    def setUp(self):
        super().setUp()
        self.d = self.crear_en_bd(estado="Validada")
        self.novedad = Novedad.objects.create(donante=self.donante, donacion=self.d, descripcion="x")

    def test_solo_administradores(self):
        self.assertEqual(self.cliente.get(self.url).status_code, 403)

    def test_filtros_y_detalle(self):
        otra = self.crear_en_bd(donante=self.otro_donante, estado="Validada")
        Novedad.objects.create(donante=self.otro_donante, donacion=otra, descripcion="y", estado="Rechazada", respuesta_admin="no")
        self.assertEqual(self.cliente_admin.get(self.url).data["count"], 2)
        self.assertEqual(self.cliente_admin.get(self.url, {"estado": "Pendiente"}).data["count"], 1)
        self.assertEqual(self.cliente_admin.get(self.url, {"donacion": otra.pk}).data["count"], 1)
        self.assertEqual(self.cliente_admin.get(self.url, {"donante": self.donante.pk}).data["count"], 1)
        r = self.cliente_admin.get(f"{self.url}{self.novedad.pk}/")
        self.assertEqual(r.data["donante"]["correo"], "dona@k.com")
        self.assertEqual(r.data["solicitudes_afectadas"], 0)

    def test_rechazar_exige_respuesta(self):
        for datos in ({}, {"respuesta_admin": " "}):
            r = self.cliente_admin.post(f"{self.url}{self.novedad.pk}/rechazar/", datos, format="json")
            self.assertEqual(r.status_code, 400)
            self.assertEqual(r.data["respuesta_admin"], ["Debes indicar la respuesta al donante."])

    def test_solo_se_revisan_pendientes(self):
        Novedad.objects.filter(pk=self.novedad.pk).update(estado="Aceptada")
        r = self.cliente_admin.post(f"{self.url}{self.novedad.pk}/aceptar/", {}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("ya fue revisada", str(r.data))
        r = self.cliente_admin.post(f"{self.url}{self.novedad.pk}/rechazar/", {"respuesta_admin": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
