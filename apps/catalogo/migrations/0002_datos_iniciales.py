from django.db import migrations

CATEGORIAS = [
    ("Granos", "Arroz, frijol, lenteja, maíz"),
    ("Lácteos", "Leche, queso, yogur"),
    ("Frutas y verduras", "Productos frescos"),
    ("Carnes", "Res, pollo, cerdo, pescado"),
    ("Panadería", "Pan, galletas, harinas"),
    ("Enlatados", "Atún, sardinas, conservas"),
    ("Bebidas", "Jugos, agua, bebidas no alcohólicas"),
    ("Otros", "Aceite, panela, sal, azúcar"),
]


def crear_datos(apps, schema_editor):
    Configuracion = apps.get_model("catalogo", "Configuracion")
    Categoria = apps.get_model("catalogo", "Categoria")

    # Fila única con los valores por defecto del modelo (3, 1, 10, 30, 2).
    Configuracion.objects.get_or_create(id=1)

    for nombre, descripcion in CATEGORIAS:
        Categoria.objects.get_or_create(nombre=nombre, defaults={"descripcion": descripcion})


def borrar_datos(apps, schema_editor):
    Configuracion = apps.get_model("catalogo", "Configuracion")
    Categoria = apps.get_model("catalogo", "Categoria")
    Categoria.objects.filter(nombre__in=[nombre for nombre, _ in CATEGORIAS]).delete()
    Configuracion.objects.filter(id=1).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalogo", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_datos, borrar_datos),
    ]
