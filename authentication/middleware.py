"""
SSO con la plataforma UAPA:

* Entrada automática: un anónimo que trae las cookies compartidas (access_token/refresh_token) de
  otra app entra a CEP sin escribir nada.
* Revalidación: cada 5 minutos se confirma con la plataforma que la sesión sigue vigente.
* Logout en otra app: al salir de cualquier app se borran las cookies compartidas. Si esta sesión ya las vio
  y el navegador deja de traerlas, se cierra al momento (sin esperar el aviso de la plataforma ni los 5 minutos).

Va en MIDDLEWARE después de PlataformaAuthMiddleware (authentication/usuario_plataforma.py) y MessageMiddleware.
"""
import hashlib
import logging
import time

from django.conf import settings

from . import plataforma
from .acceso import AccesoDenegado, completar_acceso, guardar_respuesta, olvidar_sesion
from .roles import roles_en
from .usuario_plataforma import cerrar_sesion

log = logging.getLogger('core.plataforma')

EXCLUIDAS = ('/accounts/logout/', '/accounts/auth-receiver/', '/static/', '/media/', '/admin/')
REVALIDAR_CADA = 5 * 60
REINTENTAR_EN = 60
SESION_CON_COOKIE = 'plataforma_con_cookie'
SESION_COOKIE_RECHAZADA = 'plataforma_cookie_rechazada'
ESTADOS_OK = ('already_logged_in', 'logged_in')


def _excluida(path):
    return path.startswith(EXCLUIDAS) or path.startswith(settings.STATIC_URL)


def _huella(access, refresh):
    return hashlib.sha256(f'{access or ""}|{refresh or ""}'.encode()).hexdigest()


def _cookies_borradas(request):
    """True si esta sesión ya vio las cookies compartidas y el navegador ya no las trae: alguien cerró la sesión
    (en esta u otra app, que las borran al salir). Sesiones que nunca las tuvieron no se tocan. En peticiones
    entre sitios el navegador puede no mandarlas: no se decide."""
    if request.COOKIES.get(plataforma.COOKIE_ACCESS) or request.COOKIES.get(plataforma.COOKIE_REFRESH):
        if not request.session.get(SESION_CON_COOKIE):
            request.session[SESION_CON_COOKIE] = True
        return False
    if request.META.get('HTTP_SEC_FETCH_SITE') == 'cross-site':
        return False
    return bool(request.session.get(SESION_CON_COOKIE))


class PlataformaMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _excluida(request.path):
            if request.user.is_authenticated:
                if _cookies_borradas(request):
                    log.info('Plataforma: las cookies compartidas ya no están (logout en otra app); se cierra la sesión')
                    self._cerrar(request)
                else:
                    self._revalidar(request)
            else:
                self._entrada_automatica(request)

        response = self.get_response(request)

        nuevas = getattr(request, '_plataforma_cookies', None)
        if nuevas:
            plataforma.poner_cookies(response, nuevas[0], nuevas[1], request)
        return response

    def _entrada_automatica(self, request):
        access = request.COOKIES.get(plataforma.COOKIE_ACCESS)
        refresh = request.COOKIES.get(plataforma.COOKIE_REFRESH)
        if not (access or refresh):
            return
        huella = _huella(access, refresh)
        if request.session.get(SESION_COOKIE_RECHAZADA) == huella:
            return  # esta misma cookie ya fue rechazada: no se vuelve a consultar
        try:
            datos = plataforma.autenticar(request)
        except plataforma.PlataformaError as error:
            if error.sin_servicio:
                log.warning('Plataforma: no se pudo validar la cookie compartida (%s)', error.code)
            else:
                # Sesión no vigente. Las cookies NO se borran: son de todas las apps.
                request.session[SESION_COOKIE_RECHAZADA] = huella
            return
        if datos.get('status') not in ESTADOS_OK:
            request.session[SESION_COOKIE_RECHAZADA] = huella
            return
        try:
            completar_acceso(request, datos)
        except AccesoDenegado:
            request.session[SESION_COOKIE_RECHAZADA] = huella

    def _revalidar(self, request):
        sesion = request.session
        if not sesion.get(plataforma.SESION_TOKENS):
            # Sesión iniciada antes del SSO: se cierra para que el usuario entre por la plataforma.
            self._cerrar(request)
            self._entrada_automatica(request)
            return
        if time.time() - (sesion.get(plataforma.SESION_TS) or 0) < REVALIDAR_CADA:
            return
        try:
            datos = plataforma.autenticar(request)
        except plataforma.PlataformaError as error:
            if error.sin_servicio:
                log.warning('Plataforma: no se pudo revalidar la sesión (%s); se reintenta en 1 minuto', error.code)
                sesion[plataforma.SESION_TS] = time.time() - REVALIDAR_CADA + REINTENTAR_EN
            else:
                self._cerrar(request)
            return
        email = ((datos.get('user') or {}).get('email') or '').strip().lower()
        if datos.get('status') not in ESTADOS_OK or email != (request.user.email or '').lower():
            self._cerrar(request)
            return
        if not roles_en(datos.get('apps')):  # ya no tiene ningún rol de CEP en la plataforma
            self._cerrar(request)
            return
        guardar_respuesta(request, datos)

    @staticmethod
    def _cerrar(request):
        olvidar_sesion(request)
        cerrar_sesion(request)
