"""
Ajustes al esquema OpenAPI (Swagger) que aplican a TODOS los endpoints.

drf-spectacular llama a `completar_esquema` al final de la generación
(POSTPROCESSING_HOOKS en settings). Aquí se agregan:
- las respuestas de error en español (400, 401, 403, 404, 429) con ejemplos,
  según el tipo de endpoint;
- ejemplos de petición de los endpoints principales.
Así cada vista no tiene que repetirlos y el contrato queda uniforme.
"""

ERROR = {
    "type": "object",
    "properties": {"detail": {"type": "string", "description": "Mensaje en español para el usuario."}},
    "required": ["detail"],
}
ERROR_VALIDACION = {
    "description": (
        "Errores de validación en español. Por campo: {\"campo\": [\"mensaje\"]}. "
        "Si la regla no es de un campo (por ejemplo, el estado no permite la acción): [\"mensaje\"]."
    ),
    "oneOf": [
        {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        {"type": "array", "items": {"type": "string"}},
    ],
}

# Endpoints públicos con límite de 5 peticiones por minuto por IP.
CON_LIMITE = ("/api/auth/registro/", "/api/auth/login/", "/api/auth/recuperar/")


def _respuesta(descripcion, esquema, ejemplos):
    return {
        "description": descripcion,
        "content": {
            "application/json": {
                "schema": esquema,
                "examples": {clave: {"summary": resumen, "value": valor} for clave, (resumen, valor) in ejemplos.items()},
            }
        },
    }


def _errores(path, metodo, operacion):
    publica = all(not requisito for requisito in operacion.get("security", [{}]))
    con_cuerpo = metodo in ("post", "put", "patch")
    query_obligatoria = any(
        p.get("in") == "query" and p.get("required") for p in operacion.get("parameters", [])
    )
    errores = {}
    if con_cuerpo or query_obligatoria:
        errores["400"] = _respuesta(
            "Datos inválidos o la regla de negocio no permite la acción.",
            {"$ref": "#/components/schemas/ErrorValidacion"},
            {
                "por_campo": ("Error en un campo", {"motivo_rechazo": ["Debes indicar el motivo del rechazo."]}),
                "regla": ("Regla de negocio", ["No se puede aprobar: la solicitud está Cancelada y debe estar Pendiente."]),
            },
        )
    if not publica or path == "/api/auth/login/":
        errores["401"] = _respuesta(
            "Sin token, token vencido o credenciales incorrectas.",
            {"$ref": "#/components/schemas/Error"},
            {
                "sin_token": ("Sin token", {"detail": "Las credenciales de autenticación no se proveyeron."}),
                "credenciales": ("Login fallido", {"detail": "Correo o contraseña incorrectos."}),
            },
        )
    if not publica or path == "/api/auth/login/":
        errores["403"] = _respuesta(
            "La cuenta no está activa o el rol no tiene permiso.",
            {"$ref": "#/components/schemas/Error"},
            {
                "rol": ("Rol sin permiso", {"detail": "Esta acción es solo para administradores."}),
                "bloqueado": ("Cuenta bloqueada", {"detail": "Tu cuenta está bloqueada hasta el 20/10/2026 a las 18:30."}),
            },
        )
    if "{" in path:
        errores["404"] = _respuesta(
            "No existe o no es del usuario (no se revela que existe).",
            {"$ref": "#/components/schemas/Error"},
            {"no_encontrado": ("No encontrado", {"detail": "No encontramos esa solicitud."})},
        )
    if path.startswith(CON_LIMITE):
        errores["429"] = _respuesta(
            "Más de 5 peticiones por minuto desde la misma IP.",
            {"$ref": "#/components/schemas/Error"},
            {"limite": ("Límite superado", {"detail": "Solicitud fue regulada (throttled). Se espera que esté disponible en 60 segundos."})},
        )
    return errores


# Ejemplos de petición (JSON) de los endpoints principales: (método, ruta) -> {nombre: (resumen, valor)}.
EJEMPLOS = {
    ("post", "/api/auth/registro/solicitar-codigo/"): {"pedir": ("Pedir código", {"correo": "laura@correo.com"})},
    ("post", "/api/auth/registro/"): {
        "beneficiario": ("Beneficiario", {
            "correo": "laura@correo.com", "codigo": "482913", "contrasena": "Clave.Segura2026", "rol": "Beneficiario",
            "nombres": "Laura", "apellidos": "Gómez", "tipo_documento": "CC", "numero_documento": "1012345678",
            "telefono": "3001234567", "direccion": "Calle 1 Sur # 5-20",
        }),
        "negocio": ("Donante tipo Negocio", {
            "correo": "tienda@correo.com", "codigo": "105377", "contrasena": "Clave.Segura2026", "rol": "Donante",
            "nombres": "Carlos", "apellidos": "Pérez", "tipo_documento": "CC", "numero_documento": "79123456",
            "telefono": "3007654321", "tipo_donante": "Negocio", "nombre_negocio": "Tienda La Esquina",
            "nit": "900123456-7",
        }),
    },
    ("post", "/api/auth/login/"): {"login": ("Iniciar sesión", {"correo": "laura@correo.com", "contrasena": "Clave.Segura2026"})},
    ("post", "/api/auth/refresh/"): {"refresh": ("Renovar", {"refresh": "<refresh token del login>"})},
    ("post", "/api/auth/perfil/cambiar-contrasena/"): {
        "cambiar": ("Cambiar", {"contrasena_actual": "Clave.Segura2026", "contrasena_nueva": "Otra.Clave2026"})
    },
    ("patch", "/api/auth/perfil/"): {"telefono": ("Cambiar teléfono y dirección", {"telefono": "3109998877", "direccion": "Carrera 9 # 10-11"})},
    ("post", "/api/auth/recuperar/solicitar/"): {"solicitar": ("Pedir código", {"correo": "laura@correo.com"})},
    ("post", "/api/auth/recuperar/confirmar/"): {
        "confirmar": ("Confirmar código", {"correo": "laura@correo.com", "codigo": "482913", "contrasena_nueva": "Nueva.Clave2026"})
    },
    ("post", "/api/carrito/"): {"agregar": ("Agregar 2 kg", {"donacion_alimento": 15, "cantidad": "2.00"})},
    ("patch", "/api/carrito/{id}/"): {"cantidad": ("Nueva cantidad", {"cantidad": "1.50"})},
    ("post", "/api/carrito/enviar/"): {
        "sin_eleccion": ("Donaciones con una sola modalidad", {"donaciones": []}),
        "ambas": ("Donación 'Ambas' a domicilio", {"donaciones": [
            {"donacion": 4, "modalidad_entrega": "Domicilio", "direccion_entrega": "Calle 1 Sur # 5-20"}
        ]}),
    },
    ("post", "/api/novedades/"): {
        "alimento": ("Sobre un alimento", {"donacion": 4, "donacion_alimento": 15, "descripcion": "Se dañó en la nevera."}),
        "toda": ("Sobre toda la donación", {"donacion": 4, "descripcion": "Ya no puedo entregar esta donación."}),
    },
    ("post", "/api/solicitudes/{id}/entregar/"): {"entregar": ("Con observación", {"observacion": "Faltó una panela."})},
    ("post", "/api/mis-solicitudes/{id}/confirmar-entrega/"): {
        "recibido": ("Lo recibí", {"recibido": True}),
        "reclamo": ("No lo recibí", {"recibido": False, "comentario": "Nadie llegó a la dirección acordada."}),
    },
    ("post", "/api/admin/solicitudes/{id}/revisar-reclamo/"): {
        "revisar": ("Responder", {"respuesta_admin": "El donante confirmó que no pudo ir; ya hablamos con él."})
    },
    ("post", "/api/admin/solicitudes/{id}/entregar/"): {"entregar": ("Con observación", {"observacion": "Entregado completo."})},
    ("post", "/api/admin/solicitudes/{id}/rechazar/"): {
        "rechazar": ("Rechazar", {"motivo_rechazo": "Se prioriza a familias sin entregas recientes."})
    },
    ("post", "/api/admin/donaciones/{id}/rechazar/"): {"rechazar": ("Rechazar", {"motivo_rechazo": "El detergente no es un alimento."})},
    ("post", "/api/admin/novedades/{id}/aceptar/"): {"aceptar": ("Aceptar", {"respuesta_admin": "Gracias por avisar."})},
    ("post", "/api/admin/novedades/{id}/rechazar/"): {
        "rechazar": ("Rechazar", {"respuesta_admin": "Revisamos las fotos y el alimento está en buen estado."})
    },
    ("post", "/api/admin/sanciones/"): {
        "advertencia": ("Advertencia por no recoger", {
            "usuario": 12, "tipo": "Advertencia", "motivo": "No recogió su solicitud dentro del plazo.", "solicitud": 30,
        }),
        "bloqueo": ("Bloqueo temporal", {"usuario": 12, "tipo": "Bloqueo temporal", "motivo": "Segunda vez sin recoger."}),
    },
    ("post", "/api/admin/usuarios/{id}/cambiar-estado/"): {"inactivar": ("Inactivar", {"estado": "Inactivo"})},
    ("patch", "/api/admin/usuarios/{id}/"): {"corregir": ("Corregir correo", {"correo": "laura.gomez@correo.com"})},
    ("post", "/api/admin/categorias/"): {"crear": ("Crear", {"nombre": "Cereales", "descripcion": "Avena, granola"})},
    ("patch", "/api/admin/categorias/{id}/"): {"inactivar": ("Inactivar", {"activa": False})},
    ("patch", "/api/admin/configuracion/"): {"plazo": ("Cambiar el plazo", {"plazo_recogida_dias": 4})},
}


def completar_esquema(result, generator, request, public, **kwargs):
    esquemas = result.setdefault("components", {}).setdefault("schemas", {})
    esquemas["Error"] = ERROR
    esquemas["ErrorValidacion"] = ERROR_VALIDACION

    for path, operaciones in result.get("paths", {}).items():
        for metodo, operacion in operaciones.items():
            if metodo not in ("get", "post", "put", "patch", "delete"):
                continue
            for codigo, respuesta in _errores(path, metodo, operacion).items():
                operacion["responses"].setdefault(codigo, respuesta)
            ejemplos = EJEMPLOS.get((metodo, path))
            contenido = operacion.get("requestBody", {}).get("content", {}).get("application/json")
            if ejemplos and contenido is not None:
                contenido["examples"] = {
                    clave: {"summary": resumen, "value": valor} for clave, (resumen, valor) in ejemplos.items()
                }
    return result
