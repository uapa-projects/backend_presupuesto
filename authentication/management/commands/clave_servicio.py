"""
Guarda la contraseña de la cuenta de servicio de CEP en la plataforma (parámetro PLATFORM_SERVICE_PASSWORD).

La pide sin mostrarla en pantalla, así no queda en el historial de la terminal ni en el log:

    python manage.py clave_servicio

Sin esa contraseña nadie puede entrar a CEP. Si la plataforma la rechaza, se deja el valor anterior.
"""
from getpass import getpass

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from authentication import plataforma
from presupuesto.models import Configuracion

NOMBRE = 'PLATFORM_SERVICE_PASSWORD'


class Command(BaseCommand):
    help = 'Guarda la contraseña de la cuenta de servicio de la plataforma (PLATFORM_SERVICE_PASSWORD).'

    def add_arguments(self, parser):
        parser.add_argument('--sin-probar', action='store_true',
                            help='No comprobar la contraseña contra la plataforma antes de guardarla.')

    def handle(self, *args, **opciones):
        usuario = plataforma.parametro('PLATFORM_SERVICE_USERNAME')
        if not usuario:
            raise CommandError('Falta PLATFORM_SERVICE_USERNAME. Ejecuta primero: python manage.py migrate')
        clave = getpass(f'Contraseña de la cuenta de servicio «{usuario}»: ')
        if not clave:
            raise CommandError('No se escribió ninguna contraseña.')
        if len(clave) > Configuracion._meta.get_field('valor').max_length:
            raise CommandError('La contraseña es demasiado larga para la tabla Configuración (máximo 100).')

        anterior = Configuracion.objects.filter(nombre=NOMBRE).first()
        valor_anterior = anterior.valor if anterior else None
        Configuracion.objects.update_or_create(nombre=NOMBRE, defaults={'valor': clave})
        cache.delete(plataforma._CACHE_TOKEN_SERVICIO)  # el token de servicio anterior ya no vale

        if not opciones['sin_probar']:
            try:
                plataforma._token_servicio(renovar=True)
            except plataforma.PlataformaError as error:
                # No se deja guardada una contraseña que la plataforma rechaza.
                if valor_anterior is None:
                    Configuracion.objects.filter(nombre=NOMBRE).delete()
                else:
                    Configuracion.objects.filter(nombre=NOMBRE).update(valor=valor_anterior)
                cache.delete(plataforma._CACHE_TOKEN_SERVICIO)
                raise CommandError(f'La plataforma no aceptó la contraseña ({error.status} {error.code}). '
                                   f'No se guardó.')
        self.stdout.write(self.style.SUCCESS('Contraseña de servicio guardada.'))
