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
- **Comando y revisión antes de listar:** se hicieron en el Paso 11.

## Paso 8 — Novedades

- **La novedad entra en el orden de bloqueos** (regla 6): usuario → novedad → solicitud → alimentos (por id) → configuración → donación. Al aceptar o rechazar, la novedad es el punto de partida: se bloquea primero, se revisa que siga `Pendiente`, y desde ella se llega a las solicitudes, los alimentos y la donación. Ninguna otra operación bloquea una novedad después de esas filas, así que no hay bloqueos cruzados.
- **Al crear una novedad se bloquea la fila del donante** (usuario, el primero del orden). Funciona como turno: dos peticiones suyas al mismo tiempo no pueden crear dos `Pendiente` sobre el mismo alimento. No hace falta bloquear la donación.
- **Los límites de RN-30 son independientes.** Puede haber a la vez una `Pendiente` por cada alimento y una general sobre toda la donación. Resuelta una, se puede reportar otra sobre lo mismo. No se aceptan novedades sobre un alimento ya retirado (`activo = 0`).
- **Una donación `Pendiente` no lleva novedades:** el mensaje le indica al donante que la edite directamente (RN-12).
- **Qué alimentos afecta.** Una novedad sobre un alimento afecta solo a ese; una general afecta a todos los alimentos de la donación.
- **Aceptar no "devuelve" cantidades.** Los alimentos afectados quedan en `activo = 0` y `cantidad_disponible = 0`, así que no tiene sentido devolverles nada; solo se marcan `cancelado = 1` los detalles de las solicitudes `Pendiente` o `Aprobada`. Las solicitudes ya terminadas (`Entregada`, `Cancelada`…) no se tocan.
- **Fecha límite tras la novedad (RN-31/RN-32).** Para las `Aprobada` que siguen con alimentos se recalcula con la fórmula de RN-24 sin los cancelados, con base en `fecha_gestion`. Si esa fecha ya pasó (porque el límite venció mientras la novedad estaba `Pendiente`), la base pasa a ser la fecha de revisión de la novedad. Al rechazar se aplica lo mismo, pero solo a las `Aprobada` afectadas cuyo límite ya pasó.
- **Notificaciones que explican el porqué.** Al beneficiario le llega `alimento_cancelado` (qué se retiró, la descripción de la novedad y, si aplica, la nueva fecha límite) o `solicitud_cancelada` si su solicitud se quedó sin alimentos. Al donante, `novedad_aceptada` o `novedad_rechazada` con la respuesta del administrador.
- **Al rechazar, se avisa al beneficiario solo si su fecha límite cambió** (ajuste hecho al inicio del Paso 9). Si a una solicitud `Aprobada` se le recalcula la fecha límite por RN-31, el beneficiario recibe la nueva fecha; si nada cambió para él, no se le avisa. Como los tipos de RN-40 son fijos, se usa `solicitud_aprobada`: el aviso es sobre su solicitud aprobada y trae, como en RN-24, la fecha límite. No se usa `novedad_rechazada`, porque ese tipo es para el donante y el beneficiario no sabe de la novedad.
- **Impacto antes de decidir.** El detalle de la novedad para el administrador incluye `solicitudes_afectadas`: cuántas solicitudes en curso se ajustarían si la acepta.
- **Respuesta del administrador:** obligatoria al rechazar (RN-33) y opcional al aceptar.
- **RN-14, RN-21 y RN-31 ya consideraban las novedades** desde los pasos 5 a 7; no hubo que cambiarlas. Se comprobó con pruebas que lo afectado se oculta mientras la novedad está `Pendiente`, vuelve a mostrarse al rechazarla y queda como "retirado" en el carrito al aceptarla.
- **Importación circular.** `donaciones/services.py` usa `calcular_fecha_limite()` y `texto_fecha()` de `solicitudes/services.py`, que a su vez importa `donaciones/services.py`. Para no crear un ciclo, esas dos funciones se importan dentro de la función que las usa.

## Paso 9 — Sanciones y bloqueos

- **La sanción entra en el orden de bloqueos** (regla 6): usuario → sanción → novedad → solicitud → alimentos (por id) → configuración → donación. Al sancionar se bloquea primero al usuario, que funciona como turno: dos sanciones a la vez, o una sanción y una inactivación, no pasan juntas las validaciones (por ejemplo, "no dos veces por la misma solicitud"). Después se bloquean sus sanciones vigentes, porque el permanente deja `vigente = 0` al temporal. Por último, la cascada baja a solicitudes, alimentos y donaciones. Ninguna otra operación bloquea sanciones después de esas filas.
- **En la cascada se bloquea todo de una vez y en orden.** Primero todas las solicitudes afectadas (por id), luego todos sus alimentos (por id) y al final las donaciones (por id); recién entonces se cambian. Así, aunque haya varias solicitudes, nunca se bloquea una donación antes que una solicitud. En el caso del donante, las donaciones se revisan ya bloqueadas: si una `Pendiente` se valida justo en ese momento, se trata con su estado real.
- **Escala (RN-05).** La sugerencia es el nivel siguiente al más alto que el usuario ya recibió, contando todas sus sanciones (vigentes o no). Si ya tiene un permanente no hay sugerencia (`null`). El panel la consulta en `admin/usuarios/{id}/impacto/`, que además trae el historial (cuántas de cada tipo). El administrador envía el `tipo` al crear: confirma la sugerencia o, si lo justifica, elige otro.
- **Una sola fuente para el impacto y la cascada (RN-74).** `_alcance()` arma las consultas de lo que se afecta según el rol y el efecto. El impacto las **cuenta** y la cascada las **aplica**; por eso lo que ve el administrador coincide con lo que pasa. Las pruebas lo comprueban comparando el impacto con los `efectos` que devuelve la sanción creada.
- **Tres efectos.** Advertencia: no cambia nada (RN-76). Bloqueo temporal: RN-70 o RN-72. Bloqueo permanente o inactivación: RN-71 o RN-73. Inactivar y bloquear de forma permanente cierran exactamente lo mismo; cambia solo el motivo que se guarda ("desactivada" o "bloqueo permanente").
- **Beneficiario.** Con el temporal (RN-70) se cancelan sus `Pendiente`, sus `Aprobada` siguen y se vacía el carrito. Con el permanente o la inactivación (RN-71) se cancelan las `Pendiente` y las `Aprobada`. En ambos casos se devuelven las cantidades (con lo que vuelve a recalcularse la donación, RN-16). Solo en RN-71 se avisa al donante, porque así lo pide la regla; en RN-70 solo se cancelan `Pendiente`, de las que el donante nunca recibió aviso.
- **Donante.** Con el temporal (RN-72) no se cambia ningún registro: sus alimentos se ocultan solos porque RN-14 exige que el donante esté `Activo`, y vuelven solos al levantarse el bloqueo (RN-77). Las solicitudes siguen; como el donante no puede entrar, la entrega la registra el administrador. Con el permanente o la inactivación (RN-73): sus donaciones `Pendiente` pasan a `Rechazada` (con motivo, `revisada_por` = el administrador que actuó), sus solicitudes en curso a `Cancelada` (con aviso a cada beneficiario), los alimentos de sus donaciones `Validada`/`Agotada` quedan `activo = 0` y `cantidad_disponible = 0`, y esas donaciones pasan a `Finalizada` usando `recalcular_estado_donacion()` (RN-16). No se devuelven cantidades porque los alimentos se retiran del todo.
- **Al bloquear se cierran sus sesiones** (se invalidan todos sus refresh tokens) y el permiso `EsUsuarioActivo` lo corta en la siguiente petición, aunque su access token siga vigente. La advertencia no cierra sesiones.
- **Bloqueo temporal de 7 días** fijo (constante `DIAS_BLOQUEO_TEMPORAL`), como dice RN-06; no está en `configuracion` porque el script no lo tiene.
- **Relación con la donación o la solicitud.** Si se envían, deben ser del usuario: la donación como su donante o como una donación de la que pidió; la solicitud como su beneficiario o como solicitud sobre su donación. Si se envían las dos, la solicitud debe ser de esa donación. No se sanciona dos veces al mismo usuario por la misma solicitud ni por la misma donación (RN-05).
- **Respuesta de la sanción.** Trae `efectos` con los conteos de lo que cambió. La notificación al usuario (RN-08) incluye el tipo, el motivo, hasta cuándo (si es temporal) y cuántas solicitudes se le cancelaron.
- **Caso límite: sancionar a un usuario `Inactivo`.** Se permite y queda registrada (cuenta para la escala), pero el estado sigue `Inactivo` y no se aplica cascada: al inactivarlo ya se cerró todo (RN-71/RN-73). El impacto lo advierte. Si luego se reactiva, RN-75 lo deja `Bloqueado` mientras ese bloqueo siga vigente.
- **Caso límite: bloqueo temporal a quien ya tiene uno vigente.** Se rechaza con un mensaje que dice hasta cuándo va el actual: los temporales no se acumulan ni se alargan. Si la falta es grave, la escala ya sugiere el permanente. Sí se puede registrar una advertencia mientras está bloqueado.
- **Caso límite: usuario con bloqueo permanente.** No se le registran más sanciones, porque es el nivel máximo y definitivo (RN-06).
- **Bloqueos vencidos.** Antes de sancionar o calcular el impacto se aplica RN-03 (`desbloquear_si_vencido`), para no tratar como bloqueado a quien ya cumplió su bloqueo.
- **RN-03, RN-75 y RN-77 siguen funcionando con sanciones reales.** Se comprobó con pruebas: login bloqueado con fecha y desbloqueo al vencer; reactivar a un inactivo con bloqueo vigente; y alimentos del donante que vuelven a mostrarse al vencer su bloqueo. No hubo que cambiarlos.
- **Bloqueos vencidos sin que el usuario entre:** se resolvió en el Paso 11 con `procesar_vencimientos` (RN-50). Además se levantan al iniciar sesión, en cualquier petición suya, al reactivarlo o al sancionarlo de nuevo.
- **Novedades de un donante con bloqueo permanente.** Si tenía una novedad `Pendiente`, queda para que el administrador la resuelva: sus donaciones ya están `Finalizada` y aceptarla o rechazarla no cambia nada más. No se cierra sola porque ninguna regla lo pide.

## Paso 10 — Notificaciones

- **Cada usuario ve solo las suyas** (RN-41). La consulta siempre filtra por el usuario del token. Marcar como leída una ajena responde **404** y no 403, para no revelar que existe. Un id que no es número también da 404.
- **Orden y filtros.** De la más nueva a la más vieja (por `fecha` y, si empatan, por `id`); filtros `leida` y `tipo`. No hay búsqueda ni otro orden: la app no los necesita.
- **Contador `no_leidas` dentro del listado.** La paginación normal (`count`, `next`, `previous`, `results`) trae además el total de no leídas del usuario. Así la app pinta el contador sin pedir otra ruta. El total no depende de los filtros ni de la página (si se filtra `tipo=sancion`, el contador sigue siendo el total del usuario).
- **Leer una ya leída no falla:** responde 200 y no cambia nada.
- **`leer-todas/` devuelve cuántas cambió** (`marcadas`; las que ya estaban leídas no cuentan) y el nuevo `no_leidas` (0). Es un solo `UPDATE`, sin traer las filas.
- **Sirve para los tres roles.** El administrador también recibe notificaciones (`nueva_donacion`, `nueva_solicitud`, `novedad_creada`), así que la ruta solo exige un usuario `Activo`.
- **Revisión de RN-40: se usan los 14 tipos.** `nueva_donacion` (crear donación), `donacion_validada` y `donacion_rechazada` (revisión de la donación), `nueva_solicitud` (enviar carrito), `solicitud_aprobada` (aprobar y nueva fecha límite tras una novedad rechazada), `solicitud_rechazada`, `solicitud_cancelada` (RN-28, novedad aceptada, RN-71 y RN-73), `alimento_cancelado` (novedad aceptada con cancelación parcial), `solicitud_entregada`, `solicitud_no_recogida` (RN-27), `novedad_creada`, `novedad_aceptada`, `novedad_rechazada` y `sancion`. Algunos casos no tienen aviso porque ninguna regla lo pide: cancelar una solicitud el propio beneficiario (RN-23), las `Pendiente` canceladas por RN-70 al donante, y el rechazo automático de donaciones `Pendiente` por RN-73 (el donante ya recibe la notificación `sancion`).

## Paso 11 — Vencimientos (RN-50)

- **El comando reutiliza las funciones que ya existían.**
  - RN-27 y RN-28 con `aplicar_vencimientos()`, la misma que se usa antes de aprobar, rechazar o entregar.
  - Los bloqueos con `desbloquear_si_vencido()`, la misma del login (RN-03).
  - RN-16 con `recalcular_estado_donacion()`.
  - Lo único nuevo es cómo encontrar los registros y cómo contarlos.
- **Orden del comando.** Primero las solicitudes, porque devolver cantidades recalcula la donación. Después los bloqueos y al final las donaciones, para las que quedaron desactualizadas por otra causa (por ejemplo, un alimento que venció sin que nadie lo pidiera).
- **Un registro por transacción.** Cada solicitud, usuario y donación se procesa en su propia transacción. Si uno falla, el error queda en el log, se cuenta en "Errores" y se sigue con los demás.
- **Se puede ejecutar varias veces sin efectos dobles.** Cada función vuelve a revisar el estado con la fila bloqueada antes de cambiarla. Una solicitud que ya pasó a `No recogida` no se vuelve a tocar, así que no se devuelve la cantidad dos veces ni se repiten notificaciones. Si dos ejecuciones coinciden, la segunda espera el bloqueo y luego no encuentra nada que hacer.
- **Bloqueos vencidos.**
  - A quien está `Bloqueado` con `bloqueado_hasta` vencido se le pasa a `Activo` y su temporal queda `vigente = 0`.
  - A quien está `Inactivo` solo se le cierra la sanción y el estado no cambia; RN-75 decide al reactivarlo.
  - Los alimentos del donante vuelven a mostrarse solos (RN-77), porque RN-14 solo exige que el donante esté `Activo`.
  - El cierre de sanciones de los inactivos excluye a los `Bloqueado`: si a uno de ellos le falló el desbloqueo, no se le cierra la sanción dejándolo bloqueado sin motivo.
- **Consultas eficientes.**
  - Para no revisar los registros uno por uno, una sola consulta con subconsultas `EXISTS` devuelve solo los ids que de verdad cambiarían:
    - `Pendiente` con algún detalle no cancelado cuyo vencimiento sea menor que hoy + margen. Es lo mismo que "vencimiento − margen ya pasó" de RN-28.
    - `Aprobada` con `fecha_limite_recogida` vencida y sin novedad `Pendiente` (RN-31).
  - En las donaciones, la consulta busca las `Validada` sin disponibilidad y las `Agotada` con disponibilidad o sin solicitudes en curso. Son justo los casos en que RN-16 daría otro estado.
  - Si no hay nada vencido, el costo es de **2 consultas** (la configuración y la búsqueda). Las pruebas lo verifican con `assertNumQueries`.
- **Antes de listar.**
  - Rutas: `mis-solicitudes/`, `admin/solicitudes/`, `donaciones/` del donante, `admin/donaciones/` y `donaciones/{id}/solicitudes-recibidas/`.
  - Se aplican los vencimientos solo a los registros que ese listado puede mostrar y que de verdad estén vencidos: las solicitudes del beneficiario, las de las donaciones del donante, las de esa donación, o todas para el administrador.
  - Se usa el conjunto **antes de los filtros y de la paginación**. Si se procesara solo la página o lo ya filtrado, `?estado=Aprobada` mostraría una solicitud que en realidad acaba de pasar a `No recogida`, y `?estado=No recogida` no la mostraría.
  - En los listados del administrador la búsqueda recorre todo, pero es una sola consulta. Además, como el comando corre cada hora, casi nunca hay algo vencido que procesar.
- **También antes de enviar el carrito** (RN-50). Se aplican los vencimientos a las solicitudes en curso del beneficiario: una `Aprobada` vencida pasa a `No recogida` y deja de ocupar un cupo de `max_solicitudes_activas`.
- **`aplicar_vencimientos()` ahora devuelve el nuevo estado** (`No recogida`, `Cancelada` o nada) en lugar de verdadero/falso, para que el comando pueda contar cada caso.
- **Dónde vive el comando:** `apps/solicitudes/management/commands/procesar_vencimientos.py`. Solo coordina: la lógica está en los `services.py` de solicitudes, sanciones y donaciones.

### Cómo programar el comando en producción

Se recomienda **cada hora**. El plazo de recogida se mide en días, así que una hora de retraso no afecta. Además, los listados y las acciones aplican los vencimientos de lo que tocan aunque el comando no haya corrido.

**Windows (Programador de tareas)**, desde una consola como administrador:

```
schtasks /Create /SC HOURLY /TN "Kumora - procesar vencimientos" ^
  /TR "cmd /c cd /d C:\ruta\kuska-backend && venv\Scripts\python.exe manage.py procesar_vencimientos >> logs\vencimientos.log 2>&1"
```

- Se cambia `C:\ruta\kuska-backend` por la carpeta real y se crea antes la carpeta `logs`.
- El `cd` hace falta para que Django encuentre el `.env`.
- Para revisar la tarea: `schtasks /Query /TN "Kumora - procesar vencimientos"`. Para correrla a mano: `schtasks /Run /TN "Kumora - procesar vencimientos"`.
- También se puede crear desde la interfaz gráfica: Programador de tareas → Crear tarea básica → Diariamente, repetir cada 1 hora → Iniciar un programa (`cmd`), con los mismos argumentos.

**Linux (cron)**, con `crontab -e`:

```
0 * * * * cd /ruta/kuska-backend && venv/bin/python manage.py procesar_vencimientos >> logs/vencimientos.log 2>&1
```

En los dos casos el resumen y los errores quedan en `logs/vencimientos.log`. Si aparece "Errores: N", se revisa el detalle en ese archivo y se vuelve a ejecutar: lo que ya se procesó no se repite.
