"""Pruebas del Paso 9: sanciones y bloqueos (RN-03 a RN-08, RN-70 a RN-77)."""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.donaciones.models import Donacion, DonacionAlimento
from apps.solicitudes.models import CarritoItem, Solicitud
from apps.solicitudes.tests import BaseGestionTest
from apps.usuarios.models import Usuario
from apps.usuarios.tests import CLAVE, cliente_de, crear_admin

from .models import Sancion


class BaseSancionTest(BaseGestionTest):
    def sancionar(self, usuario, tipo, motivo="No recogió la solicitud", **extra):
        return self.cliente_admin.post(
            "/api/admin/sanciones/", {"usuario": usuario.pk, "tipo": tipo, "motivo": motivo, **extra},
            format="json",
        )

    def impacto(self, usuario, accion):
        return self.cliente_admin.get(f"/api/admin/usuarios/{usuario.pk}/impacto/", {"accion": accion})

    def inactivar(self, usuario):
        return self.cliente_admin.post(
            f"/api/admin/usuarios/{usuario.pk}/cambiar-estado/", {"estado": "Inactivo"}, format="json"
        )

    def historica(self, usuario, tipo, vigente=False):
        """Sanción antigua creada directo en la base (para probar la escala)."""
        return Sancion.objects.create(
            usuario=usuario, tipo=tipo, motivo="antes", creada_por=self.admin, vigente=vigente,
            fecha_fin=timezone.now() - timedelta(days=1) if tipo == "Bloqueo temporal" else None,
        )

    def estado(self, obj):
        obj.refresh_from_db()
        return obj.estado

    def visibles(self):
        return {a["id"] for a in self.cb.get("/api/alimentos-disponibles/").data["results"]}

    def en_curso_del_beneficiario(self):
        """Pendiente en d2 (panela) y Aprobada en d1 (arroz), más un ítem en el carrito."""
        pendiente = self.crear_solicitud(self.d2, [(self.panela, "2")], codigo="KUM-20261012-0001")
        aprobada = self.crear_solicitud(
            self.d1, [(self.arroz, "3")], estado="Aprobada",
            limite=timezone.now() + timedelta(days=2), codigo="KUM-20261012-0002",
        )
        self.en_carrito(self.frijol, "1")
        return pendiente, aprobada


# =====================================================================
# Crear y listar (RN-05, RN-08)
# =====================================================================
class CrearSancionTests(BaseSancionTest):
    def test_solo_administradores(self):
        self.assertEqual(self.cb.get("/api/admin/sanciones/").status_code, 403)
        self.assertEqual(self.sancionar(self.beneficiario, "Advertencia").status_code, 201)
        self.assertEqual(self.cliente.post("/api/admin/sanciones/", {}, format="json").status_code, 403)

    def test_motivo_obligatorio_y_tipo_valido(self):
        r = self.sancionar(self.beneficiario, "Advertencia", motivo="  ")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["motivo"], ["El motivo de la sanción es obligatorio."])
        r = self.sancionar(self.beneficiario, "Expulsión")
        self.assertIn("Advertencia, Bloqueo temporal o Bloqueo permanente", str(r.data["tipo"]))
        self.assertEqual(self.cliente_admin.post("/api/admin/sanciones/", {"usuario": 999999, "tipo": "Advertencia",
                                                                           "motivo": "x"}, format="json").status_code, 404)

    def test_rn04_no_se_sanciona_a_un_administrador(self):
        otro = crear_admin("admin2@k.com", "2")
        for objetivo in (otro, self.admin):
            r = self.sancionar(objetivo, "Advertencia")
            self.assertEqual(r.status_code, 403)
            self.assertIn("Administrador", str(r.data["detail"]))
        self.assertEqual(self.impacto(otro, "advertencia").status_code, 403)
        self.assertFalse(Sancion.objects.exists())

    def test_rn05_sugerencia_segun_todo_el_historial(self):
        sugerida = lambda: self.impacto(self.beneficiario, "advertencia").data["sancion_sugerida"]
        self.assertEqual(sugerida(), "Advertencia")
        self.sancionar(self.beneficiario, "Advertencia")
        self.assertEqual(sugerida(), "Bloqueo temporal")
        # Un temporal antiguo, ya no vigente, también cuenta.
        self.historica(self.beneficiario, "Bloqueo temporal")
        r = self.impacto(self.beneficiario, "advertencia")
        self.assertEqual(r.data["sancion_sugerida"], "Bloqueo permanente")
        self.assertEqual(
            r.data["historial"], {"advertencias": 1, "bloqueos_temporales": 1, "bloqueos_permanentes": 0}
        )

    def test_rn08_notifica_y_se_lista_con_filtros(self):
        r = self.sancionar(self.beneficiario, "Advertencia")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["usuario"]["correo"], "bene@k.com")
        self.assertEqual(r.data["creada_por"], "admin@k.com")
        aviso = self.notificaciones("sancion", self.beneficiario).get()
        self.assertIn("No recogió la solicitud", aviso.mensaje)
        self.sancionar(self.donante, "Advertencia")

        ids = lambda **f: [s["id"] for s in self.cliente_admin.get("/api/admin/sanciones/", f).data["results"]]
        self.assertEqual(ids(usuario=self.beneficiario.pk), [r.data["id"]])
        self.assertEqual(len(ids(tipo="Advertencia")), 2)
        self.assertEqual(ids(vigente="false"), [])
        detalle = self.cliente_admin.get(f"/api/admin/sanciones/{r.data['id']}/")
        self.assertEqual(detalle.data["tipo"], "Advertencia")

    def test_relacion_con_la_donacion_o_la_solicitud(self):
        propia = self.crear_solicitud(self.d1, [(self.arroz, "1")])
        ajena = self.crear_en_bd(donante=self.otro_donante, estado="Validada")

        r = self.sancionar(self.beneficiario, "Advertencia", donacion=ajena.pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn("no es de este usuario", str(r.data["donacion"]))
        r = self.sancionar(self.beneficiario, "Advertencia", solicitud=propia.pk, donacion=self.d2.pk)
        self.assertIn("no pertenece a la donación", str(r.data["solicitud"]))
        r = self.sancionar(self.otro_donante, "Advertencia", solicitud=propia.pk)
        self.assertIn("no es de este usuario", str(r.data["solicitud"]))

        # Como beneficiario (pidió de d1) y como donante (dueño de d1).
        self.assertEqual(self.sancionar(self.beneficiario, "Advertencia", solicitud=propia.pk,
                                        donacion=self.d1.pk).status_code, 201)
        self.assertEqual(self.sancionar(self.donante, "Advertencia", solicitud=propia.pk).status_code, 201)

        # No dos veces al mismo usuario por la misma solicitud o donación.
        r = self.sancionar(self.beneficiario, "Bloqueo temporal", solicitud=propia.pk)
        self.assertIn("ya fue sancionado por esta solicitud", str(r.data["solicitud"]))
        r = self.sancionar(self.beneficiario, "Bloqueo temporal", donacion=self.d1.pk)
        self.assertIn("ya fue sancionado por esta donación", str(r.data["donacion"]))
        self.assertEqual(self.estado(self.beneficiario), "Activo")

    def test_rn76_advertencia_no_afecta_nada(self):
        pendiente, aprobada = self.en_curso_del_beneficiario()
        refresh = RefreshToken.for_user(self.beneficiario)
        r = self.sancionar(self.beneficiario, "Advertencia")
        self.assertEqual(set(r.data["efectos"].values()), {0})
        self.assertEqual(self.estado(self.beneficiario), "Activo")
        self.assertEqual((self.estado(pendiente), self.estado(aprobada)), ("Pendiente", "Aprobada"))
        self.assertTrue(CarritoItem.objects.exists())
        self.assertFalse(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())
        self.assertIsNone(Sancion.objects.get().fecha_fin)


# =====================================================================
# Beneficiario (RN-06, RN-70, RN-71)
# =====================================================================
class SancionBeneficiarioTests(BaseSancionTest):
    def test_rn70_bloqueo_temporal(self):
        pendiente, aprobada = self.en_curso_del_beneficiario()
        refresh = RefreshToken.for_user(self.beneficiario)
        antes = timezone.now()

        r = self.sancionar(self.beneficiario, "Bloqueo temporal")
        self.assertEqual(r.status_code, 201, r.data)
        sancion = Sancion.objects.get()
        self.assertAlmostEqual(
            (sancion.fecha_fin - antes).total_seconds(), timedelta(days=7).total_seconds(), delta=60
        )
        self.beneficiario.refresh_from_db()
        self.assertEqual(self.beneficiario.estado, "Bloqueado")
        self.assertEqual(self.beneficiario.bloqueado_hasta, sancion.fecha_fin)
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists())
        self.assertEqual(self.cb.get("/api/carrito/").status_code, 403)  # aplica de inmediato

        # Pendiente cancelada (devuelve la cantidad); Aprobada sigue; carrito vacío.
        pendiente.refresh_from_db()
        self.assertEqual(pendiente.estado, "Cancelada")
        self.assertIn("bloqueo temporal", pendiente.motivo_cancelacion)
        self.assertEqual(self.disponible(self.panela), Decimal("10"))
        self.assertEqual(self.estado(aprobada), "Aprobada")
        self.assertEqual(self.disponible(self.arroz), Decimal("7"))
        self.assertFalse(CarritoItem.objects.exists())
        # RN-70 no avisa al donante; el beneficiario recibe su sanción (RN-08).
        self.assertFalse(self.notificaciones("solicitud_cancelada", self.donante).exists())
        self.assertIn("Se cancelaron 1 solicitud", self.notificaciones("sancion", self.beneficiario).get().mensaje)
        self.assertEqual(
            {k: v for k, v in r.data["efectos"].items() if v},
            {"solicitudes_canceladas": 1, "items_carrito": 1, "solicitudes_que_siguen": 1},
        )

    def test_caso_limite_bloqueo_temporal_no_se_acumula(self):
        self.sancionar(self.beneficiario, "Bloqueo temporal")
        r = self.sancionar(self.beneficiario, "Bloqueo temporal", motivo="Otra falta")
        self.assertEqual(r.status_code, 400)
        self.assertIn("ya tiene un bloqueo temporal vigente", str(r.data["tipo"]))
        self.assertEqual(self.impacto(self.beneficiario, "bloqueo_temporal").status_code, 400)
        # Una advertencia sí se registra y no cambia el bloqueo.
        self.assertEqual(self.sancionar(self.beneficiario, "Advertencia", motivo="Otra").status_code, 201)
        self.assertEqual(self.estado(self.beneficiario), "Bloqueado")

    def test_rn06_rn71_permanente_reemplaza_al_temporal(self):
        pendiente, aprobada = self.en_curso_del_beneficiario()
        self.sancionar(self.beneficiario, "Bloqueo temporal")
        r = self.sancionar(self.beneficiario, "Bloqueo permanente", motivo="Reincidió")
        self.assertEqual(r.status_code, 201, r.data)

        temporal, permanente = Sancion.objects.order_by("id")
        self.assertFalse(temporal.vigente)
        self.assertTrue(permanente.vigente)
        self.assertIsNone(permanente.fecha_fin)
        self.beneficiario.refresh_from_db()
        self.assertEqual((self.beneficiario.estado, self.beneficiario.bloqueado_hasta), ("Bloqueado", None))

        # RN-71: ahora también la Aprobada; devuelve la cantidad y avisa al donante.
        aprobada.refresh_from_db()
        self.assertEqual(aprobada.estado, "Cancelada")
        self.assertIn("bloqueo permanente", aprobada.motivo_cancelacion)
        self.assertEqual(self.disponible(self.arroz), Decimal("10"))
        self.assertTrue(self.notificaciones("solicitud_cancelada", self.donante).filter(solicitud=aprobada).exists())
        self.assertEqual(r.data["efectos"]["solicitudes_canceladas"], 1)

    def test_rn06_permanente_es_definitivo(self):
        self.sancionar(self.beneficiario, "Bloqueo permanente")
        r = self.sancionar(self.beneficiario, "Advertencia", motivo="Otra")
        self.assertEqual(r.status_code, 400)
        self.assertIn("definitivo", str(r.data["tipo"]))
        self.assertIsNone(self.impacto(self.beneficiario, "inactivar").data["sancion_sugerida"])

    def test_rn16_la_donacion_agotada_vuelve_a_validada(self):
        DonacionAlimento.objects.filter(pk=self.panela.pk).update(max_por_solicitud=Decimal("10"))
        self.crear_solicitud(self.d2, [(self.panela, "10")])
        Donacion.objects.filter(pk=self.d2.pk).update(estado="Agotada")
        self.sancionar(self.beneficiario, "Bloqueo temporal")
        self.assertEqual(self.estado(self.d2), "Validada")


# =====================================================================
# Donante (RN-72, RN-73)
# =====================================================================
class SancionDonanteTests(BaseSancionTest):
    def test_rn72_bloqueo_temporal(self):
        pendiente_d = self.crear_en_bd()  # donación Pendiente: queda en espera
        aprobada = self.crear_solicitud(self.d1, [(self.arroz, "1")], estado="Aprobada",
                                        limite=timezone.now() + timedelta(days=2))
        self.assertIn(self.leche.pk, self.visibles())

        r = self.sancionar(self.donante, "Bloqueo temporal", motivo="Entregó tarde")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["efectos"]["alimentos_ocultos"], 4)
        self.assertEqual(self.visibles(), set())
        self.assertEqual(self.estado(pendiente_d), "Pendiente")
        self.assertEqual(self.estado(self.d1), "Validada")  # RN-16: lo temporal no cambia el estado
        self.leche.refresh_from_db()
        self.assertTrue(self.leche.activo)

        # La solicitud en curso sigue y la entrega la registra el administrador.
        self.assertEqual(self.cliente.post(f"/api/solicitudes/{aprobada.pk}/entregar/").status_code, 403)
        self.assertEqual(self.accion(aprobada, "entregar").status_code, 200)

    def test_rn73_bloqueo_permanente(self):
        pendiente_d = self.crear_en_bd()
        s1 = self.crear_solicitud(self.d1, [(self.arroz, "1")], codigo="KUM-20261012-0001")
        s2 = self.crear_solicitud(self.d3, [(self.frijol, "1")], estado="Aprobada",
                                  limite=timezone.now() + timedelta(days=2), codigo="KUM-20261012-0002")
        entregada = self.crear_solicitud(self.d2, [(self.panela, "1")], estado="Entregada",
                                         codigo="KUM-20261012-0003")

        r = self.sancionar(self.donante, "Bloqueo permanente", motivo="Donó algo que no era comida")
        self.assertEqual(r.status_code, 201, r.data)

        pendiente_d.refresh_from_db()
        self.assertEqual(pendiente_d.estado, "Rechazada")
        self.assertIn("bloqueo permanente", pendiente_d.motivo_rechazo)
        self.assertEqual(pendiente_d.revisada_por, self.admin)
        for s in (s1, s2):
            s.refresh_from_db()
            self.assertEqual(s.estado, "Cancelada")
            self.assertTrue(self.notificaciones("solicitud_cancelada", self.beneficiario).filter(solicitud=s).exists())
        self.assertEqual(self.estado(entregada), "Entregada")
        # Se retiran los alimentos de las donaciones revisadas; los de la Rechazada no hacen falta.
        revisadas = DonacionAlimento.objects.filter(donacion__in=[self.d1, self.d2, self.d3])
        self.assertFalse(revisadas.filter(activo=True).exists())
        self.assertFalse(revisadas.filter(cantidad_disponible__gt=0).exists())
        for d in (self.d1, self.d2, self.d3):
            self.assertEqual(self.estado(d), "Finalizada")
        self.assertEqual(
            {k: v for k, v in r.data["efectos"].items() if v},
            {"solicitudes_canceladas": 2, "donaciones_rechazadas": 1, "donaciones_finalizadas": 3,
             "alimentos_retirados": 4},
        )

    def test_rn73_no_toca_las_de_otro_donante(self):
        ajena = self.crear_en_bd(donante=self.otro_donante, estado="Validada")
        self.sancionar(self.donante, "Bloqueo permanente")
        self.assertEqual(self.estado(ajena), "Validada")
        self.assertTrue(ajena.alimentos.get().activo)


# =====================================================================
# Inactivar (RN-71, RN-73 desde cambiar-estado)
# =====================================================================
class InactivarTests(BaseSancionTest):
    def test_inactivar_beneficiario_rn71(self):
        pendiente, aprobada = self.en_curso_del_beneficiario()
        self.assertEqual(self.inactivar(self.beneficiario).status_code, 200)
        for s in (pendiente, aprobada):
            s.refresh_from_db()
            self.assertEqual(s.estado, "Cancelada")
            self.assertIn("desactivada", s.motivo_cancelacion)
        self.assertEqual(self.disponible(self.arroz), Decimal("10"))
        self.assertEqual(self.disponible(self.panela), Decimal("10"))
        self.assertFalse(CarritoItem.objects.exists())
        self.assertEqual(self.notificaciones("solicitud_cancelada", self.donante).count(), 2)

    def test_inactivar_donante_rn73(self):
        pendiente_d = self.crear_en_bd()
        s = self.crear_solicitud(self.d1, [(self.arroz, "1")])
        self.assertEqual(self.inactivar(self.donante).status_code, 200)
        pendiente_d.refresh_from_db()
        self.assertEqual(pendiente_d.estado, "Rechazada")
        self.assertIn("desactivada", pendiente_d.motivo_rechazo)
        self.assertEqual(self.estado(s), "Cancelada")
        self.assertEqual(self.estado(self.d1), "Finalizada")

    def test_rn75_reactivar_no_recupera_lo_cancelado(self):
        pendiente, _ = self.en_curso_del_beneficiario()
        self.inactivar(self.beneficiario)
        self.cliente_admin.post(f"/api/admin/usuarios/{self.beneficiario.pk}/cambiar-estado/",
                                {"estado": "Activo"}, format="json")
        self.assertEqual(self.estado(self.beneficiario), "Activo")
        self.assertEqual(self.estado(pendiente), "Cancelada")


# =====================================================================
# Impacto (RN-74)
# =====================================================================
class ImpactoTests(BaseSancionTest):
    def test_no_aplica_nada(self):
        pendiente, aprobada = self.en_curso_del_beneficiario()
        for accion in ("advertencia", "bloqueo_temporal", "bloqueo_permanente", "inactivar"):
            self.assertEqual(self.impacto(self.beneficiario, accion).status_code, 200)
        self.assertEqual(self.estado(self.beneficiario), "Activo")
        self.assertEqual((self.estado(pendiente), self.estado(aprobada)), ("Pendiente", "Aprobada"))
        self.assertTrue(CarritoItem.objects.exists())
        self.assertFalse(Sancion.objects.exists())

    def test_coincide_con_lo_que_pasa_beneficiario(self):
        self.en_curso_del_beneficiario()
        r = self.impacto(self.beneficiario, "bloqueo_temporal")
        self.assertEqual(r.data["conteos"]["solicitudes_canceladas"], 1)
        self.assertIn("1 solicitud(es) se cancelarán y sus cantidades volverán a estar disponibles.", r.data["resumen"])
        self.assertIn("Se vaciará su carrito (1 alimento(s)).", r.data["resumen"])
        efectos = self.sancionar(self.beneficiario, "Bloqueo temporal").data["efectos"]
        self.assertEqual(efectos, r.data["conteos"])

        r = self.impacto(self.beneficiario, "bloqueo_permanente")
        self.assertEqual(r.data["conteos"]["solicitudes_canceladas"], 1)  # la Aprobada que siguió
        self.assertEqual(self.sancionar(self.beneficiario, "Bloqueo permanente").data["efectos"], r.data["conteos"])

    def test_coincide_con_lo_que_pasa_donante(self):
        self.crear_en_bd()
        self.crear_solicitud(self.d1, [(self.arroz, "1")])
        r = self.impacto(self.donante, "bloqueo_permanente")
        self.assertIn("1 donación(es) pendiente(s) se rechazarán.", r.data["resumen"])
        self.assertIn("3 donación(es) pasarán a Finalizada y 4 alimento(s) se retirarán.", r.data["resumen"])
        efectos = self.sancionar(self.donante, "Bloqueo permanente").data["efectos"]
        self.assertEqual(efectos, r.data["conteos"])

    def test_inactivar_cuenta_lo_mismo_que_el_permanente(self):
        self.en_curso_del_beneficiario()
        a = self.impacto(self.beneficiario, "inactivar").data["conteos"]
        b = self.impacto(self.beneficiario, "bloqueo_permanente").data["conteos"]
        self.assertEqual(a, b)

    def test_advertencia_y_sin_nada_en_curso(self):
        self.en_curso_del_beneficiario()
        r = self.impacto(self.beneficiario, "advertencia")
        self.assertEqual(r.data["resumen"], ["No afecta nada en curso: solo queda registrada."])
        r = self.impacto(self.otro_donante, "bloqueo_permanente")
        self.assertEqual(r.data["resumen"], ["No tiene nada en curso que se vea afectado."])

    def test_validaciones(self):
        r = self.cliente_admin.get(f"/api/admin/usuarios/{self.beneficiario.pk}/impacto/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Indica la acción", str(r.data["accion"]))
        self.assertEqual(self.impacto(self.beneficiario, "expulsar").status_code, 400)
        self.assertEqual(self.cb.get(f"/api/admin/usuarios/{self.beneficiario.pk}/impacto/",
                                     {"accion": "advertencia"}).status_code, 403)
        self.inactivar(self.beneficiario)
        r = self.impacto(self.beneficiario, "inactivar")
        self.assertIn("ya está inactivo", str(r.data["accion"]))


# =====================================================================
# Casos límite y reglas de pasos anteriores (RN-03, RN-75, RN-77)
# =====================================================================
class EstadoYVencimientoTests(BaseSancionTest):
    def login(self, usuario):
        cache.clear()
        return self.client.post("/api/auth/login/", {"correo": usuario.correo, "contrasena": CLAVE},
                                content_type="application/json")

    def vencer_bloqueo(self, usuario):
        pasado = timezone.now() - timedelta(minutes=1)
        Sancion.objects.filter(usuario=usuario, tipo="Bloqueo temporal").update(fecha_fin=pasado)
        Usuario.objects.filter(pk=usuario.pk).update(bloqueado_hasta=pasado)

    def test_caso_limite_sancionar_a_un_inactivo(self):
        self.inactivar(self.beneficiario)
        r = self.impacto(self.beneficiario, "bloqueo_temporal")
        self.assertIn("inactivo", r.data["aviso"])
        self.assertEqual(r.data["resumen"], ["No afecta nada en curso: solo queda registrada."])
        self.assertEqual(self.sancionar(self.beneficiario, "Bloqueo temporal").status_code, 201)
        self.assertEqual(self.estado(self.beneficiario), "Inactivo")
        self.assertTrue(self.notificaciones("sancion", self.beneficiario).exists())
        # RN-75: al reactivarlo entra Bloqueado mientras el bloqueo siga vigente.
        self.cliente_admin.post(f"/api/admin/usuarios/{self.beneficiario.pk}/cambiar-estado/",
                                {"estado": "Activo"}, format="json")
        self.beneficiario.refresh_from_db()
        self.assertEqual(self.beneficiario.estado, "Bloqueado")
        self.assertEqual(self.beneficiario.bloqueado_hasta, Sancion.objects.get().fecha_fin)

    def test_rn03_login_con_bloqueo_real_y_al_vencer(self):
        self.sancionar(self.beneficiario, "Bloqueo temporal")
        r = self.login(self.beneficiario)
        self.assertEqual(r.status_code, 403)
        self.assertIn("bloqueada hasta el", str(r.data["detail"]))
        self.vencer_bloqueo(self.beneficiario)
        self.assertEqual(self.login(self.beneficiario).status_code, 200)
        self.assertEqual(self.estado(self.beneficiario), "Activo")
        self.assertFalse(Sancion.objects.get().vigente)

    def test_rn03_permanente_no_entra(self):
        self.sancionar(self.beneficiario, "Bloqueo permanente")
        r = self.login(self.beneficiario)
        self.assertEqual(r.status_code, 403)
        self.assertIn("permanente", str(r.data["detail"]))

    def test_rn77_al_vencer_el_bloqueo_los_alimentos_vuelven(self):
        self.sancionar(self.donante, "Bloqueo temporal")
        self.assertEqual(self.visibles(), set())
        self.vencer_bloqueo(self.donante)
        # Lo levanta cualquier petición del donante (permiso EsUsuarioActivo) o su login.
        self.assertEqual(self.cliente.get("/api/donaciones/").status_code, 200)
        self.assertEqual(self.estado(self.donante), "Activo")
        self.assertEqual(len(self.visibles()), 4)

    def test_vencido_se_levanta_antes_de_una_nueva_sancion(self):
        self.sancionar(self.beneficiario, "Bloqueo temporal")
        self.vencer_bloqueo(self.beneficiario)
        r = self.sancionar(self.beneficiario, "Bloqueo permanente", motivo="Reincidió")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.estado(self.beneficiario), "Bloqueado")


# =====================================================================
# Paso 11: el comando levanta los bloqueos vencidos (RN-50, RN-03, RN-77)
# =====================================================================
class LevantarBloqueosVencidosTests(BaseSancionTest):
    def comando(self):
        salida = StringIO()
        call_command("procesar_vencimientos", stdout=salida, stderr=StringIO())
        return salida.getvalue()

    def vencer_bloqueo(self, usuario):
        pasado = timezone.now() - timedelta(minutes=1)
        Sancion.objects.filter(usuario=usuario, tipo="Bloqueo temporal").update(fecha_fin=pasado)
        Usuario.objects.filter(pk=usuario.pk).update(bloqueado_hasta=pasado)

    def test_bloqueado_vencido_pasa_a_activo_aunque_no_entre(self):
        self.sancionar(self.donante, "Bloqueo temporal")
        self.vencer_bloqueo(self.donante)
        self.assertEqual(self.visibles(), set())
        salida = self.comando()
        self.assertIn("Usuarios desbloqueados (RN-03, RN-77): 1", salida)
        self.donante.refresh_from_db()
        self.assertEqual((self.donante.estado, self.donante.bloqueado_hasta), ("Activo", None))
        self.assertFalse(Sancion.objects.get().vigente)
        self.assertEqual(len(self.visibles()), 4)  # RN-77
        self.assertIn("Usuarios desbloqueados (RN-03, RN-77): 0", self.comando())  # sin efectos dobles

    def test_inactivo_solo_cierra_la_sancion(self):
        self.sancionar(self.beneficiario, "Bloqueo temporal")
        self.inactivar(self.beneficiario)
        self.vencer_bloqueo(self.beneficiario)
        salida = self.comando()
        self.assertIn("cerradas sin cambiar el estado (p. ej. inactivos): 1", salida)
        self.assertEqual(self.estado(self.beneficiario), "Inactivo")
        self.assertFalse(Sancion.objects.get().vigente)

    def test_no_toca_bloqueos_vigentes_ni_permanentes(self):
        self.sancionar(self.beneficiario, "Bloqueo temporal")
        self.sancionar(self.donante, "Bloqueo permanente")
        self.assertIn("Usuarios desbloqueados (RN-03, RN-77): 0", self.comando())
        self.assertEqual((self.estado(self.beneficiario), self.estado(self.donante)), ("Bloqueado", "Bloqueado"))
        self.assertEqual(Sancion.objects.filter(vigente=True).count(), 2)
