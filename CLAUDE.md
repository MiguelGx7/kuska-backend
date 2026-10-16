# Kumora — Backend (Django REST Framework + MySQL en Aiven)

Kumora es una **Red de Alimentos Solidarios**: personas o negocios donan alimentos, los beneficiarios los solicitan y un administrador valida, gestiona y controla todo.

Este repositorio es **solo el backend** (API REST). La app móvil (Donante y Beneficiario) y el panel web (Administrador) son proyectos aparte. **No crear frontend aquí.**

Autor: Miguel Angel Gomez

---

## 1. Reglas de trabajo para Claude

1. **Trabajar por pasos** (sección 9). Al terminar cada paso: detenerse, resumir qué se hizo, decir cómo probarlo y **esperar confirmación** antes de seguir.
2. **No inventar** tablas, campos, estados, roles ni reglas. La fuente de verdad es este documento y `docs/kumora_bd.sql`. Si algo falta o choca, **proponer el cambio y preguntar** antes de hacerlo.
3. Nombres de modelos, campos y valores de estados **en español**, idénticos al script SQL (`db_table` igual al nombre de la tabla).
4. Los estados son **fijos** (`TextChoices`), no tablas. El administrador solo **cambia** estados, no los crea.
5. La lógica de negocio va en `services.py` de cada app. Las vistas y serializers deben ser delgados.
6. Toda operación que toque cantidades o varios registros va en `transaction.atomic()` y usa `select_for_update()` sobre `donacion_alimento`. Toda acción que cambie el estado de una solicitud, una donación o una novedad **bloquea primero esa fila** con `select_for_update()` y vuelve a revisar su estado antes de actuar (evita choques, ej. el beneficiario cancela mientras el admin aprueba).
   - **Orden fijo de bloqueos**: usuario → sanción → novedad → solicitud → entrega → alimentos (`donacion_alimento`, en orden por `id`) → configuración → donación. La sanción va justo después del usuario porque al sancionar se bloquea primero al usuario (funciona como turno: dos sanciones o una sanción y una inactivación del mismo usuario no pasan juntas las validaciones), luego sus sanciones vigentes (el permanente deja `vigente = 0` al temporal) y después la cascada de RN-70 a RN-73 baja hacia sus solicitudes, alimentos y donaciones. Las sanciones solo dependen del usuario, y ninguna otra operación las bloquea después de esas filas. La novedad va antes que la solicitud porque al aceptarla es el punto de partida: primero se bloquea la novedad para revisar que siga `Pendiente`, y desde ella se llega a las solicitudes, los alimentos y la donación afectados; ninguna otra operación bloquea una novedad después de esas filas. Al crear una novedad se bloquea solo la fila del donante (usuario), que funciona como turno para no crear dos `Pendiente` iguales al mismo tiempo. Toda operación que bloquee varias filas lo hace en este orden (saltando las que no necesita). Si dos peticiones bloquearan las mismas filas en orden distinto, cada una podría quedarse esperando la fila que tiene la otra (bloqueo cruzado o *deadlock*); con un orden único, la segunda simplemente espera su turno. La entrega va justo después de su solicitud: al confirmar o revisar una entrega (RN-26b) se bloquea primero la solicitud y después su entrega, y se vuelve a revisar el estado con las filas bloqueadas. El código de verificación (`codigo_verificacion`) se bloquea después del usuario. En el registro (RN-04d) solo se bloquea el código, porque el usuario todavía no existe; que no se creen dos cuentas con el mismo correo lo garantiza su índice único.
7. Mensajes de error y de validación **en español**, claros para el usuario final.
8. Nunca escribir credenciales en el código. Todo sale de `.env` (que está en `.gitignore`).
9. No usar `db.sqlite3`. La base de datos es MySQL en Aiven.
10. Explicar con palabras sencillas: el autor debe poder **defender cada decisión en la sustentación**.

---

## 2. Stack

- Python 3.14, Django 5.2 (LTS), Django REST Framework
- `djangorestframework-simplejwt` (login con JWT) con su app `token_blacklist`
- `mysqlclient` (si falla en Windows, usar `PyMySQL`)
- `python-dotenv`, `django-cors-headers`, `django-filter`, `drf-spectacular` (Swagger)
- Fotos: `cloudinary`, `django-cloudinary-storage`, `Pillow`
- Zona horaria: `America/Bogota`, `USE_TZ = True`, idioma `es-co`

### Conexión a Aiven

- Variables en `.env`: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_SSL_CA` (ruta a `ca.pem` descargado de Aiven), `CLOUDINARY_URL`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`. Solo en producción: `DB_SSL_CA_BASE64` (en lugar de `DB_SSL_CA`), `CSRF_TRUSTED_ORIGINS`, `SWAGGER_PUBLICO`, `SECURE_HSTS_SECONDS` y `EMAIL_TIMEOUT` (ver `.env.example`).
- Correo (recuperación de contraseña): SMTP con `EMAIL_USE_TLS = True`. Con Gmail se usa una **contraseña de aplicación**, nunca la contraseña normal.
- `OPTIONS: {"ssl": {"ca": DB_SSL_CA}, "charset": "utf8mb4"}`.
- La base `kumora` se crea en la consola de Aiven (sección Databases del servicio MySQL).
- El plan gratis de Aiven apaga el servicio por inactividad: si no conecta, encenderlo desde la consola. **Las tablas las crean las migraciones de Django**; el script `docs/kumora_bd.sql` es la referencia del diseño y **no se ejecuta** en Aiven.

### Entornos

Hay dos entornos sobre el **mismo servicio MySQL de Aiven**, cada uno con su propia base de datos.

| | Desarrollo | Producción |
|---|---|---|
| Dónde corre | PC local (`python manage.py runserver`) | **Railway**: https://kuska-backend-production.up.railway.app |
| Base de datos | `kumora` (y `test_kumora`, que crean y borran las pruebas) | `kumora_prod` |
| Configuración | Archivo `.env` con `DEBUG=True` | Variables del servicio en Railway, con `DEBUG=False` |
| Certificado de Aiven | `DB_SSL_CA=ca.pem` (archivo local, fuera de git) | `DB_SSL_CA_BASE64` (el `ca.pem` en base64, en una línea) |
| Servidor | `runserver` | `gunicorn` + `whitenoise`. Arranca con `migrate` → `collectstatic` → `gunicorn` (`railpack.json`) |
| Swagger | Siempre (`/api/docs/`) | Solo con `SWAGGER_PUBLICO=True` |
| Vencimientos (RN-50) | A mano: `python manage.py procesar_vencimientos` | Servicio cron de Railway, cada hora (`0 * * * *`) |
| Datos de demostración | `cargar_demo` (solo con `DEBUG=True`) | No se cargan |
| Administrador | `createsuperuser` contra `kumora` | Creado con `createsuperuser` desde el PC, apuntando a `kumora_prod` |

- **Nunca** apuntar el `.env` local a `kumora_prod`, salvo para una tarea puntual como crear un administrador, y volver a `kumora` enseguida.
- La guía de despliegue y los problemas encontrados están en `docs/despliegue_railway.md`.

---

## 3. Estructura de apps

| App | Modelos |
|---|---|
| `usuarios` | Rol, Usuario (modelo de usuario personalizado), RecuperacionContrasena |
| `catalogo` | Categoria, Configuracion |
| `donaciones` | Donacion, DonacionAlimento, Novedad |
| `solicitudes` | CarritoItem, Solicitud, DetalleSolicitud, Entrega |
| `sanciones` | Sancion |
| `notificaciones` | Notificacion |
| `dashboard` | Sin modelos (solo consultas y estadísticas) |

Proyecto principal: `config/`. Las apps van dentro de la carpeta `apps/` y en cada `apps.py` se usa `name = "apps.<nombre>"`.

---

## 4. Roles

| Rol | Dónde se usa | Qué hace |
|---|---|---|
| **Donante** | App móvil | Registra donaciones, ve sus donaciones y quién pidió cada alimento, registra entregas, crea novedades, ve sus estadísticas y notificaciones. |
| **Beneficiario** | App móvil | Ve alimentos disponibles, usa el carrito, envía y cancela solicitudes, ve sus solicitudes y notificaciones. |
| **Administrador** | Panel web | Valida/rechaza donaciones, aprueba/rechaza solicitudes, registra entregas, revisa novedades, sanciona, CRUD de categorías, cambia estado de usuarios, edita la configuración, ve el dashboard. |

Cada cuenta tiene **un solo rol** (correo y documento son únicos). Quien quiera donar y también recibir necesita dos cuentas distintas; así cada flujo, permiso y sanción es claro.

---

## 5. Tablas (14)

El detalle exacto de tipos, restricciones e índices está en `docs/kumora_bd.sql`. Django y simplejwt crean además sus **tablas técnicas** (migraciones, permisos, admin, sesiones, `token_blacklist`); no son del negocio y no cuentan en las 14.

1. **rol**: id, nombre (Donante, Beneficiario, Administrador).
2. **usuario**: rol, nombres, apellidos, tipo_documento (CC, CE, PAS, PPT), numero_documento (único), correo (único, es el login), contrasena, telefono, direccion, estado (Activo, Bloqueado, Inactivo), bloqueado_hasta, tipo_donante (Persona, Negocio), nombre_negocio, nit (único), fechas.
   - En Django: `AbstractBaseUser` + `PermissionsMixin`, `USERNAME_FIELD = "correo"`, campo `password` con `db_column="contrasena"`. Se permiten los campos técnicos que exige Django (`last_login`, `is_superuser`, `is_staff`).
   - `is_active` **no es columna**: es una propiedad calculada desde `estado` (`estado != "Inactivo"`), para no tener dos campos que digan lo mismo. El login personalizado (RN-03) maneja los bloqueados.
3. **categoria**: nombre (único), descripcion, activa.
4. **donacion**: donante, titulo, descripcion, direccion_recogida, modalidad_entrega (Recoger, Domicilio, Ambas), estado (Pendiente, Validada, Rechazada, Agotada, Finalizada), motivo_rechazo, revisada_por, fecha_revision, fechas.
5. **donacion_alimento**: donacion, categoria, nombre, descripcion, unidad_medida (kg, g, L, ml, unidad, paquete), cantidad_total, cantidad_disponible, max_por_solicitud, fecha_vencimiento, url_foto, activo.
6. **carrito_item**: beneficiario, donacion_alimento, cantidad, fecha_agregado. Único por (beneficiario, alimento).
7. **solicitud**: codigo_pedido (formato `KUM-AAAAMMDD-NNNN`), beneficiario, donacion, estado (Pendiente, Aprobada, Rechazada, Entregada, No recogida, Cancelada), modalidad_entrega (Recoger, Domicilio), direccion_entrega, motivo_rechazo, motivo_cancelacion, gestionada_por, fecha_solicitud, fecha_gestion, fecha_limite_recogida.
8. **detalle_solicitud**: solicitud, donacion_alimento, cantidad, cancelado.
9. **entrega**: solicitud (única), registrada_por, fecha_entrega, observacion, y la confirmación del beneficiario (RN-26b): estado_confirmacion (Pendiente, Confirmada, Asumida, Reclamada, Revisada), fecha_confirmacion, comentario_beneficiario (obligatorio si Reclamada o Revisada), respuesta_admin, revisada_por y fecha_revision (obligatorias si Revisada). Todo estado distinto de Pendiente exige fecha_confirmacion.
10. **novedad**: donante, donacion, donacion_alimento (opcional: NULL = toda la donación), descripcion, estado (Pendiente, Aceptada, Rechazada), respuesta_admin, revisada_por, fechas.
11. **sancion**: usuario, tipo (Advertencia, Bloqueo temporal, Bloqueo permanente), motivo, donacion (opcional), solicitud (opcional), creada_por, fecha_inicio, fecha_fin, vigente.
12. **notificacion**: usuario, tipo (valores fijos de RN-40, con `TextChoices`), titulo, mensaje, donacion (opcional), solicitud (opcional), leida, fecha.
13. **configuracion**: una sola fila (id = 1): plazo_recogida_dias (3), margen_vencimiento_dias (1), max_alimentos_por_solicitud (10), porcentaje_max_por_alimento (30), max_solicitudes_activas (2).
14. **codigo_verificacion**: usuario (opcional: NULL en los de registro, porque el usuario aún no existe), proposito (Registro, Recuperar contraseña), correo (al que se envió), codigo_hash (el código de 6 dígitos se guarda con hash, nunca en texto plano), fecha_creacion, fecha_expiracion (15 minutos), intentos (máximo 5), usado. Un código solo sirve para su propósito y su correo. Antes se llamaba `recuperacion_contrasena` (se renombró en el Paso 16 conservando sus registros).

### Fotos (Cloudinary)

- Las fotos de los alimentos se guardan en **Cloudinary**, en desarrollo y en producción. MySQL solo guarda la referencia en `url_foto`.
- En Django: `ImageField(db_column="url_foto", max_length=500)` con el almacenamiento de `django-cloudinary-storage` configurado en `STORAGES`, leyendo `CLOUDINARY_URL` del `.env`. Se guarda la ruta (`kumora/alimentos/...`) y la URL completa se arma al leerla.
- Carpeta en Cloudinary: `kumora/alimentos/`.
- Al eliminar un alimento o una donación `Pendiente` (RN-12) también se borra su foto en Cloudinary. Las fotos de donaciones rechazadas se conservan como evidencia para una posible sanción.
- **Formato para crear una donación** (`POST donaciones/`): petición `multipart/form-data` con un campo `datos` (texto JSON con la donación y su lista `alimentos`) y las fotos como `foto_0`, `foto_1`... en el mismo orden de los alimentos. Así una sola petición crea todo y las fotos viajan como archivos, no en base64. Para agregar un alimento a una donación `Pendiente` (`POST donaciones/{id}/alimentos/`) se usan campos normales del multipart más `foto`.
- **Orden para no dejar fotos huérfanas**: primero se validan todos los datos y todas las fotos (con Pillow); solo si todo está bien se sube a Cloudinary dentro de `transaction.atomic()`. Si algo falla después de subir, se borran las fotos ya subidas. Al eliminar, las fotos se borran con `transaction.on_commit()`, es decir, solo cuando la base confirmó el cambio.

### Notas de implementación en Django

- `ENUM` del script → `CharField` con `choices` (`TextChoices`). MySQL quedará con VARCHAR; es normal.
- Los `CHECK` y los índices del script se declaran en `Meta.constraints` (`CheckConstraint` con `condition=`, `UniqueConstraint`) y `Meta.indexes`.
- `on_delete`: `CASCADE` donde el script dice `ON DELETE CASCADE`; `PROTECT` en el resto (equivale al RESTRICT de MySQL).
- Excepción: `notificacion.donacion` y `notificacion.solicitud` usan `SET_NULL` (`ON DELETE SET NULL` en el script), porque la notificación es historial y debe conservarse aunque se elimine una donación `Pendiente` (RN-12).
- `fecha_registro`/`fecha_creacion` → `auto_now_add=True`; `fecha_actualizacion` → `auto_now=True`.
- `configuracion.id` → `PositiveSmallIntegerField(primary_key=True, default=1)`.
- Los roles se buscan siempre por `nombre` ("Donante", "Beneficiario", "Administrador"), nunca por `id`.
- "Hoy" siempre es la fecha de Bogotá: usar `timezone.localdate()` (y `timezone.localtime()` para "al final del día"). Nunca la fecha UTC, porque después de las 7 p. m. en Colombia sería el día siguiente.
- `createsuperuser` debe asignar el rol Administrador (`rol_id` es obligatorio) y pedir nombres, apellidos, tipo y número de documento y teléfono. Por eso la migración de datos con los roles corre antes.

- **Notificaciones internas**: se crean solo con `notificaciones/services.py`: `crear_notificacion(usuario, tipo, titulo, mensaje, donacion=None, solicitud=None)` y `notificar_administradores(...)` (a cada administrador `Activo`). Los servicios las llaman **dentro de su misma transacción**: si la operación falla, la notificación tampoco se guarda. Los textos largos se recortan al tamaño de la columna.

**Datos iniciales** (migración de datos): los 3 roles, la fila de configuración y las categorías del script.

---

## 6. Reglas de negocio

### Usuarios y acceso
- **RN-01** El registro público solo crea **Donante** o **Beneficiario**, y solo después de verificar el correo con un código (RN-04d). El primer Administrador se crea por consola (`createsuperuser`), nunca desde el registro. Los demás administradores también se crean solo por consola; el panel no crea administradores. `createsuperuser` y `cargar_demo` crean usuarios directamente, sin código.
- **RN-02** Correo y documento únicos. El **Donante** debe indicar `tipo_donante`; si es **Negocio**, requiere `nombre_negocio` y `nit`. Para Beneficiario y Administrador, `tipo_donante`, `nombre_negocio` y `nit` quedan en NULL.
- **RN-03** Login:
  - `Inactivo` → no entra.
  - `Bloqueado` con `bloqueado_hasta` vencido → se desbloquea al iniciar sesión (pasa a `Activo` y su sanción temporal queda `vigente = 0`). También lo desbloquea `procesar_vencimientos` (RN-50), aunque no inicie sesión.
  - `Bloqueado` con `bloqueado_hasta` NULL (permanente) o aún vigente → no entra y se le dice hasta cuándo.
- **RN-04** El administrador cambia el estado de un usuario entre `Activo` e `Inactivo`. El estado `Bloqueado` solo se pone mediante sanciones. Desde la API **no se puede cambiar el estado ni sancionar a un Administrador** (evita quedarse sin administradores o que uno se bloquee a sí mismo).
- **RN-04b** Recuperar contraseña (código por correo):
  - `auth/recuperar/solicitar/` recibe el correo. Si existe, se genera un código de 6 dígitos, se guarda con hash, vence en 15 minutos y se envía por correo. Pedir uno nuevo invalida los anteriores del usuario. La respuesta es **siempre la misma** ("Si el correo está registrado, te enviamos un código"), para no revelar qué correos existen.
  - `auth/recuperar/confirmar/` recibe correo, código y nueva contraseña. Se rechaza si el código está vencido, usado o con 5 intentos fallidos (cada fallo suma 1). Si es correcto, cambia la contraseña, marca `usado = 1` e **invalida todos los refresh tokens** del usuario (cierra sesión en todos sus dispositivos).
  - Recuperar la contraseña **no desbloquea**: el login sigue aplicando RN-03.
- **RN-04a** Perfil: el usuario puede editar nombres, apellidos, teléfono, dirección y contraseña. Rol, tipo y número de documento, correo, `tipo_donante`, `nombre_negocio` y `nit` no se editan desde el perfil.
- **RN-04c** El administrador puede **corregir el correo y el teléfono** de un usuario (`admin/usuarios/{id}/`), validando que el correo siga siendo único. Sirve, por ejemplo, si el usuario se registró con el correo mal escrito y no puede recuperar su contraseña.
- **RN-04d** Verificación del correo al registrarse (código por correo):
  - `auth/registro/solicitar-codigo/` recibe el correo. Si ya está registrado responde el mismo error de RN-02. Si no, genera un código de 6 dígitos (hash, 15 minutos, 5 intentos), invalida los códigos de registro anteriores de ese correo y lo envía con el asunto "Kumora: tu código para crear la cuenta". No crea ningún usuario. Si el correo no se puede enviar, responde 503 y no guarda el código.
  - `auth/registro/` recibe los datos de la cuenta y el `codigo`. Primero valida todos los datos (RN-01, RN-02, contraseña) y después el código. Si el código no es válido, venció, ya se usó o se acabaron los intentos, responde un único mensaje genérico (como RN-04b) y no crea nada. Si todo está bien, en una transacción crea el usuario, marca el código como usado y devuelve lo mismo que el login (`access`, `refresh` y los datos del usuario).
  - Un código de registro solo sirve para ese correo y ese propósito: los de recuperar contraseña no sirven para registrarse, ni al revés.
  - Los dos endpoints tienen el límite de 5 peticiones por minuto por IP.

### Sanciones (solo el Administrador)
- **RN-05** Escala: **Advertencia → Bloqueo temporal (7 días) → Bloqueo permanente**. El sistema sugiere el siguiente nivel según las sanciones anteriores del usuario; el admin confirma. Motivo obligatorio. No se puede sancionar dos veces al mismo usuario por la misma `solicitud_id` o `donacion_id`. La escala cuenta **todas** las sanciones históricas del usuario, vigentes o no.
- **RN-06** Efectos: Advertencia no cambia el estado. Bloqueo temporal: `fecha_fin = inicio + 7 días`, usuario `Bloqueado` con `bloqueado_hasta = fecha_fin`. Bloqueo permanente: usuario `Bloqueado` con `bloqueado_hasta = NULL`; es **definitivo** y no se levanta. Si el usuario tenía un bloqueo temporal vigente, ese queda `vigente = 0` y lo reemplaza el permanente.
- **RN-07** Motivos típicos: Donante que dona algo que no es comida o algo raro (con `donacion_id`); Beneficiario con solicitud `No recogida` en modalidad `Recoger` (con `solicitud_id`). Si la solicitud era `Domicilio`, el admin evalúa quién falló y la sanción puede ser para el donante (con `solicitud_id`).
- **RN-08** Toda sanción genera notificación al usuario.

### Donaciones
- **RN-10** El donante crea una donación con **al menos un alimento**. **Sin límite de cantidad.** Queda `Pendiente`, `cantidad_disponible = cantidad_total` y se notifica a los administradores (`nueva_donacion`).
- **RN-10a** Para `unidad` y `paquete` todas las cantidades son **enteras** (cantidad_total, carrito y detalle de solicitud). Para kg, g, L y ml se permiten hasta 2 decimales.
- **RN-11a** Cada alimento lleva **una foto obligatoria** (jpg, png o webp, máximo 5 MB). Se valida con Pillow que el archivo sea realmente una imagen de esos formatos, no solo por la extensión. El admin la usa para validar que es comida y que está en buen estado.
- **RN-11** La `fecha_vencimiento` de cada alimento debe ser posterior a hoy + `margen_vencimiento_dias`. Se revisa al crear y otra vez al validar (RN-13).
- **RN-12** Mientras está `Pendiente`, el donante puede editarla, agregar o quitar alimentos y eliminarla. Después de revisada **no puede eliminar nada**: debe crear una novedad. Una donación `Rechazada` es final: no se edita ni se reenvía; el donante crea una nueva. Después de revisada, la donación **no se edita** (ni título, ni alimentos, ni cantidades): si el donante tiene más comida crea una donación nueva, y si algo falla crea una novedad. El administrador tampoco edita donaciones: valida o rechaza con motivo.
- **RN-13** Validar: primero se vuelve a revisar RN-11 en cada alimento; si alguno ya no cumple, **no se puede validar** y el admin debe rechazarla indicando el motivo. Si cumple, pasa a `Validada`, guarda `revisada_por` y `fecha_revision`, y calcula `max_por_solicitud = cantidad_total × porcentaje_max_por_alimento / 100` (para `unidad` y `paquete` se redondea hacia abajo al entero, mínimo 1; para kg, g, L y ml se redondea hacia abajo a 2 decimales, mínimo 0,01). El valor queda fijo: si luego el admin cambia el porcentaje en la configuración, solo aplica a las validaciones nuevas. Rechazar: `motivo_rechazo` obligatorio. Ambos notifican al donante.
- **RN-14** Un alimento es **visible para pedir** si: el donante está `Activo`, donación `Validada`, `activo = 1`, `cantidad_disponible > 0`, no tiene novedad `Pendiente` (ni sobre él ni sobre toda la donación) y `fecha_vencimiento > hoy + margen_vencimiento_dias`.
- **RN-15** El donante ve en cada alimento sus **solicitudes recibidas** (quién, cuánto, estado). Ve el **teléfono y la dirección** del beneficiario solo si la solicitud está `Aprobada`, `Entregada` o `No recogida`; si está `Pendiente`, solo el nombre.
- **RN-16** Estado de la donación (se recalcula después de cada evento). Usa solo condiciones **permanentes**: un alimento "tiene disponibilidad" si `activo = 1`, `cantidad_disponible > 0` y `fecha_vencimiento > hoy + margen_vencimiento_dias`. Las condiciones temporales (novedad `Pendiente`, donante con bloqueo temporal) solo ocultan alimentos (RN-14), **no cambian el estado**.
  - `Validada`: al menos un alimento tiene disponibilidad.
  - `Agotada`: ningún alimento tiene disponibilidad y hay solicitudes `Pendiente` o `Aprobada`. Si vuelve cantidad (cancelación, rechazo, no recogida), regresa a `Validada`.
  - `Finalizada`: ningún alimento tiene disponibilidad y no hay solicitudes en curso. Es **final**.

- **RN-17** Categorías: una categoría inactiva (`activa = 0`) no se puede usar en alimentos nuevos ni al editar; los alimentos que ya la tienen no cambian. Una categoría solo se puede borrar si ningún alimento la usa; si no, se inactiva.

### Carrito
- **RN-20** Solo beneficiarios `Activo`. Se agregan alimentos visibles (RN-14). Cantidad ≤ `max_por_solicitud` y ≤ `cantidad_disponible` (entera si es `unidad` o `paquete`). Si el alimento ya está, se actualiza la cantidad. No se pueden agregar alimentos de una donación en la que el beneficiario ya tiene una solicitud `Pendiente` o `Aprobada`. El carrito **no reserva**.
- **RN-21** Al consultar el carrito se marcan los ítems que ya no son válidos (agotados, vencidos, con novedad, o de una donación con solicitud activa del beneficiario) y se avisa si el carrito tiene alimentos de más donaciones de las que permite `max_solicitudes_activas` (cada donación genera una solicitud).

### Solicitudes
- **RN-22** Enviar carrito (todo en una transacción):
  - Se crea **una solicitud por donación**, todas con el mismo `codigo_pedido` (formato `KUM-AAAAMMDD-NNNN`, consecutivo del día). Para que dos envíos simultáneos no generen el mismo código, se calcula bloqueando la fila de `configuracion` con `select_for_update()` (funciona como un turno, sin tablas nuevas).
  - Validar: máximo **una solicitud activa (`Pendiente` o `Aprobada`) por beneficiario y por donación** (así el tope `max_por_solicitud` no se salta con varios pedidos; al terminar esa solicitud puede volver a pedir); solicitudes activas (`Pendiente` + `Aprobada`) + nuevas ≤ `max_solicitudes_activas`; cada solicitud tiene como máximo `max_alimentos_por_solicitud` **alimentos distintos**; cada alimento ≤ `max_por_solicitud` y ≤ `cantidad_disponible`.
  - Modalidad: si la donación es `Recoger` o `Domicilio`, se usa esa; si es `Ambas`, la elige el beneficiario. `Domicilio` exige `direccion_entrega` (por defecto la del usuario).
  - **Se reserva de inmediato**: se descuenta `cantidad_disponible`.
  - Se vacía el carrito y se notifica a los administradores: **una notificación por pedido** (`codigo_pedido`) a cada admin, no una por solicitud.
- **RN-23** El beneficiario solo puede cancelar solicitudes `Pendiente`. Se devuelve la cantidad. Sin sanción.
- **RN-24** Aprobar (`Pendiente → Aprobada`): guarda `gestionada_por` y `fecha_gestion` y calcula:
  - `fecha_limite_recogida` = la **menor** entre `fecha_gestion + plazo_recogida_dias` y `(vencimiento más cercano de los alimentos no cancelados − margen_vencimiento_dias)` al final de ese día.
  - Si esa fecha ya pasó, no se puede aprobar.
  - Notifica al beneficiario y al donante. La notificación al beneficiario incluye la modalidad, la dirección de recogida o entrega y la `fecha_limite_recogida` (así la tiene aunque luego no pueda entrar a la app, ej. RN-70).
- **RN-25** Rechazar: `motivo_rechazo` obligatorio, se devuelve la cantidad y se notifica al beneficiario.
- **RN-26** Entrega: la registra el **donante dueño** o el **administrador** sobre una solicitud `Aprobada`. Crea el registro en `entrega`, la solicitud pasa a `Entregada` y se notifica al beneficiario. La entrega es **total** (todos los detalles no cancelados); no existen entregas parciales. Cualquier diferencia se anota en `observacion`. La notificación al beneficiario lo invita a confirmar: "¿Recibiste tu pedido? Confírmalo en la app antes de <fecha>."
- **RN-26b** Confirmación de la entrega por el beneficiario:
  - Al registrarse una entrega, su confirmación queda `Pendiente`.
  - El beneficiario dueño tiene **48 horas** desde `fecha_entrega` (constante del sistema, no de `configuracion`) para responder en `mis-solicitudes/{id}/confirmar-entrega/`:
    - "Recibido" → `Confirmada`;
    - "No recibido" → `Reclamada`, con comentario obligatorio (máx. 255). Se notifica `entrega_reclamada` a cada administrador `Activo` y al donante, con el código de pedido y el comentario.
  - Solo responde una vez, solo sobre sus solicitudes `Entregada`, mientras esté `Pendiente` y dentro del plazo.
  - Sin respuesta en 48 horas, pasa a `Asumida` (se da por recibida), sin notificar a nadie. Lo aplica RN-50.
  - El administrador revisa un reclamo en `admin/solicitudes/{id}/revisar-reclamo/`: la respuesta es obligatoria (máx. 255) y pasa a `Revisada`. Se notifica `reclamo_revisado` al beneficiario y al donante. Revisar no sanciona: si corresponde, se sanciona aparte con la `solicitud_id` (RN-05, RN-07).
  - La solicitud sigue `Entregada` en todos los casos, y el dashboard las sigue contando como entregadas.
- **RN-27** No recogida: una solicitud `Aprobada` con `fecha_limite_recogida` vencida pasa a `No recogida`, se devuelve la cantidad (de los detalles no cancelados) y se notifica al beneficiario y al donante. El admin decide si sanciona y a quién (RN-07): en `Recoger` normalmente al beneficiario; en `Domicilio` evalúa si falló el donante o el beneficiario.
- **RN-28** Pendiente vencida: una solicitud `Pendiente` cuyo vencimiento más cercano (de los alimentos no cancelados) − `margen_vencimiento_dias` ya pasó, no se puede aprobar. Pasa a `Cancelada` con `motivo_cancelacion` ("Los alimentos vencieron antes de ser aprobada"), se devuelve la cantidad y se notifica al beneficiario y al donante. Sin sanción.
- **RN-29** Lo que ve el beneficiario del donante: en una solicitud `Pendiente`, solo el nombre del donante (o `nombre_negocio`). Si está `Aprobada`, `Entregada` o `No recogida`, además el teléfono y la dirección de recogida, para coordinar la entrega.

### Novedades
- **RN-30** Solo el donante dueño, sobre una donación `Validada` o `Agotada`. Puede ser sobre un alimento o sobre toda la donación. Máximo **una novedad `Pendiente`** por alimento y una por toda la donación. Al crearla se notifica a los administradores (`novedad_creada`).
- **RN-31** Mientras está `Pendiente`, lo afectado **deja de mostrarse** para pedir. Además, las solicitudes que tienen alimentos afectados **no se pueden aprobar ni entregar**, y RN-27 no las marca `No recogida` (no es culpa del beneficiario). Al resolver la novedad, si su `fecha_limite_recogida` ya pasó, se recalcula con la fórmula de RN-24 tomando como base la fecha de revisión de la novedad.
- **RN-32** Aceptar:
  - Alimento(s) afectados: `activo = 0` y `cantidad_disponible = 0`.
  - En solicitudes `Pendiente` o `Aprobada` con esos alimentos, el detalle queda `cancelado = 1`.
  - Si todos los detalles de una solicitud quedan cancelados → la solicitud pasa a `Cancelada` con `motivo_cancelacion`.
  - Si no, y está `Aprobada`, se **recalcula** `fecha_limite_recogida` sin los cancelados.
  - Notifica a cada beneficiario afectado (explicando por qué) y al donante. Recalcula la donación (RN-16).
- **RN-33** Rechazar: `respuesta_admin` obligatoria; lo afectado vuelve a mostrarse y se notifica al donante.

### Usuarios bloqueados o inactivos
Idea: un bloqueo **temporal** no desperdicia comida ya comprometida; un bloqueo **permanente** o una **inactivación** (baja) cierran todo lo que está en curso.

- **RN-70 Beneficiario con bloqueo temporal:** sus solicitudes `Pendiente` pasan a `Cancelada` (se devuelve la cantidad); las `Aprobada` **siguen** (puede recoger; si no, aplica RN-27); su carrito se vacía.
- **RN-71 Beneficiario con bloqueo permanente o inactivo:** sus solicitudes `Pendiente` y `Aprobada` pasan a `Cancelada` (se devuelve la cantidad y se notifica al donante); su carrito se vacía.
- **RN-72 Donante con bloqueo temporal:** sus donaciones `Pendiente` quedan en espera (el admin decide); sus alimentos **dejan de mostrarse** para nuevos pedidos (RN-14); las solicitudes en curso siguen y el **administrador registra las entregas**. Al desbloquearse, vuelven a mostrarse.
- **RN-73 Donante con bloqueo permanente o inactivo:** sus donaciones `Pendiente` pasan a `Rechazada` con motivo; en las `Validada`/`Agotada`, todas las solicitudes `Pendiente` y `Aprobada` pasan a `Cancelada` (con notificación a cada beneficiario), los alimentos quedan `activo = 0` y `cantidad_disponible = 0`, y la donación pasa a `Finalizada`.
- **RN-74** Antes de confirmar una sanción de bloqueo o una inactivación, el sistema le muestra al admin un **resumen de lo que se afectará** (ej. "3 solicitudes se cancelarán"). Endpoint `admin/usuarios/{id}/impacto/?accion=...`: devuelve los conteos **sin aplicar nada**.
- **RN-75** Al reactivar un usuario `Inactivo` vuelve a `Activo`, o a `Bloqueado` si aún tiene un bloqueo vigente. Lo cancelado **no** se recupera.
- **RN-76** Una `Advertencia` no afecta nada en curso.
- **RN-77** Cuando un bloqueo temporal vence (RN-03 o RN-50), sus alimentos vuelven a mostrarse.

### Notificaciones
- **RN-40** Tipos: `nueva_donacion`, `donacion_validada`, `donacion_rechazada`, `nueva_solicitud`, `solicitud_aprobada`, `solicitud_rechazada`, `solicitud_cancelada`, `alimento_cancelado`, `solicitud_entregada`, `solicitud_no_recogida`, `novedad_creada`, `novedad_aceptada`, `novedad_rechazada`, `sancion`, `entrega_reclamada`, `reclamo_revisado` (16 tipos; los dos últimos desde RN-26b).
- **RN-41** Cada usuario solo ve las suyas; puede marcar una o todas como leídas.

### Vencimientos automáticos
- **RN-50** Comando `python manage.py procesar_vencimientos`: aplica RN-27 y RN-28, levanta los bloqueos temporales vencidos (RN-03, RN-77; solo pasa a `Activo` a quien está `Bloqueado`; si el usuario está `Inactivo`, solo marca la sanción `vigente = 0`) recalcula el estado de las donaciones (RN-16) y pasa a `Asumida` las confirmaciones de entrega `Pendiente` con más de 48 horas (RN-26b). Además se ejecuta antes de listar solicitudes y donaciones (las entregas vencidas se asumen antes de listar `mis-solicitudes/`, `admin/solicitudes/` y `solicitudes-recibidas/`), y antes de **aprobar, rechazar o entregar** una solicitud y de **enviar el carrito** (sobre los registros afectados), para que ninguna acción se haga con datos vencidos aunque no haya tarea programada.

### Dashboard
- **RN-60** Administrador: donaciones recibidas (validadas en adelante), solicitudes entregadas, solicitudes pendientes, personas beneficiadas (beneficiarios distintos con al menos una entrega), y series por mes y por categoría. Las series **cuentan** donaciones, solicitudes, entregas y alimentos; no suman cantidades (las unidades son distintas).
- **RN-61** Donante: sus donaciones por estado, número de entregas y de alimentos entregados, cantidades entregadas **agrupadas por `unidad_medida`** (no se suman unidades distintas), personas a las que ha ayudado, y series por mes y por categoría.
- **RN-62** Beneficiario: resumen para su pantalla de inicio: solicitudes `Pendiente` y `Aprobada`, cupos disponibles (`max_solicitudes_activas` − activas, mínimo 0), su próxima `fecha_limite_recogida` (de la `Aprobada` más cercana, con su `codigo_pedido` y donación) y notificaciones no leídas.

---

## 7. Seguridad y permisos

- Autenticación JWT (access + refresh). Todas las rutas requieren login excepto registro, login y recuperar contraseña.
- La respuesta del login incluye `access`, `refresh` y los datos básicos del usuario, **incluido su rol**. Cada frontend decide qué roles acepta: el panel web solo `Administrador`; la app móvil solo `Donante` y `Beneficiario`.
- Clases de permiso: `EsDonante`, `EsBeneficiario`, `EsAdministrador`.
- Permiso base `EsUsuarioActivo` en todas las rutas protegidas: en **cada petición** se revisa `estado`; si no está `Activo` se rechaza aunque el token sea válido (un bloqueo o una inactivación aplican de inmediato). Si está `Bloqueado` con `bloqueado_hasta` vencido, se desbloquea como en RN-03.
- Cada usuario solo accede a **sus propios** datos (donaciones, solicitudes, carrito, notificaciones), salvo el administrador.
- Contraseñas con el hash de Django. Nunca devolver la contraseña.
- Reglas de contraseña (validadores por defecto de Django en `AUTH_PASSWORD_VALIDATORS`): mínimo 8 caracteres, no solo números, no una contraseña común y no parecida a los datos del usuario. Se aplican en el registro, el cambio y la recuperación de contraseña. Los frontends no las repiten: muestran el mensaje del servidor.
- Tokens: access de **30 minutos**; refresh de **7 días** con rotación (`ROTATE_REFRESH_TOKENS`) y lista negra (`BLACKLIST_AFTER_ROTATION`). Al cambiar o recuperar la contraseña, y al bloquear o inactivar a un usuario, se invalidan todos sus refresh tokens.
- Límite de peticiones (throttling de DRF, sin tablas): login, registro y recuperar contraseña, **5 por minuto por IP**.

---

## 8. Endpoints (prefijo `/api/`)

| Grupo | Rutas principales |
|---|---|
| Auth | `auth/registro/solicitar-codigo/`, `auth/registro/` (con el código, RN-04d), `auth/login/`, `auth/refresh/`, `auth/perfil/` (ver y editar), `auth/perfil/cambiar-contrasena/`, `auth/recuperar/solicitar/`, `auth/recuperar/confirmar/` |
| Donante | `donaciones/` (CRUD mientras Pendiente), `donaciones/{id}/alimentos/` (agregar) y `donaciones/{id}/alimentos/{alimento_id}/` (quitar), `donaciones/{id}/solicitudes-recibidas/`, `novedades/`, `solicitudes/{id}/entregar/`, `dashboard/donante/` |
| Beneficiario | `alimentos-disponibles/` (filtros: categoría, búsqueda, modalidad), `carrito/`, `carrito/enviar/`, `mis-solicitudes/` (filtro por estado), `mis-solicitudes/{id}/cancelar/`, `mis-solicitudes/{id}/confirmar-entrega/` (RN-26b), `dashboard/beneficiario/` |
| Administrador | `admin/donaciones/` (+ `validar/`, `rechazar/`), `admin/solicitudes/` (+ `aprobar/`, `rechazar/`, `entregar/`, `revisar-reclamo/`; filtro `confirmacion`), `admin/novedades/` (+ `aceptar/`, `rechazar/`), `admin/sanciones/`, `admin/usuarios/` (+ editar correo y teléfono, `cambiar-estado/`, `impacto/`), `admin/categorias/`, `admin/configuracion/`, `dashboard/admin/` |
| Común | `categorias/` (solo lectura, solo las activas), `notificaciones/`, `notificaciones/{id}/leer/`, `notificaciones/leer-todas/` |
| Docs | `api/docs/` (Swagger): disponible con `DEBUG = True` o con `SWAGGER_PUBLICO = True` |

Listados con paginación y filtros (`django-filter`).

---

## 9. Plan por pasos

1. **Base del proyecto**: entorno, dependencias, `.env`, conexión a Aiven, carpeta `apps/` y apps vacías. Sin migrar todavía (`AUTH_USER_MODEL` queda comentado).
2. **Modelos + migraciones**: los 14 modelos, usuario personalizado (activar `AUTH_USER_MODEL`), migración de datos iniciales, `migrate`, `createsuperuser` y registro en el admin de Django.
3. **Autenticación**: registro, login JWT (con RN-03), perfil, recuperar contraseña (RN-04b) y permisos.
4. **Administración básica**: categorías, usuarios (cambiar estado) y configuración.
5. **Donaciones**: crear/editar/eliminar en Pendiente, validar/rechazar (RN-10 a RN-14).
6. **Carrito y envío de solicitudes** (RN-20 a RN-23). Aquí también se implementó RN-16 (`recalcular_estado_donacion()` en `donaciones/services.py`), porque es el primer paso que cambia cantidades.
7. **Gestión de solicitudes y entregas** (RN-24 a RN-28, RN-15, RN-16).
8. **Novedades** (RN-30 a RN-33).
9. **Sanciones y bloqueos** (RN-03 a RN-08, RN-70 a RN-77). Incluye los efectos en cascada de RN-71/RN-73 al inactivar (el endpoint `admin/usuarios/{id}/cambiar-estado/` ya existe desde el Paso 4 y tiene un `# TODO Paso 9` en `usuarios/services.py`) y el endpoint `admin/usuarios/{id}/impacto/` (RN-74).
10. **Notificaciones** (RN-40, RN-41).
11. **Vencimientos** (RN-50).
12. **Dashboard** (RN-60, RN-61, RN-62).
13. **Pruebas y documentación** (tests de las reglas clave y Swagger).