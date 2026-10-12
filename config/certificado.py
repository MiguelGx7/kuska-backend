"""
Certificado CA de Aiven para la conexión SSL a MySQL.

En local el ca.pem está en la carpeta del proyecto (DB_SSL_CA, como siempre).
En Railway el archivo no existe (ca.pem está en .gitignore): su contenido llega
en la variable DB_SSL_CA_CONTENIDO y se escribe en un archivo temporal al
arrancar, porque el driver de MySQL necesita una ruta, no el texto.
"""

import tempfile
from pathlib import Path

NOMBRE_TEMPORAL = "kumora_aiven_ca.pem"


def ruta_certificado(base_dir, ruta=None, contenido=None):
    """
    Devuelve la ruta del ca.pem que debe usar la conexión.
    - Con `contenido` (DB_SSL_CA_CONTENIDO): lo escribe en la carpeta temporal del
      sistema y devuelve esa ruta. Acepta saltos de línea reales o escritos como \\n.
    - Si no, `ruta` (DB_SSL_CA), relativa a la raíz del proyecto o absoluta.
    """
    if contenido and contenido.strip():
        texto = contenido.strip().replace("\\n", "\n") + "\n"
        if "BEGIN CERTIFICATE" not in texto:
            raise ValueError("DB_SSL_CA_CONTENIDO no parece un certificado PEM (falta BEGIN CERTIFICATE).")
        destino = Path(tempfile.gettempdir()) / NOMBRE_TEMPORAL
        # Cada worker de gunicorn carga los settings: solo se escribe si cambió.
        if not destino.exists() or destino.read_text(encoding="utf-8") != texto:
            destino.write_text(texto, encoding="utf-8")
        return str(destino)
    if not ruta:
        raise ValueError("Falta el certificado de Aiven: define DB_SSL_CA (ruta) o DB_SSL_CA_CONTENIDO (contenido).")
    return str(Path(base_dir) / ruta)
