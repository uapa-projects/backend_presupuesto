"""API de la pantalla Administración de React (presupuesto/administracion.py)."""
import json
from datetime import date

from django.core.cache import cache
from django.test import Client

from presupuesto.models import Configuracion, CuentaAnalitica, CuentaContable, SolicitudPresupuesto

from .base import HOST, CEPTestCase, crear_usuario, login_plataforma, sede
from .test_roles import apps_con

ANALITICAS = '/api/administracion/cuentas-analiticas/'
CONTABLES = '/api/administracion/cuentas-contables/'
PARAMETROS = '/api/administracion/parametros/'


class AdministracionTests(CEPTestCase):

    def entrar(self, *roles):
        self.login(crear_usuario(), apps=apps_con(*roles))

    def enviar(self, metodo, url, datos=None):
        return getattr(self.client, metodo)(url, data=json.dumps(datos or {}), content_type='application/json')

    # ---- permisos ----
    def test_sin_sesion_401(self):
        for url in (ANALITICAS, CONTABLES, PARAMETROS):
            self.assertEqual(self.client.get(url).status_code, 401)

    def test_colaborador_403(self):
        self.entrar('Colaborador')
        for url in (ANALITICAS, CONTABLES, PARAMETROS):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_supervisor_no_administra(self):
        """El Supervisor revisa solicitudes, pero la administración es solo del Administrador."""
        self.entrar('Supervisor')
        for url in (ANALITICAS, CONTABLES, PARAMETROS):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.enviar('post', ANALITICAS, {'codigo': '1', 'nombre': 'x'}).status_code, 403)

    def test_administrador_administra_todo(self):
        self.entrar('Administrador')
        for url in (ANALITICAS, CONTABLES, PARAMETROS):
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_exige_csrf(self):
        cliente = Client(enforce_csrf_checks=True, HTTP_HOST=HOST)
        login_plataforma(cliente, crear_usuario(), apps=apps_con('Administrador'))
        r = cliente.post(ANALITICAS, data='{"codigo": "1", "nombre": "x"}', content_type='application/json')
        self.assertEqual(r.status_code, 403)

    # ---- cuentas ----
    def test_crear_editar_y_eliminar_cuenta_analitica(self):
        self.entrar('Administrador')
        r = self.enviar('post', ANALITICAS, {'codigo': ' 101 ', 'nombre': 'Docencia'})
        self.assertEqual(r.status_code, 201)
        cuenta = r.json()
        self.assertEqual((cuenta['codigo'], cuenta['nombre'], cuenta['en_uso']), ('101', 'Docencia', False))
        r = self.enviar('put', f"{ANALITICAS}{cuenta['id']}/", {'codigo': '102', 'nombre': 'Investigación'})
        self.assertEqual(r.json()['nombre'], 'Investigación')
        lista = {c['id']: c for c in self.client.get(ANALITICAS).json()}
        self.assertEqual(lista[cuenta['id']]['codigo'], '102')
        self.assertEqual(self.client.delete(f"{ANALITICAS}{cuenta['id']}/").status_code, 204)
        self.assertFalse(CuentaAnalitica.objects.filter(pk=cuenta['id']).exists())

    def test_cuenta_sin_datos(self):
        self.entrar('Administrador')
        r = self.enviar('post', ANALITICAS, {'codigo': '', 'nombre': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'Escribe el código y el nombre.')
        self.assertEqual(self.enviar('post', ANALITICAS, {'codigo': 'x' * 21, 'nombre': 'x'}).status_code, 400)

    def test_cuenta_contable_con_codigo_repetido(self):
        self.entrar('Administrador')
        CuentaContable.objects.get_or_create(codigo='5001', defaults={'nombre': 'Viajes'})
        r = self.enviar('post', CONTABLES, {'codigo': '5001', 'nombre': 'Otra'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Ya existe', r.json()['error'])

    def test_cuenta_en_uso_no_se_elimina(self):
        self.entrar('Administrador')
        cuenta = CuentaAnalitica.objects.create(codigo='ZZ-PRUEBA', nombre='Docencia')
        SolicitudPresupuesto.objects.create(
            colaborador=crear_usuario('b@uapa.edu.do'), titulo='t', descripcion='d', tipo_solicitud='DESEMBOLSO',
            rubro_presupuestal='SERVICIOS', ubicacion=sede('RSS'),
            cuenta_analitica=cuenta, presupuesto_pre_aprobado=1, monto_a_ejecutar=1, fecha_actividad=date.today())
        self.assertTrue({c['id']: c for c in self.client.get(ANALITICAS).json()}[cuenta.pk]['en_uso'])
        r = self.client.delete(f'{ANALITICAS}{cuenta.pk}/')
        self.assertEqual(r.status_code, 409)
        self.assertTrue(CuentaAnalitica.objects.filter(pk=cuenta.pk).exists())

    # ---- parámetros ----
    def test_parametros_sin_contrasenas(self):
        self.entrar('Administrador')
        r = self.client.get(PARAMETROS)
        nombres = [p['nombre'] for p in r.json()]
        self.assertIn('PLATFORM_STATIC_URL', nombres)
        self.assertNotIn('PLATFORM_SERVICE_PASSWORD', nombres)
        self.assertNotIn('clave-svc', r.content.decode())

    def test_editar_parametro_limpia_la_cache(self):
        self.entrar('Administrador')
        self.client.get('/api/sesion/')  # deja PLATFORM_STATIC_URL en caché
        r = self.enviar('put', f'{PARAMETROS}PLATFORM_STATIC_URL/', {'valor': 'https://otro.example/static'})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(cache.get('cep:parametro:PLATFORM_STATIC_URL'))
        self.assertEqual(self.client.get('/api/sesion/').json()['static'], 'https://otro.example/static')

    def test_la_contrasena_no_se_edita(self):
        self.entrar('Administrador')
        r = self.enviar('put', f'{PARAMETROS}PLATFORM_SERVICE_PASSWORD/', {'valor': 'nueva'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Configuracion.objects.get(nombre='PLATFORM_SERVICE_PASSWORD').valor, 'clave-svc')

    def test_parametro_inexistente_o_invalido(self):
        self.entrar('Administrador')
        self.assertEqual(self.enviar('put', f'{PARAMETROS}NO_EXISTE/', {'valor': 'x'}).status_code, 404)
        self.assertEqual(self.enviar('put', f'{PARAMETROS}PLATFORM_STATIC_URL/', {'valor': 1}).status_code, 400)
        self.assertEqual(self.enviar('put', f'{PARAMETROS}PLATFORM_STATIC_URL/', {'valor': 'x' * 101}).status_code, 400)
