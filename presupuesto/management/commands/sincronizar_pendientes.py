"""Pone al día en UAPA Suite los pendientes de CEP (presupuesto/pendientes.py): «Revisar solicitud …» de los
Supervisores (solicitudes en PENDIENTE), «Corregir solicitud …» del colaborador (en POR_REVISION) y «Confirmar
solicitud …» de Compras (APROBADA con certificado y sin confirmar).

Sincroniza todas las solicitudes en PENDIENTE, POR_REVISION o aprobadas sin confirmar, y cierra los de las aprobadas o rechazadas modificadas en
los últimos --dias. Sirve para la carga inicial y como respaldo si la plataforma no respondió en algún momento: se
puede correr las veces que haga falta (no duplica nada).

    python manage.py sincronizar_pendientes --simular
    python manage.py sincronizar_pendientes
    python manage.py sincronizar_pendientes --dias 0       # cierra los de TODAS las confirmadas o rechazadas
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from presupuesto import pendientes
from presupuesto.models import SolicitudPresupuesto


class Command(BaseCommand):
    help = 'Pone al día los pendientes de CEP en UAPA Suite (supervisores, colaboradores y Compras).'

    def add_arguments(self, parser):
        parser.add_argument('--url-base', default='https://cep.uapa.edu.do',
                            help='Dirección pública de CEP para el enlace «Abrir» (por defecto https://cep.uapa.edu.do)')
        parser.add_argument('--dias', type=int, default=30,
                            help='Cerrar los de confirmadas o rechazadas modificadas en estos días (0 = todas)')
        parser.add_argument('--simular', action='store_true', help='Solo muestra lo que haría')

    def handle(self, *args, **opciones):
        base = opciones['url_base'].rstrip('/') + settings.PREFIX_URL.rstrip('/')
        en_curso = (Q(estado__iexact='PENDIENTE') | Q(estado__iexact='POR_REVISION')
                    | Q(estado__iexact='APROBADA', confirmado=False))
        abiertas = list(SolicitudPresupuesto.objects.filter(en_curso).order_by('pk'))
        cerradas = SolicitudPresupuesto.objects.exclude(en_curso)
        if opciones['dias']:
            cerradas = cerradas.filter(updated_at__gte=timezone.now() - timedelta(days=opciones['dias']))
        cerradas = list(cerradas.order_by('pk'))
        self.stdout.write(f'{len(abiertas)} solicitud(es) en curso y {len(cerradas)} para cerrar.')
        if opciones['simular']:
            self.stdout.write(f"Supervisores: {', '.join(pendientes.supervisores()) or '(ninguno)'}")
            self.stdout.write('Simulación: no se envió nada.')
            return
        fallos = 0
        for solicitud in abiertas + cerradas:
            try:
                resultado = pendientes.enviar(solicitud, f'{base}/request/{solicitud.pk}/',
                                              f'{base}/request/{solicitud.pk}/confirm')
                self.stdout.write(f'  #{solicitud.pk} {solicitud.estado}: {resultado}')
            except Exception as exc:
                fallos += 1
                self.stderr.write(f'  #{solicitud.pk}: no se pudo ({type(exc).__name__}: {exc})')
        estilo = self.style.WARNING if fallos else self.style.SUCCESS
        self.stdout.write(estilo(f'Listo: {len(abiertas) + len(cerradas) - fallos} bien, {fallos} con error.'))
