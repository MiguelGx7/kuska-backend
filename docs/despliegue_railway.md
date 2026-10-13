# Despliegue en Railway

Guía paso a paso para publicar el backend de Kumora en [Railway](https://railway.com).

- **Base de datos:** se sigue usando **MySQL en Aiven**; Railway no aloja la base.
- **Fotos:** siguen en **Cloudinary**.
- **Qué hace Railway:** construye el proyecto desde GitHub y lo corre con gunicorn.

> **Estado actual: desplegado y funcionando.**
> - **Dominio:** https://kuska-backend-production.up.railway.app
> - **Base:** `kumora_prod` en Aiven.
> - **Certificado:** por `DB_SSL_CA_BASE64`.
> - **Vencimientos:** cron cada hora.
> - **Administrador de producción:** ya creado.
>
> Los pasos de abajo son los que se siguieron. Los tropiezos que hubo están marcados con ⚠ y resumidos en "Lo que aprendimos", al final.

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
   ⚠ Revisa también que **Auto deploy** (despliegue automático al hacer push) esté **activo**. Si está apagado, los `git push` llegan a GitHub pero Railway **no los despliega**: sigue corriendo la versión anterior, y cualquier cambio de código (como el del correo con Brevo) parece no funcionar. Haz lo mismo en el servicio del cron.
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
| `ALLOWED_HOSTS` | El dominio de Railway, por ejemplo `kuska-backend-production.up.railway.app` (ver el paso 4). |
| `CSRF_TRUSTED_ORIGINS` | El mismo dominio con `https://`, por ejemplo `https://kuska-backend-production.up.railway.app`. |
| `CORS_ALLOWED_ORIGINS` | Los orígenes del panel web separados por comas, **con** `http://` o `https://`, **sin** barra final ni ruta y sin espacios. Mientras desarrollas el panel en tu PC contra esta API: `http://localhost:5173`. Cuando el panel tenga dominio: `https://<dominio-del-panel>,http://localhost:5173`, y cuando ya no desarrolles en local, quita `localhost`. Un origen mal escrito (por ejemplo, con `/` al final) hace fallar el arranque. La app móvil no necesita CORS: es una restricción solo de los navegadores. |
| `SWAGGER_PUBLICO` | `True` mientras se desarrollan los frontends; `False` después. |
| `SECURE_HSTS_SECONDS` | `3600` al principio; `31536000` (1 año) cuando el dominio sea definitivo. |
| `DB_NAME` | `kumora_prod` (o la base que uses). |
| `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | Los de tu servicio en Aiven (**Overview → Connection information**). |
| `DB_SSL_CA_BASE64` | Tu `ca.pem` codificado en **base64, en una sola línea**. Mira cómo generarlo justo debajo de esta tabla. |
| `CLOUDINARY_URL` | `cloudinary://<api_key>:<api_secret>@<cloud_name>` |
| `BREVO_API_KEY` | La API key v3 de Brevo (empieza por `xkeysib-`). Ver "Correo con Brevo", más abajo. |
| `DEFAULT_FROM_EMAIL` | El remitente **verificado** en Brevo, por ejemplo `Kumora <tu-correo@gmail.com>`. |
| `EMAIL_TIMEOUT` | `10` |

Las variables de SMTP (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`) **no se usan en Railway**: con `BREVO_API_KEY` el correo sale por la API de Brevo.

No definas `DB_SSL_CA` ni `DB_SSL_CA_CONTENIDO` en Railway: con `DB_SSL_CA_BASE64` basta. `PORT` lo pone Railway solo; no lo agregues.

### Cómo generar `DB_SSL_CA_BASE64`

El editor de variables de Railway borra o daña los textos de varias líneas, y el `ca.pem` tiene muchas. Por eso se pasa en base64: es el mismo certificado convertido en **una sola línea** de letras y números, que la app vuelve a convertir en archivo al arrancar.

En PowerShell, dentro de la carpeta del proyecto (donde está tu `ca.pem`):

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("ca.pem")) | Set-Clipboard
```

Eso deja el base64 copiado en el portapapeles. Pégalo como valor de `DB_SSL_CA_BASE64` en Railway: es una sola línea larga (unos 2200 caracteres) que empieza por `LS0tLS1CRUdJTi`. Desde Git Bash, `base64 -w0 ca.pem` da exactamente lo mismo.

> No uses `certutil -encode`: agrega encabezados y saltos de línea.

> ⚠ **El `ca.pem` debe ir sin sangría.** Cada línea tiene que empezar en la primera columna. Por ejemplo, la primera debe ser exactamente `-----BEGIN CERTIFICATE-----`, sin espacios ni tabulaciones antes.
> - **Cómo pasa:** al copiar el certificado desde la consola de Aiven o pegarlo en un editor, a veces queda indentado. La app lo acepta (contiene BEGIN/END), pero la conexión SSL con MySQL falla.
> - **Cómo revisarlo:**
>   - abre el `ca.pem` y verifica que ninguna línea empiece con espacios;
>   - el base64 correcto empieza por `LS0tLS1CRUdJTi`; si empieza por `ICAg` (espacios codificados), el archivo tiene sangría.
> - **Arreglo:** quita la sangría, guarda y vuelve a generar el base64.

Orden de prioridad del certificado: `DB_SSL_CA_BASE64` → `DB_SSL_CA_CONTENIDO` → `DB_SSL_CA` (ruta, solo en local). Si el base64 está incompleto o no es un certificado, el despliegue falla al arrancar con un mensaje claro en los logs.

### Correo con Brevo

Railway bloquea el SMTP saliente (puertos 25, 465, 587 y 2525) en los planes Trial y Hobby. Por eso, en producción, el correo de recuperación sale por la **API HTTPS de Brevo** (con `django-anymail`), que Railway no bloquea. El backend elige solo el canal: si hay `BREVO_API_KEY` usa Brevo; si no, SMTP; si tampoco, la consola.

1. **Crear la cuenta:** regístrate gratis en [brevo.com](https://www.brevo.com). El plan gratis permite unos 300 correos al día, de sobra para Kumora.
2. **Verificar el remitente:**
   - Ve a **Senders, Domains & Dedicated IPs → Senders → Add a sender**.
   - Pon el nombre (`Kumora`) y el correo desde el que saldrán los mensajes, por ejemplo tu Gmail.
   - Brevo envía un código o enlace a ese correo; confírmalo.
   - Solo se puede enviar desde remitentes verificados. Ese mismo correo va en `DEFAULT_FROM_EMAIL`: `Kumora <tu-correo@gmail.com>`.
3. **Crear la API key:**
   - En **SMTP & API → API Keys → Generate a new API key**, ponle un nombre (`kumora-railway`) y cópiala: Brevo **la muestra una sola vez**.
   - Debe ser una **API key v3** (empieza por `xkeysib-`), **no** la "SMTP key" de la pestaña SMTP.
4. **IPs autorizadas:**
   - Brevo puede bloquear las llamadas desde IPs que no conoce, y la IP de salida de Railway cambia.
   - En **Settings → Security → Authorised IPs**, elige que no se revisen las IPs desconocidas, o aprueba la que aparezca.
   - Si no, el envío falla con `unauthorized: IP not authorized` o `not verified`.
5. **Cargar las variables en Railway:** `BREVO_API_KEY` y `DEFAULT_FROM_EMAIL` (en las variables compartidas, paso 3). Railway redespliega solo.
6. **Probar:**
   - En Swagger, `POST /api/auth/recuperar/solicitar/` con el correo de un usuario real de producción.
   - Debe llegar "Kumora: código para recuperar tu contraseña", con el código de 6 dígitos y "Vence en 15 minutos".
   - En Brevo, **Transactional → Logs** muestra el envío.

**Qué buscar en los logs** del servicio (**Deployments → View logs**):

- Al arrancar: `INFO apps.usuarios.apps: Correo: se usa Brevo (API HTTPS). Remitente: ...`. Si dice `consola` o `SMTP`, `BREVO_API_KEY` no llegó al servicio: revisa que la variable esté en **este** servicio, no solo en las compartidas sin agregar.
- `WARNING ... BREVO_API_KEY no parece una API key v3`: pegaste la SMTP key (`xsmtpsib-`) en lugar de la API key (`xkeysib-`).
- Al pedir un código:
  - `INFO ... Código de recuperación enviado al usuario <id> (Brevo (API HTTPS))`: Brevo aceptó el envío.
  - `WARNING ... correo que no es de ningún usuario: xx***@dominio`: ese correo no existe en `kumora_prod`. Los usuarios de desarrollo no están en producción.
  - `ERROR ... No se pudo enviar el código ... al usuario <id>`, seguido del detalle de anymail: Brevo rechazó la petición.
- **Brevo solo registra los correos que acepta.** Una petición rechazada (clave inválida, IP no autorizada, remitente sin verificar) no aparece en **Transactional → Logs**; el motivo está en el log de Railway.

Si el envío falla, la API responde igual que siempre ("Si el correo está registrado…", RN-04b) y el error queda en los logs del servicio: `No se pudo enviar el código de recuperación a ...`.

> Un remitente `@gmail.com` enviado por Brevo puede llegar a la carpeta de spam, porque el dominio gmail.com no autoriza a Brevo. Para la sustentación basta; para un uso real conviene un dominio propio autenticado en Brevo (registros DNS).

## 4. Dominio público

1. En el servicio `kumora-api`, ve a **Settings → Networking → Generate Domain**.
2. Railway da un dominio como `kuska-backend-production.up.railway.app`. Si te pide el puerto, deja el que propone: gunicorn escucha en `$PORT`.
   ⚠ Usa el **dominio público** (`*.up.railway.app`), no el privado `*.railway.internal`. El privado solo sirve para que los servicios de Railway se hablen entre sí: el navegador, Swagger y las apps no llegan a él. Si se pone en `ALLOWED_HOSTS` o en `CSRF_TRUSTED_ORIGINS`, el sitio responde 400 o falla el CSRF del admin.
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

1. **Que esté corriendo el último commit.** En **Deployments**, el despliegue activo (*Active*) muestra el commit con el que se construyó. Debe ser el último que subiste (compáralo con `git log -1 --oneline`). Si es uno anterior, revisa **Auto deploy** (paso 2) o usa **Deploy latest commit** / **Redeploy**.
2. **Logs del despliegue** (`kumora-api` → **Deployments** → el último → **View logs**). En orden deben aparecer:
   - las migraciones (`Applying ...` la primera vez, o `No migrations to apply`);
   - `... static files copied`;
   - `Listening at: http://0.0.0.0:<puerto>` y `Booting worker` (dos veces, una por worker).
3. **Swagger:** abre `https://<tu-dominio>/api/docs/` (con `SWAGGER_PUBLICO=True`). Debe cargar con estilos.
4. **Login:** en Swagger, `POST /api/auth/login/` con el administrador del paso 5 debe responder 200 con `access` y `refresh`. Luego usa **Authorize** con el `access` y prueba `GET /api/admin/configuracion/`.
5. **Admin de Django:** abre `https://<tu-dominio>/admin/`. Debe verse con estilos (WhiteNoise) y dejarte entrar sin error CSRF. ⚠ Si aparece "CSRF verification failed":
   - Revisa que `CSRF_TRUSTED_ORIGINS` sea exactamente `https://` + el dominio público, sin `/` al final.
   - **Prueba en una ventana de incógnito.** El navegador guarda la cookie CSRF de los intentos anteriores (de antes de corregir las variables o de otro dominio), y con esa cookie vieja el error sigue aunque la configuración ya esté bien.
6. **HTTP → HTTPS:** abre `http://<tu-dominio>/api/docs/`; debe redirigir a `https://`.
7. **Fotos:** como donante, crea una donación con foto desde Swagger y revisa que aparezca en Cloudinary (`kumora/alimentos/`).
8. **Cron:** en `kumora-vencimientos` → **Deployments**, después de la siguiente hora en punto debe aparecer una ejecución con el resumen `Vencimientos procesados (...)` y `Errores: 0`.

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| Un cambio subido con `git push` no se refleja (por ejemplo, el correo con Brevo no sale y Brevo no registra nada) | **Auto deploy** está desactivado y Railway sigue con una versión anterior. Revisa en **Settings → Source** que esté activo, y en **Deployments** que el commit del despliegue activo sea el último. Si no lo es: **Deploy latest commit** / **Redeploy**. |
| El build falla instalando `mysqlclient` (`pkg-config`, `mysql_config not found`) | El servicio no usa Railpack, así que no leyó `railpack.json`. Revisa **Settings → Build → Builder**. Plan B: cambiar `mysqlclient` por `PyMySQL` (ver `docs/decisiones.md`, Paso 14). |
| `KeyError: 'SECRET_KEY'` (o `DB_NAME`, `CLOUDINARY_URL`...) | Falta esa variable en el servicio. |
| `Falta el certificado de Aiven` | No está definida `DB_SSL_CA_BASE64` en el servicio (o en las variables compartidas). |
| `DB_SSL_CA_BASE64 no es un base64 válido` o `no contiene un certificado PEM` | El valor se copió incompleto o no salió del `ca.pem`. Vuelve a generarlo con el comando de PowerShell y pégalo completo. |
| `Can't connect to MySQL server` | El servicio de Aiven está apagado (plan gratis) o los datos de conexión no coinciden. |
| `Bad Request (400)` en todo | El dominio no está en `ALLOWED_HOSTS`, o se puso el dominio privado `*.railway.internal` en lugar del público `*.up.railway.app`. |
| "CSRF verification failed" en `/admin/` | `CSRF_TRUSTED_ORIGINS` no es `https://` + el dominio público, o el navegador tiene una cookie CSRF vieja: probar en una ventana de incógnito. |
| El certificado se decodifica, pero falla la conexión SSL con MySQL | El `ca.pem` tenía sangría. Quitarla y volver a generar el base64; el correcto empieza por `LS0tLS1CRUdJTi`. |
| El certificado pegado en `DB_SSL_CA_CONTENIDO` llega dañado | El editor de variables de Railway estropea los textos de varias líneas. Usar `DB_SSL_CA_BASE64`. |
| Redirecciones infinitas | Falta el encabezado del proxy. No debería pasar en Railway; revisa que `DEBUG=False` venga de las variables y no de otro lado. |
| `/api/docs/` responde 404 | `SWAGGER_PUBLICO` no está en `True`. |
| El código de recuperación no llega y en los logs dice `No se pudo enviar el código` | Mira el detalle del error en el log:<br>• `unauthorized` / `Key not found`: la API key está mal o es una "SMTP key".<br>• `IP not authorized` / `not verified`: hay que autorizar la IP en Brevo.<br>• `sender is not valid`: `DEFAULT_FROM_EMAIL` no es un remitente verificado. |
| El código de recuperación no llega y no hay error en los logs | Revisa la carpeta de spam y **Transactional → Logs** en Brevo. Si no hay `BREVO_API_KEY`, el correo salió por consola: búscalo en los logs del servicio. |

## Lo que aprendimos en el despliegue real

1. **El certificado va en base64.** El editor de variables de Railway borra o daña los valores de varias líneas, así que pegar el PEM en `DB_SSL_CA_CONTENIDO` no funcionó. `DB_SSL_CA_BASE64` es una sola línea y llega intacta.
2. **El `ca.pem` debe ir sin sangría.** Si sus líneas tienen espacios al inicio, el texto parece un certificado pero la conexión SSL falla. El base64 correcto empieza por `LS0tLS1CRUdJTi`.
3. **Siempre el dominio público.** `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, el navegador y las apps usan `kuska-backend-production.up.railway.app`, nunca `*.railway.internal` (que es la red privada entre servicios de Railway).
4. **El CSRF del admin se prueba en incógnito.** Después de corregir `CSRF_TRUSTED_ORIGINS`, una cookie vieja del navegador puede mantener el error; en una ventana de incógnito se ve el resultado real.
5. **Auto deploy activo y el último commit desplegado.** El correo con Brevo "no funcionaba" porque Auto deploy estaba desactivado: los push no se desplegaban y seguía corriendo el código anterior, sin anymail. Antes de buscar un error en el código, hay que revisar en **Deployments** que el commit activo sea el último.
6. **Una base aparte para producción.** `kumora_prod` en el mismo servicio de Aiven: no se mezcla con desarrollo ni con `cargar_demo`. El administrador se creó con `createsuperuser` desde el PC, cambiando un momento `DB_NAME` a `kumora_prod`.
