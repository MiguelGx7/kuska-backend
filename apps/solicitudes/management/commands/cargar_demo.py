"""
python manage.py cargar_demo [--borrar]

Datos de demostración para la sustentación (solo con DEBUG = True).

Todo se crea con las mismas funciones de services.py que usa la API, para
que respete las reglas de negocio (no hay inserts directos). Solo las
fechas se mueven al pasado después de crear, porque los campos de fecha
usan auto_now_add y el dashboard necesita datos repartidos en varios meses.

- Si ya hay datos @demo.kumora, no crea nada (no duplica).
- --borrar elimina todo lo de @demo.kumora, incluidas sus fotos en Cloudinary.
"""

import io
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, F, Q, Value
from django.db.models.functions import Replace
from django.utils import timezone
from PIL import Image, ImageDraw

from apps.catalogo.models import Categoria
from apps.donaciones import services as donaciones
from apps.donaciones.models import Donacion, DonacionAlimento, Novedad
from apps.donaciones.serializers import DonacionEntradaSerializer
from apps.notificaciones.models import Notificacion
from apps.sanciones import services as sanciones
from apps.sanciones.models import Sancion
from apps.solicitudes import services as solicitudes
from apps.solicitudes.models import CarritoItem, Entrega, Solicitud
from apps.usuarios import services as usuarios
from apps.usuarios.models import CodigoVerificacion, Rol, Usuario
from apps.usuarios.serializers import DatosRegistroSerializer

DOMINIO = "@demo.kumora"
CONTRASENA = "Demo.Kumora2026"

USUARIOS = {
    "ana": {
        "correo": f"ana.donante{DOMINIO}", "rol": Rol.DONANTE, "nombres": "Ana María",
        "apellidos": "Rojas", "numero_documento": "990000001", "telefono": "3001110001",
        "direccion": "Calle 45 # 12-30, Bogotá", "tipo_donante": "Persona",
    },
    "tienda": {
        "correo": f"tienda.esquina{DOMINIO}", "rol": Rol.DONANTE, "nombres": "Carlos",
        "apellidos": "Pérez", "numero_documento": "990000002", "telefono": "3001110002",
        "direccion": "Carrera 7 # 60-15, Bogotá", "tipo_donante": "Negocio",
        "nombre_negocio": "Tienda La Esquina Solidaria", "nit": "990000002-1",
    },
    "laura": {
        "correo": f"laura.beneficiaria{DOMINIO}", "rol": Rol.BENEFICIARIO, "nombres": "Laura",
        "apellidos": "Gómez", "numero_documento": "990000003", "telefono": "3001110003",
        "direccion": "Calle 1 Sur # 5-20, Bogotá",
    },
    "jorge": {
        "correo": f"jorge.beneficiario{DOMINIO}", "rol": Rol.BENEFICIARIO, "nombres": "Jorge",
        "apellidos": "Martínez", "numero_documento": "990000004", "telefono": "3001110004",
        "direccion": "Carrera 30 # 22-10, Bogotá",
    },
    "maria": {
        "correo": f"maria.beneficiaria{DOMINIO}", "rol": Rol.BENEFICIARIO, "nombres": "María",
        "apellidos": "López", "numero_documento": "990000005", "telefono": "3001110005",
        "direccion": "Avenida 68 # 40-50, Bogotá",
    },
}

COLORES = ["#E07A5F", "#81B29A", "#F2CC8F", "#3D405B", "#6D597A", "#B56576", "#2A9D8F", "#E9C46A"]


def _foto(nombre, indice):
    """Imagen simple (PNG) con el nombre del alimento, generada con Pillow."""
    imagen = Image.new("RGB", (480, 360), COLORES[indice % len(COLORES)])
    dibujo = ImageDraw.Draw(imagen)
    dibujo.rectangle((20, 20, 460, 340), outline="white", width=6)
    dibujo.text((40, 160), f"{nombre}\nKumora (demo)", fill="white", font_size=36)
    contenido = io.BytesIO()
    imagen.save(contenido, "PNG")
    return SimpleUploadedFile(f"{nombre}.png", contenido.getvalue(), content_type="image/png")


def hace(dias, horas=0):
    return timezone.now() - timedelta(days=dias, hours=horas)


class Command(BaseCommand):
    help = "Carga (o con --borrar elimina) los datos de demostración @demo.kumora. Solo con DEBUG = True."

    def add_arguments(self, parser):
        parser.add_argument(
            "--borrar", action="store_true", help="Elimina todo lo de @demo.kumora, incluidas sus fotos."
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("cargar_demo solo funciona con DEBUG = True (nunca en producción).")
        if options["borrar"]:
            self.borrar()
            return
        if Usuario.objects.filter(correo__iendswith=DOMINIO).exists():
            self.stdout.write(self.style.WARNING(
                "Los datos de demostración ya están cargados; no se crea nada para no duplicarlos. "
                "Para recargarlos: python manage.py cargar_demo --borrar y luego cargar_demo."
            ))
            return
        self.admin = (
            Usuario.objects.filter(rol__nombre=Rol.ADMINISTRADOR, estado=Usuario.Estado.ACTIVO)
            .order_by("id")
            .first()
        )
        if self.admin is None:
            raise CommandError("No hay un Administrador activo. Créalo antes con: python manage.py createsuperuser")

        self.fotos = []  # rutas subidas, por si hay que deshacer
        try:
            with transaction.atomic():
                self.cargar()
        except Exception:
            # La base se deshizo; las fotos ya subidas a Cloudinary se borran a mano.
            donaciones._borrar_fotos(self.fotos)
            raise
        self.resumen()

    # -----------------------------------------------------------------
    # Carga
    # -----------------------------------------------------------------
    def registrar(self, datos):
        # Mismas validaciones del registro, pero sin código de correo (no pasa por el registro público).
        serializer = DatosRegistroSerializer(data={**datos, "contrasena": CONTRASENA, "tipo_documento": "CC"})
        serializer.is_valid(raise_exception=True)
        return usuarios.crear_usuario(serializer.validated_data)

    def donar(self, donante, titulo, modalidad, alimentos, descripcion=None):
        """alimentos: (nombre, categoria, unidad, cantidad, dias para vencer)."""
        categorias = dict(Categoria.objects.values_list("nombre", "id"))
        datos = {
            "titulo": titulo,
            "descripcion": descripcion,
            "modalidad_entrega": modalidad,
            "direccion_recogida": None if modalidad == "Domicilio" else donante.direccion,
            "alimentos": [
                {
                    "categoria": categorias[categoria], "nombre": nombre, "unidad_medida": unidad,
                    "cantidad_total": cantidad,
                    "fecha_vencimiento": (timezone.localdate() + timedelta(days=dias)).isoformat(),
                }
                for nombre, categoria, unidad, cantidad, dias in alimentos
            ],
        }
        serializer = DonacionEntradaSerializer(data=datos)
        serializer.is_valid(raise_exception=True)
        fotos = {i: _foto(a[0], len(self.fotos) + i) for i, a in enumerate(alimentos)}
        donacion = donaciones.crear_donacion(donante, serializer.validated_data, fotos)
        self.fotos += [a.foto.name for a in donacion.alimentos.all()]
        return donacion

    def alimento(self, donacion, nombre):
        return donacion.alimentos.get(nombre=nombre)

    def pedir(self, beneficiario, items, elecciones=None):
        """Agrega al carrito y lo envía (RN-20, RN-22). Devuelve la solicitud creada."""
        for alimento, cantidad in items:
            solicitudes.agregar_al_carrito(beneficiario, alimento.pk, Decimal(cantidad))
        _, creadas = solicitudes.enviar_carrito(beneficiario, elecciones or {})
        return creadas[0]

    def cargar(self):
        u = {clave: self.registrar(datos) for clave, datos in USUARIOS.items()}
        ana, tienda, laura, jorge, maria = (u[k] for k in ("ana", "tienda", "laura", "jorge", "maria"))
        admin = self.admin

        # --- Donaciones (RN-10) ---
        pan = self.donar(ana, "Pan y galletas", "Recoger", [
            ("Pan tajado", "Panadería", "paquete", "2", 60),
        ])
        mercado = self.donar(ana, "Mercado de granos", "Ambas", [
            ("Arroz", "Granos", "kg", "10", 180),
            ("Frijol", "Granos", "kg", "8", 180),
            ("Aceite", "Otros", "L", "5", 240),
        ], descripcion="Sobrantes de un mercado familiar, en buen estado.")
        varios = self.donar(ana, "Productos varios", "Recoger", [
            ("Detergente", "Otros", "unidad", "3", 365),
        ])
        leche = self.donar(tienda, "Leche entera", "Domicilio", [
            ("Leche entera", "Lácteos", "unidad", "3", 25),
        ])
        frutas = self.donar(tienda, "Frutas y verduras de la semana", "Recoger", [
            ("Bananos", "Frutas y verduras", "kg", "6", 10),
            ("Tomates", "Frutas y verduras", "kg", "4", 9),
            ("Yogur", "Lácteos", "unidad", "6", 15),
        ])
        enlatados = self.donar(tienda, "Enlatados", "Recoger", [
            ("Atún en lata", "Enlatados", "unidad", "12", 365),
            ("Sardinas", "Enlatados", "unidad", "10", 365),
        ])

        # --- Revisión del administrador (RN-13) ---
        for donacion in (pan, mercado, leche, frutas):
            donaciones.validar_donacion(admin, donacion.pk)
        donaciones.rechazar_donacion(admin, varios.pk, "El detergente no es un alimento.")
        # «enlatados» queda Pendiente.

        # --- Finalizada: se entrega todo el pan (RN-24, RN-26, RN-16) ---
        pan_tajado = self.alimento(pan, "Pan tajado")
        s_pan_laura = self.pedir(laura, [(pan_tajado, "1")])
        s_pan_jorge = self.pedir(jorge, [(pan_tajado, "1")])
        for s in (s_pan_laura, s_pan_jorge):
            solicitudes.aprobar_solicitud(admin, s.pk)
        solicitudes.registrar_entrega(admin, s_pan_laura.pk, "Entregado completo.")
        solicitudes.registrar_entrega(ana, s_pan_jorge.pk, "Lo recogió en la portería.", solo_donante=True)

        # --- Mercado de granos: rechazada, cancelada, no recogida y entregada ---
        arroz, frijol, aceite = (self.alimento(mercado, n) for n in ("Arroz", "Frijol", "Aceite"))
        recoger = {mercado.pk: {"modalidad_entrega": "Recoger"}}
        s_rechazada = self.pedir(laura, [(arroz, "3"), (frijol, "2")], recoger)
        solicitudes.rechazar_solicitud(admin, s_rechazada.pk, "Por ahora se prioriza a familias sin entregas recientes.")
        s_cancelada = self.pedir(jorge, [(aceite, "1")], {mercado.pk: {"modalidad_entrega": "Domicilio"}})
        solicitudes.cancelar_solicitud(jorge, s_cancelada.pk)
        s_no_recogida = self.pedir(maria, [(arroz, "2")], recoger)
        solicitudes.aprobar_solicitud(admin, s_no_recogida.pk)
        Solicitud.objects.filter(pk=s_no_recogida.pk).update(fecha_limite_recogida=hace(0, 1))
        solicitudes.aplicar_vencimientos(s_no_recogida.pk)  # RN-27: pasa a No recogida
        s_entregada = self.pedir(laura, [(arroz, "2")], recoger)
        solicitudes.aprobar_solicitud(admin, s_entregada.pk)
        solicitudes.registrar_entrega(admin, s_entregada.pk)

        # --- Sanción por la no recogida (RN-05, RN-07): advertencia, no bloquea ---
        sancion = sanciones.crear_sancion(
            admin, maria.pk, "Advertencia",
            "No recogió su solicitud dentro del plazo (modalidad Recoger).",
            solicitud_id=s_no_recogida.pk,
        )

        # --- Agotada: toda la leche queda reservada (una Aprobada y dos Pendiente) ---
        leche_entera = self.alimento(leche, "Leche entera")
        s_leche_laura = self.pedir(laura, [(leche_entera, "1")])
        solicitudes.aprobar_solicitud(admin, s_leche_laura.pk)
        self.pedir(jorge, [(leche_entera, "1")])
        self.pedir(maria, [(leche_entera, "1")])

        # --- Solicitudes en curso, novedad pendiente y un carrito con algo ---
        s_frijol_jorge = self.pedir(jorge, [(frijol, "2")], recoger)
        solicitudes.aprobar_solicitud(admin, s_frijol_jorge.pk)
        self.pedir(laura, [(self.alimento(frutas, "Bananos"), "1.5")])
        donaciones.crear_novedad(
            tienda, frutas.pk, self.alimento(frutas, "Tomates").pk,
            "Los tomates se maduraron más rápido de lo esperado.",
        )
        solicitudes.agregar_al_carrito(maria, self.alimento(frutas, "Yogur").pk, Decimal("1"))

        # --- Fechas repartidas en los últimos meses (para el dashboard) ---
        self.mover_donacion(pan, 125)
        self.mover_solicitud(s_pan_laura, 123, entrega=121)
        self.mover_solicitud(s_pan_jorge, 122, entrega=120)
        self.mover_donacion(varios, 95)
        self.mover_donacion(mercado, 70)
        self.mover_solicitud(s_rechazada, 68)
        self.mover_solicitud(s_cancelada, 66)
        self.mover_solicitud(s_no_recogida, 64, limite=61)
        Sancion.objects.filter(pk=sancion.pk).update(fecha_inicio=hace(60))
        self.mover_solicitud(s_entregada, 40, entrega=38)
        self.mover_donacion(leche, 20)
        self.mover_donacion(frutas, 3)

    # -----------------------------------------------------------------
    # Fechas
    # -----------------------------------------------------------------
    def mover_donacion(self, donacion, dias):
        Donacion.objects.filter(pk=donacion.pk).update(
            fecha_registro=hace(dias), fecha_revision=hace(dias - 1, 0)
        )

    def mover_solicitud(self, solicitud, dias, entrega=None, limite=None):
        """
        Mueve una solicitud ya terminada al pasado y ajusta su código al nuevo día
        (KUM-AAAAMMDD-9NNN: el 9 evita chocar con los códigos reales de ese día).
        También corrige el código en las notificaciones que lo mencionan.
        """
        solicitud.refresh_from_db()
        fecha = hace(dias)
        nuevo = f"KUM-{timezone.localtime(fecha):%Y%m%d}-9{solicitud.pk % 1000:03d}"
        cambios = {"fecha_solicitud": fecha, "codigo_pedido": nuevo}
        if solicitud.fecha_gestion:
            cambios["fecha_gestion"] = hace(dias - 1)
        if solicitud.fecha_limite_recogida:
            cambios["fecha_limite_recogida"] = hace(limite if limite is not None else dias - 4)
        Solicitud.objects.filter(pk=solicitud.pk).update(**cambios)
        Notificacion.objects.filter(mensaje__contains=solicitud.codigo_pedido).update(
            mensaje=Replace(F("mensaje"), Value(solicitud.codigo_pedido), Value(nuevo))
        )
        if entrega is not None:
            Entrega.objects.filter(solicitud=solicitud).update(fecha_entrega=hace(entrega))

    # -----------------------------------------------------------------
    # Borrar
    # -----------------------------------------------------------------
    def borrar(self):
        with transaction.atomic():
            demo = Usuario.objects.filter(correo__iendswith=DOMINIO)
            if not demo.exists():
                self.stdout.write("No hay datos de demostración para borrar.")
                return
            donaciones_demo = Donacion.objects.filter(donante__in=demo)
            solicitudes_demo = Solicitud.objects.filter(Q(beneficiario__in=demo) | Q(donacion__in=donaciones_demo))
            codigos = set(solicitudes_demo.values_list("codigo_pedido", flat=True))
            fotos = list(
                DonacionAlimento.objects.filter(donacion__in=donaciones_demo).values_list("foto", flat=True)
            )

            # Notificaciones de los usuarios demo y las de los administradores sobre lo demo.
            avisos = Q(usuario__in=demo) | Q(donacion__in=donaciones_demo) | Q(solicitud__in=solicitudes_demo)
            for codigo in codigos:
                avisos |= Q(tipo=Notificacion.Tipo.NUEVA_SOLICITUD, mensaje__contains=codigo)
            conteos = {"notificaciones": Notificacion.objects.filter(avisos).delete()[0]}
            conteos["sanciones"] = Sancion.objects.filter(usuario__in=demo).delete()[0]
            Entrega.objects.filter(solicitud__in=solicitudes_demo).delete()
            conteos["solicitudes"] = Solicitud.objects.filter(
                pk__in=list(solicitudes_demo.values_list("pk", flat=True))
            ).delete()[1].get("solicitudes.Solicitud", 0)
            CarritoItem.objects.filter(Q(beneficiario__in=demo) | Q(donacion_alimento__donacion__in=donaciones_demo)).delete()
            Novedad.objects.filter(donante__in=demo).delete()
            conteos["donaciones"] = Donacion.objects.filter(
                pk__in=list(donaciones_demo.values_list("pk", flat=True))
            ).delete()[1].get("donaciones.Donacion", 0)
            CodigoVerificacion.objects.filter(
                Q(usuario__in=demo) | Q(correo__in=list(demo.values_list("correo", flat=True)))
            ).delete()
            conteos["usuarios"] = Usuario.objects.filter(pk__in=list(demo.values_list("pk", flat=True))).delete()[1].get(
                "usuarios.Usuario", 0
            )
            # Las fotos se borran de Cloudinary solo si la base confirmó el borrado.
            donaciones._borrar_fotos_al_confirmar(fotos)

        self.stdout.write(self.style.SUCCESS(
            "Datos de demostración eliminados: "
            f"{conteos['usuarios']} usuarios, {conteos['donaciones']} donaciones ({len(fotos)} fotos), "
            f"{conteos['solicitudes']} solicitudes, {conteos['sanciones']} sanciones y "
            f"{conteos['notificaciones']} notificaciones."
        ))

    # -----------------------------------------------------------------
    # Resumen
    # -----------------------------------------------------------------
    def resumen(self):
        demo = Usuario.objects.filter(correo__iendswith=DOMINIO)
        donaciones_demo = Donacion.objects.filter(donante__in=demo)
        def por_estado(qs):
            filas = qs.values("estado").annotate(total=Count("id")).order_by("estado")
            return ", ".join(f"{f['estado']}: {f['total']}" for f in filas)

        lineas = [
            self.style.SUCCESS("Datos de demostración cargados."),
            f"Contraseña de todos los usuarios demo: {CONTRASENA}",
            *[f"  {d['rol']:<12} {d['correo']}" for d in USUARIOS.values()],
            f"  {'Administrador':<12} {self.admin.correo} (el existente; su contraseña no cambia)",
            f"Donaciones  -> {por_estado(donaciones_demo)}",
            f"Solicitudes -> {por_estado(Solicitud.objects.filter(beneficiario__in=demo))}",
            f"Novedades pendientes: {Novedad.objects.filter(donante__in=demo, estado='Pendiente').count()}  "
            f"Sanciones: {Sancion.objects.filter(usuario__in=demo).count()}",
        ]
        self.stdout.write("\n".join(lineas))
