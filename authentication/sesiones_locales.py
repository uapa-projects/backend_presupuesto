"""Pruebas locales (correr-local.ps1): cerrar sesión en una app la cierra en todas las de este equipo.

En producción, al cerrar sesión la plataforma avisa a cada app (POST /accounts/logout/ con un JWT firmado) y además
las apps comparten la cookie del dominio. En local la plataforma avisa a las apps de PRODUCCIÓN, no a las de este
equipo. Por eso, al cerrar sesión en CEP se avisa de servidor a servidor a las demás apps locales
(DJANGO_APPS_LOCALES, que pone correr-local.ps1), y cuando otra app avisa, CEP borra las sesiones de ese usuario.
Es el mismo protocolo que `sesion_tuneles.py` de la suite, PopIn, Tu Espacio GTH y SIES.

A diferencia de esas apps, CEP corre en local con DEBUG = False (sin local_settings.py), así que esto no se activa con
DEBUG sino con DJANGO_APPS_LOCALES: en el servidor no existe y la ruta responde 404.
"""
import ipaddress
import json
import logging
import os

import httpx
from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .acceso import cerrar_sesiones_de
from .plataforma import SESION_USUARIO

log = logging.getLogger(__name__)

APP = 'cep'
RUTA = '/sesiones-locales/cerrar'


def _apps_locales():
    """{slug: url base} de DJANGO_APPS_LOCALES=suite=http://127.0.0.1:8000,popin=http://127.0.0.1:8001,…"""
    apps = {}
    for par in os.environ.get('DJANGO_APPS_LOCALES', '').split(','):
        slug, _, url = par.partition('=')
        if slug.strip() and url.strip().startswith(('http://', 'https://')):
            apps[slug.strip()] = url.strip().rstrip('/')
    return apps


def avisar_a_las_demas(request):
    """Llamar ANTES de vaciar la sesión (Logout): avisa a las demás apps locales de que este usuario salió."""
    otras = {s: u for s, u in _apps_locales().items() if s != APP}
    if not otras:
        return
    usuario = request.session.get(SESION_USUARIO) or {}
    datos = {'email': (usuario.get('email') or '').strip().lower(), 'user_id': usuario.get('id')}
    if not datos['email']:
        return
    for slug, base in otras.items():
        try:
            httpx.post(base + RUTA, json=datos, timeout=3)
        except httpx.HTTPError as exc:
            log.info('Logout en cadena: %s no respondió (%s)', slug, type(exc).__name__)


def _desde_este_equipo(request):
    """Solo otra app de este equipo: por 127.0.0.1 y sin pasar por un proxy ni un túnel."""
    try:
        local = ipaddress.ip_address(request.META.get('REMOTE_ADDR', '')).is_loopback
    except ValueError:
        local = False
    host = request.get_host().rsplit(':', 1)[0].strip('[]').lower()
    return (local and host in ('127.0.0.1', 'localhost', '::1')
            and 'HTTP_CF_CONNECTING_IP' not in request.META and 'HTTP_X_FORWARDED_FOR' not in request.META)


@csrf_exempt  # la llama el servidor de otra app de este equipo, no un navegador
@require_POST
def cerrar(request):
    if not _apps_locales():
        raise Http404
    if not _desde_este_equipo(request):
        return JsonResponse({'detail': 'Solo desde este equipo.'}, status=403)
    try:
        datos = json.loads(request.body or b'{}')
    except ValueError:
        datos = {}
    email = str(datos.get('email') or '').strip().lower() if isinstance(datos, dict) else ''
    cerradas = cerrar_sesiones_de(email) if email else 0
    log.info('Logout en cadena: %s sesión(es) cerrada(s)', cerradas)
    return JsonResponse({'cerradas': cerradas})
