# Despliegue en Railway

Guía paso a paso para publicar el backend de Kumora en [Railway](https://railway.com).

- **Base de datos:** se sigue usando **MySQL en Aiven**; Railway no aloja la base.
- **Fotos:** siguen en **Cloudinary**.
- **Qué hace Railway:** construye el proyecto desde GitHub y lo corre con gunicorn.

## Qué ya está preparado en el código

| Archivo | Para qué |
|---|---|
| `requirements.txt` | Incluye `gunicorn` (servidor de producción) y `whitenoise` (sirve los estáticos del admin y de Swagger). |
| `.python-version` | Fija **Python 3.14**, la misma versión del desarrollo. Railpack (el constructor de Railway) la lee de este archivo. |
| `railpack.json` | Paquetes del sistema para compilar `mysqlclient` (`pkg-config`, `build-essential`, `default-libmysqlclient-dev`) y la librería que necesita al correr (`libmariadb3`). También el **comando de inicio**: `migrate` → `collectstatic` → `gunicorn` en el puerto `$PORT`. |
| `config/settings.py` y `config/certificado.py` | Con `DEBUG=False` activa HTTPS detrás del proxy de Railway, cookies seguras, HSTS y logs en la consola. Lee el certificado de Aiven desde `DB_SSL_CA_BASE64` (o `DB_SSL_CA_CONTENIDO`) y lo escribe en un archivo temporal. |
| `config/urls.py` | Swagger (`/api/docs/`) se muestra en producción solo si `SWAGGER_PUBLICO=True`. |

> **Antes de empezar:** sube todos los cambios a la rama `master` de GitHub. Railway despliega desde el repositorio, no desde tu PC. El `.env` y el `ca.pem` **no** se suben (están en `.gitignore`); sus valores se cargan como variables en Railway.

## 1. Base de datos de producción (recomendado)

Usa una base aparte para producción, para no mezclarla con los datos de desarrollo ni con `cargar_demo`.

1. En la consola de Aiven, abre tu servicio MySQL y ve a **Databases → Create database**. Ponle el nombre `kumora_prod`.
2. Revisa que el servicio esté encendido: el plan gratis de Aiven lo apaga por inactividad, y entonces la API no podrá conectarse.

Las tablas las crea el propio despliegue, porque el comando de inicio corre `migrate`.

## 2. Crear el proyecto desde GitHub

1. Entra a [railway.com](https://railway.com) e inicia sesión con tu cuenta de GitHub.
2. **New Project → Deploy from GitHub repo**.
3. Autoriza a Railway a leer el repositorio (si lo pide) y elige `kuska-backend`.
4. Railway crea un servicio y empieza a construir. **El primer despliegue va a fallar** porque todavía no hay variables; es normal.
5. Abre el servicio, ve a **Settings** y cambia el nombre a `kumora-api`.
6. En **Settings → Source**, revisa que la rama sea `master`.
7. En **Settings → Build**, revisa que el constructor sea **Railpack**, que es el predeterminado. Si dice Nixpacks, cámbialo a Railpack: `railpack.json` solo lo lee Railpack.

## 3. Variables de entorno

Las variables se comparten entre el servicio web y el cron, así que conviene crearlas como **variables compartidas** del proyecto:

1. En el proyecto, ve a **Settings → Shared Variables**.
2. Agrega cada variable de la tabla.
3. Luego, en cada servicio, ve a **Variables → Shared Variables** y agrégalas todas (o usa **Raw Editor** para pegarlas de una vez).

| Variable | Valor (sin datos reales aquí) |
|---|---|
| `SECRET_KEY` | Una clave nueva, distinta de la de desarrollo. Genérala en tu PC con `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| `DEBUG` | `False` |
| `ALLOWED_HOSTS` | El dominio de Railway, por ejemplo `kumora-api-production.up.railway.app` (ver el paso 4). |
| `CSRF_TRUSTED_ORIGINS` | El mismo dominio con `https://`, por ejemplo `https://kumora-api-production.up.railway.app`. |
| `CORS_ALLOWED_ORIGINS` | La URL del panel web cuando exista (con `https://`). La app móvil no necesita CORS. |
| `SWAGGER_PUBLICO` | `True` mientras se desarrollan los frontends; `False` después. |
| `SECURE_HSTS_SECONDS` | `3600` al principio; `31536000` (1 año) cuando el dominio sea definitivo. |
| `DB_NAME` | `kumora_prod` (o la base que uses). |
| `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Los de tu servicio en Aiven (**Overview → Connection information**). |
| `DB_SSL_CA_BASE64` | Tu `ca.pem` codificado en **base64, en una sola línea**. Mira cómo generarlo justo debajo de esta tabla. |
| `CLOUDINARY_URL` | `cloudinary://<api_key>:<api_secret>@<cloud_name>` |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Los del correo (ver la nota de abajo). |
| `EMAIL_TIMEOUT` | `10` |

No definas `DB_SSL_CA` ni `DB_SSL_CA_CONTENIDO` en Railway: con `DB_SSL_CA_BASE64` basta. `PORT` lo pone Railway solo; no lo agregues.

### Cómo generar `DB_SSL_CA_BASE64`

El editor de variables de Railway borra o daña los textos de varias líneas, y el `ca.pem` tiene muchas. Por eso se pasa en base64: es el mismo certificado convertido en **una sola línea** de letras y números, que la app vuelve a convertir en archivo al arrancar.

En PowerShell, dentro de la carpeta del proyecto (donde está tu `ca.pem`):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("ca.pem")) | Set-Clipboard
```

Eso deja el base64 copiado en el portapapeles. Pégalo como valor de `DB_SSL_CA_BASE64` en Railway: es una sola línea larga (unos 2200 caracteres) que empieza por `LS0tLS1CRUdJTi`. Desde Git Bash, `base64 -w0 ca.pem` da exactamente lo mismo.

> No uses `certutil -encode`: agrega encabezados y saltos de línea.

Orden de prioridad del certificado: `DB_SSL_CA_BASE64` → `DB_SSL_CA_CONTENIDO` → `DB_SSL_CA` (ruta, solo en local). Si el base64 está incompleto o no es un certificado, el despliegue falla al arrancar con un mensaje claro en los logs.

> **Correo en el plan Hobby:** Railway bloquea el SMTP saliente (puertos 25, 465, 587 y 2525) en los planes Trial y Hobby. Se habilita en Pro. En esos planes:
> - el correo con el código de recuperación **no saldrá**. La API no se queda colgada (se corta a los `EMAIL_TIMEOUT` segundos) y sigue respondiendo el mensaje de siempre, pero el usuario no recibe el código.
> - Para la sustentación puedes dejar `EMAIL_HOST_USER` vacío: el código aparece en los **logs** del servicio (**Deployments → View logs**).
> - Para un uso real hay dos caminos: el plan Pro, o un servicio de correo por HTTPS. Ese cambio no está hecho.

## 4. Dominio público

1. En el servicio `kumora-api`, ve a **Settings → Networking → Generate Domain**.
2. Railway da un dominio como `kumora-api-production.up.railway.app`. Si te pide el puerto, deja el que propone: gunicorn escucha en `$PORT`.
3. Copia ese dominio y actualiza estas variables:
   - `ALLOWED_HOSTS` = el dominio (sin `https://`);
   - `CSRF_TRUSTED_ORIGINS` = `https://` + el dominio.
4. Railway redespliega solo al cambiar las variables. Si no lo hace: **Deployments → Redeploy**.

El HTTPS lo da Railway. Django redirige a HTTPS lo que llegue por HTTP y confía en el encabezado `X-Forwarded-Proto` del proxy de Railway.

## 5. Primer Administrador

El panel no crea administradores (RN-01); se crean por consola, contra la base de producción. Desde **tu PC**:

1. En tu `.env` local, cambia temporalmente `DB_NAME=kumora_prod`. El servidor de Aiven es el mismo, así que tu `ca.pem` sirve.
2. Corre `python manage.py createsuperuser` y responde lo que pide (correo, nombres, documento, teléfono y contraseña).
3. Vuelve a dejar `DB_NAME` como estaba.

Espera a que el primer despliegue haya terminado: `migrate` crea las tablas y los roles.

## 6. Vencimientos cada hora (servicio cron)

`procesar_vencimientos` (RN-50) debe correr cada hora. En Railway eso se hace con un **segundo servicio** del mismo repositorio, que se enciende, ejecuta el comando y se apaga.

1. En el proyecto: **New → GitHub Repo** y elige otra vez `kuska-backend`.
2. En **Settings** del nuevo servicio:
   - **Service name:** `kumora-vencimientos`.
   - **Deploy → Custom Start Command:** `python manage.py procesar_vencimientos`. Esto reemplaza el comando de `railpack.json`, así que el cron no levanta gunicorn ni corre migraciones.
   - **Deploy → Cron Schedule:** `0 * * * *` (al minuto 0 de cada hora). Railway usa hora UTC; como corre cada hora, da igual.
   - **Networking:** no generes dominio; este servicio no recibe peticiones.
3. En **Variables**, agrega las mismas variables compartidas del paso 3.

Notas:
- **Railway no inicia una ejecución si la anterior sigue activa.** El comando termina solo al acabar, así que no se acumulan.
- **Es seguro ejecutarlo de más:** no hace nada dos veces.
- **Aunque el cron falle**, los listados, el dashboard y las acciones (aprobar, rechazar, entregar y enviar el carrito) aplican los vencimientos de lo que tocan.

## 7. Verificar que funciona

1. **Logs del despliegue** (`kumora-api` → **Deployments** → el último → **View logs**). En orden deben aparecer:
   - las migraciones (`Applying ...` la primera vez, o `No migrations to apply`);
   - `... static files copied`;
   - `Listening at: http://0.0.0.0:<puerto>` y `Booting worker` (dos veces, una por worker).
2. **Swagger:** abre `https://<tu-dominio>/api/docs/` (con `SWAGGER_PUBLICO=True`). Debe cargar con estilos.
3. **Login:** en Swagger, `POST /api/auth/login/` con el administrador del paso 5 debe responder 200 con `access` y `refresh`. Luego usa **Authorize** con el `access` y prueba `GET /api/admin/configuracion/`.
4. **Admin de Django:** abre `https://<tu-dominio>/admin/`. Debe verse con estilos (WhiteNoise) y dejarte entrar sin error CSRF. Si aparece "CSRF verification failed", revisa `CSRF_TRUSTED_ORIGINS`.
5. **HTTP → HTTPS:** abre `http://<tu-dominio>/api/docs/`; debe redirigir a `https://`.
6. **Fotos:** como donante, crea una donación con foto desde Swagger y revisa que aparezca en Cloudinary (`kumora/alimentos/`).
7. **Cron:** en `kumora-vencimientos` → **Deployments**, después de la siguiente hora en punto debe aparecer una ejecución con el resumen `Vencimientos procesados (...)` y `Errores: 0`.

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| El build falla instalando `mysqlclient` (`pkg-config`, `mysql_config not found`) | El servicio no usa Railpack, así que no leyó `railpack.json`. Revisa **Settings → Build → Builder**. Plan B: cambiar `mysqlclient` por `PyMySQL` (ver `docs/decisiones.md`, Paso 14). |
| `KeyError: 'SECRET_KEY'` (o `DB_NAME`, `CLOUDINARY_URL`...) | Falta esa variable en el servicio. |
| `Falta el certificado de Aiven` | No está definida `DB_SSL_CA_BASE64` en el servicio (o en las variables compartidas). |
| `DB_SSL_CA_BASE64 no es un base64 válido` o `no contiene un certificado PEM` | El valor se copió incompleto o no salió del `ca.pem`. Vuelve a generarlo con el comando de PowerShell y pégalo completo. |
| `Can't connect to MySQL server` | El servicio de Aiven está apagado (plan gratis) o los datos de conexión no coinciden. |
| `Bad Request (400)` en todo | El dominio no está en `ALLOWED_HOSTS`. |
| Redirecciones infinitas | Falta el encabezado del proxy. No debería pasar en Railway; revisa que `DEBUG=False` venga de las variables y no de otro lado. |
| `/api/docs/` responde 404 | `SWAGGER_PUBLICO` no está en `True`. |
| El código de recuperación no llega | SMTP bloqueado en el plan Hobby (ver el paso 3). |
