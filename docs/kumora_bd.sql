-- =====================================================================
-- KUMORA - Red de Alimentos Solidarios
-- Script de base de datos (MySQL 8)
-- Autor: Miguel Angel Gomez
-- Nota: es la referencia del diseño. En Aiven las tablas las crean
-- las migraciones de Django (ENUM se vuelve VARCHAR con choices).
-- =====================================================================

CREATE DATABASE IF NOT EXISTS kumora
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE kumora;

-- ---------------------------------------------------------------------
-- 1. ROL
-- ---------------------------------------------------------------------
CREATE TABLE rol (
  id      INT AUTO_INCREMENT PRIMARY KEY,
  nombre  VARCHAR(30) NOT NULL UNIQUE
);

-- ---------------------------------------------------------------------
-- 2. USUARIO
-- Campos de donante (tipo_donante, nombre_negocio, nit) solo aplican
-- al rol Donante (tipo_donante obligatorio para Donante, se valida en
-- el backend). Si es Negocio, nombre_negocio y nit son obligatorios;
-- si no es Negocio, ambos van en NULL.
-- ---------------------------------------------------------------------
CREATE TABLE usuario (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  rol_id              INT NOT NULL,
  nombres             VARCHAR(80)  NOT NULL,
  apellidos           VARCHAR(80)  NOT NULL,
  tipo_documento      ENUM('CC','CE','PAS','PPT') NOT NULL,  -- PPT: Permiso por Proteccion Temporal
  numero_documento    VARCHAR(20)  NOT NULL UNIQUE,
  correo              VARCHAR(120) NOT NULL UNIQUE,
  contrasena          VARCHAR(255) NOT NULL,
  telefono            VARCHAR(20)  NOT NULL,
  direccion           VARCHAR(200) NULL,
  estado              ENUM('Activo','Bloqueado','Inactivo') NOT NULL DEFAULT 'Activo',
  bloqueado_hasta     DATETIME NULL,
  tipo_donante        ENUM('Persona','Negocio') NULL,
  nombre_negocio      VARCHAR(120) NULL,
  nit                 VARCHAR(20)  NULL UNIQUE,
  fecha_registro      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_usuario_rol FOREIGN KEY (rol_id) REFERENCES rol(id),
  CONSTRAINT chk_usuario_negocio CHECK (
    (tipo_donante = 'Negocio' AND nombre_negocio IS NOT NULL AND nit IS NOT NULL)
    OR ((tipo_donante IS NULL OR tipo_donante = 'Persona')
        AND nombre_negocio IS NULL AND nit IS NULL)
  )
);

-- ---------------------------------------------------------------------
-- 3. CATEGORIA (CRUD del administrador)
-- Inactiva: no se usa en alimentos nuevos. Solo se borra si nadie la usa.
-- ---------------------------------------------------------------------
CREATE TABLE categoria (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  nombre       VARCHAR(60)  NOT NULL UNIQUE,
  descripcion  VARCHAR(255) NULL,
  activa       TINYINT(1)   NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------
-- 4. DONACION
-- Pendiente -> Validada / Rechazada
-- Rechazada es final: no se edita ni se reenvía (el donante crea otra).
-- Validada  -> Agotada (ningun alimento con disponibilidad y con
--              solicitudes en curso; vuelve a Validada si regresa cantidad)
--           -> Finalizada (ningun alimento con disponibilidad y sin
--              solicitudes en curso; es final)
-- Solo cuentan condiciones permanentes (RN-16).
-- ---------------------------------------------------------------------
CREATE TABLE donacion (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  donante_id          BIGINT NOT NULL,
  titulo              VARCHAR(120) NOT NULL,
  descripcion         TEXT NULL,
  direccion_recogida  VARCHAR(200) NULL,
  modalidad_entrega   ENUM('Recoger','Domicilio','Ambas') NOT NULL,
  estado              ENUM('Pendiente','Validada','Rechazada','Agotada','Finalizada')
                      NOT NULL DEFAULT 'Pendiente',
  motivo_rechazo      VARCHAR(255) NULL,
  revisada_por        BIGINT NULL,
  fecha_revision      DATETIME NULL,
  fecha_registro      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_actualizacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_donacion_donante FOREIGN KEY (donante_id)   REFERENCES usuario(id),
  CONSTRAINT fk_donacion_admin   FOREIGN KEY (revisada_por) REFERENCES usuario(id),
  CONSTRAINT chk_donacion_direccion CHECK (
    modalidad_entrega = 'Domicilio' OR direccion_recogida IS NOT NULL
  ),
  CONSTRAINT chk_donacion_rechazo CHECK (
    estado <> 'Rechazada' OR motivo_rechazo IS NOT NULL
  ),
  INDEX idx_donacion_estado (estado)
);

-- ---------------------------------------------------------------------
-- 5. DONACION_ALIMENTO (alimentos dentro de una donacion)
-- cantidad_total: lo que dona el donante, sin limite.
-- max_por_solicitud: tope por beneficiario, lo calcula el sistema
--   al validar (porcentaje_max_por_alimento de configuracion).
-- activo = 0: dado de baja por una novedad aceptada.
-- url_foto: guarda la ruta de Cloudinary (kumora/alimentos/...);
--   la URL completa se arma al leerla.
-- unidad y paquete: cantidades enteras (en carrito y detalle se valida
--   en el backend, porque depende de la unidad del alimento).
-- max_por_solicitud queda fijo al validar.
-- ---------------------------------------------------------------------
CREATE TABLE donacion_alimento (
  id                   BIGINT AUTO_INCREMENT PRIMARY KEY,
  donacion_id          BIGINT NOT NULL,
  categoria_id         INT NOT NULL,
  nombre               VARCHAR(100) NOT NULL,
  descripcion          VARCHAR(255) NULL,
  unidad_medida        ENUM('kg','g','L','ml','unidad','paquete') NOT NULL,
  cantidad_total       DECIMAL(10,2) NOT NULL,
  cantidad_disponible  DECIMAL(10,2) NOT NULL,
  max_por_solicitud    DECIMAL(10,2) NULL,
  fecha_vencimiento    DATE NOT NULL,
  url_foto             VARCHAR(500) NOT NULL,  -- Cloudinary (foto obligatoria)
  activo               TINYINT(1) NOT NULL DEFAULT 1,
  CONSTRAINT fk_alimento_donacion  FOREIGN KEY (donacion_id)  REFERENCES donacion(id) ON DELETE CASCADE,
  CONSTRAINT fk_alimento_categoria FOREIGN KEY (categoria_id) REFERENCES categoria(id),
  CONSTRAINT chk_alimento_total      CHECK (cantidad_total > 0),
  CONSTRAINT chk_alimento_disponible CHECK (cantidad_disponible >= 0 AND cantidad_disponible <= cantidad_total),
  CONSTRAINT chk_alimento_entero CHECK (
    unidad_medida NOT IN ('unidad','paquete')
    OR (cantidad_total = FLOOR(cantidad_total)
        AND cantidad_disponible = FLOOR(cantidad_disponible))
  )
);

-- ---------------------------------------------------------------------
-- 6. CARRITO_ITEM (no reserva cantidad; se reserva al enviar)
-- ---------------------------------------------------------------------
CREATE TABLE carrito_item (
  id                    BIGINT AUTO_INCREMENT PRIMARY KEY,
  beneficiario_id       BIGINT NOT NULL,
  donacion_alimento_id  BIGINT NOT NULL,
  cantidad              DECIMAL(10,2) NOT NULL,
  fecha_agregado        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_carrito_beneficiario FOREIGN KEY (beneficiario_id)      REFERENCES usuario(id),
  CONSTRAINT fk_carrito_alimento     FOREIGN KEY (donacion_alimento_id) REFERENCES donacion_alimento(id) ON DELETE CASCADE,
  CONSTRAINT uq_carrito UNIQUE (beneficiario_id, donacion_alimento_id),
  CONSTRAINT chk_carrito_cantidad CHECK (cantidad > 0)
);

-- ---------------------------------------------------------------------
-- 7. SOLICITUD (una por donacion; codigo_pedido agrupa el carrito)
-- codigo_pedido: formato KUM-AAAAMMDD-NNNN (consecutivo del dia); se
--   genera bloqueando la fila de configuracion para evitar duplicados.
-- Una solicitud Pendiente que ya no se puede aprobar a tiempo por
-- vencimiento pasa a Cancelada (RN-28).
-- Maximo una solicitud activa (Pendiente/Aprobada) por beneficiario y
-- por donacion (se valida en el backend).
-- ---------------------------------------------------------------------
CREATE TABLE solicitud (
  id                     BIGINT AUTO_INCREMENT PRIMARY KEY,
  codigo_pedido          VARCHAR(20) NOT NULL,
  beneficiario_id        BIGINT NOT NULL,
  donacion_id            BIGINT NOT NULL,
  estado                 ENUM('Pendiente','Aprobada','Rechazada','Entregada','No recogida','Cancelada')
                         NOT NULL DEFAULT 'Pendiente',
  modalidad_entrega      ENUM('Recoger','Domicilio') NOT NULL,
  direccion_entrega      VARCHAR(200) NULL,
  motivo_rechazo         VARCHAR(255) NULL,
  motivo_cancelacion     VARCHAR(255) NULL,
  gestionada_por         BIGINT NULL,
  fecha_solicitud        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_gestion          DATETIME NULL,
  fecha_limite_recogida  DATETIME NULL,
  fecha_actualizacion    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_solicitud_beneficiario FOREIGN KEY (beneficiario_id) REFERENCES usuario(id),
  CONSTRAINT fk_solicitud_donacion     FOREIGN KEY (donacion_id)     REFERENCES donacion(id),
  CONSTRAINT fk_solicitud_admin        FOREIGN KEY (gestionada_por)  REFERENCES usuario(id),
  CONSTRAINT uq_solicitud_pedido UNIQUE (codigo_pedido, donacion_id),
  CONSTRAINT chk_solicitud_direccion CHECK (
    modalidad_entrega = 'Recoger' OR direccion_entrega IS NOT NULL
  ),
  CONSTRAINT chk_solicitud_rechazo CHECK (
    estado <> 'Rechazada' OR motivo_rechazo IS NOT NULL
  ),
  INDEX idx_solicitud_pedido (codigo_pedido),
  INDEX idx_solicitud_beneficiario_estado (beneficiario_id, estado)
);

-- ---------------------------------------------------------------------
-- 8. DETALLE_SOLICITUD (que alimento, cuanto y para quien)
-- cancelado = 1: ese alimento se quito por una novedad aceptada.
-- ---------------------------------------------------------------------
CREATE TABLE detalle_solicitud (
  id                    BIGINT AUTO_INCREMENT PRIMARY KEY,
  solicitud_id          BIGINT NOT NULL,
  donacion_alimento_id  BIGINT NOT NULL,
  cantidad              DECIMAL(10,2) NOT NULL,
  cancelado             TINYINT(1) NOT NULL DEFAULT 0,
  CONSTRAINT fk_detalle_solicitud FOREIGN KEY (solicitud_id)         REFERENCES solicitud(id) ON DELETE CASCADE,
  CONSTRAINT fk_detalle_alimento  FOREIGN KEY (donacion_alimento_id) REFERENCES donacion_alimento(id),
  CONSTRAINT uq_detalle UNIQUE (solicitud_id, donacion_alimento_id),
  CONSTRAINT chk_detalle_cantidad CHECK (cantidad > 0)
);

-- ---------------------------------------------------------------------
-- 9. ENTREGA (la registra el donante o el administrador)
-- Es total: no hay entregas parciales (diferencias en observacion).
-- Confirmacion del beneficiario (RN-26b): 48 horas desde fecha_entrega
-- para responder Recibido (Confirmada) o No recibido (Reclamada, con
-- comentario). Sin respuesta pasa a Asumida. El admin revisa los
-- reclamos (Revisada, con respuesta). La solicitud sigue Entregada.
-- ---------------------------------------------------------------------
CREATE TABLE entrega (
  id                       BIGINT AUTO_INCREMENT PRIMARY KEY,
  solicitud_id             BIGINT NOT NULL UNIQUE,
  registrada_por           BIGINT NOT NULL,
  fecha_entrega            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  observacion              VARCHAR(255) NULL,
  estado_confirmacion      ENUM('Pendiente','Confirmada','Asumida','Reclamada','Revisada') NOT NULL DEFAULT 'Pendiente',
  fecha_confirmacion       DATETIME NULL,  -- cuando respondio o se asumio
  comentario_beneficiario  VARCHAR(255) NULL,
  respuesta_admin          VARCHAR(255) NULL,
  revisada_por             BIGINT NULL,
  fecha_revision           DATETIME NULL,
  CONSTRAINT fk_entrega_solicitud FOREIGN KEY (solicitud_id)   REFERENCES solicitud(id),
  CONSTRAINT fk_entrega_usuario   FOREIGN KEY (registrada_por) REFERENCES usuario(id),
  CONSTRAINT fk_entrega_revisor   FOREIGN KEY (revisada_por)   REFERENCES usuario(id),
  CONSTRAINT chk_entrega_fecha_confirmacion CHECK (
    estado_confirmacion = 'Pendiente' OR fecha_confirmacion IS NOT NULL
  ),
  CONSTRAINT chk_entrega_reclamo CHECK (
    estado_confirmacion NOT IN ('Reclamada','Revisada') OR comentario_beneficiario IS NOT NULL
  ),
  CONSTRAINT chk_entrega_revision CHECK (
    estado_confirmacion <> 'Revisada'
    OR (respuesta_admin IS NOT NULL AND revisada_por IS NOT NULL AND fecha_revision IS NOT NULL)
  ),
  INDEX idx_entrega_confirmacion (estado_confirmacion, fecha_entrega)
);

-- ---------------------------------------------------------------------
-- 10. NOVEDAD (la crea el donante; la revisa el administrador)
-- donacion_alimento_id NULL = afecta toda la donacion.
-- Maximo una novedad Pendiente por alimento y una por toda la donacion
-- (se valida en el backend).
-- ---------------------------------------------------------------------
CREATE TABLE novedad (
  id                    BIGINT AUTO_INCREMENT PRIMARY KEY,
  donante_id            BIGINT NOT NULL,
  donacion_id           BIGINT NOT NULL,
  donacion_alimento_id  BIGINT NULL,
  descripcion           TEXT NOT NULL,
  estado                ENUM('Pendiente','Aceptada','Rechazada') NOT NULL DEFAULT 'Pendiente',
  respuesta_admin       VARCHAR(255) NULL,
  revisada_por          BIGINT NULL,
  fecha_creacion        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_revision        DATETIME NULL,
  CONSTRAINT fk_novedad_donante  FOREIGN KEY (donante_id)           REFERENCES usuario(id),
  CONSTRAINT fk_novedad_donacion FOREIGN KEY (donacion_id)          REFERENCES donacion(id),
  CONSTRAINT fk_novedad_alimento FOREIGN KEY (donacion_alimento_id) REFERENCES donacion_alimento(id),
  CONSTRAINT fk_novedad_admin    FOREIGN KEY (revisada_por)         REFERENCES usuario(id),
  INDEX idx_novedad_estado (estado)
);

-- ---------------------------------------------------------------------
-- 11. SANCION (solo la crea el administrador)
-- Escala: Advertencia -> Bloqueo temporal (1 semana) -> Bloqueo permanente
-- El bloqueo permanente es definitivo.
-- No se sanciona a un Administrador ni dos veces por la misma
-- solicitud o donacion (se valida en el backend).
-- ---------------------------------------------------------------------
CREATE TABLE sancion (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  usuario_id    BIGINT NOT NULL,
  tipo          ENUM('Advertencia','Bloqueo temporal','Bloqueo permanente') NOT NULL,
  motivo        VARCHAR(255) NOT NULL,
  donacion_id   BIGINT NULL,
  solicitud_id  BIGINT NULL,
  creada_por    BIGINT NOT NULL,
  fecha_inicio  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_fin     DATETIME NULL,
  vigente       TINYINT(1) NOT NULL DEFAULT 1,
  CONSTRAINT fk_sancion_usuario   FOREIGN KEY (usuario_id)   REFERENCES usuario(id),
  CONSTRAINT fk_sancion_donacion  FOREIGN KEY (donacion_id)  REFERENCES donacion(id),
  CONSTRAINT fk_sancion_solicitud FOREIGN KEY (solicitud_id) REFERENCES solicitud(id),
  CONSTRAINT fk_sancion_admin     FOREIGN KEY (creada_por)   REFERENCES usuario(id),
  CONSTRAINT chk_sancion_fecha CHECK (
    tipo <> 'Bloqueo temporal' OR fecha_fin IS NOT NULL
  ),
  INDEX idx_sancion_usuario (usuario_id, vigente)
);

-- ---------------------------------------------------------------------
-- 12. NOTIFICACION
-- tipo: valores fijos de RN-40 (TextChoices en Django), 16 tipos:
-- los 14 originales mas entrega_reclamada y reclamo_revisado (RN-26b).
-- ---------------------------------------------------------------------
CREATE TABLE notificacion (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  usuario_id    BIGINT NOT NULL,
  tipo          VARCHAR(40)  NOT NULL,
  titulo        VARCHAR(120) NOT NULL,
  mensaje       VARCHAR(500) NOT NULL,
  donacion_id   BIGINT NULL,
  solicitud_id  BIGINT NULL,
  leida         TINYINT(1) NOT NULL DEFAULT 0,
  fecha         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_notif_usuario   FOREIGN KEY (usuario_id)   REFERENCES usuario(id),
  -- SET NULL: la notificacion es historial y se conserva aunque se elimine una donacion Pendiente.
  CONSTRAINT fk_notif_donacion  FOREIGN KEY (donacion_id)  REFERENCES donacion(id)  ON DELETE SET NULL,
  CONSTRAINT fk_notif_solicitud FOREIGN KEY (solicitud_id) REFERENCES solicitud(id) ON DELETE SET NULL,
  INDEX idx_notif_usuario (usuario_id, leida)
);

-- ---------------------------------------------------------------------
-- 13. CONFIGURACION (una sola fila; limites solo para beneficiarios)
-- max_alimentos_por_solicitud: cuantos alimentos DISTINTOS puede
--   tener una solicitud (no suma cantidades, porque las unidades
--   de medida son diferentes: kg, g, L, ml, unidad, paquete).
-- ---------------------------------------------------------------------
CREATE TABLE configuracion (
  id                           TINYINT PRIMARY KEY DEFAULT 1,
  plazo_recogida_dias          INT NOT NULL DEFAULT 3,
  margen_vencimiento_dias      INT NOT NULL DEFAULT 1,
  max_alimentos_por_solicitud  INT NOT NULL DEFAULT 10,
  porcentaje_max_por_alimento  DECIMAL(5,2) NOT NULL DEFAULT 30.00,
  max_solicitudes_activas      INT NOT NULL DEFAULT 2,
  actualizada_por              BIGINT NULL,
  fecha_actualizacion          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_config_admin FOREIGN KEY (actualizada_por) REFERENCES usuario(id),
  CONSTRAINT chk_config_unica CHECK (id = 1),
  CONSTRAINT chk_config_valores CHECK (
    plazo_recogida_dias > 0 AND margen_vencimiento_dias >= 0
    AND max_alimentos_por_solicitud > 0 AND max_solicitudes_activas > 0
    AND porcentaje_max_por_alimento > 0 AND porcentaje_max_por_alimento <= 100
  )
);

-- ---------------------------------------------------------------------
-- 14. CODIGO_VERIFICACION (codigo de 6 digitos enviado al correo)
-- Sirve para dos propositos: verificar el correo al registrarse (RN-04d)
-- y recuperar la contrasena (RN-04b). Un codigo solo vale para su
-- proposito y su correo.
-- codigo_hash: el codigo se guarda con hash, nunca en texto plano.
-- Vence en 15 minutos; maximo 5 intentos; pedir uno nuevo invalida los
-- anteriores del mismo proposito (del usuario o, en el registro, del correo).
-- usuario_id es NULL en los codigos de registro: el usuario aun no existe.
-- Antes se llamaba recuperacion_contrasena (Paso 16).
-- ---------------------------------------------------------------------
CREATE TABLE codigo_verificacion (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  usuario_id        BIGINT NULL,
  proposito         ENUM('Registro','Recuperar contraseña') NOT NULL,
  correo            VARCHAR(120) NOT NULL,
  codigo_hash       VARCHAR(255) NOT NULL,
  fecha_creacion    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_expiracion  DATETIME NOT NULL,
  intentos          INT NOT NULL DEFAULT 0,
  usado             TINYINT(1) NOT NULL DEFAULT 0,
  CONSTRAINT fk_codigo_usuario FOREIGN KEY (usuario_id) REFERENCES usuario(id) ON DELETE CASCADE,
  CONSTRAINT chk_codigo_intentos CHECK (intentos >= 0 AND intentos <= 5),
  -- Registro: sin usuario. Recuperar contrasena: siempre con usuario.
  CONSTRAINT chk_codigo_usuario CHECK (
    (proposito = 'Registro' AND usuario_id IS NULL)
    OR (proposito = 'Recuperar contraseña' AND usuario_id IS NOT NULL)
  ),
  INDEX idx_codigo_usuario (usuario_id, usado),
  INDEX idx_codigo_correo (correo, proposito, usado)
);

-- =====================================================================
-- DATOS INICIALES
-- =====================================================================
INSERT INTO rol (nombre) VALUES ('Donante'), ('Beneficiario'), ('Administrador');

INSERT INTO configuracion (id) VALUES (1);

INSERT INTO categoria (nombre, descripcion) VALUES
  ('Granos',             'Arroz, frijol, lenteja, maíz'),
  ('Lácteos',            'Leche, queso, yogur'),
  ('Frutas y verduras',  'Productos frescos'),
  ('Carnes',             'Res, pollo, cerdo, pescado'),
  ('Panadería',          'Pan, galletas, harinas'),
  ('Enlatados',          'Atún, sardinas, conservas'),
  ('Bebidas',            'Jugos, agua, bebidas no alcohólicas'),
  ('Otros',              'Aceite, panela, sal, azúcar');