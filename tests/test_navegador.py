"""
Pruebas en un navegador real (Chromium headless vía Playwright): la ventana de acceso, el menú y todas las pantallas
de CEP, sin errores de JavaScript, sin modales abiertos al cargar y sin desplazamiento horizontal a 390 y 1366 px.

Los archivos de static_service se sirven desde la copia local (../../ServiceServer/static_service/static) si existe,
así se prueban suite.css, suite-shell.js y acceso.js antes de publicarlos. La plataforma (auth_service) es la
simulada de tests/base.py.

Requisito, una sola vez:   pip install -r requirements-dev.txt; python -m playwright install chromium
Solo estas:                python manage.py test tests.test_navegador
Sin estas (más rápido):    python manage.py test tests --exclude-tag=navegador
Ver el navegador:          $env:CEP_HEADED=1 antes de correr.
"""
import mimetypes
import os
import unittest
from datetime import date
from pathlib import Path

import httpx
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import tag

from authentication import plataforma
from presupuesto.models import Configuracion, CuentaAnalitica, SolicitudPresupuesto
from importlib import import_module

PARAMETROS = import_module('presupuesto.migrations.0027_parametros_plataforma').PARAMETROS

from .base import PlataformaFalsa, crear_usuario, error, login_plataforma, respuesta_ok, sede

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

PLATAFORMA_STATIC = 'https://gtsst.uapa.edu.do/serviceserver/static'
SERVICIOS = 'https://gtsst.uapa.edu.do/serviceserver'
STATIC_SERVICE_LOCAL = Path(settings.BASE_DIR).parent.parent / 'ServiceServer' / 'static_service' / 'static'

# Errores de consola que no dependen de CEP (recursos externos bloqueados en las pruebas).
ERRORES_IGNORADOS = (
    'accounts.google.com', 'gsi/', '[GSI_LOGGER]', 'net::ERR_', 'Failed to load resource', 'favicon',
    'suite-shell: no se pudo cargar el catálogo', 'serviceserver/auth', 'asistente',
)

# Todas las pantallas de CEP con sesión (agregue aquí las nuevas). {id} = una solicitud de prueba.
PANTALLAS = ('/', '/create', '/request/{id}', '/request/{id}/edit')


@tag('navegador')
@unittest.skipIf(sync_playwright is None, 'Playwright no está instalado')
class NavegadorTests(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch(headless=not os.environ.get('CEP_HEADED'))
        except Exception as exc:  # Chromium no descargado
            cls._pw.stop()
            super().tearDownClass()
            raise unittest.SkipTest(f'No se pudo abrir Chromium ({exc}). Ejecute: python -m playwright install chromium')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        for nombre, valor, _ in PARAMETROS:  # StaticLiveServerTestCase vacía la base entre pruebas
            Configuracion.objects.get_or_create(nombre=nombre, defaults={'valor': valor})
        Configuracion.objects.update_or_create(nombre='PLATFORM_SERVICE_PASSWORD', defaults={'valor': 'clave-svc'})
        self.plataforma = PlataformaFalsa()
        plataforma.transporte = httpx.MockTransport(self.plataforma)
        self.addCleanup(setattr, plataforma, 'transporte', None)
        self.user = crear_usuario()
        self.sede = sede('RSS')
        self.cuenta = CuentaAnalitica.objects.first() or CuentaAnalitica.objects.create(codigo='1.01', nombre='Docencia')
        self.solicitud = SolicitudPresupuesto.objects.create(
            colaborador=self.user, titulo='Taller de capacitación docente con un título bastante largo para probar',
            descripcion='Compra de materiales para el taller.\nSegunda línea de la descripción.',
            tipo_solicitud='DESEMBOLSO', rubro_presupuestal='ACTIVIDADES', ubicacion=self.sede,
            cuenta_analitica=self.cuenta, presupuesto_pre_aprobado=150000,
            monto_a_ejecutar=125000.5, fecha_actividad=date.today())
        self.context = self.browser.new_context(viewport={'width': 1366, 'height': 900}, locale='es-DO')
        self._rutas_externas(self.context)
        self.page = self.context.new_page()
        self.errores = []
        self.page.on('pageerror', lambda exc: self.errores.append(f'JS: {exc}'))
        self.page.on('console', self._en_consola)
        self.page.on('response', self._en_respuesta)

    def tearDown(self):
        self.context.close()

    # --- helpers --------------------------------------------------------------------------------

    @staticmethod
    def _rutas_externas(context):
        def static_service(route):
            relativa = route.request.url.split('?')[0][len(PLATAFORMA_STATIC) + 1:]
            archivo = STATIC_SERVICE_LOCAL / relativa
            if relativa and archivo.is_file():
                tipo = mimetypes.guess_type(archivo.name)[0] or 'application/octet-stream'
                route.fulfill(path=str(archivo), content_type=tipo, headers={'Access-Control-Allow-Origin': '*'})
            else:
                route.continue_()

        def campana(route):
            origen = route.request.headers.get('origin', '*')
            cabeceras = {'Access-Control-Allow-Origin': origen, 'Access-Control-Allow-Credentials': 'true',
                         'Access-Control-Allow-Headers': 'X-Suite-Shell, Content-Type'}
            if route.request.method in ('OPTIONS', 'POST'):
                route.fulfill(status=204, headers=cabeceras)
            else:
                route.fulfill(status=200, json={'no_leidas': 0, 'items': []}, headers=cabeceras)

        if STATIC_SERVICE_LOCAL.is_dir():
            context.route(PLATAFORMA_STATIC + '/**', static_service)
        context.route(SERVICIOS + '/notify/**', campana)
        context.route(SERVICIOS + '/auth/**', lambda route: route.abort())  # catálogo y asistente: sin red
        context.route('https://accounts.google.com/**', lambda route: route.abort())

    def _en_consola(self, msg):
        if msg.type == 'error' and not any(t in msg.text for t in ERRORES_IGNORADOS):
            self.errores.append(f'console.error: {msg.text}')

    def _en_respuesta(self, response):
        if response.url.startswith(self.live_server_url) and response.status >= 400:
            self.errores.append(f'HTTP {response.status}: {response.url}')

    def entrar(self, apps=None):
        """Inicia sesión copiando la cookie de sesión del cliente de pruebas de Django al navegador."""
        login_plataforma(self.client, self.user, apps=apps)
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME]
        self.context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': cookie.value,
                                   'url': self.live_server_url}])

    def abrir(self, ruta):
        self.page.goto(self.live_server_url + ruta)
        self.page.wait_for_load_state('networkidle')

    def revisar_pantalla(self, ruta):
        desborde = self.page.evaluate('document.documentElement.scrollWidth - document.documentElement.clientWidth')
        self.assertLessEqual(desborde, 0, f'{ruta}: desplazamiento horizontal de {desborde}px')
        scroll_interno = self.page.evaluate('''() => [...document.querySelectorAll('main *')].filter((el) => {
            const s = getComputedStyle(el);
            return /auto|scroll/.test(s.overflowX) && el.scrollWidth > el.clientWidth + 1;
        }).map((el) => el.className || el.tagName)''')
        self.assertEqual(scroll_interno, [], f'{ruta}: elementos con scroll horizontal')
        self.assertEqual(self.page.locator('.modal:visible').count(), 0, f'{ruta}: hay un modal abierto al cargar')
        self.assertEqual(self.errores, [], f'{ruta}: errores en el navegador')

    # --- pruebas --------------------------------------------------------------------------------

    def test_todas_las_pantallas_sin_errores_ni_scroll_horizontal(self):
        self.entrar()
        for ancho in (390, 1366):
            self.page.set_viewport_size({'width': ancho, 'height': 900})
            for plantilla in PANTALLAS:
                ruta = plantilla.format(id=self.solicitud.pk)
                with self.subTest(ruta=ruta, ancho=ancho):
                    self.errores = []
                    self.abrir(ruta)
                    self.page.wait_for_selector('#root main, #root > *', state='attached')
                    self.assertTrue(self.page.locator('[data-suite-shell="sidebar"] a').count() > 0,
                                    'suite-shell.js no pintó la barra lateral')
                    self.revisar_pantalla(ruta)

    def test_administracion_en_react(self):
        """Administrador: las tres secciones sin errores ni scroll horizontal; agregar una cuenta desde la pantalla."""
        self.entrar(apps=[{'app_slug': 'cep', 'roles': ['Administrador']}])
        for ancho in (390, 1366):
            self.page.set_viewport_size({'width': ancho, 'height': 900})
            for seccion in ('cuentas-analiticas', 'cuentas-contables', 'parametros'):
                ruta = f'/administracion/{seccion}'
                with self.subTest(ruta=ruta, ancho=ancho):
                    self.errores = []
                    self.abrir(ruta)
                    self.page.wait_for_selector('#root .cep-adm-lista, #root .cep-vacio')
                    self.assertEqual(self.page.url, self.live_server_url + ruta, 'la sección redirigió a otra')
                    self.assertEqual(self.page.locator('.suite-subnav .nav-link.active').inner_text().strip(), 'Administrar')
                    self.revisar_pantalla(ruta)
        self.assertNotIn('PLATFORM_SERVICE_PASSWORD', self.page.locator('#root').inner_text())
        self.assertIn('PLATFORM_STATIC_URL', self.page.locator('#root').inner_text())
        self.assertEqual(self.page.locator('#root a[href="/admin/"]').count(), 1)  # «Admin técnico»

        self.abrir('/administracion/cuentas-analiticas')
        self.page.fill('#cuentas-analiticas-codigo', 'ZZ-01')
        self.page.fill('#cuentas-analiticas-nombre', 'Cuenta de prueba del navegador')
        self.page.locator('.cep-adm-alta button[type="submit"]').click()
        self.page.wait_for_selector('text=Cuenta de prueba del navegador')
        self.assertEqual(self.errores, [])

    def test_supervisor_no_ve_administracion(self):
        self.entrar(apps=[{'app_slug': 'cep', 'roles': ['Supervisor']}])
        self.abrir('/')
        self.page.wait_for_selector('#root .suite-subnav')
        self.assertEqual(self.page.locator('.suite-subnav a[href="/administracion"]').count(), 0)
        self.abrir('/administracion')
        self.page.wait_for_selector('#root .cep-vacio')
        self.assertIn('No tienes permiso', self.page.locator('#root main').inner_text())

    def test_rol_debajo_del_nombre_centrado_con_el_avatar(self):
        self.page.set_viewport_size({'width': 1366, 'height': 900})
        self.entrar(apps=[{'app_slug': 'cep', 'roles': ['Compra RSD']}])
        self.abrir('/')
        rol = self.page.locator('[data-suite-usuario] .user-name + .user-role')
        rol.wait_for()
        self.assertEqual(rol.inner_text(), 'Compra RSD')
        self.assertEqual(self.page.locator('[data-suite-usuario] .user-role').count(), 1)
        centros = self.page.evaluate('''() => {
          const c = (el) => { const b = el.getBoundingClientRect(); return (b.top + b.bottom) / 2; };
          const nombre = document.querySelector('[data-suite-usuario] .user-name');
          return [c(document.querySelector('[data-suite-usuario] .avatar')), c(nombre.parentElement)];
        }''')
        self.assertLessEqual(abs(centros[0] - centros[1]), 1, f'nombre y rol no centrados con el avatar: {centros}')

    def test_inicio_sin_sesion(self):
        for ancho in (390, 1366):
            with self.subTest(ancho=ancho):
                self.page.set_viewport_size({'width': ancho, 'height': 900})
                self.abrir('/')
                self.revisar_pantalla('/ (sin sesión)')

    def test_ventana_de_acceso_entra_con_la_plataforma(self):
        self.plataforma.autenticar = [respuesta_ok(self.user.email)]
        self.abrir('/')
        self.page.locator('[data-suite-shell="topbar"] [data-acceso-abrir]').click()
        ventana = self.page.locator('#acceso')
        ventana.wait_for(state='visible')
        ventana.locator('input[name="username"]').fill('ana')
        ventana.locator('input[name="password"]').fill('secreta')
        ventana.locator('button.btn-primary').click()
        self.page.wait_for_selector('#root .page-title')
        self.assertIn('Mis solicitudes', self.page.locator('#root .page-title').inner_text())
        self.page.wait_for_selector('#root .cep-solicitud')
        self.assertIn('Taller de capacitación', self.page.locator('#root').inner_text())
        self.assertEqual(self.plataforma.cuerpos('/auth/authenticate')[0]['email'], 'ana@uapa.edu.do')
        self.assertEqual(self.errores, [])

    def test_ventana_de_acceso_muestra_el_error_dentro(self):
        self.plataforma.autenticar = [error(401, 'invalid_credentials', 'Correo o contraseña incorrectos.')]
        self.abrir('/')
        self.page.locator('[data-suite-shell="topbar"] [data-acceso-abrir]').click()
        ventana = self.page.locator('#acceso')
        ventana.wait_for(state='visible')
        ventana.locator('input[name="username"]').fill('ana')
        ventana.locator('input[name="password"]').fill('mala')
        ventana.locator('button.btn-primary').click()
        aviso = ventana.locator('[data-acceso-error]')
        aviso.wait_for(state='visible')
        self.assertEqual(aviso.inner_text(), 'Correo o contraseña incorrectos.')
        self.assertTrue(ventana.is_visible())
        self.errores = [e for e in self.errores if '/accounts/login/' not in e]  # el 401 es la respuesta esperada
        self.assertEqual(self.errores, [])

    def test_pagina_privada_sin_sesion_abre_la_ventana(self):
        self.abrir(f'/request/{self.solicitud.pk}')
        self.page.locator('#acceso').wait_for(state='visible')
        self.assertEqual(self.page.locator('#acceso input[name="next"]').input_value(), f'/request/{self.solicitud.pk}')

    def test_menu_navega_sin_recargar_y_actualiza_la_ruta(self):
        self.entrar()
        self.abrir('/')
        self.page.evaluate('window.__sinRecargar = true')
        self.page.locator('.suite-subnav a[href="/create"]').click()
        self.page.wait_for_selector('#root main form')  # la barra superior también tiene un form (Cerrar sesión)
        self.assertTrue(self.page.evaluate('window.__sinRecargar === true'), 'el menú recargó la página')
        self.assertEqual(self.page.locator('.suite-ruta-actual').inner_text(), 'Nueva solicitud')
        self.assertEqual(self.page.locator('.suite-subnav .nav-link.active').get_attribute('href'), '/create')
        self.assertEqual(self.page.locator('.suite-subnav [aria-current="page"]').count(), 1)

    def test_menu_fijo_al_hacer_scroll(self):
        self.entrar()
        for i in range(12):  # contenido suficiente para desplazar la página
            SolicitudPresupuesto.objects.create(
                colaborador=self.user, titulo=f'Solicitud {i}', descripcion='x', tipo_solicitud='DESEMBOLSO',
                rubro_presupuestal='SERVICIOS', ubicacion=self.sede,
                cuenta_analitica=self.cuenta, presupuesto_pre_aprobado=1, monto_a_ejecutar=1)
        self.abrir('/')
        self.page.wait_for_selector('.cep-solicitud')
        antes = self.page.locator('.suite-subnav').bounding_box()['y']
        self.page.mouse.wheel(0, 1200)
        self.page.wait_for_timeout(300)
        despues = self.page.locator('.suite-subnav').bounding_box()['y']
        self.assertGreater(self.page.evaluate('window.scrollY'), 0)
        self.assertLessEqual(abs(despues - min(antes, despues)), 1)
        self.assertGreaterEqual(despues, 0)

    def test_cerrar_sesion_desde_el_menu_del_usuario(self):
        self.entrar()
        self.abrir('/')
        self.page.locator('[data-suite-usuario] [data-bs-toggle="dropdown"]').first.click()
        self.page.get_by_text('Cerrar sesión').click()
        self.page.wait_for_selector('main .cep-hero')
        self.assertEqual(len(self.plataforma.peticiones('/auth/logout')), 1)
