# Kumora — Backend

**Kumora** es una **Red de Alimentos Solidarios**. Personas o negocios donan alimentos, los beneficiarios los solicitan y un administrador valida, gestiona y controla todo el proceso.

Este repositorio es **solo el backend**: una API REST con Django REST Framework y MySQL en Aiven. La app móvil (Donante y Beneficiario) y el panel web (Administrador) son proyectos aparte.

Autor: Miguel Angel Gomez

## Roles

| Rol | Dónde | Qué hace |
|---|---|---|
| Donante | App móvil | Registra donaciones con fotos, ve quién pidió cada alimento, registra entregas, reporta novedades y ve sus estadísticas. |
| Beneficiario | App móvil | Ve alimentos disponibles, usa el carrito, envía y cancela solicitudes, ve sus solicitudes y un resumen de inicio. |
| Administrador | Panel web | Valida donaciones, gestiona solicitudes y entregas, revisa novedades, sanciona, administra categorías, usuarios y configuración, y ve el dashboard. |

Las reglas de negocio (RN-01 a RN-77) están en [CLAUDE.md](CLAUDE.md), y las decisiones de diseño paso a paso, en [docs/decisiones.md](docs/decisiones.md).

## Tecnologías

- Python 3.14, Django 5.2 (LTS), Django REST Framework.
- Login con JWT (`djangorestframework-simplejwt`, con lista negra de tokens).
- MySQL en **Aiven** (con SSL), mediante `mysqlclient`.
- Fotos en **Cloudinary** (`django-cloudinary-storage`), validadas con Pillow.
- `django-filter`, `django-cors-headers`, `python-dotenv`.
- `drf-spectacular` para la documentación Swagger / OpenAPI.

## Requisitos

- Windows 10/11 con **Python 3.14** (marca "Add Python to PATH" al instalarlo).
- Un servicio **MySQL en Aiven** con una base llamada `kumora` (se crea en la consola de Aiven, sección *Databases*) y su certificado `ca.pem`.
- Una cuenta de **Cloudinary**, para las fotos.
- Opcional: una cuenta de Gmail con **contraseña de aplicación**, para enviar los códigos de recuperación. Sin ella, los correos se muestran en la consola.

## Instalación en Windows

En PowerShell, dentro de la carpeta del proyecto:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # luego edita .env con tus valores
python manage.py migrate    # crea las tablas, los roles, la configuración y las categorías
python manage.py createsuperuser   # primer Administrador (pide nombres, documento y teléfono)
```

Si `mysqlclient` no se instala, actualiza pip (`python -m pip install --upgrade pip`) y vuelve a intentarlo.

> El plan gratis de Aiven apaga el servicio por inactividad. Si la conexión falla, enciéndelo desde la consola de Aiven.

## Variables del `.env`

El archivo `.env` está en `.gitignore`: **nunca se sube** al repositorio. La plantilla es [.env.example](.env.example).

| Variable | Para qué |
|---|---|
| `SECRET_KEY` | Clave secreta de Django: larga y aleatoria. |
| `DEBUG` | `True` en desarrollo (activa Swagger y `cargar_demo`); `False` en producción. |
| `ALLOWED_HOSTS` | Hosts permitidos, separados por coma (`10.0.2.2` es el emulador de Android). |
| `CORS_ALLOWED_ORIGINS` | Orígenes del panel web, separados por coma. |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Conexión a MySQL en Aiven. |
| `DB_SSL_CA` | Ruta al `ca.pem` de Aiven (relativa a la raíz del proyecto o absoluta). |
| `CLOUDINARY_URL` | `cloudinary://<api_key>:<api_secret>@<cloud_name>` |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Correo SMTP con TLS. Con Gmail se usa una contraseña de aplicación. Si `EMAIL_HOST_USER` está vacío, los correos salen por consola. |

## Correr el servidor

```powershell
venv\Scripts\activate
python manage.py runserver
```

- API: `http://127.0.0.1:8000/api/`
- **Swagger**: `http://127.0.0.1:8000/api/docs/` (solo con `DEBUG=True`). Para probar las rutas protegidas, inicia sesión en `POST /api/auth/login/`, copia el `access` y pégalo en el botón **Authorize**.
- El contrato completo está exportado en [docs/api_schema.yaml](docs/api_schema.yaml). Para regenerarlo: `python manage.py spectacular --file docs/api_schema.yaml`.

## Pruebas

Las pruebas corren sobre una base temporal (`test_kumora`) que Django crea en el mismo servidor de Aiven. Las fotos van a un almacenamiento en memoria: no se sube nada a Cloudinary.

```powershell
python manage.py test                     # suite completa
python manage.py test apps.solicitudes    # solo una app
python manage.py test --keepdb            # reutiliza la base de pruebas (más rápido)
```

La suite completa tarda unos 30 minutos por la latencia con Aiven. La tabla que relaciona cada regla con sus pruebas está en [docs/cobertura_pruebas.md](docs/cobertura_pruebas.md).

## Datos de demostración (`cargar_demo`)

Para la sustentación. **Solo funciona con `DEBUG=True`** y necesita un Administrador ya creado (con `createsuperuser`).

```powershell
python manage.py cargar_demo            # carga los datos (si ya están, no duplica nada)
python manage.py cargar_demo --borrar   # elimina todo lo de @demo.kumora, incluidas sus fotos en Cloudinary
```

Todos los usuarios demo usan la contraseña **`Demo.Kumora2026`**:

| Correo | Rol |
|---|---|
| `ana.donante@demo.kumora` | Donante (Persona) |
| `tienda.esquina@demo.kumora` | Donante (Negocio: Tienda La Esquina Solidaria) |
| `laura.beneficiaria@demo.kumora` | Beneficiario |
| `jorge.beneficiario@demo.kumora` | Beneficiario |
| `maria.beneficiaria@demo.kumora` | Beneficiario (tiene una advertencia) |

El Administrador es el que ya existe; su contraseña no cambia.

Qué se carga:
- Donaciones en todos los estados (Pendiente, Validada, Agotada, Finalizada y Rechazada).
- Solicitudes en todos los estados (Pendiente, Aprobada, Entregada, No recogida, Cancelada y Rechazada).
- Una novedad pendiente, una sanción (advertencia) y un carrito con un alimento.
- Fechas repartidas en los últimos meses, para que el dashboard tenga datos.

Todo se crea con las mismas funciones que usa la API, así que respeta las reglas de negocio y envía las notificaciones. Las fotos son imágenes simples generadas con Pillow y se suben a Cloudinary.

## Vencimientos (`procesar_vencimientos`)

```powershell
python manage.py procesar_vencimientos
```

Aplica sobre todos los registros:
- RN-27: solicitud Aprobada vencida → No recogida.
- RN-28: solicitud Pendiente vencida → Cancelada.
- Levanta los bloqueos temporales vencidos (RN-03, RN-77).
- Recalcula el estado de las donaciones (RN-16).

Al final imprime un resumen con los conteos. Cada registro se procesa en su propia transacción y el comando se puede ejecutar varias veces sin efectos dobles.

Aunque no esté programado, los listados, el dashboard y las acciones (aprobar, rechazar, entregar y enviar el carrito) aplican los vencimientos de los registros que tocan. Aun así, en producción conviene programarlo **cada hora**:

**Windows (Programador de tareas)**, en una consola como administrador:

```powershell
schtasks /Create /SC HOURLY /TN "Kumora - procesar vencimientos" /TR "cmd /c cd /d C:\ruta\kuska-backend && venv\Scripts\python.exe manage.py procesar_vencimientos >> logs\vencimientos.log 2>&1"
```

Crea antes la carpeta `logs`. Para correr la tarea a mano: `schtasks /Run /TN "Kumora - procesar vencimientos"`.

**Linux (cron)**, con `crontab -e`:

```
0 * * * * cd /ruta/kuska-backend && venv/bin/python manage.py procesar_vencimientos >> logs/vencimientos.log 2>&1
```

## Estructura

```
config/                 Proyecto: settings (todo sale del .env), urls y openapi.py (errores y ejemplos de Swagger)
apps/
  usuarios/             Rol, Usuario (personalizado, login por correo), RecuperacionContrasena.
                        Registro, login JWT, perfil, recuperar contraseña, permisos y administración de usuarios.
  catalogo/             Categoria y Configuracion (fila única con plazos y límites).
  donaciones/           Donacion, DonacionAlimento (con foto) y Novedad. Donaciones, validación,
                        alimentos disponibles, estado de la donación (RN-16) y novedades.
  solicitudes/          CarritoItem, Solicitud, DetalleSolicitud y Entrega. Carrito, envío, aprobación,
                        entregas y vencimientos. Comandos: procesar_vencimientos y cargar_demo.
  sanciones/            Sancion. Escala de sanciones, bloqueos con efectos en cascada e impacto previo.
  notificaciones/       Notificacion. Avisos internos y endpoints para leerlos.
  dashboard/            Sin modelos: estadísticas del administrador y del donante, e inicio del beneficiario.
docs/
  kumora_bd.sql         Diseño de la base (referencia; las tablas las crean las migraciones).
  decisiones.md         Decisiones de cada paso, para la sustentación.
  cobertura_pruebas.md  Cada regla de negocio y las pruebas que la cubren.
  api_schema.yaml       Contrato OpenAPI exportado.
```

Cada app separa sus capas así:
- `models.py`: las tablas, con los mismos nombres del script SQL.
- `services.py`: **toda la lógica de negocio**.
- `serializers.py`: el formato de entrada y salida.
- `views.py`: vistas delgadas que validan el formato, llaman al servicio y responden.
- `tests.py`: las pruebas.

## Antes de un despliegue real

`python manage.py check --deploy` muestra advertencias que son normales en desarrollo:
- HTTPS: `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS`, `SESSION_COOKIE_SECURE` y `CSRF_COOKIE_SECURE`.
- `DEBUG=True`.

Qué hay que hacer en producción está explicado en [docs/decisiones.md](docs/decisiones.md) (Paso 13).
