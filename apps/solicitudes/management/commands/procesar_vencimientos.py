"""
python manage.py procesar_vencimientos (RN-50).

Aplica, sobre todos los registros:
1. RN-27 (Aprobada vencida -> No recogida) y RN-28 (Pendiente vencida -> Cancelada).
2. Levanta los bloqueos temporales vencidos (RN-03, RN-77).
3. RN-16 en las donaciones Validada o Agotada cuyo estado quedó desactualizado.
4. RN-26b: las entregas sin confirmar después de 48 horas pasan a Asumida.

Cada registro va en su propia transacción: un error en uno no detiene los
demás. Se puede ejecutar varias veces sin efectos dobles: cada función
vuelve a revisar el estado con la fila bloqueada antes de cambiarla.
Programarlo: ver docs/decisiones.md (Paso 11).
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.donaciones.models import Donacion
from apps.donaciones.services import procesar_donaciones_desactualizadas
from apps.sanciones.services import levantar_bloqueos_vencidos
from apps.solicitudes.models import Solicitud
from apps.solicitudes.services import asumir_entregas_vencidas, procesar_solicitudes_vencidas


class Command(BaseCommand):
    help = "Aplica los vencimientos: RN-27, RN-28, bloqueos temporales vencidos y RN-16 (RN-50)."

    def handle(self, *args, **options):
        inicio = timezone.localtime()
        # Primero las solicitudes: devolver cantidades puede cambiar el estado de la donación.
        solicitudes = procesar_solicitudes_vencidas(
            Solicitud.objects.filter(estado__in=Solicitud.ESTADOS_EN_CURSO)
        )
        bloqueos = levantar_bloqueos_vencidos()
        donaciones = procesar_donaciones_desactualizadas(
            Donacion.objects.filter(estado__in=[Donacion.Estado.VALIDADA, Donacion.Estado.AGOTADA])
        )

        # Un solo UPDATE en su transacción: sin efectos por fila, no hace falta ir una por una.
        asumidas = asumir_entregas_vencidas()

        errores = solicitudes["errores"] + bloqueos["errores"] + donaciones["errores"]
        lineas = [
            f"Vencimientos procesados ({inicio:%d/%m/%Y %H:%M}):",
            f"  Solicitudes Aprobada -> No recogida (RN-27): {solicitudes['no_recogidas']}",
            f"  Solicitudes Pendiente -> Cancelada (RN-28): {solicitudes['canceladas']}",
            f"  Usuarios desbloqueados (RN-03, RN-77): {bloqueos['desbloqueados']}",
            f"  Sanciones temporales cerradas sin cambiar el estado (p. ej. inactivos): "
            f"{bloqueos['sanciones_cerradas']}",
            f"  Donaciones -> Validada (RN-16): {donaciones['validadas']}",
            f"  Donaciones -> Agotada (RN-16): {donaciones['agotadas']}",
            f"  Donaciones -> Finalizada (RN-16): {donaciones['finalizadas']}",
            f"  Entregas asumidas como recibidas (RN-26b): {asumidas}",
            f"  Errores: {errores}",
        ]
        self.stdout.write("\n".join(lineas))
        if errores:
            self.stderr.write(
                self.style.WARNING(f"{errores} registro(s) fallaron; el detalle quedó en el log. "
                                   "Los demás sí se procesaron.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("Listo, sin errores."))
