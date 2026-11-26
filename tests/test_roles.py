"""Roles de la plataforma (user_app_roles de la app cep) en lugar de is_staff, is_superuser y los grupos de Django
(authentication/roles.py)."""
from django.contrib.auth.models import Group, User

from .base import CEPTestCase, crear_usuario, respuesta_ok, sede


def apps_con(*roles, app='cep'):
    return [{'app_slug': app, 'app_name': app.upper(), 'roles': list(roles)}]


class RolesTests(CEPTestCase):

    def usuario(self):
        return self.client.get('/api/sesion/').json()['usuario']

    def rol(self):
        return self.usuario()['role']

    def test_rol_supervisor_de_la_plataforma(self):
        self.login(crear_usuario(), apps=apps_con('Supervisor'))
        self.assertEqual(self.rol(), 'SUPERVISOR')
        self.assertFalse(self.usuario()['administrador'])

    def test_administrador_tambien_es_supervisor_en_cep(self):
        self.login(crear_usuario(), apps=apps_con('Administrador'))
        self.assertEqual(self.rol(), 'SUPERVISOR')
        self.assertTrue(self.usuario()['administrador'])

    def test_roles_de_compra_salen_de_la_plataforma(self):
        self.login(crear_usuario(), apps=apps_con('Compra RCO'))
        self.assertEqual(self.rol(), 'COMPRA RCO')

    def test_cuentan_los_roles_de_la_sesion(self):
        """Los roles son los que mandó la plataforma al entrar (o al revalidar), no los del directorio."""
        self.login(crear_usuario(grupo='Supervisor'), apps=apps_con('Colaborador'))
        self.assertEqual(self.rol(), 'COLABORADOR')

    def test_roles_de_otra_app_no_cuentan(self):
        self.login(crear_usuario(), apps=apps_con('Administrador', app='popin'))
        self.assertEqual(self.rol(), 'COLABORADOR')

    def test_rol_desde_el_login_por_la_plataforma(self):
        crear_usuario()
        self.plataforma.autenticar = [respuesta_ok('ana@uapa.edu.do', apps=apps_con('Supervisor'))]
        r = self.fetch_post('/accounts/login/', {'username': 'ana@uapa.edu.do', 'password': 'x'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rol(), 'SUPERVISOR')

    def test_el_login_no_crea_usuarios_ni_grupos_locales(self):
        user = crear_usuario(grupo='Compra RSD')
        self.plataforma.autenticar = [respuesta_ok(user.email, apps=apps_con('Compra RSS', 'Supervisor'))]
        r = self.fetch_post('/accounts/login/', {'username': user.email, 'password': 'x'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rol(), 'SUPERVISOR')
        self.assertFalse(User.objects.exists())
        self.assertFalse(Group.objects.filter(user__isnull=False).exists())

    def test_admin_de_django_solo_para_administrador(self):
        self.login(crear_usuario(), apps=apps_con('Supervisor'))
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.login(crear_usuario('admin@uapa.edu.do'), apps=apps_con('Administrador'))
        r = self.client.get('/admin/')
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'Acciones recientes')
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 404)  # sin usuarios locales
        self.assertEqual(self.client.get('/admin/presupuesto/solicitudpresupuesto/').status_code, 200)

    def test_sin_rol_administrador_no_hay_admin(self):
        user = crear_usuario()
        self.login(user, apps=apps_con('Colaborador'))
        r = self.client.get('/admin/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/admin/login/', r['Location'])
        self.assertEqual(self.rol(), 'COLABORADOR')

    def test_el_admin_guarda_sin_historial(self):
        """django_admin_log apunta a auth_user (vacía): el admin guarda sin escribir el historial."""
        from django.contrib.admin.models import LogEntry
        from presupuesto.models import CuentaAnalitica
        self.login(crear_usuario(), apps=apps_con('Administrador'))
        r = self.client.post('/admin/presupuesto/cuentaanalitica/add/', {'codigo': 'ADM-1', 'nombre': 'Desde el admin'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CuentaAnalitica.objects.filter(codigo='ADM-1').exists())
        self.assertFalse(LogEntry.objects.exists())

    def test_el_admin_no_muestra_contrasenas(self):
        from presupuesto.models import Configuracion
        self.login(crear_usuario(), apps=apps_con('Administrador'))
        texto = self.client.get('/admin/presupuesto/configuracion/').content.decode()
        self.assertNotIn('clave-svc', texto)
        self.assertNotIn('PLATFORM_SERVICE_PASSWORD', texto)
        fila = Configuracion.objects.get(nombre='PLATFORM_SERVICE_PASSWORD')
        self.assertEqual(self.client.get(f'/admin/presupuesto/configuracion/{fila.pk}/change/').status_code, 302)


class EquivalenciaConGruposTests(CEPTestCase):
    """Cada rol hace lo mismo que hacía el grupo de Django del mismo nombre."""

    def setUp(self):
        super().setUp()
        from datetime import date
        from presupuesto.models import CuentaAnalitica, SolicitudPresupuesto
        cuenta = CuentaAnalitica.objects.create(codigo='ZZ-1', nombre='Prueba')
        otro = crear_usuario('otro@uapa.edu.do')
        self.por_sede = {}
        for codigo in ('RSS', 'RSD', 'RCO'):
            self.por_sede[codigo] = SolicitudPresupuesto.objects.create(
                colaborador=otro, titulo=f'Solicitud {codigo}', descripcion='d', tipo_solicitud='DESEMBOLSO',
                rubro_presupuestal='SERVICIOS', ubicacion=sede(codigo), cuenta_analitica=cuenta,
                presupuesto_pre_aprobado=1, monto_a_ejecutar=1, fecha_actividad=date.today())
        self.todas = {s.pk for s in self.por_sede.values()}

    def ver(self, *roles):
        self.login(crear_usuario(), apps=apps_con(*roles))
        return {s['id'] for s in self.client.get('/api/solicitudes/list').json()['solicitudes']} & self.todas

    def test_supervisor_ve_las_de_todos(self):
        self.assertEqual(self.ver('Supervisor'), self.todas)

    def test_administrador_ve_las_de_todos(self):
        self.assertEqual(self.ver('Administrador'), self.todas)

    def test_compra_rss_ve_las_de_todas_las_sedes(self):
        self.assertEqual(self.ver('Compra RSS'), self.todas)

    def test_compra_rsd_solo_ve_las_de_su_sede(self):
        self.assertEqual(self.ver('Compra RSD'), {self.por_sede['RSD'].pk})

    def test_compra_rco_solo_ve_las_de_su_sede(self):
        self.assertEqual(self.ver('Compra RCO'), {self.por_sede['RCO'].pk})

    def test_colaborador_solo_ve_las_suyas(self):
        self.assertEqual(self.ver('Colaborador'), set())

    def test_sin_rol_en_cep_es_colaborador(self):
        self.assertEqual(self.ver(), set())
        self.assertEqual(self.client.get('/api/sesion/').json()['usuario']['role'], 'COLABORADOR')

    def test_prioridad_de_roles_como_la_de_los_grupos(self):
        """Supervisor > Compra RSS > Compra RSD > Compra RCO > Colaborador."""
        casos = [(('Compra RCO', 'Supervisor'), 'SUPERVISOR'), (('Compra RSD', 'Compra RSS'), 'COMPRA RSS'),
                 (('Compra RCO', 'Compra RSD'), 'COMPRA RSD'), (('Colaborador', 'Compra RCO'), 'COMPRA RCO')]
        for i, (roles, esperado) in enumerate(casos):
            with self.subTest(roles=roles):
                self.login(crear_usuario(f'p{i}@uapa.edu.do'), apps=apps_con(*roles))
                self.assertEqual(self.client.get('/api/sesion/').json()['usuario']['role'], esperado)

    def test_supervisor_puede_eliminar_solicitudes_ajenas(self):
        self.login(crear_usuario(), apps=apps_con('Supervisor'))
        pk = self.por_sede['RSS'].pk
        r = self.client.post(f'/api/solicitudes/delete/{pk}/')
        self.assertNotIn(r.status_code, (401, 403))

    def test_correo_a_compras_llega_a_quien_tiene_el_rol(self):
        """Quién tiene el rol de Compra lo dice el directorio de la plataforma (no hace falta que haya entrado)."""
        from unittest import mock
        from django.test import RequestFactory
        from presupuesto import utils
        crear_usuario('compras.rsd@uapa.edu.do', grupo='Compra RSD')
        crear_usuario('compras.rss@uapa.edu.do', grupo='Compra RSS')
        crear_usuario('compras.rco@uapa.edu.do', grupo='Compra RCO')
        request = RequestFactory().get('/', HTTP_HOST='cep.uapa.edu.do')
        with mock.patch.object(utils, 'send_email') as enviar:
            utils.enviar_email_a_compras(request, self.por_sede['RSD'])
        self.assertEqual(enviar.call_args.kwargs['send_to_list'],
                         ['otro@uapa.edu.do', 'compras.rss@uapa.edu.do', 'compras.rsd@uapa.edu.do'])
        self.assertEqual(enviar.call_args.kwargs['context']['sede'], 'Recinto Santo Domingo')
