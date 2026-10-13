# Cobertura de pruebas por regla de negocio

Cada regla de [CLAUDE.md](../CLAUDE.md) (sección 6) y las pruebas que la verifican. Las pruebas están en el `tests.py` de cada app. Por ejemplo, `solicitudes · AprobarSolicitudTests.test_solo_pendientes` es la prueba `test_solo_pendientes` de la clase `AprobarSolicitudTests` en `apps/solicitudes/tests.py`.

Para correr solo las pruebas de una regla, se usa la ruta completa: `python manage.py test apps.solicitudes.tests.AprobarSolicitudTests`.

Total: **360 pruebas**. Las 4 pruebas marcadas con ★ se agregaron en el Paso 13 para cubrir partes de una regla que no tenían prueba (una de ellas aparece en dos filas: RN-28 y RN-50).

## Usuarios y acceso

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-01 | El registro solo crea Donante o Beneficiario, y solo con el correo verificado; el Administrador se crea por consola con su rol | usuarios · `RegistroTests.test_rechaza_rol_administrador`, `test_registra_beneficiario_sin_datos_de_donante`, ★ `test_rn01_el_administrador_se_crea_por_consola_con_su_rol` , `RegistroConCodigoTests.test_crear_usuario_directo_no_pide_codigo` |
| RN-02 | Correo y documento únicos; datos de donante según el tipo | usuarios · `RegistroTests.test_correo_y_documento_unicos`, `test_registra_donante_negocio_sin_devolver_contrasena`, `test_negocio_exige_nombre_y_nit`, `test_donante_exige_tipo_donante`, `test_persona_no_lleva_nit`, `test_beneficiario_no_lleva_datos_de_donante`, `test_aplica_validaciones_de_contrasena` |
| RN-03 | Login: inactivo no entra; bloqueado dice hasta cuándo; el bloqueo vencido se levanta | usuarios · `LoginTests` (todas), `RefreshTests.test_rechaza_usuario_bloqueado`, `PermisosTests.test_es_usuario_activo_rechaza_bloqueado_con_token_valido`; sanciones · `EstadoYVencimientoTests.test_rn03_login_con_bloqueo_real_y_al_vencer`, `test_rn03_permanente_no_entra`, `LevantarBloqueosVencidosTests.test_bloqueado_vencido_pasa_a_activo_aunque_no_entre` |
| RN-04 | Solo Activo ↔ Inactivo; no se cambia el estado de un Administrador ni el propio | usuarios · `CambiarEstadoTests.test_no_se_aplica_a_otro_administrador`, `test_no_se_aplica_a_uno_mismo`, `test_solo_activo_o_inactivo`, `test_ya_activo`, `test_bloqueado_no_se_activa_desde_aqui`, `test_inactivar_invalida_refresh_y_corta_el_acceso` |
| RN-04a | Perfil: solo nombres, apellidos, teléfono, dirección y contraseña | usuarios · `PerfilTests` (todas), `CambiarContrasenaTests` (todas) |
| RN-04b | Recuperar contraseña con código: respuesta uniforme, hash, 15 min, 5 intentos | usuarios · `RecuperarContrasenaTests` (todas) |
| RN-04c | El admin corrige correo y teléfono; el correo sigue único | usuarios · `AdminUsuarioTests.test_corregir_correo_y_telefono`, `test_correo_debe_seguir_siendo_unico`, `test_puede_dejar_su_mismo_correo`, `test_solo_correo_y_telefono_son_editables` |
| RN-04d | Registro con código por correo: pedir código (correo libre o ya registrado, envío fallido), registrar con código válido, inválido, vencido, sin intentos, usado, de otro correo o de otro propósito; no se crea nada si falla; los datos se validan antes; límite 5/min | usuarios · `RegistroConCodigoTests` (todas), `RegistroTests` (todas, ahora con código), `MigracionCodigoVerificacionTests` |

## Sanciones

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-05 | Escala sugerida por todo el historial; motivo obligatorio; no dos veces por la misma solicitud o donación | sanciones · `CrearSancionTests.test_rn05_sugerencia_segun_todo_el_historial`, `test_motivo_obligatorio_y_tipo_valido`, `test_relacion_con_la_donacion_o_la_solicitud`, `test_rn04_no_se_sanciona_a_un_administrador` |
| RN-06 | Efectos: temporal 7 días; el permanente es definitivo y reemplaza al temporal | sanciones · `SancionBeneficiarioTests.test_rn70_bloqueo_temporal`, `test_rn06_rn71_permanente_reemplaza_al_temporal`, `test_rn06_permanente_es_definitivo`, `test_caso_limite_bloqueo_temporal_no_se_acumula` |
| RN-07 | La sanción puede referir una solicitud al beneficiario o al donante | sanciones · `CrearSancionTests.test_relacion_con_la_donacion_o_la_solicitud` |
| RN-08 | Toda sanción notifica al usuario | sanciones · `CrearSancionTests.test_rn08_notifica_y_se_lista_con_filtros`, `EstadoYVencimientoTests.test_caso_limite_sancionar_a_un_inactivo` |

## Donaciones

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-10 | Al menos un alimento, sin límite; queda Pendiente; notifica a los admins | donaciones · `CrearDonacionTests.test_crea_pendiente_con_disponible_igual_al_total_y_fotos`, `test_acepta_mas_alimentos_que_los_campos_de_swagger`, `test_exige_al_menos_un_alimento`, `test_notifica_a_los_administradores` |
| RN-10a | Enteros para unidad y paquete; hasta 2 decimales para el resto | donaciones · `CrearDonacionTests.test_unidad_y_paquete_exigen_enteros`, `test_maximo_dos_decimales`; solicitudes · `AgregarAlCarritoTests.test_enteros_para_unidad` |
| RN-11a | Foto obligatoria, imagen real JPG/PNG/WEBP, máximo 5 MB | donaciones · `CrearDonacionTests.test_foto_obligatoria`, `test_foto_debe_ser_imagen_real`, `test_foto_solo_jpg_png_webp`, `test_foto_maximo_5_mb`, `test_falta_una_foto_intermedia`, `test_mas_fotos_que_alimentos`, `test_si_falla_despues_de_subir_no_queda_nada` |
| RN-11 | Vencimiento posterior a hoy + margen (al crear y al validar) | donaciones · `CrearDonacionTests.test_vencimiento_posterior_a_hoy_mas_margen`, `AdminDonacionTests.test_no_valida_si_un_alimento_ya_no_cumple_el_vencimiento` |
| RN-12 | Pendiente: editar, agregar, quitar, eliminar; después nadie la edita | donaciones · `GestionDonacionTests` (todas), ★ `AdminDonacionTests.test_rn12_el_administrador_no_edita_ni_borra_donaciones` |
| RN-13 | Validar: revisa RN-11, calcula max_por_solicitud con redondeo; rechazar con motivo; notifica | donaciones · `AdminDonacionTests.test_validar_calcula_maximo_y_notifica`, `test_reglas_de_redondeo`, `test_solo_se_revisan_pendientes`, `test_rechazar_exige_motivo`, `test_rechazar_notifica_y_conserva_fotos` |
| RN-14 | Visible para pedir: donante Activo, Validada, activo, disponible, sin novedad Pendiente, no vencido | donaciones · `AlimentosDisponiblesTests` (todas), `NovedadDonanteTests.test_rn14_lo_afectado_deja_de_mostrarse`; sanciones · `SancionDonanteTests.test_rn72_bloqueo_temporal` |
| RN-15 | El donante ve quién pidió; contacto solo desde Aprobada | solicitudes · `SolicitudesRecibidasTests` (todas) |
| RN-16 | Estado de la donación solo con condiciones permanentes | donaciones · `RecalcularEstadoTests` (todas); solicitudes · `EnviarCarritoTests.test_recalcula_la_donacion_si_se_agota`, `ProcesarVencimientosComandoTests.test_rn16_agotada_sin_solicitudes_en_curso_pasa_a_finalizada`; sanciones · `SancionBeneficiarioTests.test_rn16_la_donacion_agotada_vuelve_a_validada` |
| RN-17 | Categoría inactiva no se usa en alimentos nuevos; solo se borra si nadie la usa | catalogo · `AdminCategoriaTests` (todas), `CategoriaPublicaTests.test_solo_activas_y_sin_paginar`; donaciones · `CrearDonacionTests.test_solo_categorias_activas`, `GestionDonacionTests.test_agregar_alimento_valida_foto_y_reglas` |

## Carrito y solicitudes

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-20 | Solo alimentos visibles, topes, una solicitud en curso por donación, no reserva | solicitudes · `AgregarAlCarritoTests` (todas) |
| RN-21 | Marca ítems inválidos y avisa de los límites | solicitudes · `VerCarritoTests` (todas), `AceptarNovedadTests.test_rn21_el_carrito_marca_el_alimento_retirado`, `RechazarNovedadTests.test_lo_afectado_vuelve_a_mostrarse_y_se_notifica_al_donante` |
| RN-22 | Enviar: una solicitud por donación, código del día, límites, modalidad, reserva, una notificación por pedido | solicitudes · `EnviarCarritoTests` (todas), `VencimientosAlListarTests.test_enviar_carrito_libera_el_cupo_de_una_aprobada_vencida` |
| RN-23 | El beneficiario solo cancela Pendiente; devuelve cantidades | solicitudes · `MisSolicitudesTests.test_cancelar_devuelve_cantidades_y_recalcula`, `test_solo_cancela_pendientes`, `test_no_devuelve_detalles_cancelados`, `test_puede_volver_a_pedir_despues_de_cancelar` |
| RN-24 | Aprobar: fecha límite = menor entre plazo y vencimiento; notifica con los datos de entrega | solicitudes · `AprobarSolicitudTests` (todas) |
| RN-25 | Rechazar con motivo; devuelve cantidades; notifica | solicitudes · `RechazarSolicitudTests` (todas) |
| RN-26 | Entrega total por el donante dueño o el admin; solo Aprobada | solicitudes · `EntregarSolicitudTests` (todas), `ConfirmacionEntregaTests.test_al_entregar_queda_pendiente_e_invita_a_confirmar` |
| RN-26b | Confirmación del beneficiario: recibido, reclamo con y sin comentario, ajena (404), no Entregada, fuera de plazo, una sola vez, bloqueado; Asumida por el comando y antes de listar; el donante ve solo el estado; revisar reclamo (con y sin respuesta, no Reclamada, solo admin); filtro `confirmacion`; las 2 notificaciones; los CHECK; la migración | solicitudes · `ConfirmacionEntregaTests` (todas), `CargarDemoTests.test_carga_todos_los_estados_sin_duplicar_y_se_puede_borrar` |
| RN-27 | Aprobada vencida → No recogida; devuelve lo no cancelado; notifica a ambos | solicitudes · `VencimientosTests.test_rn27_*`, `EntregarSolicitudTests.test_rn27_vencida_pasa_a_no_recogida_antes_de_entregar`, `ProcesarVencimientosComandoTests.test_aplica_todo_e_imprime_el_resumen` |
| RN-28 | Pendiente vencida → Cancelada con motivo | solicitudes · `VencimientosTests.test_rn28_*`, `AprobarSolicitudTests.test_rn28_pendiente_vencida_se_cancela_antes_de_aprobar`, ★ `RechazarSolicitudTests.test_rn50_aplica_vencimientos_antes_de_rechazar` |
| RN-29 | El beneficiario ve el contacto del donante solo desde Aprobada | solicitudes · `MisSolicitudesTests.test_rn29_en_pendiente_solo_el_nombre_del_donante`, `test_rn29_desde_aprobada_telefono_y_direccion` |

## Novedades

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-30 | Solo el dueño, sobre Validada o Agotada; una Pendiente por alimento y una general; notifica | donaciones · `NovedadDonanteTests` (todas) |
| RN-31 | Mientras está Pendiente: oculta, no aprueba ni entrega, no marca No recogida; fecha límite al resolver | solicitudes · `AprobarSolicitudTests.test_rn31_*`, `EntregarSolicitudTests.test_rn31_*`, `AceptarNovedadTests.test_rn31_si_la_fecha_ya_paso_la_base_es_la_revision`, `RechazarNovedadTests.test_rn31_recalcula_la_fecha_limite_si_ya_paso`, `ProcesarVencimientosComandoTests.test_rn31_con_novedad_pendiente_no_se_marca_no_recogida` |
| RN-32 | Aceptar: retira alimentos, cancela detalles o la solicitud, recalcula y notifica | solicitudes · `AceptarNovedadTests` (todas); donaciones · `AdminNovedadTests.test_solo_se_revisan_pendientes` |
| RN-33 | Rechazar con respuesta; lo afectado vuelve a mostrarse; notifica | solicitudes · `RechazarNovedadTests` (todas); donaciones · `AdminNovedadTests.test_rechazar_exige_respuesta` |

## Notificaciones

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-40 | Los 16 tipos fijos (14 + 2 de RN-26b) | ★ notificaciones · `NotificacionServiceTests.test_rn40_tipos_fijos`. Cada tipo se verifica además en la prueba de la acción que lo genera (aprobar, rechazar, novedad, sanción...). |
| RN-41 | Solo las suyas; marcar una o todas como leídas | notificaciones · `NotificacionApiTests` (todas) |

## Vencimientos automáticos

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-50 | Comando, idempotencia, aislamiento de errores, consulta eficiente y aplicación antes de listar o actuar | solicitudes · `ProcesarVencimientosComandoTests`, `VencimientosConsultaEficienteTests`, `VencimientosAlListarTests` (todas), ★ `RechazarSolicitudTests.test_rn50_aplica_vencimientos_antes_de_rechazar`; sanciones · `LevantarBloqueosVencidosTests` (todas); dashboard · `DashboardAdminTests.test_aplica_los_vencimientos_antes_de_calcular`, `DashboardDonanteTests.test_aplica_solo_sus_vencimientos`, `DashboardBeneficiarioTests.test_aplica_vencimientos_y_libera_el_cupo` |

## Dashboard

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-60 | Totales del admin, series por mes (Bogotá, 12 meses con ceros) y por categoría | dashboard · `DashboardAdminTests` (todas), `PermisosDashboardTests.test_cada_dashboard_para_su_rol` |
| RN-61 | Datos del donante, cantidades por unidad, personas ayudadas | dashboard · `DashboardDonanteTests` (todas) |
| RN-62 | Inicio del beneficiario: Pendiente y Aprobada, cupos (mínimo 0), próxima recogida o null, no leídas, 403 para otros roles | dashboard · `DashboardBeneficiarioTests` (todas) |

## Usuarios bloqueados o inactivos

| Regla | Qué se verifica | Pruebas |
|---|---|---|
| RN-70 | Beneficiario temporal: cancela Pendiente, siguen Aprobada, vacía carrito | sanciones · `SancionBeneficiarioTests.test_rn70_bloqueo_temporal` |
| RN-71 | Beneficiario permanente o inactivo: cancela Pendiente y Aprobada, avisa al donante | sanciones · `SancionBeneficiarioTests.test_rn06_rn71_permanente_reemplaza_al_temporal`, `InactivarTests.test_inactivar_beneficiario_rn71` |
| RN-72 | Donante temporal: oculta sus alimentos; las entregas las registra el admin | sanciones · `SancionDonanteTests.test_rn72_bloqueo_temporal` |
| RN-73 | Donante permanente o inactivo: rechaza Pendiente, cancela, retira y finaliza | sanciones · `SancionDonanteTests.test_rn73_bloqueo_permanente`, `test_rn73_no_toca_las_de_otro_donante`, `InactivarTests.test_inactivar_donante_rn73` |
| RN-74 | Impacto sin aplicar nada, igual a lo que pasa | sanciones · `ImpactoTests` (todas) |
| RN-75 | Reactivar: Activo o Bloqueado si sigue vigente; lo cancelado no vuelve | usuarios · `CambiarEstadoTests.test_rn75_*`; sanciones · `InactivarTests.test_rn75_reactivar_no_recupera_lo_cancelado`, `EstadoYVencimientoTests.test_caso_limite_sancionar_a_un_inactivo` |
| RN-76 | La advertencia no afecta nada | sanciones · `CrearSancionTests.test_rn76_advertencia_no_afecta_nada`, `ImpactoTests.test_advertencia_y_sin_nada_en_curso` |
| RN-77 | Al vencer el temporal, los alimentos vuelven a mostrarse | sanciones · `EstadoYVencimientoTests.test_rn77_al_vencer_el_bloqueo_los_alimentos_vuelven`, `LevantarBloqueosVencidosTests.test_bloqueado_vencido_pasa_a_activo_aunque_no_entre` |

## Seguridad y otros (sección 7 y apoyo)

| Tema | Pruebas |
|---|---|
| JWT, refresh con rotación y lista negra | usuarios · `LoginTests.test_login_devuelve_tokens_y_rol`, `RefreshTests.test_rota_y_pone_el_anterior_en_lista_negra` |
| Permisos por rol y cuenta activa en cada petición | usuarios · `PermisosTests` (todas); y la prueba `test_solo_*` de cada app |
| Throttling 5/min por IP | usuarios · `ThrottleTests` (todas) |
| Configuración (límites válidos) | catalogo · `ConfiguracionTests` (todas) |
| Notificaciones internas (recorte, a cada admin activo) | notificaciones · `NotificacionServiceTests` |
| Contrato de la API: todo endpoint con descripción y errores | usuarios · `ContratoApiTests.test_todos_los_endpoints_tienen_descripcion_y_errores` |
| Datos de demostración | solicitudes · `CargarDemoTests` (todas) |
