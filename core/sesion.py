"""GET /api/sesion/: todo lo que la interfaz (React) necesita para dibujar CEP dentro de UAPA Suite.

Django no dibuja ninguna pantalla: React pinta los contenedores de la suite, el menú, la portada y la ventana de
acceso con estos datos, y luego carga suite-shell.js y acceso.js desde static_service.
"""
from django.contrib import messages
from django.core.cache import cache
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.urls import reverse
from django.views.decorators.cache import never_cache

from authentication import plataforma
from authentication.usuario import datos_cep, usuario_plataforma

CEP_VERSION = '2.0'  # suite-shell.js antepone «Versión»
PLATFORM_STATIC_POR_DEFECTO = 'https://gtsst.uapa.edu.do/serviceserver/static'
# Sin sesión, los enlaces de CEP que empiezan así abren la ventana de acceso en vez de navegar (acceso.js).
RUTAS_PRIVADAS = ['/create', '/request/', '/administracion']


def parametro_en_cache(nombre, defecto=''):
    clave = f'cep:parametro:{nombre}'
    valor = cache.get(clave)
    if valor is None:
        valor = plataforma.parametro(nombre, defecto) or defecto
        cache.set(clave, valor, 300)
    return valor


def static_url():
    """URL de static_service (parámetro PLATFORM_STATIC_URL): Bootstrap, iconos, suite.css y los scripts."""
    return parametro_en_cache('PLATFORM_STATIC_URL', PLATFORM_STATIC_POR_DEFECTO).rstrip('/')


def config_shell(request, cep, csrf):
    """Configuración de suite-shell.js (React la escribe en #suite-shell-config). Todo sale de la sesión."""
    config = {
        'app': 'cep',
        'pestanaNueva': False,
        'asistente': 'publico',  # AlicIA también sin sesión (auth_service decide: ASISTENTE_ANONIMO_APPS)
        'info': {'titulo': 'CEP', 'subtitulo': 'Sistema de Gestión Presupuestaria', 'version': CEP_VERSION},
    }
    if cep is None:
        config['usuario'] = None
        return config
    usuario = usuario_plataforma(request)
    config['usuario'] = {'nombre': cep['name'], 'email': usuario.get('email') or cep['email'],
                         'foto': cep['picture']}
    apps = request.session.get(plataforma.SESION_APPS)
    # Sus roles en CEP según la plataforma, bajo el nombre en la barra superior.
    config['usuario']['rol'] = next((
        [str(r) for r in a.get('roles') or [] if r] for a in apps or []
        if isinstance(a, dict) and a.get('app_slug') == 'cep'), [])
    if apps:  # sin la lista, todas salen disponibles: cada app aplica sus permisos
        config['apps'] = [a['app_slug'] for a in apps if isinstance(a, dict) and a.get('app_slug')]
    config['logout'] = {'url': reverse('logout'), 'csrf': csrf}
    config['menu'] = []
    return config


@never_cache
def sesion(request):
    cep = datos_cep(request)
    csrf = get_token(request)  # también deja la cookie csrftoken para las peticiones de React
    datos = {
        'usuario': cep,
        'csrf': csrf,
        'static': static_url(),
        'shell': config_shell(request, cep, csrf),
        'acceso': None,
        # Avisos de Django (p. ej. «Tu sesión se ha cerrado correctamente.»): se entregan una sola vez.
        'mensajes': [{'nivel': m.level_tag or 'info', 'texto': str(m)} for m in messages.get_messages(request)],
    }
    if cep is None:
        datos['acceso'] = {
            'login': reverse('login'),
            'google': reverse('auth_receiver'),
            'google_client_id': parametro_en_cache('GOOGLE_OAUTH_CLIENT_ID'),
            'rutas_privadas': RUTAS_PRIVADAS,
        }
    return JsonResponse(datos)
