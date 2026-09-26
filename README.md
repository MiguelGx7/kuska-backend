# Red de Alimentos Solidarios — Backend

Backend de la plataforma **Red de Alimentos Solidarios**, una aplicación para facilitar la donación de alimentos entre donantes, beneficiarios y administradores.

## Descripción

Un donante registra alimentos disponibles para donar. Un administrador valida la donación. Los beneficiarios consultan las donaciones disponibles y realizan solicitudes. El administrador gestiona esas solicitudes y registra la entrega final.

## Tecnologías

- **Python / Django**
- **MySQL** (alojado en [Aiven](https://aiven.io))
- Django REST Framework *(si aplica a tu API)*

## Roles del sistema

| Rol | Puede... |
|---|---|
| Donante | Registrar alimentos disponibles y su cantidad |
| Beneficiario | Consultar donaciones y realizar solicitudes |
| Administrador | Validar donaciones, gestionar solicitudes y registrar entregas |

## Requisitos previos

- Python 3.11+
- MySQL client (`mysqlclient`)
- Cuenta de servicio MySQL en Aiven (o local, para desarrollo)

## Instalación

```bash
git clone <url-del-repo>
cd <nombre-del-repo>

python -m venv venv
source venv/bin/activate      # En Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Configuración

Crea un archivo `.env` en la raíz del proyecto con tus credenciales (no lo subas al repositorio):

```env
DB_NAME=defaultdb
DB_USER=avnadmin
DB_PASSWORD=tu_password_de_aiven
DB_HOST=tu-servicio.aivencloud.com
DB_PORT=12345
DB_SSL_CA=./certs/ca.pem
SECRET_KEY=tu_secret_key_de_django
DEBUG=True
```

Descarga el certificado `ca.pem` desde el panel de Aiven (pestaña *Overview* del servicio) y colócalo en `certs/` (ya está en `.gitignore`).

## Migraciones

```bash
python manage.py makemigrations
python manage.py migrate
```

## Ejecutar el servidor

```bash
python manage.py runserver
```

El API quedará disponible en `http://localhost:8000/`.

## Estructura de la base de datos

El modelo incluye: `Usuario` (con roles Donante/Beneficiario/Administrador), `Donacion` y `DetalleDonacion`, `Solicitud` y `DetalleSolicitud`, `Entrega`, `Alimento` (catálogo), `HistorialEstado` (auditoría) y `Notificacion`.

Ver diagrama completo y diccionario de datos en la documentación del proyecto (`/docs`).

## Autores

- Miguel Angel Gomez

## Licencia

Este proyecto está bajo la licencia MIT — ver el archivo [LICENSE](LICENSE) para más detalles.