"""
Certificado CA de Aiven para la conexión SSL a MySQL.

En local el ca.pem está en la carpeta del proyecto (DB_SSL_CA, como siempre).
En Railway el archivo no existe (ca.pem está en .gitignore): llega en una
variable y se escribe en un archivo temporal al arrancar, porque el driver de
MySQL necesita una ruta, no el texto. Prioridad:

1. DB_SSL_CA_BASE64: el ca.pem en base64, en UNA línea (recomendado en Railway,
   porque su editor de variables daña los textos de varias líneas).
2. DB_SSL_CA_CONTENIDO: el texto del ca.pem.
3. DB_SSL_CA: la ruta al archivo (desarrollo local).
"""

import base64
import binascii
import tempfile
from pathlib import Path

NOMBRE_TEMPORAL = "kumora_aiven_ca.pem"


def _exigir_pem(texto, variable):
    if "-----BEGIN CERTIFICATE-----" not in texto or "-----END CERTIFICATE-----" not in texto:
        raise ValueError(
            f"{variable} no contiene un certificado PEM (faltan las líneas BEGIN/END CERTIFICATE)."
        )


def _escribir_temporal(texto):
    destino = Path(tempfile.gettempdir()) / NOMBRE_TEMPORAL
    # Cada worker de gunicorn carga los settings: solo se escribe si cambió.
    if not destino.exists() or destino.read_text(encoding="utf-8") != texto:
        destino.write_text(texto, encoding="utf-8")
    return str(destino)


def _desde_base64(valor):
    # Se quitan espacios y saltos de línea por si el valor se pegó partido.
    limpio = "".join(valor.split())
    try:
        texto = base64.b64decode(limpio, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        raise ValueError(
            "DB_SSL_CA_BASE64 no es un base64 válido. Genéralo desde el ca.pem en una sola línea "
            "(ver docs/despliegue_railway.md)."
        )
    _exigir_pem(texto, "DB_SSL_CA_BASE64 (ya decodificado)")
    return texto.strip() + "\n"


def ruta_certificado(base_dir, ruta=None, contenido=None, en_base64=None):
    """
    Devuelve la ruta del ca.pem que debe usar la conexión, con la prioridad
    DB_SSL_CA_BASE64 (`en_base64`) > DB_SSL_CA_CONTENIDO (`contenido`) > DB_SSL_CA (`ruta`).
    """
    if en_base64 and en_base64.strip():
        return _escribir_temporal(_desde_base64(en_base64))
    if contenido and contenido.strip():
        # Acepta saltos de línea reales o escritos como \n.
        texto = contenido.strip().replace("\\n", "\n") + "\n"
        _exigir_pem(texto, "DB_SSL_CA_CONTENIDO")
        return _escribir_temporal(texto)
    if not ruta:
        raise ValueError(
            "Falta el certificado de Aiven: define DB_SSL_CA_BASE64 o DB_SSL_CA_CONTENIDO (Railway) "
            "o DB_SSL_CA (ruta, en local)."
        )
    return str(Path(base_dir) / ruta)
