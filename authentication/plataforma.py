"""
Cliente de la plataforma de servicios UAPA (auth_service).

CEP ya no valida tokens de Google ni contraseñas: envía los datos a ``POST /auth/authenticate`` y actúa
según la respuesta. La configuración está en la tabla ``presupuesto.Configuracion`` (por migración de datos):

    PLATFORM_AUTH_URL, PLATFORM_APP_SLUG, PLATFORM_SERVICE_USERNAME, PLATFORM_SERVICE_PASSWORD,
    PLATFORM_COOKIE_DOMAIN

Regla: ninguna función de este módulo escribe contraseñas ni tokens en el log.
"""
import ipaddress
import logging

import httpx
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger('core.plataforma')

TIMEOUT = 10
ACCESS_MAX_AGE = 30 * 60
REFRESH_MAX_AGE = 14 * 24 * 60 * 60
LLAVE_CACHE_SEGUNDOS = 60 * 60

COOKIE_ACCESS = 'access_token'
COOKIE_REFRESH = 'refresh_token'
SESION_TOKENS = 'plataforma_tokens'
SESION_USUARIO = 'plataforma_usuario'
SESION_APPS = 'plataforma_apps'
SESION_TS = 'plataforma_ts'

_CACHE_TOKEN_SERVICIO = 'plataforma:token_servicio'
_CACHE_LLAVE = 'plataforma:llave_publica'

# Códigos 401 que se refieren al usuario y no al token de la cuenta de servicio.
CODIGOS_USUARIO = {'invalid_credentials', 'google_invalid_token', 'not_logged_in'}

MSG_NO_DISPONIBLE = 'El servicio de acceso no está disponible. Inténtalo más tarde.'
MSG_NO_CONFIGURADO = 'El servicio de acceso no está disponible.'
MSG_DEMASIADOS = 'Demasiados intentos. Espera un minuto.'

# Las pruebas inyectan aquí un httpx.MockTransport; en producción queda en None (red real).
transporte = None


class PlataformaError(Exception):
    """Error de la plataforma. `status` 0 = sin respuesta (red caída o timeout)."""

    def __init__(self, status, code, mensaje):
        super().__init__(f'{status} {code}')
        self.status = status
        self.code = code
        self.mensaje = mensaje

    @property
    def sin_servicio(self):
        """Red caída, 5xx o CEP mal configurado: no dice nada sobre la sesión del usuario."""
        return self.status == 0 or self.status >= 500 or self.code in (
            'app_not_found', 'service_not_configured', 'service_auth_failed')


# --- configuración ----------------------------------------------------------------------------

def parametro(nombre, defecto=''):
    from presupuesto.models import Configuracion
    fila = Configuracion.objects.filter(nombre=nombre).first()
    return (fila.valor or '').strip() if fila else defecto


def url_base():
    return parametro('PLATFORM_AUTH_URL').rstrip('/')


def app_slug():
    return parametro('PLATFORM_APP_SLUG', 'cep') or 'cep'


def cookie_domain():
    return parametro('PLATFORM_COOKIE_DOMAIN', '.uapa.edu.do')


def _cliente(**kwargs):
    return httpx.Client(timeout=TIMEOUT, transport=transporte, **kwargs)


def _post(ruta, **kwargs):
    try:
        with _cliente() as cliente:
            return cliente.post(url_base() + ruta, **kwargs)
    except httpx.HTTPError as exc:
        log.warning('Plataforma: sin respuesta en %s (%s)', ruta, type(exc).__name__)
        raise PlataformaError(0, 'network', MSG_NO_DISPONIBLE)


def _error(response):
    """Convierte una respuesta de error en PlataformaError, decidiendo siempre por `code`."""
    status = response.status_code
    if status == 429:
        return PlataformaError(429, 'rate_limited', MSG_DEMASIADOS)
    if status >= 500:
        log.error('Plataforma: HTTP %s en %s', status, response.request.url.path if response.request else '?')
        return PlataformaError(status, 'server_error', MSG_NO_DISPONIBLE)
    try:
        detalle = response.json().get('detail') or {}
    except ValueError:
        detalle = {}
    if not isinstance(detalle, dict):
        detalle = {'message': str(detalle)}
    code = detalle.get('code') or 'error'
    mensaje = detalle.get('message') or MSG_NO_DISPONIBLE
    if code == 'app_not_found':
        log.error('Plataforma: app_not_found. Revise el parámetro PLATFORM_APP_SLUG (%s).', app_slug())
        mensaje = MSG_NO_DISPONIBLE
    return PlataformaError(status, code, mensaje)


# --- cuenta de servicio -----------------------------------------------------------------------

def _token_servicio(renovar=False):
    if not renovar:
        token = cache.get(_CACHE_TOKEN_SERVICIO)
        if token:
            return token
    usuario = parametro('PLATFORM_SERVICE_USERNAME')
    clave = parametro('PLATFORM_SERVICE_PASSWORD')
    if not url_base() or not usuario or not clave:
        log.error('Plataforma: falta configurar PLATFORM_AUTH_URL, PLATFORM_SERVICE_USERNAME o '
                  'PLATFORM_SERVICE_PASSWORD en Configuración (python manage.py clave_servicio).')
        raise PlataformaError(503, 'service_not_configured', MSG_NO_CONFIGURADO)
    response = _post('/service-auth/login', json={'username': usuario, 'password': clave})
    if response.status_code != 200:
        log.error('Plataforma: la cuenta de servicio %s no pudo autenticarse (HTTP %s).', usuario,
                  response.status_code)
        raise PlataformaError(503, 'service_auth_failed', MSG_NO_DISPONIBLE)
    token = response.json().get('access_token')
    cache.set(_CACHE_TOKEN_SERVICIO, token, 25 * 60)
    return token


# --- utilidades de petición -------------------------------------------------------------------

def ip_cliente(request):
    """IP del usuario final. X-Forwarded-For solo se cree si REMOTE_ADDR es un proxy de confianza (Apache)."""
    remota = request.META.get('REMOTE_ADDR', '')
    if remota in getattr(settings, 'PLATFORM_TRUSTED_PROXIES', ()):
        reenviada = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if reenviada:
            return reenviada.split(',')[0].strip()
    return remota


def tokens(request):
    """(access, refresh): las cookies del navegador mandan (otra app pudo rotarlas); si no, la sesión."""
    access = request.COOKIES.get(COOKIE_ACCESS)
    refresh = request.COOKIES.get(COOKIE_REFRESH)
    if access or refresh:
        return access or None, refresh or None
    guardados = request.session.get(SESION_TOKENS) or {}
    return guardados.get('access') or None, guardados.get('refresh') or None


# --- API --------------------------------------------------------------------------------------

def autenticar(request, method=None, email=None, password=None, credential=None):
    """Llama a POST /auth/authenticate. Devuelve el JSON de la respuesta 200 o lanza PlataformaError."""
    access, refresh = tokens(request)
    cuerpo = {'app_slug': app_slug(), 'access_token': access, 'refresh_token': refresh}
    if method == 'password':
        cuerpo.update(method='password', email=email, password=password)
    elif method == 'google':
        cuerpo.update(method='google', credential=credential)

    for intento in range(2):
        token = _token_servicio(renovar=intento > 0)
        response = _post('/auth/authenticate', json=cuerpo, headers={
            'Authorization': f'Bearer {token}',
            'X-Client-IP': ip_cliente(request),
        })
        if response.status_code == 200:
            return response.json()
        error = _error(response)
        if error.status == 401 and error.code not in CODIGOS_USUARIO and intento == 0:
            continue  # token de servicio vencido: se pide otro y se reintenta una vez
        raise error


def logout(access_token, refresh_token):
    """Cierra la sesión del usuario en todas las apps (POST /auth/logout)."""
    cookies = {}
    if access_token:
        cookies[COOKIE_ACCESS] = access_token
    if refresh_token:
        cookies[COOKIE_REFRESH] = refresh_token
    if not cookies:
        return
    try:
        with _cliente(cookies=cookies) as cliente:
            response = cliente.post(url_base() + '/auth/logout', json={'app_slug': app_slug()})
    except httpx.HTTPError as exc:
        log.warning('Plataforma: sin respuesta en /auth/logout (%s)', type(exc).__name__)
        raise PlataformaError(0, 'network', MSG_NO_DISPONIBLE)
    if response.status_code >= 400:
        raise _error(response)


def llave_publica():
    """Llave pública (PEM) para validar los avisos de logout. Se guarda 1 hora en la caché."""
    llave = cache.get(_CACHE_LLAVE)
    if llave:
        return llave
    try:
        with _cliente() as cliente:
            response = cliente.get(url_base() + '/auth/public-key')
    except httpx.HTTPError as exc:
        log.warning('Plataforma: no se pudo descargar la llave pública (%s)', type(exc).__name__)
        raise PlataformaError(0, 'network', MSG_NO_DISPONIBLE)
    if response.status_code != 200:
        log.warning('Plataforma: /auth/public-key respondió HTTP %s', response.status_code)
        raise PlataformaError(response.status_code, 'public_key', MSG_NO_DISPONIBLE)
    llave = response.text
    if response.headers.get('content-type', '').startswith('application/json'):
        datos = response.json()
        llave = datos.get('public_key') or datos.get('key') or llave
    cache.set(_CACHE_LLAVE, llave, LLAVE_CACHE_SEGUNDOS)
    return llave


# --- cookies compartidas ----------------------------------------------------------------------

def _host(request):
    return request.get_host().rsplit(':', 1)[0].strip('[]').lower()


def _host_en_dominio(request):
    dominio = cookie_domain().lower()
    host = _host(request)
    return bool(dominio) and (host.endswith(dominio) or '.' + host == dominio)


def host_local(request):
    """Solo desarrollo (DEBUG): CEP abierto en 127.0.0.1, localhost o una IP privada. Ahí no aplica el dominio
    de las cookies (.uapa.edu.do): se escriben solo para ese host, como hace la suite local."""
    if not settings.DEBUG or request is None:
        return False
    host = _host(request)
    if host in ('127.0.0.1', 'localhost', '::1'):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def poner_cookies(response, access, refresh, request):
    """Escribe las cookies compartidas por todas las apps, solo si CEP está bajo PLATFORM_COOKIE_DOMAIN
    (o, en desarrollo, para el host local)."""
    if _host_en_dominio(request):
        comunes = {'domain': cookie_domain(), 'path': '/', 'httponly': True, 'secure': True, 'samesite': 'Lax'}
    elif host_local(request):
        # Chrome acepta cookies «secure» por http solo en 127.0.0.1 / localhost.
        seguro = request.is_secure() or _host(request) in ('127.0.0.1', 'localhost', '::1')
        comunes = {'domain': None, 'path': '/', 'httponly': True, 'secure': seguro, 'samesite': 'Lax'}
    else:
        return
    if access:
        response.set_cookie(COOKIE_ACCESS, access, max_age=ACCESS_MAX_AGE, **comunes)
    if refresh:
        response.set_cookie(COOKIE_REFRESH, refresh, max_age=REFRESH_MAX_AGE, **comunes)


def borrar_cookies(response, request=None):
    local = request is not None and not _host_en_dominio(request) and host_local(request)
    dominio = None if local else cookie_domain()
    for nombre in (COOKIE_ACCESS, COOKIE_REFRESH):
        response.delete_cookie(nombre, path='/', domain=dominio, samesite='Lax')
