"""CEP sin usuarios ni sedes locales: colaborador, supervisor y sede son UUID de la plataforma y se resuelven con su
directorio (authentication/directorio.py)."""
from datetime import date
from unittest import mock

from presupuesto.models import CuentaAnalitica, RevisionSolicitud, SolicitudPresupuesto

from .base import RECINTOS, CEPTestCase, crear_usuario, sede, uid


def apps_con(*roles):
    return [{'app_slug': 'cep', 'roles': list(roles)}]


class DirectorioTests(CEPTestCase):

    def setUp(self):
        super().setUp()
        self.cuenta = CuentaAnalitica.objects.create(codigo='ZZ-1', nombre='Prueba')
        self.ana = crear_usuario()
        self.supervisor = crear_usuario('sup@uapa.edu.do', grupo='Supervisor', first_name='Luis', last_name='Gómez')

    def solicitud(self, **extra):
        datos = dict(colaborador=self.ana, titulo='Taller', descripcion='d', tipo_solicitud='DESEMBOLSO',
                     rubro_presupuestal='SERVICIOS', ubicacion=sede('RSD'), cuenta_analitica=self.cuenta,
                     presupuesto_pre_aprobado=10, monto_a_ejecutar=5, fecha_actividad=date.today())
        datos.update(extra)
        return SolicitudPresupuesto.objects.create(**datos)

    def test_se_guardan_los_uuid_de_la_plataforma(self):
        s = self.solicitud()
        s.refresh_from_db()
        self.assertEqual(s.colaborador_id, uid('ana@uapa.edu.do'))
        self.assertEqual(s.ubicacion_id, sede('RSD'))
        self.assertEqual(s.colaborador.get_full_name(), 'Ana Pérez')
        self.assertEqual((s.ubicacion.codigo, s.ubicacion.nombre), ('RSD', 'Recinto Santo Domingo'))

    def test_el_listado_trae_colaborador_sede_y_revisor_en_una_llamada(self):
        s = self.solicitud()
        RevisionSolicitud.objects.create(solicitud=s, supervisor=self.supervisor, contenido='Corrige el monto')
        self.login(self.supervisor, apps=apps_con('Supervisor'))
        item = self.client.get('/api/solicitudes/list').json()['solicitudes'][0]
        self.assertEqual(item['colaborador'], {'id': str(self.ana.id), 'username': 'ana@uapa.edu.do',
                                               'email': 'ana@uapa.edu.do', 'first_name': 'Ana', 'last_name': 'Pérez'})
        self.assertEqual(item['ubicacion'], {'id': str(sede('RSD')), 'codigo': 'RSD', 'nombre': 'Recinto Santo Domingo'})
        self.assertEqual(item['review_notes'][0]['supervisor'], 'Luis Gómez')
        self.assertNotIn('colaborador_id', item)
        self.assertEqual(len(self.plataforma.peticiones('/directory/usuarios/resolver')), 1)

    def test_usuario_que_ya_no_esta_en_la_plataforma_no_rompe_el_listado(self):
        self.solicitud(colaborador=uid('borrado@uapa.edu.do'))
        self.login(self.supervisor, apps=apps_con('Supervisor'))
        item = self.client.get('/api/solicitudes/list').json()['solicitudes'][0]
        self.assertEqual(item['colaborador']['id'], str(uid('borrado@uapa.edu.do')))
        self.assertEqual(item['colaborador']['email'], '')

    def test_el_colaborador_solo_ve_las_suyas(self):
        mia = self.solicitud()
        self.solicitud(colaborador=self.supervisor)
        self.login(self.ana, apps=apps_con('Colaborador'))
        ids = [s['id'] for s in self.client.get('/api/solicitudes/list').json()['solicitudes']]
        self.assertEqual(ids, [mia.pk])

    def test_ubicaciones_son_los_recintos(self):
        self.login(self.ana, apps=apps_con('Colaborador'))
        datos = self.client.get('/api/ubicaciones/list/').json()
        self.assertEqual({d['codigo'] for d in datos}, {r['recinto_abreviatura'] for r in RECINTOS})
        self.assertEqual(datos, sorted(datos, key=lambda d: d['nombre']))

    def test_crear_con_el_uuid_de_la_sede(self):
        self.login(self.ana, apps=apps_con('Colaborador'))
        with mock.patch('presupuesto.solicitudes_view.obtener_carpeta_en_drive', return_value='carpeta'), \
                mock.patch('presupuesto.solicitudes_view.enviar_email_solicitud_creada') as correo:
            r = self.fetch_post('/api/solicitudes/crear/', json_body={
                'titulo': 'Nueva', 'descripcion': 'd', 'tipo_solicitud': 'DESEMBOLSO', 'rubro_presupuestal': 'SERVICIOS',
                'ubicacion_id': str(sede('RCO')), 'cuenta_analitica_id': self.cuenta.pk, 'monto_a_ejecutar': '5',
                'presupuesto_pre_aprobado': '10', 'fecha_actividad': '2026-12-01T00:00:00Z'})
        self.assertEqual(r.status_code, 201, r.content)
        s = SolicitudPresupuesto.objects.get(titulo='Nueva')
        self.assertEqual((s.colaborador_id, s.ubicacion_id), (self.ana.id, sede('RCO')))
        self.assertEqual(correo.call_args.args[0]['sede'], 'Recinto Cibao Oriental')
        self.assertEqual(correo.call_args.args[0]['solicitante'], 'Ana Pérez')

    def test_solo_el_dueno_o_un_supervisor_elimina(self):
        s = self.solicitud()
        self.login(crear_usuario('otra@uapa.edu.do'), apps=apps_con('Colaborador'))
        self.assertEqual(self.client.delete(f'/api/solicitudes/delete/{s.pk}/').status_code, 403)
        self.login(self.ana, apps=apps_con('Colaborador'))
        self.assertEqual(self.client.delete(f'/api/solicitudes/delete/{s.pk}/').status_code, 200)
