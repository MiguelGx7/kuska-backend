"""
Paso 16 (RN-04d): recuperacion_contrasena se generaliza como codigo_verificacion.

Se renombra (no se borra) para conservar los registros existentes, que quedan
con proposito "Recuperar contraseña" y el correo de su usuario.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def completar_correo(apps, schema_editor):
    CodigoVerificacion = apps.get_model("usuarios", "CodigoVerificacion")
    for codigo in CodigoVerificacion.objects.select_related("usuario").filter(correo=""):
        codigo.correo = codigo.usuario.correo
        codigo.save(update_fields=["correo"])


class Migration(migrations.Migration):

    dependencies = [
        ("usuarios", "0002_roles_iniciales"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="recuperacioncontrasena", name="chk_recuperacion_intentos"),
        migrations.RenameModel(old_name="RecuperacionContrasena", new_name="CodigoVerificacion"),
        migrations.AlterModelTable(name="codigoverificacion", table="codigo_verificacion"),
        migrations.RenameIndex(
            model_name="codigoverificacion", new_name="idx_codigo_usuario", old_name="idx_recuperacion_usuario"
        ),
        migrations.AlterModelOptions(
            name="codigoverificacion",
            options={"verbose_name": "código de verificación", "verbose_name_plural": "códigos de verificación"},
        ),
        migrations.AlterField(
            model_name="codigoverificacion",
            name="usuario",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="codigos_verificacion",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Los registros que ya existían son de recuperación de contraseña.
        migrations.AddField(
            model_name="codigoverificacion",
            name="proposito",
            field=models.CharField(
                choices=[("Registro", "Registro"), ("Recuperar contraseña", "Recuperar contraseña")],
                default="Recuperar contraseña",
                max_length=25,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="codigoverificacion",
            name="correo",
            field=models.CharField(default="", max_length=120),
            preserve_default=False,
        ),
        migrations.RunPython(completar_correo, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="codigoverificacion",
            constraint=models.CheckConstraint(
                condition=models.Q(("intentos__gte", 0), ("intentos__lte", 5)), name="chk_codigo_intentos"
            ),
        ),
        migrations.AddConstraint(
            model_name="codigoverificacion",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("proposito", "Registro"), ("usuario__isnull", True)),
                    models.Q(("proposito", "Recuperar contraseña"), ("usuario__isnull", False)),
                    _connector="OR",
                ),
                name="chk_codigo_usuario",
                violation_error_message="El código de registro no lleva usuario; el de recuperación sí.",
            ),
        ),
        migrations.AddIndex(
            model_name="codigoverificacion",
            index=models.Index(fields=["correo", "proposito", "usado"], name="idx_codigo_correo"),
        ),
    ]
