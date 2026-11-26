"""Parte 1: acceso por la plataforma (login centralizado, SSO total, logout en todas las apps y foto)."""
import hashlib
import time
from unittest import mock

from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from authentication import plataforma
from authentication.acceso import MSG_NO_REGISTRADA, MSG_SUSPENDIDO
from authentication.models import SesionPlataforma
from presupuesto.models import Configuracion

from .base import (CEPTestCase, OTRA_LLAVE_PRIVADA, error, login_plataforma, respuesta_ok, token_logout,
                   crear_usuario)


class LoginConContrasenaTests(CEPTestCase):

    def test_contrasena_correcta_inicia_sesion_y_pone_cookies(self):
        user = crear_usuario()
        self.plataforma.autenticar = [respuesta_ok(user.email)]
        r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'secreta', 'next': '/create'})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'ok': True, 'next': '/create'})
        cuerpo = self.plataforma.cuerpos('/auth/authenticate')[0]
        self.assertEqual(cuerpo['email'], 'ana@uapa.edu.do')  # sin @ se agrega el dominio
        self.assertEqual(cuerpo['method'], 'password')
        self.assertEqual(cuerpo['app_slug'], 'cep')
        peticion = self.plataforma.peticiones('/auth/authenticate')[0]
        self.assertEqual(peticion.headers['Authorization'], 'Bearer token-de-servicio')
        self.assertIn('X-Client-IP', peticion.headers)

        sesion = self.client.session
        self.assertEqual(sesion[plataforma.SESION_USUARIO]['id'], str(user.id))
        self.assertEqual(sesion[plataforma.SESION_TOKENS], {'access': 'access-nuevo', 'refresh': 'refresh-nuevo'})
        self.assertEqual(sesion[plataforma.SESION_USUARIO]['foto_url'], 'https://lh3.googleusercontent.com/foto')
        for nombre in ('access_token', 'refresh_token'):
            cookie = r.cookies[nombre]
            self.assertEqual(cookie['domain'], '.uapa.edu.do')
            self.assertTrue(cookie['secure'])
            self.assertTrue(cookie['httponly'])
            self.assertEqual(cookie['samesite'], 'Lax')
        self.assertTrue(SesionPlataforma.objects.filter(email='ana@uapa.edu.do').exists())

    def test_la_respuesta_nunca_contiene_la_contrasena(self):
        crear_usuario()
        self.plataforma.autenticar = [error(401, 'invalid_credentials', 'Correo o contraseña incorrectos.')]
        r = self.fetch_post(reverse('login'), {'username': 'ana@uapa.edu.do', 'password': 'Clave-Muy-Secreta-9'})
        self.assertNotIn(b'Clave-Muy-Secreta-9', r.content)
        self.assertNotIn('Clave-Muy-Secreta-9', str(self.client.session.items()))

    def test_errores_de_la_plataforma_muestran_su_mensaje(self):
        crear_usuario()
        casos = [(401, 'invalid_credentials'), (403, 'user_not_registered'), (403, 'user_inactive'),
                 (403, 'google_domain_not_allowed')]
        for status, code in casos:
            with self.subTest(code=code):
                self.plataforma.autenticar = [error(status, code, f'Mensaje {code}')]
                r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'x'})
                self.assertEqual(r.status_code, status)
                self.assertEqual(r.json(), {'ok': False, 'error': f'Mensaje {code}'})

    def test_sin_rol_de_cep_en_la_plataforma_no_entra(self):
        self.plataforma.autenticar = [respuesta_ok('nuevo@uapa.edu.do')]  # sin roles en la app cep
        r = self.fetch_post(reverse('login'), {'username': 'nuevo', 'password': 'x'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], MSG_NO_REGISTRADA)
        self.assertNotIn(plataforma.SESION_USUARIO, self.client.session)

    def test_usuario_inactivo_en_cep(self):
        crear_usuario(activo=False)
        self.plataforma.autenticar = [respuesta_ok('ana@uapa.edu.do')]
        r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'x'})
        self.assertEqual(r.json()['error'], MSG_SUSPENDIDO)

    def test_faltan_datos(self):
        r = self.fetch_post(reverse('login'), {'username': 'ana'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'Escribe tu correo y tu contraseña.')
        self.assertEqual(self.plataforma.llamadas, [])

    def test_sin_contrasena_de_servicio(self):
        Configuracion.objects.filter(nombre='PLATFORM_SERVICE_PASSWORD').update(valor='')
        with self.assertLogs('core.plataforma', 'ERROR'):
            r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'x'})
        self.assertEqual(r.json()['error'], 'El servicio de acceso no está disponible.')

    def test_plataforma_caida_o_limite(self):
        self.plataforma.caida = True
        r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'x'})
        self.assertEqual(r.json()['error'], 'El servicio de acceso no está disponible. Inténtalo más tarde.')
        self.plataforma.caida = False
        self.plataforma.autenticar = [error(429, '')]
        r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'x'})
        self.assertEqual(r.json()['error'], 'Demasiados intentos. Espera un minuto.')

    def test_token_de_servicio_vencido_se_renueva_una_vez(self):
        user = crear_usuario()
        self.plataforma.autenticar = [error(401, 'invalid_service_token'), respuesta_ok(user.email)]
        r = self.fetch_post(reverse('login'), {'username': 'ana', 'password': 'x'})
        self.assertTrue(r.json()['ok'])
        self.assertEqual(len(self.plataforma.peticiones('/service-auth/login')), 2)

    def test_sin_javascript_redirige_con_mensaje(self):
        self.plataforma.autenticar = [error(401, 'invalid_credentials', 'Correo o contraseña incorrectos.')]
        r = self.client.post(reverse('login'), {'username': 'ana', 'password': 'x', 'next': '/create'})
        self.assertRedirects(r, '/?acceder=%2Fcreate', fetch_redirect_response=False)


class LoginConGoogleTests(CEPTestCase):

    def test_reenvia_el_credential_sin_verificarlo(self):
        user = crear_usuario()
        self.plataforma.autenticar = [respuesta_ok(user.email, method='google')]
        r = self.fetch_post(reverse('auth_receiver'), json_body={'credential': 'id-token-de-google', 'next': '/'})
        self.assertEqual(r.json(), {'ok': True, 'next': '/'})
        cuerpo = self.plataforma.cuerpos('/auth/authenticate')[0]
        self.assertEqual(cuerpo['method'], 'google')
        self.assertEqual(cuerpo['credential'], 'id-token-de-google')
        self.assertNotIn('password', cuerpo)

    def test_dominio_no_permitido(self):
        self.plataforma.autenticar = [error(403, 'google_domain_not_allowed', 'Dominio no permitido.')]
        r = self.fetch_post(reverse('auth_receiver'), json_body={'credential': 'x'})
        self.assertEqual(r.json(), {'ok': False, 'error': 'Dominio no permitido.'})

    def test_fetch_exige_csrf(self):
        from django.test import Client
        cliente = Client(enforce_csrf_checks=True, HTTP_HOST='cep.uapa.edu.do')
        r = cliente.post(reverse('auth_receiver'), data='{"credential": "x"}', content_type='application/json',
                         HTTP_X_REQUESTED_WITH='fetch')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'La sesión expiró. Recarga la página.')

    def test_formulario_de_google_en_modo_redireccion(self):
        user = crear_usuario()
        self.plataforma.autenticar = [respuesta_ok(user.email, method='google')]
        r = self.client.post(reverse('auth_receiver'), {'credential': 'id-token'})
        self.assertRedirects(r, '/', fetch_redirect_response=False)


class EntradaAutomaticaTests(CEPTestCase):

    def test_cookie_valida_entra_en_la_primera_peticion(self):
        user = crear_usuario()
        self.plataforma.autenticar = [respuesta_ok(user.email, status='already_logged_in', method='session',
                                                   access=None, refresh=None)]
        self.client.cookies['access_token'] = 'access-de-otra-app'
        r = self.client.get('/api/sesion/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.session[plataforma.SESION_USUARIO]['id'], str(user.id))
        self.assertEqual(self.client.session[plataforma.SESION_TOKENS]['access'], 'access-de-otra-app')
        self.assertEqual(r.json()['usuario']['email'], user.email)

    def test_cookie_invalida_se_consulta_una_sola_vez_y_no_se_borra(self):
        self.client.cookies['access_token'] = 'vencida'
        for _ in range(3):
            r = self.client.get('/api/sesion/')
            self.assertNotIn('access_token', r.cookies)  # las cookies compartidas no se tocan
        self.assertEqual(len(self.plataforma.peticiones('/auth/authenticate')), 1)
        huella = self.client.session['plataforma_cookie_rechazada']
        self.assertEqual(huella, hashlib.sha256(b'vencida|').hexdigest())

    def test_plataforma_caida_sigue_como_anonimo(self):
        self.plataforma.caida = True
        self.client.cookies['access_token'] = 'x'
        with self.assertLogs('core.plataforma', 'WARNING'):
            r = self.client.get('/api/sesion/')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(plataforma.SESION_USUARIO, self.client.session)

    def test_rutas_excluidas(self):
        self.client.cookies['access_token'] = 'x'
        self.client.get('/admin/')
        self.client.get('/django_static/assets/index.css')
        self.assertEqual(self.plataforma.peticiones('/auth/authenticate'), [])


class RevalidacionTests(CEPTestCase):

    def _vencer(self):
        sesion = self.client.session
        sesion[plataforma.SESION_TS] = time.time() - 301
        sesion.save()

    def test_antes_de_5_minutos_no_consulta(self):
        self.login(crear_usuario())
        self.client.get('/api/me/')
        self.assertEqual(self.plataforma.llamadas, [])

    def test_401_cierra_la_sesion(self):
        self.login(crear_usuario())
        self._vencer()
        self.plataforma.autenticar = [error(401, 'not_logged_in')]
        r = self.client.get('/api/me/')
        self.assertEqual(r.status_code, 401)
        self.assertNotIn(plataforma.SESION_USUARIO, self.client.session)

    def test_sin_rol_de_cep_al_revalidar_cierra_la_sesion(self):
        self.login(crear_usuario())
        self._vencer()
        self.plataforma.autenticar = [respuesta_ok('ana@uapa.edu.do', status='already_logged_in',
                                                   apps=[{'app_slug': 'popin', 'roles': ['Usuario']}])]
        self.assertEqual(self.client.get('/api/me/').status_code, 401)
        self.assertNotIn(plataforma.SESION_USUARIO, self.client.session)

    def test_otro_email_cierra_la_sesion(self):
        self.login(crear_usuario())
        self._vencer()
        self.plataforma.autenticar = [respuesta_ok('otra@uapa.edu.do', status='already_logged_in')]
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_refresh_reemplaza_tokens_y_cookies(self):
        self.login(crear_usuario())
        self._vencer()
        self.plataforma.autenticar = [respuesta_ok('ana@uapa.edu.do', status='already_logged_in', method='refresh',
                                                   access='access-rotado', refresh='refresh-rotado')]
        r = self.client.get('/api/me/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.session[plataforma.SESION_TOKENS],
                         {'access': 'access-rotado', 'refresh': 'refresh-rotado'})
        self.assertEqual(r.cookies['access_token'].value, 'access-rotado')
        self.assertEqual(r.cookies['refresh_token'].value, 'refresh-rotado')

    def test_sin_respuesta_conserva_la_sesion_y_reintenta_en_un_minuto(self):
        self.login(crear_usuario())
        self._vencer()
        self.plataforma.caida = True
        with self.assertLogs('core.plataforma', 'WARNING'):
            self.assertEqual(self.client.get('/api/me/').status_code, 200)
        restante = 300 - (time.time() - self.client.session[plataforma.SESION_TS])
        self.assertAlmostEqual(restante, 60, delta=5)

    def test_sesion_anterior_al_sso_se_cierra(self):
        self.login(crear_usuario())
        sesion = self.client.session
        del sesion[plataforma.SESION_TOKENS]  # sin plataforma_tokens
        sesion.save()
        self.assertEqual(self.client.get('/api/me/').status_code, 401)

    def test_logout_en_otra_app_borra_las_cookies_y_cierra_al_momento(self):
        self.login(crear_usuario())
        self.client.cookies['access_token'] = 'compartida'
        self.assertEqual(self.client.get('/api/me/').status_code, 200)
        del self.client.cookies['access_token']
        self.assertEqual(self.client.get('/api/me/').status_code, 401)


class LogoutTests(CEPTestCase):

    def test_get_cierra_en_la_plataforma_y_borra_cookies(self):
        user = crear_usuario()
        self.login(user)
        r = self.client.get(reverse('logout'))
        self.assertRedirects(r, '/', fetch_redirect_response=False)
        self.assertEqual(len(self.plataforma.peticiones('/auth/logout')), 1)
        self.assertNotIn(plataforma.SESION_USUARIO, self.client.session)
        for nombre in ('access_token', 'refresh_token'):
            self.assertEqual(r.cookies[nombre].value, '')
            self.assertEqual(r.cookies[nombre]['domain'], '.uapa.edu.do')
        self.assertFalse(SesionPlataforma.objects.exists())

    def test_get_con_la_plataforma_caida(self):
        self.login(crear_usuario())
        self.plataforma.caida = True
        with self.assertLogs('core.plataforma', 'WARNING'):
            r = self.client.get(reverse('logout'))
        self.assertNotIn(plataforma.SESION_USUARIO, self.client.session)
        self.assertEqual(r.cookies['access_token'].value, '')
        self.assertEqual(r.cookies['refresh_token'].value, '')

    def test_post_sin_token_exige_csrf(self):
        from django.test import Client
        cliente = Client(enforce_csrf_checks=True, HTTP_HOST='cep.uapa.edu.do')
        login_plataforma(cliente, crear_usuario())
        self.assertEqual(cliente.post(reverse('logout')).status_code, 403)


class AvisoDeLogoutTests(CEPTestCase):

    def _sesion_de(self, user):
        from django.test import Client
        cliente = Client(HTTP_HOST='cep.uapa.edu.do')
        login_plataforma(cliente, user)
        SesionPlataforma.objects.create(email=user.email, session_key=cliente.session.session_key)
        return cliente.session.session_key

    def test_token_valido_cierra_todas_las_sesiones_del_usuario(self):
        ana, luis = crear_usuario(), crear_usuario('luis@uapa.edu.do')
        sesiones_ana = [self._sesion_de(ana), self._sesion_de(ana)]
        sesion_luis = self._sesion_de(luis)

        r = self.client.post(reverse('logout'), {'logout_token': token_logout('ANA@uapa.edu.do')})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b'')
        self.assertFalse(Session.objects.filter(session_key__in=sesiones_ana).exists())
        self.assertTrue(Session.objects.filter(session_key=sesion_luis).exists())
        self.assertFalse(SesionPlataforma.objects.filter(email='ana@uapa.edu.do').exists())

    def test_tokens_invalidos(self):
        casos = {
            'firma': token_logout('ana@uapa.edu.do', llave=OTRA_LLAVE_PRIVADA),
            'vencido': token_logout('ana@uapa.edu.do', exp=-10),
            'aud': token_logout('ana@uapa.edu.do', aud='popin'),
            'type': token_logout('ana@uapa.edu.do', tipo='access'),
            'basura': 'no-es-un-jwt',
        }
        for caso, token in casos.items():
            with self.subTest(caso=caso):
                self.assertEqual(self.client.post(reverse('logout'), {'logout_token': token}).status_code, 400)

    def test_llave_no_descargable(self):
        self.plataforma.caida = True
        with self.assertLogs('core.plataforma', 'WARNING'):
            r = self.client.post(reverse('logout'), {'logout_token': token_logout('ana@uapa.edu.do')})
        self.assertEqual(r.status_code, 503)

    def test_la_llave_se_guarda_en_cache(self):
        for _ in range(2):
            self.client.post(reverse('logout'), {'logout_token': token_logout('ana@uapa.edu.do')})
        self.assertEqual(len(self.plataforma.peticiones('/auth/public-key')), 1)


class FotoTests(CEPTestCase):

    def test_foto_de_google_en_la_configuracion_del_esqueleto(self):
        self.login(crear_usuario(), foto_url='https://lh3.googleusercontent.com/a/foto')
        datos = self.client.get('/api/sesion/').json()
        self.assertEqual(datos['shell']['usuario']['foto'], 'https://lh3.googleusercontent.com/a/foto')
        self.assertEqual(datos['usuario']['picture'], 'https://lh3.googleusercontent.com/a/foto')

    def test_sin_foto_queda_el_respaldo(self):
        self.login(crear_usuario(), foto_url=None)
        datos = self.client.get('/api/sesion/').json()
        self.assertEqual(datos['shell']['usuario']['foto'], '')  # suite-shell.js pinta las iniciales


class ClaveServicioTests(CEPTestCase):

    def test_guarda_y_prueba_la_contrasena(self):
        with mock.patch('authentication.management.commands.clave_servicio.getpass', return_value='nueva'):
            call_command('clave_servicio', stdout=mock.MagicMock())
        self.assertEqual(Configuracion.get_value('PLATFORM_SERVICE_PASSWORD'), 'nueva')
        self.assertEqual(self.plataforma.cuerpos('/service-auth/login')[0], {'username': 'cep-svc', 'password': 'nueva'})

    def test_si_la_plataforma_la_rechaza_vuelve_al_valor_anterior(self):
        self.plataforma.servicio_status = 401
        with mock.patch('authentication.management.commands.clave_servicio.getpass', return_value='mala'), \
                self.assertLogs('core.plataforma', 'ERROR'), self.assertRaises(CommandError):
            call_command('clave_servicio')
        self.assertEqual(Configuracion.get_value('PLATFORM_SERVICE_PASSWORD'), 'clave-svc')

    def test_sin_probar(self):
        self.plataforma.caida = True
        with mock.patch('authentication.management.commands.clave_servicio.getpass', return_value='otra'):
            call_command('clave_servicio', '--sin-probar', stdout=mock.MagicMock())
        self.assertEqual(Configuracion.get_value('PLATFORM_SERVICE_PASSWORD'), 'otra')
        self.assertEqual(self.plataforma.llamadas, [])
