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

## Paso 12 — Dashboard (RN-60, RN-61)

- **Sin modelos, solo consultas.** Todo sale de agregaciones de la base (`Count`, `Sum`, `annotate`, `aggregate`). No se recorren los registros en Python: Python solo arma la lista de los 12 meses y rellena con 0.
- **Pocas consultas y siempre las mismas.** El dashboard del administrador hace unas 12 consultas: 4 de vencimientos, 2 de totales, 5 series por mes (una por cada serie) y 1 por categoría. El número **no crece con los datos**; una prueba lo comprueba comparando el conteo con 1 y con varias entregas.
- **Qué es cada número (RN-60).**
  - "Donaciones recibidas": `Validada`, `Agotada` o `Finalizada`. Una `Pendiente` aún no se revisa y una `Rechazada` nunca se recibió.
  - "Personas beneficiadas": beneficiarios **distintos** con al menos una solicitud `Entregada`.
  - Los totales son históricos (desde siempre).
- **"Alimentos entregados"** son los detalles no cancelados de solicitudes `Entregada`. La entrega es total (RN-26), así que cada detalle cuenta como un alimento entregado. Los detalles cancelados por una novedad no cuentan.
- **Las series cuentan, no suman cantidades.** Por mes se cuentan donaciones recibidas, solicitudes enviadas (en cualquier estado), entregas, alimentos donados y alimentos entregados. El donante tiene las mismas series, salvo "solicitudes".
- **Cantidades del donante agrupadas por unidad (RN-61).** `cantidades_entregadas` da, por cada `unidad_medida`, cuántos alimentos y qué cantidad total se entregó. Así 2,5 kg de arroz y 1 kg de panela suman 3,5 kg, pero nunca se suman con litros ni con unidades.
- **Meses en hora de Bogotá.** Se agrupa en la base con `TruncMonth(..., tzinfo=America/Bogota)`, que en MySQL usa `CONVERT_TZ`. Una donación del 1.º a las 03:00 UTC (22:00 del día anterior en Colombia) cuenta en el mes anterior. Esto requiere que MySQL tenga cargadas las zonas horarias; se comprobó que el servicio de Aiven las tiene.
- **Últimos 12 meses con ceros.** La serie siempre trae 12 meses, del más viejo al actual, en formato `AAAA-MM`. Los meses sin datos van en 0 para que la gráfica no tenga huecos. Lo anterior a esos 12 meses no sale en la serie, pero sí en los totales.
- **Por categoría: histórico y solo las que tienen datos.** Una sola consulta da por categoría los alimentos donados (en donaciones recibidas) y los entregados. Se omiten las categorías sin alimentos donados, para no llenar la gráfica de ceros. Van de la que tiene más alimentos donados a la que menos.
- **Datos al día.** Antes de calcular se aplican los vencimientos pendientes con las mismas funciones eficientes del Paso 11: todo para el administrador y solo lo suyo para el donante. Si nada está vencido, eso cuesta 4 consultas.
- **Solo sus datos.** `dashboard/donante/` filtra todo por el donante del token. `dashboard/admin/` es solo para administradores. Cada rol recibe 403 en el dashboard del otro.
- **RN-62, inicio del beneficiario (`dashboard/beneficiario/`, agregado durante el Paso 13).** Es un resumen para la pantalla de inicio de la app, no estadísticas. Trae:
  - las solicitudes `Pendiente` y `Aprobada`;
  - los cupos disponibles, que son `max_solicitudes_activas` − (Pendiente + Aprobada) con **mínimo 0**: si el administrador baja el límite cuando el beneficiario ya tiene más solicitudes en curso, se muestra 0 y no un número negativo;
  - la **próxima recogida**: la `Aprobada` con la fecha límite más cercana que **aún no vence**, con su código de pedido, modalidad y donación. Una `Aprobada` con el límite ya pasado solo puede existir si tiene una novedad `Pendiente` (RN-31); su fecha se recalculará, así que no se muestra como "próxima". Si no tiene ninguna, el campo es `null`;
  - las notificaciones no leídas, contadas con la misma función de `notificaciones/`.

  Antes de calcular se aplican los vencimientos de sus solicitudes en curso con la función eficiente del Paso 11: una `Aprobada` vencida pasa a `No recogida` y libera el cupo. Hace unas 6 consultas (dos de vencimientos, una agregación con dos `Count` filtrados, la configuración, la próxima recogida y las no leídas), siempre las mismas.

## Paso 13 — Pruebas y documentación

- **Cobertura por regla.** [docs/cobertura_pruebas.md](cobertura_pruebas.md) relaciona cada RN con sus pruebas. Al armarla aparecieron cuatro partes de reglas sin prueba propia, y se agregaron:
  - RN-01: `createsuperuser` asigna el rol Administrador.
  - RN-12: el administrador no puede editar ni borrar donaciones (405).
  - RN-40: los 14 tipos de notificación son exactamente los de la regla.
  - RN-50: los vencimientos también se aplican antes de rechazar una solicitud.
- **`cargar_demo` usa los servicios reales.** Registra con `RegistroSerializer` + `registrar_usuario()`, crea donaciones con `crear_donacion()`, valida, pide con el carrito y aprueba, entrega, sanciona y reporta novedades con las mismas funciones de la API. Así los datos cumplen todas las reglas (topes, reservas, estados, notificaciones). Si una regla cambia, la demo falla en lugar de quedar con datos imposibles.
- **Se planeó para respetar los límites.** Por ejemplo:
  - Para la donación `Agotada` se usan 3 unidades de leche; con el 30 % el tope por solicitud es 1, así que los 3 beneficiarios la agotan.
  - Para la `Finalizada` se usan 2 paquetes de pan, ambos entregados.
  - Ningún beneficiario pasa de 2 solicitudes en curso.
  - La `No recogida` se logra aprobando la solicitud, adelantando su fecha límite y aplicando RN-27 con `aplicar_vencimientos()`.
- **Fechas en el pasado después de crear.** `fecha_registro`, `fecha_solicitud` y `fecha_entrega` usan `auto_now_add`, así que se mueven con `update()` al final. Solo se mueven registros ya terminados; las solicitudes en curso conservan fechas actuales, para que `procesar_vencimientos` no las cambie. Una prueba lo comprueba: después de cargar la demo, el comando no encuentra nada que cambiar.
- **Códigos de pedido coherentes con la nueva fecha.** Al mover una solicitud, su código pasa a `KUM-<nuevo día>-9NNN`. El 9 evita chocar con códigos reales de ese día. También se corrige el código en las notificaciones que lo mencionan.
- **No duplica.** Si ya hay usuarios `@demo.kumora`, avisa y no crea nada. Para recargar: `--borrar` y luego cargar.
- **Todo o nada.** La carga va en una transacción. Si algo falla, la base se deshace y las fotos ya subidas a Cloudinary se borran a mano, porque Cloudinary no participa en la transacción.
- **`--borrar` limpio.** Elimina, en un orden que respeta los `PROTECT`:
  - las notificaciones de los usuarios demo y las de los administradores sobre la demo (por donación, solicitud o código de pedido);
  - las sanciones;
  - las entregas, solicitudes, carritos, novedades y donaciones;
  - y por último los usuarios.
  Las fotos se borran de Cloudinary con `on_commit`, solo si la base confirmó. Los datos reales no se tocan, y hay una prueba que lo verifica.
- **Solo con `DEBUG=True`**, para que nunca se cargue en producción. Usa el Administrador existente (el primero activo) y no le cambia la contraseña.
- **Contrato de la API.** [docs/api_schema.yaml](api_schema.yaml) se exporta con `spectacular` y valida sin advertencias.
  - Las 19 operaciones que no tenían descripción ya la tienen.
  - Un *hook* de drf-spectacular ([config/openapi.py](../config/openapi.py)) agrega a todos los endpoints sus respuestas de error en español, con ejemplos:
    - 400 en las que reciben datos o parámetros obligatorios;
    - 401 y 403 en las protegidas (y en el login);
    - 404 en las que llevan `{id}`;
    - 429 en las que tienen límite de peticiones.
  - El mismo hook agrega ejemplos de petición a los endpoints principales.
  - Estar en un solo lugar evita repetirlo en cada vista y mantiene el contrato uniforme. Una prueba (`ContratoApiTests`) falla si un endpoint nuevo queda sin descripción o sin errores.
- **Los mensajes de los ejemplos son los reales.** Los de DRF salen en español por `LANGUAGE_CODE = "es-co"`; por ejemplo, "Las credenciales de autenticación no se proveyeron."

### Revisión de producción (`check --deploy`)

No se cambió la configuración de desarrollo. Estas son las advertencias y lo que haría falta en un despliegue real:

| Advertencia | Qué significa | Qué hacer en producción |
|---|---|---|
| `security.W018` DEBUG en True | Con DEBUG se ven trazas con detalles internos, y Swagger y `cargar_demo` quedan disponibles. | `DEBUG=False` en el `.env` del servidor. |
| `security.W008` SECURE_SSL_REDIRECT | Las peticiones por HTTP no se redirigen a HTTPS, y los tokens viajarían sin cifrar. | `SECURE_SSL_REDIRECT=True`, o que el proxy (Nginx, IIS, el servicio de hosting) redirija; en ese caso también `SECURE_PROXY_SSL_HEADER`. |
| `security.W004` SECURE_HSTS_SECONDS | El navegador no recuerda que el sitio solo funciona por HTTPS. | Activar HSTS cuando HTTPS ya funcione bien: empezar con un valor bajo (3600) y subirlo a un año. Es difícil de revertir, por eso se hace al final. |
| `security.W012` SESSION_COOKIE_SECURE | La cookie de sesión (solo la usa el admin de Django) podría viajar por HTTP. | `SESSION_COOKIE_SECURE=True`. |
| `security.W016` CSRF_COOKIE_SECURE | Lo mismo con la cookie CSRF del admin de Django. | `CSRF_COOKIE_SECURE=True`. |

Además de esas advertencias, un despliegue real necesita:
- Un `SECRET_KEY` nuevo, solo del servidor.
- `ALLOWED_HOSTS` y `CORS_ALLOWED_ORIGINS` con el dominio real.
- Servir la API con un servidor de producción (Gunicorn en Linux o Waitress en Windows) en lugar de `runserver`, y `collectstatic` para el admin.
- `procesar_vencimientos` programado cada hora.
- Un plan de Aiven que no se apague por inactividad.
- Correo SMTP real.

La forma recomendada sería leer esas opciones del `.env` dentro de un bloque `if not DEBUG:` en `settings.py`, así el mismo código sirve para los dos entornos. No se hizo ahora para no tocar la configuración de desarrollo.

### Evidencia de la suite final

- **Suite completa sin `--keepdb`:** creó la base de pruebas desde cero y corrió 301 pruebas en 6015 s (unos 100 min, por la latencia con Aiven ese día). Pasaron 298.
- **Los 3 errores** fueron dos cortes de conexión del servidor (`2013 Lost connection to server during query`); el tercero heredó la transacción rota del segundo. No son fallos del código.
- **Comprobación:** esas 3 pruebas se volvieron a correr solas, también sin `--keepdb`, y pasaron.
- **Corridas anteriores:** en las dos corridas completas previas del mismo día hubo cortes iguales, siempre en pruebas distintas. Todas las demás pasaron.
- **Para una evidencia sin cortes:** correr la suite desde una red estable o contra un MySQL local.

## Paso 14 — Preparar el despliegue en Railway

La guía de clics está en [docs/despliegue_railway.md](despliegue_railway.md). Aquí se explica por qué se hizo así.

- **Servidor y estáticos.**
  - `gunicorn` 26.2.0 reemplaza a `runserver`, que no sirve para producción.
  - `whitenoise` 6.12.0 sirve los estáticos del admin y de Swagger desde el mismo proceso, sin Nginx ni un bucket aparte.
  - En producción se usa `CompressedManifestStaticFilesStorage`: archivos comprimidos y con nombre único, que el navegador puede guardar en caché por mucho tiempo.
  - En local se mantiene el almacenamiento normal, así `runserver` y las pruebas no necesitan `collectstatic`.
- **Comando de inicio en `railpack.json`.**
  - `python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 60`.
  - Se eligió `railpack.json` y no `Procfile` ni `railway.json` por tres razones:
    - Railpack, el constructor actual de Railway, marca el `Procfile` como obsoleto.
    - En `railway.json` el comando **se impondría también al servicio cron**, porque la configuración en código reemplaza la del panel.
    - Con `railpack.json` el comando es el predeterminado de la imagen, y el servicio cron lo reemplaza con su propio "Custom Start Command".
  - Con 2 workers alcanzan los recursos básicos de Railway, y un *timeout* de 60 s da margen a la subida de fotos a Cloudinary.
  - `collectstatic` corre al arrancar y no en el build, para no depender de que las variables estén disponibles durante la construcción. Tarda pocos segundos.
- **Python 3.14 (`.python-version`).**
  - Railpack acepta cualquier versión de Python ≥ 3.10 que siga con soporte, y la toma de `.python-version` (si no hay, usa 3.13).
  - Todas las dependencias funcionan en 3.14: Django 5.2 la soporta, y Pillow y las demás tienen ruedas para 3.14 o son Python puro. `mysqlclient` se compila en el build.
  - Se fija **la misma versión del desarrollo** para que producción corra lo mismo que se probó.
- **`mysqlclient` en Linux: se queda, con sus paquetes de compilación.**
  - En Linux no tiene ruedas precompiladas: se compila y necesita `pkg-config`, un compilador y los encabezados de MySQL/MariaDB.
  - Railpack detecta MySQL e instala `default-libmysqlclient-dev`, pero no `pkg-config`. Por eso `railpack.json` agrega `pkg-config` y `build-essential` al build, y `libmariadb3` (la librería que usa al correr) a la imagen final.
  - Se prefirió esto a cambiar a PyMySQL por tres razones:
    - Es el **mismo driver** con el que pasaron las 301 pruebas.
    - Es más rápido (está escrito en C).
    - Es el que indica CLAUDE.md.
  - **Plan B**, si el build fallara igual: instalar `PyMySQL` y, al inicio de `config/__init__.py`, llamar a `pymysql.install_as_MySQLdb()`. Django 5.2 pide `mysqlclient` ≥ 1.4.3, y PyMySQL se presenta como 1.4.6, así que pasa la verificación. Habría que volver a correr la suite con ese driver.
- **Certificado de Aiven por variable.** El `ca.pem` no está en git, y debe seguir así.
  - En Railway, `DB_SSL_CA_CONTENIDO` trae el texto del certificado. Al arrancar se escribe en la carpeta temporal del sistema (`kumora_aiven_ca.pem`), porque el driver necesita una ruta.
  - Cada worker de gunicorn carga los settings, así que solo se escribe si el contenido cambió.
  - Acepta saltos de línea reales o escritos como `\n`, y falla con un mensaje claro si el texto no es un PEM.
  - En local nada cambia: sin esa variable se usa `DB_SSL_CA` como antes.
  - **Agregado: `DB_SSL_CA_BASE64`.** El editor de variables de Railway borra o daña los textos de varias líneas, así que pegar el PEM directo no sirvió. El base64 es el mismo certificado en una sola línea.
    - La app lo decodifica y lo escribe en el mismo archivo temporal. Antes de decodificar quita los espacios y saltos de línea, por si se pegó partido.
    - Si el valor no es base64 válido, o al decodificarlo no queda un PEM, falla con un mensaje que dice qué variable revisar.
    - Prioridad: `DB_SSL_CA_BASE64` → `DB_SSL_CA_CONTENIDO` → `DB_SSL_CA`. Así se puede pasar de una variable a otra sin borrar la anterior.
    - Se comprobó que el comando de PowerShell de la guía genera el mismo base64 que `base64 -w0`, y que la conexión real a Aiven funciona usando solo esa variable.
  - La lógica está en `config/certificado.py` para poder probarla sola, con 4 pruebas.
  - Se comprobó una conexión real a Aiven usando solo el contenido.
- **Producción solo con `DEBUG=False`.** Un bloque `if not DEBUG:` al final de `settings.py` activa:
  - `SECURE_PROXY_SSL_HEADER`: Railway termina el HTTPS en su proxy y le pasa la petición a gunicorn por HTTP con `X-Forwarded-Proto: https`; sin esto, Django creería que la petición no es segura y redirigiría sin fin.
  - `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE` y `CSRF_COOKIE_SECURE`.
  - `SECURE_HSTS_SECONDS`, que sale del `.env` y vale **3600 por defecto** (bajo al empezar, porque es difícil de revertir).
  - Los logs de error a la consola, que es lo que muestra Railway.

  Las pruebas y el desarrollo local no ven nada de esto, porque el `.env` local tiene `DEBUG=True`.
- **`check --deploy` sin advertencias.** Con las variables de producción simuladas, las 5 advertencias del Paso 13 desaparecieron. Al activar HSTS aparecen dos más, que se **silencian a propósito** en producción:
  - `W005`, incluir subdominios: en `*.up.railway.app` no controlamos los subdominios.
  - `W021`, *preload*: entrar a la lista de preload de los navegadores es casi irreversible y no tiene sentido en un dominio de pruebas.

  Si más adelante hay un dominio propio y estable, se pueden activar.
- **`CSRF_TRUSTED_ORIGINS` desde el `.env`.** Solo lo necesita el admin de Django, que usa sesión y cookie CSRF. La API usa JWT y no le afecta.
- **`SWAGGER_PUBLICO`.** Swagger se muestra siempre con `DEBUG=True` y, en producción, solo si `SWAGGER_PUBLICO=True`, mientras se desarrollan los frontends. Después se apaga y `/api/docs/` responde 404. Se comprobó con las dos opciones.
- **Correo con tiempo límite.** Railway bloquea el SMTP saliente en los planes Trial y Hobby. Sin límite, el envío del código de recuperación dejaría un worker esperando. `EMAIL_TIMEOUT` (10 s por defecto) corta la espera; el servicio ya capturaba el error, así que la respuesta sigue siendo la misma y el fallo queda en el log. Si se deja `EMAIL_HOST_USER` vacío, el código sale en los logs. Un correo real necesita el plan Pro o un servicio de correo por HTTPS; eso no está hecho.
- **Vencimientos en Railway.** Se hace con un segundo servicio del mismo repositorio:
  - con **Cron Schedule** `0 * * * *` y como comando `python manage.py procesar_vencimientos`;
  - el comando termina solo, que es lo que exige Railway, y Railway no lanza una ejecución si la anterior sigue activa;
  - repetirlo no tiene efectos dobles (Paso 11).
- **Base de producción aparte (recomendado).** Se sugiere una base `kumora_prod` en el mismo servicio de Aiven, para no mezclar datos de desarrollo y de demostración. El primer Administrador se crea con `createsuperuser` desde el PC, apuntando a esa base: RN-01 dice que el panel no crea administradores.
- **Pruebas de este paso.** Se corrieron las de `usuarios` (las 4 del certificado, más el contrato de la API) con `--keepdb`, y `check` y `check --deploy` con variables simuladas. En otra corrida, en la misma app, hubo otro corte de la conexión con Aiven y se repitió.

### Resultado del despliegue

- **Funcionando en Railway:**
  - **Dominio:** https://kuska-backend-production.up.railway.app.
  - **Base:** `kumora_prod` en el mismo servicio de Aiven que desarrollo (`kumora`).
  - **Certificado:** por `DB_SSL_CA_BASE64`.
  - **Vencimientos:** servicio cron cada hora.
  - **Administrador de producción:** creado con `createsuperuser` desde el PC.

  CLAUDE.md (sección 2, "Entornos") resume las diferencias entre desarrollo y producción.
- **Lo que se aprendió**, con el detalle en `docs/despliegue_railway.md`:
  - **El certificado en base64.** El editor de variables de Railway daña los textos de varias líneas. Por eso se agregó `DB_SSL_CA_BASE64`, y es la forma recomendada.
  - **El `ca.pem` sin sangría.** Con espacios al inicio de las líneas, el texto pasa la revisión de BEGIN/END pero la conexión SSL falla. Se verifica porque el base64 correcto empieza por `LS0tLS1CRUdJTi`; uno que empieza por `ICAg` delata la sangría.
  - **El dominio público, no el privado.** `*.railway.internal` es la red privada entre servicios. Con él en `ALLOWED_HOSTS` o en `CSRF_TRUSTED_ORIGINS` el sitio responde 400 o el admin falla por CSRF.
  - **El CSRF del admin se verifica en incógnito.** Una cookie CSRF guardada de intentos anteriores mantiene el error aunque la configuración ya esté corregida.
- **Sin cambios de código en este cierre:** solo documentación.

## Paso 15 — Correo real en producción (Brevo)

- **Por qué Brevo y por qué por API.** Railway bloquea el SMTP saliente en los planes Trial y Hobby, así que el correo de recuperación no salía en producción. Una API de correo por **HTTPS** no está bloqueada. Se eligió **Brevo**, que tiene plan gratis (unos 300 correos al día), y se usa con **`django-anymail`** 15.2.
- **Por qué anymail.** Es un backend de correo de Django. `send_mail()` sigue siendo la misma función: solo cambia `EMAIL_BACKEND`. Por eso **el código que envía el correo no cambió**. Si mañana se cambia de proveedor, solo se cambia la configuración.
- **Elección del backend** (`config/correo.py`, `elegir_backend()`):
  1. con `BREVO_API_KEY` → Brevo;
  2. si no, con `EMAIL_HOST_USER` → SMTP, como antes;
  3. si no → consola.

  El desarrollo local no cambia: sin `BREVO_API_KEY` sigue como estaba. Está en una función aparte para probarla sin recargar los settings, como `config/certificado.py`.
- **El remitente sale del entorno.** `DEFAULT_FROM_EMAIL` debe ser un remitente **verificado en Brevo**, porque Brevo rechaza los envíos desde direcciones no verificadas. Se escribe con nombre: `Kumora <correo>`.
- **El mismo tiempo límite.** `ANYMAIL["REQUESTS_TIMEOUT"]` usa `EMAIL_TIMEOUT` (10 s); anymail trae 30 s por defecto. Si Brevo no responde, la petición no se queda colgada.
- **Si el envío falla, nada cambia para el usuario (RN-04b).** `_enviar_codigo()` ya capturaba cualquier error: la respuesta sigue siendo "Si el correo está registrado, te enviamos un código" y el error queda en el log. Así no se revela si el correo existe ni si hubo un problema. El código sí se guarda, y el usuario puede pedir otro.
- **El correo se revisó y no hubo que cambiarlo:**
  - asunto: "Kumora: código para recuperar tu contraseña";
  - cuerpo: saludo con el nombre, "Tu código para recuperar la contraseña es: NNNNNN", "Vence en 15 minutos.", "Si no lo pediste, ignora este correo." y la firma.
- **Pruebas sin internet.**
  - El contenido se prueba con el backend en memoria (locmem), que Django usa en las pruebas.
  - El envío por Brevo se prueba simulando su API (`requests.Session.request`). Se verifica que va a `https://api.brevo.com/v3/smtp/email` con la `api-key`, el remitente, el destinatario, el asunto, el texto y el tiempo límite de 10 s.
  - Se simulan dos fallos (sin conexión, y una API key inválida con 401). En los dos la respuesta es la misma y el error queda en el log.
  - La elección del backend se prueba aparte. Son 4 pruebas nuevas en `usuarios`, y la app completa pasa (71).
- **No se agregó `anymail` a `INSTALLED_APPS`.** Solo hace falta para recibir *webhooks* de seguimiento, que Kumora no usa; para enviar basta el backend.
- **Ajuste de paso: WhiteNoise solo en producción.** Desde el Paso 14, su middleware estaba siempre activo y en desarrollo y en las pruebas avisaba `No directory at: ...staticfiles`, porque en local no se corre `collectstatic`. Ahora se agrega solo con `DEBUG=False`, como el resto de lo de producción; en local los estáticos los sirve `runserver`. `check --deploy` en producción simulada sigue sin advertencias.
- **Logs para diagnosticar** (agregados cuando en producción el correo no llegaba y Brevo no mostraba registros). Nunca muestran la clave, el código ni el correo completo:
  - **Al arrancar** (`UsuariosConfig.ready()`, porque en `settings.py` el logging aún no está configurado): INFO con el backend elegido y el remitente.
  - **Avisos de configuración:** WARNING si la clave no empieza por `xkeysib-` (por ejemplo, una SMTP key `xsmtpsib-`), si falta el remitente o si producción quedó en consola.
  - **Al enviar un código:** INFO con el id del usuario.
  - **Correo que no es de ningún usuario:** WARNING con el correo enmascarado (`do***@mail.com`).
  - **Error de envío:** ahora muestra el id del usuario en lugar de su correo.
  - **La clave se limpia** de espacios y comillas alrededor, que se cuelan al pegarla en Railway.
- **LOGGING de producción revisado.** Un solo handler de consola en el logger raíz, con formato `NIVEL logger: mensaje`. Las apps de Kumora (`apps.*`) se ven desde INFO; anymail y Django, desde WARNING. Al logger `django` se le quitan sus handlers propios, para que los errores no salgan dos veces ni se intente `mail_admins`. Se comprobó con producción simulada qué sale y qué no.
- **Resultado: el correo con Brevo funciona en producción.** La causa de que no llegara no estaba en el código: Railway tenía **Auto deploy desactivado**, así que los push no se desplegaban y seguía corriendo la versión anterior, sin anymail. Por eso Brevo no registraba nada: la petición nunca salía hacia Brevo. Al activarlo y desplegar el último commit, el correo llegó. Los logs agregados para diagnosticar se mantienen; el de arranque (`Correo: se usa …`) habría delatado el problema de inmediato, porque no aparecía. En la guía de despliegue quedó el chequeo: Auto deploy activo y, en **Deployments**, el commit del despliegue activo igual al último.
- **Pendiente para un uso real:** un dominio propio autenticado en Brevo. Con un remitente `@gmail.com` los correos pueden llegar a spam.

## Paso 16 — Registro con verificación del correo (RN-04d)

- **Por qué.** Sin verificar, cualquiera podía crear cuentas con correos ajenos o mal escritos, y después no podría recuperar la contraseña. Ahora la cuenta solo se crea si quien se registra demuestra que recibe correo en esa dirección.
- **Una tabla generalizada, no una nueva.**
  - `recuperacion_contrasena` pasó a ser `codigo_verificacion`, con `proposito` (Registro, Recuperar contraseña) y `correo`, y con `usuario` opcional. Siguen siendo 14 tablas y `usuario` no ganó campos.
  - Los dos flujos son lo mismo: un código de 6 dígitos con hash, 15 minutos y 5 intentos. Separarlos duplicaría lógica y tablas.
- **La migración renombra, no borra.** `0003_codigo_verificacion` se escribió a mano: con `makemigrations` sin preguntas, Django habría borrado la tabla y creado otra, perdiendo los registros. Los registros existentes quedan como "Recuperar contraseña" con el correo de su usuario; hay una prueba de esa parte.
  - Un CHECK (`chk_codigo_usuario`) obliga a la coherencia: los de registro sin usuario, los de recuperación con usuario.
  - El índice `(correo, proposito, usado)` sirve para buscar el código de registro de un correo.
- **Un código solo sirve para su propósito y su correo.** El registro busca `proposito = Registro` y el correo exacto; la recuperación, `proposito = Recuperar contraseña` y su usuario. Hay pruebas en las dos direcciones.
- **Primero los datos, después el código.** Un error de datos (rol, NIT, contraseña débil, documento repetido) no gasta un intento del código. El usuario lo corrige y reintenta con el mismo código.
- **Un mensaje único para cualquier falla del código** (no coincide, vencido, usado o sin intentos), como en RN-04b, para no dar pistas a quien intente adivinarlo. Cada fallo suma un intento, y se guarda aunque la petición falle (el error se lanza fuera de la transacción).
- **Correo ya registrado: 400 con el mismo mensaje de RN-02** ("Ya existe una cuenta con este correo"). A diferencia de la recuperación, aquí no hay nada que ocultar: el registro ya decía lo mismo al enviar los datos.
- **Si el correo de registro no se puede enviar: 503**, y el código no queda guardado (la creación y el envío van en la misma transacción). Sin el correo no hay forma de terminar el registro, así que es mejor decirlo que responder "te enviamos un código". En la recuperación se mantiene la respuesta uniforme de RN-04b.
- **El registro devuelve lo mismo que el login** (`access`, `refresh` y los datos con el rol), porque ya se verificó que el correo es suyo: la app entra directo, sin pedir login otra vez. El login y el registro comparten `emitir_tokens()`.
- **Bloqueos (regla 6).**
  - En el registro solo se bloquea la fila del código (`select_for_update`), porque el usuario aún no existe. Si dos peticiones registran el mismo correo a la vez, la segunda espera al código y lo encuentra usado.
  - Si dos cuentas distintas chocaran por el correo o el documento, el índice único lo impide y responde 400.
  - En la recuperación se mantiene: primero el usuario y después su código.
- **Pedir otro código invalida los anteriores** del mismo correo y propósito, para que solo valga el último que llegó.
- **`createsuperuser` y `cargar_demo` no pasan por el registro público.** `cargar_demo` usa `crear_usuario()`, con las mismas validaciones pero sin código, y su `--borrar` también limpia los códigos de los correos demo.
- **El endpoint nuevo tiene el mismo throttling** "auth" (5 por minuto por IP y por endpoint).
- **Pruebas.**
  - 18 en `RegistroConCodigoTests`: pedir código con correo libre, ya registrado o con formato inválido; envío fallido (503); el nuevo invalida el anterior; límite 5/min.
  - Registrar con código válido (entra con el `access`), incorrecto (suma intento), vencido, sin intentos, usado, de otro correo, de recuperación; el de registro no sirve para recuperar; los datos se validan antes; el código es obligatorio; `crear_usuario()` sin código; la recuperación sigue funcionando.
  - Las de `RegistroTests` ahora piden el código primero.
  - Corrieron con `--keepdb` las de `usuarios` (99) y las de `cargar_demo` (3). La migración se aplicó en `test_kumora` y en la base de desarrollo `kumora`. En producción se aplica sola al desplegar, porque el arranque corre `migrate`.

## Paso 17 — Confirmación de la entrega por el beneficiario (RN-26b)

- **Para qué.** Antes, la entrega la registraba el donante o el administrador y el beneficiario no tenía voz. Ahora confirma si recibió el pedido, y si no, reclama. Así el administrador se entera de las entregas que no ocurrieron.
- **Sin tablas nuevas (siguen 14).** La confirmación es parte de la entrega: hay a lo sumo una por entrega y nace con ella. Por eso son 6 campos en `entrega` y no una tabla aparte.
- **Los datos los cuida la base, no solo el código.** Tres CHECK:
  - todo estado distinto de Pendiente exige `fecha_confirmacion`;
  - Reclamada y Revisada exigen `comentario_beneficiario`;
  - Revisada exige `respuesta_admin`, `revisada_por` y `fecha_revision`.

  Hay una prueba que intenta guardar cada caso inválido y la base lo rechaza.
- **Las 48 horas son una constante** (`Entrega.HORAS_PARA_CONFIRMAR`), como `DIAS_BLOQUEO_TEMPORAL`: es una regla del sistema, no un valor que el administrador deba cambiar. Se cuentan **horas exactas** desde `fecha_entrega`, no "hasta el final del día". `confirmar_hasta` se calcula y se muestra en la respuesta; el mensaje usa la hora de Bogotá.
- **La migración** (`solicitudes/0002`) agrega los campos con `Pendiente` por defecto y, antes de crear los CHECK, pasa las entregas que ya existían a **Asumida**, con `fecha_confirmacion = fecha_entrega`: se registraron antes de esta regla y nadie podía confirmarlas. Hay una prueba de esa parte.
- **Bloqueos (regla 6).** El orden fijo ahora es usuario → sanción → novedad → solicitud → **entrega** → alimentos → configuración → donación. Confirmar y revisar bloquean la solicitud, luego su entrega, y vuelven a revisar el estado y el plazo con las filas bloqueadas.
- **Pasar a Asumida es un solo UPDATE condicional**, en su propia transacción (`asumir_entregas_vencidas()`):
  - "Pendiente y entregada hace más de 48 horas" pasa a Asumida.
  - Es idempotente, no tiene efectos por fila y **no notifica a nadie**, para no meter ruido. Por eso no hace falta ir una por una, como en los vencimientos de solicitudes.
  - Solo toma filas de `entrega` y no espera ninguna otra, así que no puede cruzarse con el orden de bloqueos. Si coincide con una respuesta del beneficiario en el último instante, gana quien bloquee primero; el otro, al volver a revisar, ve que ya no está Pendiente.
  - Como `fecha_confirmacion` queda **el fin del plazo** (`fecha_entrega + 48 h`), da igual cuándo corra el comando.
- **Dónde se asume.** En `procesar_vencimientos`, que tiene una línea nueva en el resumen, y antes de listar `mis-solicitudes/`, `admin/solicitudes/` y `solicitudes-recibidas/` del donante. También justo antes de que el beneficiario responda: así, si el plazo ya venció, el error dice que "se dio por recibida".
- **Reclamo, a quién se avisa.** `entrega_reclamada` va a cada administrador Activo y al donante, con el código de pedido y el comentario. `reclamo_revisado` va al **beneficiario y también al donante**, con la misma respuesta. Así el donante, que se enteró del reclamo, también conoce el cierre (cambio pedido al aprobar el plan).
- **Revisar no sanciona.** Es una alerta para revisar. Si el administrador concluye que alguien falló, sanciona aparte en `admin/sanciones/` con la `solicitud_id` (RN-05, RN-07), con la escala y las validaciones de siempre.
- **Lo que ve cada uno.**
  - El beneficiario, en `mis-solicitudes/`: su entrega con estado, plazo (`confirmar_hasta`), su comentario y la respuesta. No ve quién registró ni quién revisó.
  - El administrador: todo, incluidos `registrada_por` y `revisada_por`, y el filtro `confirmacion` para listar los reclamos (`?confirmacion=Reclamada`).
  - El donante, en `solicitudes-recibidas/`: solo el estado de la confirmación, no el comentario.
- **RN-40 pasa de 14 a 16 tipos** (`entrega_reclamada`, `reclamo_revisado`). La columna `notificacion.tipo` ya era VARCHAR(40), así que solo cambian los *choices*.
- **`cargar_demo`.** Laura confirma la entrega del pan, Jorge la reclama (se avisa a los admins y a Ana) y la del arroz de Laura queda Asumida, porque se movió 38 días al pasado. Las tres se hacen con los servicios reales.

### Casos borde

- **Un beneficiario Bloqueado o Inactivo no puede responder**, porque `EsUsuarioActivo` lo impide. Si no responde en 48 horas, su entrega queda **Asumida** como cualquier otra.
- **El dashboard no cambia.** Las entregas Reclamada o Revisada siguen contando como entregadas, porque la solicitud sigue `Entregada`. El reclamo es una alerta para revisar, no anula la entrega.
- **Pasadas las 48 horas ya no se puede reclamar**, ni siquiera si la confirmación quedó Asumida. El mensaje dice hasta cuándo era el plazo.
