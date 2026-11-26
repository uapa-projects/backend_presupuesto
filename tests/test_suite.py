"""Partes 2 a 4 con la interfaz en React: Django solo entrega la SPA, GET /api/sesion/ (esqueleto de UAPA Suite,
ventana de acceso, mensajes) y errores en JSON."""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.urls import reverse

from presupuesto.models import Configuracion

from .base import CEPTestCase, crear_usuario, respuesta_ok

STATIC = 'https://gtsst.uapa.edu.do/serviceserver/static'


class SesionTests(CEPTestCase):

    def sesion(self):
        r = self.client.get('/api/sesion/')
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_sin_sesion(self):
        datos = self.sesion()
        self.assertIsNone(datos['usuario'])
        self.assertEqual(datos['static'], STATIC)
        self.assertTrue(datos['csrf'])
        self.assertIn('csrftoken', self.client.cookies)  # React la usa en X-CSRFToken
        self.assertEqual(datos['shell']['app'], 'cep')
        self.assertIsNone(datos['shell']['usuario'])
        self.assertEqual(datos['shell']['asistente'], 'publico')  # AlicIA también sin sesión
        self.assertNotIn('logout', datos['shell'])
        acceso = datos['acceso']
        self.assertEqual(acceso['login'], reverse('login'))
        self.assertEqual(acceso['google'], reverse('auth_receiver'))
        self.assertTrue(acceso['google_client_id'].startswith('478848519153-'))
        self.assertEqual(acceso['rutas_privadas'], ['/create', '/request/', '/administracion'])

    def test_con_sesion(self):
        self.login(crear_usuario(), foto_url='https://lh3.googleusercontent.com/f',
                   apps=[{'app_slug': 'cep', 'roles': ['Compra RSD']}, {'app_slug': 'popin', 'roles': []}])
        datos = self.sesion()
        self.assertEqual(datos['usuario']['role'], 'COMPRA RSD')
        self.assertEqual(datos['usuario']['email'], 'ana@uapa.edu.do')
        self.assertIsNone(datos['acceso'])
        shell = datos['shell']
        self.assertFalse(shell['pestanaNueva'])
        self.assertEqual(shell['info']['titulo'], 'CEP')
        self.assertNotIn('Versión', shell['info']['version'])
        self.assertEqual(shell['usuario'], {'nombre': 'Ana Pérez', 'email': 'ana@uapa.edu.do',
                                            'foto': 'https://lh3.googleusercontent.com/f',
                                            'rol': ['Compra RSD']})  # sus roles en CEP, bajo el nombre
        self.assertEqual(shell['apps'], ['cep', 'popin'])
        self.assertEqual(shell['logout'], {'url': reverse('logout'), 'csrf': datos['csrf']})

    def test_sin_lista_de_apps_no_se_inventan_permisos(self):
        self.login(crear_usuario(), apps=[])
        self.assertNotIn('apps', self.sesion()['shell'])

    def test_static_url_sale_del_parametro(self):
        Configuracion.objects.filter(nombre='PLATFORM_STATIC_URL').update(valor='https://otro.example/static/')
        self.assertEqual(self.sesion()['static'], 'https://otro.example/static')

    def test_sin_cache(self):
        self.assertIn('no-cache', self.client.get('/api/sesion/')['Cache-Control'])

    def test_mensajes_de_django_se_entregan_una_vez(self):
        self.login(crear_usuario())
        self.client.get(reverse('logout'))
        self.assertEqual(self.sesion()['mensajes'],
                         [{'nivel': 'success', 'texto': 'Tu sesión se ha cerrado correctamente.'}])
        self.assertEqual(self.sesion()['mensajes'], [])

    def test_ningun_token_de_la_plataforma_en_la_respuesta(self):
        self.login(crear_usuario())
        texto = self.client.get('/api/sesion/').content.decode()
        self.assertNotIn('access-de-prueba', texto)
        self.assertNotIn('refresh-de-prueba', texto)


class SpaTests(CEPTestCase):
    """Django entrega el index.html del build tal cual: no dibuja pantallas."""

    def setUp(self):
        super().setUp()
        carpeta = Path(tempfile.mkdtemp())
        self.index = carpeta / 'index.html'
        self.index.write_text('<!DOCTYPE html><div id="root"></div>{% no es plantilla %}', encoding='utf-8')
        parche = mock.patch('presupuesto.views.INDEX_HTML', self.index)
        parche.start()
        self.addCleanup(parche.stop)

    def test_inicio_entrega_el_build_sin_procesar(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode(), self.index.read_text(encoding='utf-8'))
        self.assertIn('no-cache', r['Cache-Control'])

    def test_cualquier_pantalla_es_la_spa_con_o_sin_sesion(self):
        for ruta in ('/request/7', '/create', '/no-existe'):
            self.assertEqual(self.client.get(ruta).status_code, 200, ruta)
        self.login(crear_usuario())
        self.assertEqual(self.client.get('/request/7/edit').status_code, 200)

    def test_sin_build_lo_dice(self):
        self.index.unlink()
        r = self.client.get('/')
        self.assertEqual(r.status_code, 503)
        self.assertIn('npm run build', r.content.decode())

    def test_api_inexistente_es_404_json(self):
        r = self.client.get('/api/no-existe/')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {'error': 'No encontrado.'})


class AccesoTests(CEPTestCase):

    def test_login_get_abre_la_ventana_sobre_el_inicio(self):
        r = self.client.get('/accounts/login/?next=/x/')
        self.assertRedirects(r, '/?acceder=%2Fx%2F', fetch_redirect_response=False)

    def test_next_externo_no_se_sigue(self):
        user = crear_usuario()
        self.plataforma.autenticar = [respuesta_ok(user.email)]
        r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'x', 'next': 'https://malo.example/'})
        self.assertEqual(r.json()['next'], '/')
        self.login(user)
        r = self.client.get('/accounts/login/?next=https://malo.example/')
        self.assertRedirects(r, '/', fetch_redirect_response=False)

    def test_admin_entra_por_la_plataforma(self):
        r = self.client.get('/admin/login/?next=/admin/')
        self.assertRedirects(r, '/accounts/login/?next=/admin/', fetch_redirect_response=False)

    def test_login_antiguo_de_google_ya_no_existe(self):
        r = self.client.post('/api/google-auth/', data='{"token": "x"}', content_type='application/json')
        self.assertEqual(r.status_code, 404)


class SeguridadTests(CEPTestCase):

    def test_certificado_exige_sesion(self):
        self.assertEqual(self.client.get('/api/certificado/edit/1/').status_code, 401)
        self.assertEqual(self.client.post('/api/certificado/create/1/').status_code, 401)

    def test_certificado_exige_csrf(self):
        from django.test import Client
        from .base import HOST, login_plataforma
        cliente = Client(enforce_csrf_checks=True, HTTP_HOST=HOST)
        login_plataforma(cliente, crear_usuario(grupo='Supervisor'))
        self.assertEqual(cliente.post('/api/certificado/create/1/').status_code, 403)

    def test_cors_no_abre_la_api_a_otros_sitios(self):
        self.login(crear_usuario())
        r = self.client.get('/api/sesion/', HTTP_ORIGIN='https://malo.example')
        self.assertNotIn('Access-Control-Allow-Origin', r.headers)

    def test_cookie_de_sesion_lax(self):
        from django.conf import settings
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')


class ErroresTests(CEPTestCase):
    """Las páginas de error son de React; Django responde JSON."""

    def test_500_json(self):
        from django.test import RequestFactory
        from core.errores import error_500
        r = error_500(RequestFactory().get('/api/x/'))
        self.assertEqual(r.status_code, 500)
        self.assertIn('Algo salió mal', json.loads(r.content)['error'])

    def test_403_json(self):
        from django.test import RequestFactory
        from core.errores import error_403
        r = error_403(RequestFactory().get('/api/x/'))
        self.assertEqual(r.status_code, 403)
        self.assertIn('error', json.loads(r.content))
