# Kumora — Decisiones de diseño

Registro de las decisiones tomadas en cada paso del backend y su justificación. Las reglas de negocio (RN-xx) están en `CLAUDE.md`; aquí se explica **cómo** se implementaron y **por qué**, para poder defenderlas en la sustentación.

---

## Paso 3 — Autenticación

- **Throttling por endpoint y por IP.** Cada ruta pública (login, registro, recuperar/solicitar y recuperar/confirmar) tiene su propio límite de 5 peticiones por minuto. Con un contador compartido, registrar dos cuentas y luego iniciar sesión en el mismo minuto ya bloquearía al usuario.
- **Las vistas públicas no leen el token.** Un token vencido que el frontend envíe por error no impide iniciar sesión ni registrarse.
- **401 frente a 403.** Credenciales incorrectas responden 401. Una cuenta bloqueada o inactiva responde 403 con el motivo y, si aplica, hasta cuándo dura el bloqueo.
- **No revelar si un correo existe.** El login calcula un hash aunque el correo no exista, para que el tiempo de respuesta sea igual. En recuperar contraseña, la solicitud siempre responde lo mismo y la confirmación responde un único mensaje ante cualquier fallo (correo inexistente, código incorrecto, vencido, usado o sin intentos). El límite de 5 intentos se aplica internamente sin decir cuántos quedan.
- **Validación de la contraseña nueva al recuperar.** Las reglas que no dependen del usuario (largo, común, solo números) se revisan antes de buscar el correo, así una contraseña débil da el mismo error exista o no la cuenta, y no gasta un intento. La regla de "no parecerse a tus datos" solo se revisa con el código correcto.
- **Invalidar códigos anteriores.** Pedir un código nuevo marca los anteriores como `usado = 1`, sin columnas nuevas.
- **Desbloqueo por vencimiento.** Al desbloquear (RN-03) se deja `bloqueado_hasta` en NULL, para que un usuario `Activo` no tenga fecha de bloqueo.
- **Sesiones tras cambiar la contraseña.** Se invalidan todos los refresh tokens. El access token actual sigue válido hasta 30 minutos, que es lo que pide CLAUDE.md (solo habla de refresh).
- **Perfil estricto.** Si llega un campo no editable (rol, correo, documento, datos de donante, estado…), se responde 400 con "Este dato no se puede editar desde el perfil" en lugar de ignorarlo, y no se guarda nada.

## Paso 4 — Administración básica

- **Nombre de categoría único sin importar mayúsculas** ("granos" choca con "Granos") y sin espacios sobrantes.
- **`categorias/` sin paginación.** Devuelve solo las activas (id, nombre, descripción) para usarlas directo en un selector de la app.
- **Activar a un usuario `Bloqueado` responde 400.** Según RN-04, el estado `Bloqueado` solo lo manejan las sanciones.
- **Sí se puede inactivar a un usuario `Bloqueado`.** Es una baja, y RN-50 contempla un usuario `Inactivo` con una sanción vigente.
- **Al inactivar se conserva `bloqueado_hasta`.** Al reactivar (RN-75) el estado y la fecha se recalculan desde las sanciones vigentes: `Activo`, `Bloqueado` con fecha (temporal) o `Bloqueado` sin fecha (permanente). Un bloqueo temporal que venció mientras estaba inactivo se cierra (`vigente = 0`).
- **El administrador solo corrige correo y teléfono** (RN-04c). Cualquier otro campo responde 400.
- **Los efectos en cascada de RN-71/RN-73 y el endpoint `impacto/` (RN-74) quedan para el Paso 9**, marcados con `# TODO Paso 9` en `usuarios/services.py`.

## Paso 5 — Donaciones

- **Formato de envío.** `POST donaciones/` es `multipart/form-data` con un campo `datos` (JSON con la donación y su lista `alimentos`) y las fotos `foto_0`, `foto_1`… en el mismo orden. Una sola petición crea todo y las fotos viajan como archivos, no en base64. No hay límite de alimentos: Swagger muestra 4 campos de foto, pero el backend lee cualquier `foto_N`.
- **Validar todo antes de subir.** Si un alimento o una foto tiene error, se responde 400 con todos los errores juntos (por ejemplo `alimentos[1].cantidad_total` y `foto_1`) y no se sube nada a Cloudinary.
- **Sin fotos huérfanas.** Si algo falla después de subir, se borran las fotos ya subidas y la transacción deshace la base. Al eliminar, las fotos se borran con `transaction.on_commit()`: solo cuando la base confirmó el cambio.
- **Fotos validadas con Pillow.** Se verifica que el archivo sea realmente JPG, PNG o WEBP (no solo la extensión) y que pese máximo 5 MB. El nombre es aleatorio (uuid) y la extensión sale del formato real detectado.
- **Los alimentos no se editan uno por uno: se quitan y se agregan** (RN-12 dice "agregar o quitar"). No se puede quitar el último; para eso se elimina la donación.
- **Filtro `modalidad` del beneficiario.** `Recoger` y `Domicilio` incluyen también las donaciones `Ambas`, porque el filtro representa cómo quiere recibir el beneficiario. `Ambas` muestra solo las que permiten las dos.
- **Lo que ve el beneficiario en el listado.** El nombre del donante (el del negocio si es Negocio), pero no su teléfono ni la dirección de recogida, que según RN-29 solo se muestran desde que la solicitud es aprobada.
- **Novedad pendiente.** Se detecta con una subconsulta (`Exists`) que oculta tanto los alimentos con novedad propia como todos los de una donación con novedad general.
- **Notificaciones internas.** Se crean solo con `crear_notificacion()` y `notificar_administradores()` (a cada administrador `Activo`, en una sola consulta), dentro de la misma transacción de la operación: si la operación falla, la notificación tampoco se guarda.
- **`notificacion.donacion` y `notificacion.solicitud` usan `SET_NULL`** (decidido en el Paso 2): la notificación es historial y se conserva aunque se elimine una donación `Pendiente`.

## Paso 6 — Carrito y envío de solicitudes

- **RN-16 se implementó en este paso** (`recalcular_estado_donacion()`), porque es el primero que cambia cantidades. Solo usa condiciones permanentes; `Finalizada` es final; `Pendiente` y `Rechazada` no se tocan.
- **Orden fijo de bloqueos** (regla 6 de CLAUDE.md): usuario → solicitud → alimentos (por id) → configuración → donación; el código de recuperación va después del usuario. Si todas las operaciones bloquean en el mismo orden, dos peticiones nunca se quedan esperándose mutuamente (*deadlock*). Al aplicarlo se ajustaron también funciones de los pasos 3 a 5 (validar, rechazar, quitar alimento, eliminar donación, confirmar recuperación y cambiar estado).
- **Bloquear solo lo necesario.** Se usa `select_for_update(of=("self",))` cuando la consulta también lee otras tablas (donación, donante, rol), para no bloquear filas que no se van a modificar ni romper el orden.
- **Turno por beneficiario al enviar.** Se bloquea primero su fila de usuario, así dos envíos suyos al mismo tiempo no pueden pasar juntos el límite `max_solicitudes_activas`.
- **Un solo lugar explica por qué un alimento no se puede pedir** (`motivos_no_visible()`): se usa al agregar, al ver el carrito y al enviar, así el usuario siempre ve el mismo motivo.
- **El carrito avisa, pero no impide agregar** de más donaciones de las permitidas: RN-21 dice "avisar"; el bloqueo real ocurre al enviar.
- **Errores del envío agrupados** en `items`, `donaciones` y `carrito`, todos juntos en una sola respuesta 400, sin crear nada.
- **Una notificación por pedido.** `nueva_solicitud` no se enlaza a una solicitud concreta, porque un pedido agrupa varias; el `codigo_pedido` va en el mensaje.
- **Consecutivo del día.** Se calcula bloqueando la fila de `configuracion` (funciona como un turno) y ordenando primero por largo del código, para que el pedido 10000 no se confunda con el 9999.
- **Cancelar no notifica a nadie** (RN-23 no lo pide). El motivo queda como "Cancelada por el beneficiario."
- **Al devolver cantidades nunca se pasa de `cantidad_total`**, por seguridad.

## Paso 7 — Gestión de solicitudes y entregas

- **Vencimientos antes de actuar, en su propia transacción.** Antes de aprobar, rechazar o entregar se aplican RN-27 (No recogida) y RN-28 (Pendiente vencida) sobre esa solicitud, y ese cambio se confirma por separado. Si la acción que sigue falla (por ejemplo, porque la solicitud ya quedó `Cancelada`), el vencimiento no se pierde. El error dice el estado actual y, si existe, el motivo de cancelación.
- **"Al final del día" en hora de Bogotá.** El límite por vencimiento es las 23:59:59 de (vencimiento más cercano − margen) con `timezone.localtime`/`make_aware`. `fecha_limite_recogida` es la menor entre ese momento y `fecha_gestion + plazo_recogida_dias`.
- **RN-28 y RN-24 usan la misma fecha.** Una solicitud `Pendiente` está vencida cuando ese "final del día" ya pasó; por eso, en la práctica, "si la fecha límite ya pasó no se aprueba" (RN-24) se resuelve cancelándola por RN-28 antes de aprobar. Aun así, aprobar vuelve a revisarlo.
- **Solo cuentan los alimentos no cancelados** para el vencimiento más cercano, para RN-31 y al devolver cantidades.
- **RN-31 parcial desde ya.** Si hay una novedad `Pendiente` sobre un alimento no cancelado de la solicitud, o una general sobre la donación, no se puede aprobar ni entregar, y RN-27 no la marca `No recogida`. Las novedades se crean en el Paso 8.
- **Solo se rechazan solicitudes `Pendiente`** (`Pendiente → Rechazada`). Rechazar también guarda `gestionada_por` y `fecha_gestion`.
- **Entregar no cambia cantidades** (ya se reservaron al enviar), pero recalcula la donación: puede quedar `Finalizada`.
- **Quién registra la entrega.** El administrador en `admin/solicitudes/{id}/entregar/` y el donante dueño en `solicitudes/{id}/entregar/`. A un donante que no es el dueño se le responde 404 (no se revela que la solicitud existe).
- **Notificación de aprobación completa.** Al beneficiario le llegan la modalidad, la dirección (de recogida o de entrega), la fecha límite y el nombre y teléfono del donante, para que la tenga aunque luego no pueda entrar a la app (RN-70).
- **Solicitudes recibidas (RN-15).** Se agrupan por alimento e incluyen todos los estados, como historial. La dirección del beneficiario es la de entrega si la modalidad es `Domicilio` y la de su perfil si es `Recoger`; teléfono y dirección solo desde `Aprobada`.
- **Pendiente para el Paso 11:** el comando `procesar_vencimientos` y la revisión de vencimientos antes de listar.
