"""
API de la pantalla Administración de React (/administracion).

Todo es solo para el rol Administrador de la plataforma (el Supervisor revisa solicitudes, pero no administra):
- Cuentas analíticas y cuentas contables.
- Parámetros (modelo Configuracion). Las contraseñas (p. ej. PLATFORM_SERVICE_PASSWORD) no salen nunca por aquí:
  ni se listan ni se editan; se cargan con `manage.py clave_servicio`.

Usuarios, roles, sedes y credenciales de Google no se administran en CEP (los roles, en auth_service).
"""
import json
import re

from django.core.cache import cache
from django.db.models import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.views import View

from authentication.roles import es_administrador
from .models import Configuracion, CuentaAnalitica, CuentaContable

PATRON_SECRETO = r'(PASSWORD|SECRET|TOKEN|CLAVE)'


def es_secreto(nombre):
    return bool(re.search(PATRON_SECRETO, nombre or '', re.IGNORECASE))


def _error(texto, status):
    return JsonResponse({'error': texto}, status=status)


def _cuerpo(request):
    try:
        datos = json.loads(request.body or b'{}')
    except ValueError:
        return None
    return datos if isinstance(datos, dict) else None


class _Protegida(View):
    """401 sin sesión, 403 si no es Administrador."""
    permiso = staticmethod(es_administrador)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error('No autorizado. Por favor inicia sesión.', 401)
        if not self.permiso(request.user):
            return _error('No tienes permiso para administrar esta sección.', 403)
        return super().dispatch(request, *args, **kwargs)


# ---------------- Cuentas analíticas y contables ----------------

class _Cuentas(_Protegida):
    modelo = None
    unico = False  # el código no se puede repetir
    nombre = 'cuenta'

    def _dict(self, cuenta, en_uso=None):
        return {'id': cuenta.pk, 'codigo': cuenta.codigo, 'nombre': cuenta.nombre,
                'en_uso': bool(en_uso if en_uso is not None else cuenta.solicitudpresupuesto_set.exists())}

    def _validar(self, request, actual=None):
        datos = _cuerpo(request)
        if datos is None:
            return None, _error('Solicitud no válida.', 400)
        codigo = str(datos.get('codigo') or '').strip()
        nombre = str(datos.get('nombre') or '').strip()
        if not codigo or not nombre:
            return None, _error('Escribe el código y el nombre.', 400)
        largo = self.modelo._meta.get_field
        if len(codigo) > largo('codigo').max_length or len(nombre) > largo('nombre').max_length:
            return None, _error(f"El código admite {largo('codigo').max_length} caracteres y el nombre "
                                f"{largo('nombre').max_length}.", 400)
        if self.unico:
            repetida = self.modelo.objects.filter(codigo__iexact=codigo)
            if actual is not None:
                repetida = repetida.exclude(pk=actual.pk)
            if repetida.exists():
                return None, _error(f'Ya existe una {self.nombre} con ese código.', 400)
        return {'codigo': codigo, 'nombre': nombre}, None

    def _buscar(self, pk):
        return self.modelo.objects.filter(pk=pk).first()

    def get(self, request, pk=None):
        en_uso = set(self.modelo.objects.filter(solicitudpresupuesto__isnull=False).values_list('pk', flat=True))
        cuentas = self.modelo.objects.order_by('codigo', 'nombre')
        return JsonResponse([self._dict(c, c.pk in en_uso) for c in cuentas], safe=False)

    def post(self, request, pk=None):
        if pk is not None:
            return _error('Método no permitido.', 405)
        datos, error = self._validar(request)
        if error:
            return error
        return JsonResponse(self._dict(self.modelo.objects.create(**datos), False), status=201)

    def put(self, request, pk=None):
        cuenta = self._buscar(pk)
        if cuenta is None:
            return _error('No encontrado.', 404)
        datos, error = self._validar(request, cuenta)
        if error:
            return error
        cuenta.codigo, cuenta.nombre = datos['codigo'], datos['nombre']
        cuenta.save(update_fields=['codigo', 'nombre'])
        return JsonResponse(self._dict(cuenta))

    def delete(self, request, pk=None):
        cuenta = self._buscar(pk)
        if cuenta is None:
            return _error('No encontrado.', 404)
        try:
            cuenta.delete()
        except ProtectedError:
            return _error(f'No se puede eliminar: hay solicitudes con esta {self.nombre}.', 409)
        return HttpResponse(status=204)


class CuentasAnaliticas(_Cuentas):
    modelo = CuentaAnalitica
    nombre = 'cuenta analítica'


class CuentasContables(_Cuentas):
    modelo = CuentaContable
    unico = True
    nombre = 'cuenta contable'


# ---------------- Parámetros ----------------

class Parametros(_Protegida):

    @staticmethod
    def _dict(p):
        return {'nombre': p.nombre, 'valor': p.valor, 'descripcion': p.descripcion or ''}

    def get(self, request, nombre=None):
        filas = Configuracion.objects.exclude(nombre__iregex=PATRON_SECRETO).order_by('nombre')
        return JsonResponse([self._dict(p) for p in filas], safe=False)

    def put(self, request, nombre=None):
        if es_secreto(nombre):
            return _error('Este parámetro no se edita aquí: usa manage.py clave_servicio.', 403)
        parametro = Configuracion.objects.filter(nombre=nombre).first()
        if parametro is None:
            return _error('No encontrado.', 404)
        datos = _cuerpo(request)
        if datos is None or not isinstance(datos.get('valor'), str):
            return _error('Solicitud no válida.', 400)
        valor = datos['valor'].strip()
        maximo = Configuracion._meta.get_field('valor').max_length
        if len(valor) > maximo:
            return _error(f'El valor admite {maximo} caracteres.', 400)
        parametro.valor = valor
        parametro.save(update_fields=['valor'])
        cache.delete(f'cep:parametro:{nombre}')  # core/sesion.py lo guarda 5 minutos
        return JsonResponse(self._dict(parametro))
