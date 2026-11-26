"""
Acceso a CEP por la plataforma (auth_service). No hay página de login: el acceso es la ventana emergente
de la suite (#acceso, la maneja acceso.js de static_service) sobre el inicio de CEP; estas vistas responden
JSON cuando la petición viene de acceso.js y redirigen cuando no.
"""
import json
from urllib.parse import urlencode

import jwt
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters

from . import plataforma, sesiones_locales
from .acceso import AccesoDenegado, cerrar_sesiones_de, completar_acceso, olvidar_sesion
from .usuario_plataforma import cerrar_sesion
from .usuario import datos_cep

MSG_FALTAN_DATOS = 'Escribe tu correo y tu contraseña.'
MSG_CSRF = 'La sesión expiró. Recarga la página.'


def _es_fetch(request):
    """acceso.js (ventana emergente de static_service) envía los datos por fetch y espera JSON."""
    return request.headers.get('X-Requested-With') == 'fetch'


def _csrf_invalido(request):
    return CsrfViewMiddleware(lambda r: None).process_view(request, None, (), {}) is not None


def _destino_seguro(request, url):
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return url
    return reverse('inicio')


def url_con_login(siguiente=''):
    """Inicio de CEP con la ventana de acceso abierta (acceso.js la abre con ?acceder=…)."""
    return f"{reverse('inicio')}?{urlencode({'acceder': siguiente or ''})}"


def _acceder(request, siguiente='', **credenciales):
    """Envía las credenciales a la plataforma y, si responde 200, inicia la sesión en CEP.
    Con fetch responde {"ok", "next"} o {"ok": false, "error"}; sin JavaScript, redirige."""
    try:
        datos = plataforma.autenticar(request, **credenciales)
        completar_acceso(request, datos)
    except plataforma.PlataformaError as error:
        return _rechazo(request, siguiente, error.mensaje, error.status or 503)
    except AccesoDenegado as error:
        return _rechazo(request, siguiente, error.mensaje, 403)
    destino = _destino_seguro(request, siguiente)
    if _es_fetch(request):
        return JsonResponse({'ok': True, 'next': destino})
    return redirect(destino)


def _rechazo(request, siguiente, mensaje, status):
    if _es_fetch(request):
        return JsonResponse({'ok': False, 'error': mensaje}, status=status)
    messages.error(request, mensaje)
    return redirect(url_con_login(siguiente))


@method_decorator(never_cache, name='dispatch')
class Login(View):
    """GET abre la ventana de acceso sobre el inicio; POST inicia sesión con correo y contraseña."""

    def get(self, request):
        siguiente = request.GET.get('next', '')
        if request.user.is_authenticated:
            return redirect(_destino_seguro(request, siguiente))
        return redirect(url_con_login(siguiente))

    @method_decorator(sensitive_post_parameters('password'))
    def post(self, request):
        siguiente = request.POST.get('next') or request.GET.get('next') or ''
        email = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        if not email or not password:
            return _rechazo(request, siguiente, MSG_FALTAN_DATOS, 400)
        if '@' not in email:
            email += '@uapa.edu.do'
        return _acceder(request, siguiente, method='password', email=email, password=password)


class AuthReceiver(View):
    """Botón de Google: el ID token se reenvía tal cual a la plataforma, que lo verifica.
    - acceso.js: JSON {credential, next} por fetch, con el token CSRF de Django.
    - Botón de Google en modo redirección (login_uri): formulario con `credential`; lo protege el g_csrf_token
      de Google, no el de Django, por eso la vista es csrf_exempt y el CSRF se revisa a mano en el caso fetch."""

    @method_decorator(csrf_exempt)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request):
        if _es_fetch(request):
            if _csrf_invalido(request):
                return JsonResponse({'ok': False, 'error': MSG_CSRF}, status=403)
            try:
                cuerpo = json.loads(request.body or b'{}')
            except ValueError:
                cuerpo = None
            if not isinstance(cuerpo, dict) or not cuerpo.get('credential'):
                return JsonResponse({'ok': False, 'error': 'Solicitud no válida.'}, status=400)
            return _acceder(request, str(cuerpo.get('next') or ''), method='google',
                            credential=str(cuerpo['credential']))
        credential = request.POST.get('credential')
        if not credential:
            return HttpResponse(status=400)
        return _acceder(request, method='google', credential=credential)


class Logout(View):
    """GET (o POST con CSRF): el usuario cierra sesión en CEP y en todas las apps.
    POST con logout_token: la plataforma avisa de un logout hecho en otra app (servidor a servidor)."""

    @method_decorator(csrf_exempt)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        access, refresh = plataforma.tokens(request)
        try:
            plataforma.logout(access, refresh)
        except plataforma.PlataformaError:
            pass  # el cierre local ocurre igual
        sesiones_locales.avisar_a_las_demas(request)  # solo en pruebas locales; antes de vaciar la sesión
        olvidar_sesion(request)
        cerrar_sesion(request)
        messages.success(request, 'Tu sesión se ha cerrado correctamente.')
        response = redirect('inicio')
        plataforma.borrar_cookies(response, request)
        return response

    def post(self, request):
        token = request.POST.get('logout_token')
        if token:
            return self._aviso_de_logout(token)
        # Logout del propio usuario por formulario (suite-shell.js): exige CSRF.
        if _csrf_invalido(request):
            return HttpResponseForbidden()
        return self.get(request)

    @staticmethod
    def _aviso_de_logout(token):
        try:
            llave = plataforma.llave_publica()
        except plataforma.PlataformaError:
            return HttpResponse(status=503)
        try:
            claims = jwt.decode(token, llave, algorithms=['RS256'], audience=plataforma.app_slug(),
                                options={'require': ['exp', 'aud']})
        except jwt.InvalidTokenError:
            return HttpResponse(status=400)
        email = (claims.get('email') or '').strip()
        if claims.get('type') != 'logout' or not email:
            return HttpResponse(status=400)
        cerrar_sesiones_de(email)
        return HttpResponse(status=200)


def me(request):
    """Usuario de la sesión, o 401. La interfaz usa GET /api/sesion/, que trae esto y más."""
    datos = datos_cep(request)
    if datos is None:
        return JsonResponse({'error': 'No autorizado. Por favor inicia sesión.'}, status=401)
    return JsonResponse(datos)
