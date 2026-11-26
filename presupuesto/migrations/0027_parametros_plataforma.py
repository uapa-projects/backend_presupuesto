"""Parámetros del acceso por la plataforma (auth_service) y del diseño de UAPA Suite (static_service).

La contraseña de la cuenta de servicio queda vacía: la carga el administrador con
``python manage.py clave_servicio`` (nunca va en una migración).
"""
from django.db import migrations

PARAMETROS = [
    ('PLATFORM_AUTH_URL', 'https://gtsst.uapa.edu.do/serviceserver/auth',
     'URL base de auth_service (login centralizado de la plataforma).'),
    ('PLATFORM_APP_SLUG', 'cep', 'Slug de CEP en la plataforma.'),
    ('PLATFORM_SERVICE_USERNAME', 'cep-svc', 'Cuenta de servicio de CEP en la plataforma.'),
    ('PLATFORM_SERVICE_PASSWORD', '',
     'Contraseña de la cuenta de servicio. Cárgala con: python manage.py clave_servicio'),
    ('PLATFORM_COOKIE_DOMAIN', '.uapa.edu.do', 'Dominio de las cookies compartidas por todas las apps.'),
    ('PLATFORM_STATIC_URL', 'https://gtsst.uapa.edu.do/serviceserver/static',
     'static_service: Bootstrap, iconos, suite.css, suite-shell.js y acceso.js.'),
    ('GOOGLE_OAUTH_CLIENT_ID', '478848519153-0tklbkp5d7252099relj297632eka3qg.apps.googleusercontent.com',
     'Client ID de Google del botón «Iniciar sesión con Google» (debe estar en GOOGLE_ID_TOKEN_AUDIENCES de '
     'auth_service).'),
]


def crear(apps, schema_editor):
    Configuracion = apps.get_model('presupuesto', 'Configuracion')
    for nombre, valor, descripcion in PARAMETROS:
        Configuracion.objects.get_or_create(nombre=nombre, defaults={'valor': valor, 'descripcion': descripcion})


def borrar(apps, schema_editor):
    Configuracion = apps.get_model('presupuesto', 'Configuracion')
    Configuracion.objects.filter(nombre__in=[p[0] for p in PARAMETROS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('presupuesto', '0026_solicitudpresupuesto_fecha_actividad'),
    ]

    operations = [
        migrations.RunPython(crear, borrar),
    ]
