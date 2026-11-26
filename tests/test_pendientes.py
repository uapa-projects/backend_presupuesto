"""Pendientes «Revisar solicitud …» de los Supervisores en UAPA Suite (presupuesto/pendientes.py)."""
import json
from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import RequestFactory
from django.utils import timezone

from presupuesto import pendientes
from presupuesto.models import AdjuntoSolicitud, CuentaAnalitica, RevisionSolicitud, SolicitudPresupuesto

from .base import HOST, CEPTestCase, crear_usuario, sede
from .test_roles import apps_con


class PendientesTests(CEPTestCase):

    def setUp(self):
        super().setUp()
        self.colaborador = crear_usuario('ana@uapa.edu.do', grupo='Colaborador')
        crear_usuario('sup1@uapa.edu.do', grupo='Supervisor', first_name='Sara')
        crear_usuario('sup2@uapa.edu.do', grupo='Supervisor', first_name='Saúl')
        crear_usuario('inactivo@uapa.edu.do', grupo='Supervisor', activo=False)
        crear_usuario('compra@uapa.edu.do', grupo='Compra RSS')
        crear_usuario('compra-rsd@uapa.edu.do', grupo='Compra RSD')
        crear_usuario('compra-rco@uapa.edu.do', grupo='Compra RCO')
        cuenta = CuentaAnalitica.objects.create(codigo='ZZ-1', nombre='Prueba')
        self.solicitud = SolicitudPresupuesto.objects.create(
            colaborador=self.colaborador, titulo='Compra de sillas', descripcion='d', tipo_solicitud='DESEMBOLSO',
            rubro_presupuestal='SERVICIOS', ubicacion=sede('RSS'), cuenta_analitica=cuenta,
            presupuesto_pre_aprobado=5000, monto_a_ejecutar=4500, fecha_actividad=date.today())
        self.origen = f'cep:solicitud:{self.solicitud.pk}'
        self.revision = f'cep:solicitud:{self.solicitud.pk}:revision'
        self.compras = f'cep:solicitud:{self.solicitud.pk}:compras'

    def sincronizar(self):
        request = RequestFactory().get('/', HTTP_HOST=HOST)
        self.solicitud.refresh_from_db()  # las vistas pudieron cambiarla
        with self.captureOnCommitCallbacks(execute=True):
            pendientes.sincronizar(request, self.solicitud)

    def como_supervisor(self):
        self.login(crear_usuario('sup1@uapa.edu.do', grupo='Supervisor'), apps=apps_con('Supervisor'))

    def patch(self, url, datos):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.patch(url, data=json.dumps(datos), content_type='application/json')

    def test_pendiente_para_todos_los_supervisores_activos(self):
        self.sincronizar()
        p = self.plataforma.pendientes[self.origen]
        self.assertEqual(p['destinatarios'], ['sup1@uapa.edu.do', 'sup2@uapa.edu.do'])  # ni Compra ni inactivos
        self.assertEqual(p['titulo'], 'Revisar solicitud «Compra de sillas»')
        self.assertEqual(p['url'], f'http://{HOST}/request/{self.solicitud.pk}/')
        self.assertIn('Ana Pérez', p['texto'])
        self.assertIn('RD$ 4,500.00', p['texto'])
        self.assertFalse(p['completado'])
        # Lo pide con la cuenta de servicio, al /notify hermano de PLATFORM_AUTH_URL.
        llamada = self.plataforma.peticiones(f'/pendientes/{self.origen}')[-1]
        self.assertEqual(str(llamada.url), f'https://gtsst.uapa.edu.do/serviceserver/notify/pendientes/{self.origen}')
        self.assertEqual(llamada.headers['Authorization'], 'Bearer token-de-servicio')

    def test_aprobar_y_rechazar_lo_cierran(self):
        self.sincronizar()
        self.como_supervisor()
        r = self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'APROBADA'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.plataforma.pendientes[self.origen]['completado'])

    def test_por_revision_pasa_al_colaborador_y_vuelve_al_resolverla(self):
        self.sincronizar()
        self.assertNotIn(self.revision, self.plataforma.pendientes)  # en PENDIENTE el colaborador no tiene nada
        self.como_supervisor()
        r = self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/',
                       {'status': 'POR_REVISION', 'comments': 'Falta la cotización'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.plataforma.pendientes[self.origen]['completado'])  # los Supervisores, en pausa
        p = self.plataforma.pendientes[self.revision]
        self.assertEqual(p['destinatarios'], ['ana@uapa.edu.do'])
        self.assertEqual(p['titulo'], 'Corregir solicitud «Compra de sillas»')
        self.assertEqual(p['texto'], 'Falta la cotización')
        self.assertEqual(p['url'], f'http://{HOST}/request/{self.solicitud.pk}/')
        self.assertFalse(p['completado'])
        revision = RevisionSolicitud.objects.get(solicitud=self.solicitud)
        r = self.patch(f'/api/solicitudes/revision/{revision.pk}/', {'estado': 'RESUELTA'})
        self.assertEqual(r.json()['solicitud']['estado'], 'PENDIENTE')
        self.assertTrue(self.plataforma.pendientes[self.revision]['completado'])  # el colaborador terminó
        self.assertFalse(self.plataforma.pendientes[self.origen]['completado'])  # vuelve a los Supervisores

    def test_varias_observaciones_cuenta_las_que_faltan(self):
        self.como_supervisor()
        self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'POR_REVISION', 'comments': 'Uno'})
        self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'POR_REVISION', 'comments': 'Dos'})
        self.assertEqual(self.plataforma.pendientes[self.revision]['texto'], '2 observación(es) por resolver: Dos')
        primera = RevisionSolicitud.objects.get(solicitud=self.solicitud, contenido='Uno')
        r = self.patch(f'/api/solicitudes/revision/{primera.pk}/', {'estado': 'RESUELTA'})
        self.assertEqual(r.json()['solicitud']['estado'], 'POR_REVISION')
        p = self.plataforma.pendientes[self.revision]
        self.assertEqual((p['texto'], p['completado']), ('Dos', False))  # le queda una

    def test_aprobarla_en_revision_cierra_el_del_colaborador(self):
        self.como_supervisor()
        self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'POR_REVISION', 'comments': 'Falta'})
        self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'APROBADA'})
        self.assertTrue(self.plataforma.pendientes[self.revision]['completado'])

    def test_eliminar_los_borra(self):
        self.como_supervisor()
        self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'POR_REVISION', 'comments': 'Falta'})
        self.assertIn(self.revision, self.plataforma.pendientes)
        self.login(self.colaborador, apps=apps_con('Colaborador'))
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.delete(f'/api/solicitudes/delete/{self.solicitud.pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(self.origen, self.plataforma.pendientes)
        self.assertNotIn(self.revision, self.plataforma.pendientes)

    def test_plataforma_caida_no_rompe_la_solicitud(self):
        self.sincronizar()
        self.plataforma.notify_status = 503
        self.como_supervisor()
        with self.assertLogs('presupuesto.pendientes', 'WARNING'):
            r = self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'RECHAZADA', 'comments': 'No'})
        self.assertEqual(r.status_code, 200)
        self.solicitud.refresh_from_db()
        self.assertEqual(self.solicitud.estado, 'RECHAZADA')
        self.assertFalse(self.plataforma.pendientes[self.origen]['completado'])  # lo arreglará el comando

    def test_comando_sincronizar(self):
        aprobada = SolicitudPresupuesto.objects.get(pk=self.solicitud.pk)
        aprobada.pk = None
        aprobada.titulo, aprobada.estado = 'Ya aprobada', 'APROBADA'
        aprobada.save()
        self.plataforma.pendientes[f'cep:solicitud:{aprobada.pk}'] = {'destinatarios': ['sup1@uapa.edu.do'],
                                                                     'completado': False}
        salida = StringIO()
        call_command('sincronizar_pendientes', '--simular', stdout=salida)
        self.assertIn('Simulación', salida.getvalue())
        self.assertNotIn(self.origen, self.plataforma.pendientes)
        en_revision = SolicitudPresupuesto.objects.get(pk=self.solicitud.pk)
        en_revision.pk = None
        en_revision.titulo, en_revision.estado = 'En revisión hace tiempo', 'POR_REVISION'
        en_revision.save()
        SolicitudPresupuesto.objects.filter(pk=en_revision.pk).update(updated_at=timezone.now() - timedelta(days=90))
        RevisionSolicitud.objects.create(solicitud=en_revision, supervisor=self.colaborador, contenido='Corregir monto')
        call_command('sincronizar_pendientes', stdout=StringIO())
        self.assertEqual(self.plataforma.pendientes[self.origen]['url'],
                         f'https://cep.uapa.edu.do/request/{self.solicitud.pk}/')
        self.assertFalse(self.plataforma.pendientes[self.origen]['completado'])
        self.assertTrue(self.plataforma.pendientes[f'cep:solicitud:{aprobada.pk}']['completado'])
        # Las de POR_REVISION se sincronizan siempre, aunque sean más viejas que --dias.
        p = self.plataforma.pendientes[f'cep:solicitud:{en_revision.pk}:revision']
        self.assertEqual((p['texto'], p['completado']), ('Corregir monto', False))

    def test_sin_supervisores(self):
        from .base import DIRECTORIO, uid
        for email in ('sup1@uapa.edu.do', 'sup2@uapa.edu.do', 'inactivo@uapa.edu.do'):
            DIRECTORIO.pop(uid(email))
        with self.assertLogs('presupuesto.pendientes', 'WARNING'):
            self.sincronizar()
        self.assertNotIn(self.origen, self.plataforma.pendientes)

    # ---- Compras: desde que está aprobada con certificado hasta que la confirman ----
    def certificado(self, solicitud=None):
        solicitud = solicitud or self.solicitud
        AdjuntoSolicitud.objects.create(solicitud=solicitud, nombre='certificado.pdf', drive_id=f'drive-{solicitud.pk}',
                                        url_view='https://drive/x', mime_type='application/pdf', es_certificado=True)

    def aprobar(self):
        self.como_supervisor()
        return self.patch(f'/api/solicitudes/status/{self.solicitud.pk}/', {'status': 'APROBADA'})

    def test_compras_solo_con_aprobada_y_certificado(self):
        self.aprobar()
        self.assertNotIn(self.compras, self.plataforma.pendientes)  # aprobada, pero aún sin certificado
        self.certificado()
        self.sincronizar()
        p = self.plataforma.pendientes[self.compras]
        self.assertEqual(p['destinatarios'], ['compra@uapa.edu.do'])  # sede RSS: solo Compra RSS
        self.assertEqual(p['titulo'], 'Confirmar solicitud «Compra de sillas»')
        self.assertEqual(p['url'], f'http://{HOST}/request/{self.solicitud.pk}/confirm')
        self.assertFalse(p['completado'])

    def test_certificado_antes_de_aprobar(self):
        self.certificado()
        self.sincronizar()
        self.assertNotIn(self.compras, self.plataforma.pendientes)  # con certificado pero en PENDIENTE
        self.aprobar()
        self.assertFalse(self.plataforma.pendientes[self.compras]['completado'])

    def test_confirmar_lo_cierra(self):
        self.certificado()
        self.aprobar()
        self.login(crear_usuario('compra@uapa.edu.do', grupo='Compra RSS'), apps=apps_con('Compra RSS'))
        r = self.patch(f'/api/solicitudes/confirm/{self.solicitud.pk}/', {})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.plataforma.pendientes[self.compras]['completado'])

    def test_compras_de_la_sede(self):
        SolicitudPresupuesto.objects.filter(pk=self.solicitud.pk).update(ubicacion_id=sede('RSD'))
        self.solicitud.refresh_from_db()
        self.certificado()
        self.aprobar()
        self.assertEqual(self.plataforma.pendientes[self.compras]['destinatarios'],
                         ['compra-rsd@uapa.edu.do', 'compra@uapa.edu.do'])  # RSS siempre, más RSD; RCO no

    def test_comando_abre_el_de_compras_de_aprobadas_viejas(self):
        self.certificado()
        SolicitudPresupuesto.objects.filter(pk=self.solicitud.pk).update(
            estado='APROBADA', updated_at=timezone.now() - timedelta(days=90))
        call_command('sincronizar_pendientes', stdout=StringIO())
        p = self.plataforma.pendientes[self.compras]
        self.assertEqual((p['url'], p['completado']),
                         (f'https://cep.uapa.edu.do/request/{self.solicitud.pk}/confirm', False))
