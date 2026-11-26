"""Utilidades de las pruebas: plataforma simulada (httpx.MockTransport), llave RSA de prueba y usuarios.

CEP no tiene usuarios ni sedes locales: `crear_usuario` registra al usuario en el directorio de la plataforma
falsa (DIRECTORIO) y las sedes son los recintos de RECINTOS (`sede("RSS")` da su UUID)."""
import json
import time
import uuid

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.test import TestCase

from authentication import directorio, plataforma
from presupuesto.models import Configuracion

HOST = 'cep.uapa.edu.do'

# Directorio de la plataforma falsa: {UUID: usuario como lo devuelve /directory/usuarios (con sus roles en CEP)}.
DIRECTORIO = {}
RECINTOS = [
    {'id': '3f095981-0000-4000-8000-000000000001', 'recinto_nombre': 'Recinto Santiago', 'recinto_abreviatura': 'RSS'},
    {'id': '3bba4c95-0000-4000-8000-000000000002', 'recinto_nombre': 'Recinto Santo Domingo', 'recinto_abreviatura': 'RSD'},
    {'id': 'b037b021-0000-4000-8000-000000000003', 'recinto_nombre': 'Recinto Cibao Oriental', 'recinto_abreviatura': 'RCO'},
]


def uid(email):
    """UUID (fijo) del usuario de la plataforma con ese correo."""
    return uuid.uuid5(uuid.NAMESPACE_DNS, email.strip().lower())


def sede(codigo):
    """UUID del recinto RSS / RSD / RCO."""
    return uuid.UUID(next(r['id'] for r in RECINTOS if r['recinto_abreviatura'] == codigo))


def _llave():
    privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = privada.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return privada, pem


LLAVE_PRIVADA, LLAVE_PUBLICA = _llave()
OTRA_LLAVE_PRIVADA, _ = _llave()


def token_logout(email, aud='cep', tipo='logout', exp=300, llave=None):
    claims = {'type': tipo, 'aud': aud, 'sub': 'id-plataforma', 'email': email, 'sv': 1,
              'exp': int(time.time()) + exp}
    return jwt.encode(claims, llave or LLAVE_PRIVADA, algorithm='RS256')


def respuesta_ok(email, status='logged_in', method='password', access='access-nuevo', refresh='refresh-nuevo',
                 foto_url='https://lh3.googleusercontent.com/foto', full_name='Ana Pérez', apps=None):
    """Respuesta de /auth/authenticate. Sin `apps`, los roles en CEP son los del usuario en DIRECTORIO (ninguno si
    no está: no tiene acceso a CEP)."""
    registrado = DIRECTORIO.get(uid(email))
    return {
        'status': status, 'method': method, 'access_token': access, 'refresh_token': refresh,
        'token_type': 'bearer',
        'user': {'id': str(uid(email)), 'email': email, 'full_name': full_name,
                 'is_active': registrado['is_active'] if registrado else True, 'foto_url': foto_url},
        'apps': apps if apps is not None else [
            {'app_slug': 'cep', 'app_name': 'CEP', 'roles': list(registrado['roles']) if registrado else []},
            {'app_slug': 'popin', 'app_name': 'PopIn', 'roles': []}],
    }


def error(status, code, message='Mensaje de la plataforma'):
    return httpx.Response(status, json={'detail': {'code': code, 'message': message}})


class PlataformaFalsa:
    """auth_service simulado. `autenticar` es una lista de respuestas (o una función) para /auth/authenticate;
    `caida = True` simula la red caída. Guarda las peticiones en `llamadas`."""

    def __init__(self):
        self.llamadas = []
        self.autenticar = []
        self.caida = False
        self.logout_status = 200
        self.servicio_status = 200
        self.notify_status = 200
        # notify_service simulado: {origen: {'destinatarios': [...], 'titulo', ..., 'completado': bool}}
        self.pendientes = {}

    def _pendientes(self, request, resto):
        if self.notify_status != 200:
            return httpx.Response(self.notify_status, json={'detail': 'no'})
        if resto.endswith('/completar') and request.method == 'POST':
            origen = resto[: -len('/completar')]
            p = self.pendientes.get(origen)
            n = len(p['destinatarios']) if p and not p['completado'] else 0
            if p:
                p['completado'] = True
            return httpx.Response(200, json={'completados': n})
        if request.method == 'PUT':
            self.pendientes[resto] = dict(json.loads(request.content), completado=False)
            return httpx.Response(200, json={'destinatarios': len(self.pendientes[resto]['destinatarios']),
                                             'quitados': 0})
        if request.method == 'DELETE':
            self.pendientes.pop(resto, None)
            return httpx.Response(204)
        return httpx.Response(405)

    def cuerpos(self, ruta):
        return [json.loads(r.content or b'{}') for r in self.llamadas if r.url.path.endswith(ruta)]

    def peticiones(self, ruta):
        return [r for r in self.llamadas if r.url.path.endswith(ruta)]

    def __call__(self, request):
        self.llamadas.append(request)
        if self.caida:
            raise httpx.ConnectError('sin red', request=request)
        ruta = request.url.path
        if ruta.endswith('/service-auth/login'):
            if self.servicio_status != 200:
                return httpx.Response(self.servicio_status, json={'detail': 'no'})
            return httpx.Response(200, json={'access_token': 'token-de-servicio'})
        if ruta.endswith('/auth/public-key'):
            return httpx.Response(200, text=LLAVE_PUBLICA, headers={'content-type': 'text/plain'})
        if ruta.endswith('/auth/logout'):
            return httpx.Response(self.logout_status, json={})
        if ruta.endswith('/directory/usuarios/resolver'):
            cuerpo = json.loads(request.content or b'{}')
            ids = {uuid.UUID(i) for i in cuerpo.get('ids') or []}
            emails = {e.lower() for e in cuerpo.get('emails') or []}
            return httpx.Response(200, json=[_publico(u) for u in DIRECTORIO.values()
                                             if uuid.UUID(u['id']) in ids or u['email'] in emails])
        if ruta.endswith('/directory/usuarios'):
            rol, solo_activos = request.url.params.get('rol'), request.url.params.get('solo_activos') != 'false'
            return httpx.Response(200, json=[_publico(u) for u in DIRECTORIO.values()
                                             if (not rol or rol in u['roles']) and (u['is_active'] or not solo_activos)])
        if ruta.endswith('/directory/recintos'):
            return httpx.Response(200, json=RECINTOS)
        if '/notify/pendientes/' in ruta:
            return self._pendientes(request, ruta.split('/notify/pendientes/', 1)[1])
        if ruta.endswith('/auth/authenticate'):
            if callable(self.autenticar):
                respuesta = self.autenticar(request)
            elif self.autenticar:
                respuesta = self.autenticar.pop(0)
            else:
                respuesta = error(401, 'not_logged_in')
            return respuesta if isinstance(respuesta, httpx.Response) else httpx.Response(200, json=respuesta)
        return httpx.Response(404)


def _publico(u):
    return {k: v for k, v in u.items() if k != 'roles'} | {'roles': list(u['roles'])}


def crear_usuario(email='ana@uapa.edu.do', grupo='Colaborador', activo=True, first_name='Ana', last_name='Pérez'):
    """Registra al usuario en el directorio de la plataforma falsa, con el rol `grupo` en CEP."""
    datos = {'id': str(uid(email)), 'email': email.lower(), 'full_name': f'{first_name} {last_name}'.strip(),
             'is_active': activo, 'foto_url': None, 'es_colaborador': True, 'roles': [grupo] if grupo else []}
    DIRECTORIO[uid(email)] = datos
    return directorio.Persona(datos['id'], datos)


def login_plataforma(client, user, foto_url=None, apps=None):
    """Lo que deja la plataforma en la sesión al entrar (si faltan los tokens, PlataformaMiddleware la cierra)."""
    session = client.session
    session[plataforma.SESION_TOKENS] = {'access': 'access-de-prueba', 'refresh': 'refresh-de-prueba'}
    session[plataforma.SESION_USUARIO] = {'id': str(user.id), 'email': user.email, 'full_name': user.get_full_name(),
                                          'is_active': True, 'foto_url': foto_url}
    session[plataforma.SESION_APPS] = apps if apps is not None else [{'app_slug': 'cep'}, {'app_slug': 'popin'}]
    session[plataforma.SESION_TS] = time.time()
    session.save()


class CEPTestCase(TestCase):
    """Plataforma simulada en cada prueba, contraseña de servicio configurada y caché limpia."""

    def setUp(self):
        cache.clear()
        DIRECTORIO.clear()
        Configuracion.objects.update_or_create(nombre='PLATFORM_SERVICE_PASSWORD', defaults={'valor': 'clave-svc'})
        self.plataforma = PlataformaFalsa()
        plataforma.transporte = httpx.MockTransport(self.plataforma)
        self.client.defaults['HTTP_HOST'] = HOST
        self.addCleanup(setattr, plataforma, 'transporte', None)

    def login(self, user, **kwargs):
        login_plataforma(self.client, user, **kwargs)

    def fetch_post(self, url, data=None, json_body=None):
        cabeceras = {'HTTP_X_REQUESTED_WITH': 'fetch'}
        if json_body is not None:
            return self.client.post(url, data=json.dumps(json_body), content_type='application/json', **cabeceras)
        return self.client.post(url, data or {}, **cabeceras)
