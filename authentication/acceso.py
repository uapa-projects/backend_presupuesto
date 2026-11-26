"""
Lo que pasa en CEP cuando la plataforma confirma una sesión: comprobar que tiene acceso a CEP, abrir la sesión
de Django con el usuario de la plataforma (authentication/usuario_plataforma.py: CEP no guarda usuarios), guardar
los tokens y registrar la sesión. Lo usan Login, AuthReceiver y PlataformaMiddleware.

Tiene acceso quien tiene algún rol de CEP en la plataforma; si no, ve «Tu cuenta no está registrada…».
"""
import time
from importlib import import_module

from django.conf import settings

from . import plataforma
from .models import SesionPlataforma
from .roles import roles_en
from .usuario_plataforma import iniciar_sesion, usuario_de_la_sesion

MSG_NO_REGISTRADA = 'Tu cuenta no está registrada en CEP (Presupuesto).'
MSG_SUSPENDIDO = 'Usuario suspendido. Póngase en contacto con el administrador.'


class AccesoDenegado(Exception):
    def __init__(self, mensaje):
        super().__init__(mensaje)
        self.mensaje = mensaje


def guardar_respuesta(request, datos):
    """Guarda en la sesión los tokens (solo si vienen), el usuario de la plataforma, sus apps y la hora.
    Si llegaron tokens nuevos, los deja en request para que el middleware escriba las cookies."""
    access, refresh = datos.get('access_token'), datos.get('refresh_token')
    guardados = dict(request.session.get(plataforma.SESION_TOKENS) or {})
    if access:
        guardados['access'] = access
    if refresh:
        guardados['refresh'] = refresh
    if not guardados:
        # method "session": la plataforma no repite los tokens; se conservan los de las cookies.
        cookie_access, cookie_refresh = plataforma.tokens(request)
        guardados = {'access': cookie_access, 'refresh': cookie_refresh}
    request.session[plataforma.SESION_TOKENS] = guardados
    request.session[plataforma.SESION_USUARIO] = datos.get('user') or {}
    request.session[plataforma.SESION_APPS] = datos.get('apps') or []
    request.session[plataforma.SESION_TS] = time.time()
    request.user = usuario_de_la_sesion(request)  # con los datos y roles recién llegados
    if access or refresh:
        request._plataforma_cookies = (access, refresh)


def registrar_sesion(request, email):
    if not request.session.session_key:
        request.session.save()
    SesionPlataforma.objects.get_or_create(email=email.lower(), session_key=request.session.session_key)


def cerrar_sesiones_de(email):
    """Borra todas las sesiones de Django de ese email (aviso de logout de la plataforma)."""
    store = import_module(settings.SESSION_ENGINE).SessionStore
    filas = SesionPlataforma.objects.filter(email=email.lower())
    for fila in filas:
        store(fila.session_key).delete()
    return filas.delete()[0]


def olvidar_sesion(request):
    if request.session.session_key:
        SesionPlataforma.objects.filter(session_key=request.session.session_key).delete()


def completar_acceso(request, datos):
    """Respuesta 200 de la plataforma → usuario de CEP con sesión. Lanza AccesoDenegado si no puede entrar."""
    usuario = datos.get('user') or {}
    email = (usuario.get('email') or '').strip()
    if not email or not usuario.get('id') or not roles_en(datos.get('apps')):
        raise AccesoDenegado(MSG_NO_REGISTRADA)
    if usuario.get('is_active') is False:
        raise AccesoDenegado(MSG_SUSPENDIDO)

    iniciar_sesion(request, datos)
    guardar_respuesta(request, datos)
    registrar_sesion(request, email)
    return request.user
