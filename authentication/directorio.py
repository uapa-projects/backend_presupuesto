"""
Directorio de la plataforma (services_auth_db) para las apps (se copia a cada app, junto a su plataforma.py).

Usuarios, estamentos, recintos (sedes), puestos, cargos y tipos de estamento ya NO viven en la base de la
app: la app guarda solo el UUID (UUIDField sin ForeignKey) y aqui lo resuelve, con cache de Django.

    from authentication import directorio
    directorio.usuario(uuid)                      # dict o None
    directorio.usuarios([uuid, ...])              # {uuid: dict}  (una sola llamada para una lista)
    directorio.buscar_usuarios(q='ana', app='popin', departamento_id=..., limit=50)
    directorio.estamentos() / estamento(uuid) / descendientes(uuid)
    directorio.recintos() / recinto(uuid), puestos() / puesto(uuid), cargos(), tipos_estamento()
    directorio.nombre_usuario(uuid)               # para listados: nombre o correo, '' si no hay

En plantillas: {% load directorio %} {{ obj.formulario_solicitante_id|nombre_usuario }}
(ver templatetags/directorio.py).

Usa la cuenta de servicio de la app (plataforma._token_servicio), que debe tener el servicio "directory".
Si la plataforma no responde, lanza plataforma.PlataformaError: la vista decide que mostrar.
"""
import logging
import uuid

import httpx
from django.core.cache import cache

from authentication import plataforma

log = logging.getLogger('plataforma.directorio')

CACHE_USUARIO = 10 * 60
CACHE_CATALOGO = 10 * 60
LOTE = 500


def _uuid(valor):
    if valor in (None, ''):
        return None
    return valor if isinstance(valor, uuid.UUID) else uuid.UUID(str(valor))


def _peticion(metodo, ruta, **kwargs):
    for intento in (1, 2):
        token = plataforma._token_servicio(renovar=intento == 2)
        try:
            with httpx.Client(timeout=plataforma.TIMEOUT, transport=plataforma.transporte) as cliente:
                response = cliente.request(metodo, plataforma.url_base() + ruta,
                                           headers={'Authorization': f'Bearer {token}'}, **kwargs)
        except httpx.HTTPError as exc:
            log.warning('Directorio: sin respuesta en %s (%s)', ruta, type(exc).__name__)
            raise plataforma.PlataformaError(0, 'network', plataforma.MSG_NO_DISPONIBLE)
        if response.status_code == 401 and intento == 1:
            continue  # token de servicio vencido: se renueva una vez
        if response.status_code != 200:
            log.warning('Directorio: %s respondio HTTP %s', ruta, response.status_code)
            raise plataforma.PlataformaError(response.status_code, 'directory', plataforma.MSG_NO_DISPONIBLE)
        return response.json()


# --- usuarios -------------------------------------------------------------------------------

def _llave_usuario(uid):
    return f'directorio:usuario:{uid}'


def usuarios(ids):
    """{UUID: usuario} de los ids dados (los que no existen no vienen). Usa la cache y pide el resto juntos."""
    pedidos = {u for u in (_uuid(i) for i in ids) if u}
    if not pedidos:
        return {}
    llaves = {_llave_usuario(u): u for u in pedidos}
    # En la cache, False = "no existe en la plataforma" (asi un id huerfano no se vuelve a pedir en cada listado).
    en_cache = {llaves[k]: v for k, v in cache.get_many(list(llaves)).items()}
    encontrados = {u: v for u, v in en_cache.items() if v}
    faltan = [u for u in pedidos if u not in en_cache]
    for i in range(0, len(faltan), LOTE):
        lote = faltan[i:i + LOTE]
        recibidos = {uuid.UUID(d['id']): d for d in
                     _peticion('POST', '/directory/usuarios/resolver', json={'ids': [str(u) for u in lote]})}
        for uid in lote:
            cache.set(_llave_usuario(uid), recibidos.get(uid, False), CACHE_USUARIO)
        encontrados.update(recibidos)
    return encontrados


def usuario(uid):
    uid = _uuid(uid)
    return usuarios([uid]).get(uid) if uid else None


def usuarios_por_email(emails):
    """{email_en_minusculas: usuario}."""
    emails = [e.strip().lower() for e in emails if e and e.strip()]
    if not emails:
        return {}
    datos = _peticion('POST', '/directory/usuarios/resolver', json={'emails': emails})
    for dato in datos:
        cache.set(_llave_usuario(dato['id']), dato, CACHE_USUARIO)
    return {d['email'].lower(): d for d in datos}


def buscar_usuarios(q=None, app=None, rol=None, departamento_id=None, recursivo=False, solo_colaboradores=False,
                    solo_activos=True, limit=50, offset=0):
    params = {'limit': limit, 'offset': offset, 'solo_activos': solo_activos, 'recursivo': recursivo,
              'solo_colaboradores': solo_colaboradores}
    for nombre, valor in (('q', q), ('app', app), ('rol', rol), ('departamento_id', departamento_id)):
        if valor:
            params[nombre] = str(valor)
    return _peticion('GET', '/directory/usuarios', params=params)


def nombre_usuario(uid):
    """Nombre (o correo) del usuario; '' si no hay id o no existe. Nunca falla: para listados."""
    try:
        dato = usuario(uid)
    except (plataforma.PlataformaError, ValueError):
        return ''
    return (dato.get('full_name') or dato.get('email') or '') if dato else ''


# --- catalogos ------------------------------------------------------------------------------

def _catalogo(ruta):
    llave = f'directorio:catalogo:{ruta}'
    datos = cache.get(llave)
    if datos is None:
        datos = _peticion('GET', ruta)
        cache.set(llave, datos, CACHE_CATALOGO)
    return datos


def _por_id(lista, uid):
    uid = _uuid(uid)
    return next((x for x in lista if uuid.UUID(x['id']) == uid), None) if uid else None


def estamentos():
    return _catalogo('/directory/estamentos')


def estamento(uid):
    return _por_id(estamentos(), uid)


def descendientes(uid, incluir=True):
    """UUIDs del estamento y de todos los que cuelgan de el."""
    raiz = _uuid(uid)
    hijos = {}
    for e in estamentos():
        if e.get('estamento_sub_id'):
            hijos.setdefault(uuid.UUID(e['estamento_sub_id']), []).append(uuid.UUID(e['id']))
    salida, pila = ({raiz} if incluir else set()), [raiz]
    while pila:
        for h in hijos.get(pila.pop(), []):
            if h not in salida:
                salida.add(h)
                pila.append(h)
    return salida


def recintos():
    return _catalogo('/directory/recintos')


def recinto(uid):
    return _por_id(recintos(), uid)


def puestos():
    return _catalogo('/directory/puestos')


def puesto(uid):
    return _por_id(puestos(), uid)


def cargos():
    return _catalogo('/directory/cargos')


def tipos_estamento():
    return _catalogo('/directory/tipos')


def olvidar_catalogos():
    """Tras crear o cambiar algo del organigrama desde la app (suite / tuespaciogth)."""
    for ruta in ('/directory/estamentos', '/directory/recintos', '/directory/puestos', '/directory/cargos',
                 '/directory/tipos'):
        cache.delete(f'directorio:catalogo:{ruta}')


# --- CEP: recintos como las antiguas sedes (presupuesto_sede) ---------------------------------------------

def sede(uid):
    """{id, codigo, nombre} del recinto (codigo = recinto_abreviatura: RSS, RSD, RCO), o None."""
    r = recinto(uid)
    return {'id': r['id'], 'codigo': r.get('recinto_abreviatura') or '', 'nombre': r.get('recinto_nombre') or ''} if r else None


def sedes():
    return sorted((sede(r['id']) for r in recintos()), key=lambda s: s['nombre'])


def recinto_por_codigo(codigo):
    codigo = (codigo or '').strip().upper()
    return next((r for r in recintos() if (r.get('recinto_abreviatura') or '').upper() == codigo), None)


# --- CEP: personas como los antiguos User (para colaborador / supervisor) ----------------------------------

class Persona:
    """Lo que CEP leía de un User: id, email, username, first_name, last_name, get_full_name()."""

    def __init__(self, uid, datos=None):
        datos = datos or {}
        self.id = self.pk = _uuid(uid)
        self.email = (datos.get('email') or '').strip().lower()
        self.username = self.email
        self.full_name = (datos.get('full_name') or '').strip()
        partes = self.full_name.split(' ', 1)
        self.first_name = partes[0] if partes else ''
        self.last_name = partes[1] if len(partes) > 1 else ''
        self.is_active = bool(datos.get('is_active', False))
        self.existe = bool(datos)

    def get_full_name(self):
        return self.full_name or self.email

    def __str__(self):
        return self.get_full_name()

    def __eq__(self, otro):
        return getattr(otro, 'id', None) == self.id and self.id is not None

    def __hash__(self):
        return hash(self.id)

    def como_dict(self):
        return {'id': str(self.id) if self.id else None, 'username': self.username, 'email': self.email,
                'first_name': self.first_name, 'last_name': self.last_name}


def persona(uid):
    """Persona del id (vacía si no existe en la plataforma o no responde: nunca falla), o None sin id."""
    uid = _uuid(uid)
    if uid is None:
        return None
    try:
        return Persona(uid, usuario(uid))
    except plataforma.PlataformaError:
        return Persona(uid)


def personas(ids):
    """{UUID: Persona} de todos los ids (una sola llamada); los que no existen quedan vacíos."""
    pedidos = {u for u in (_uuid(i) for i in ids) if u}
    try:
        datos = usuarios(pedidos)
    except plataforma.PlataformaError:
        datos = {}
    return {u: Persona(u, datos.get(u)) for u in pedidos}


class SedeCEP:
    """Lo que CEP leía de una Sede: id, codigo, nombre."""

    def __init__(self, uid, datos=None):
        datos = datos or {}
        self.id = self.pk = _uuid(uid)
        self.codigo = datos.get('codigo') or ''
        self.nombre = datos.get('nombre') or ''

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

    def como_dict(self):
        return {'id': str(self.id) if self.id else None, 'codigo': self.codigo, 'nombre': self.nombre}


def sede_cep(uid):
    """SedeCEP del recinto (vacía si no existe o la plataforma no responde), o None sin id."""
    uid = _uuid(uid)
    if uid is None:
        return None
    try:
        return SedeCEP(uid, sede(uid))
    except plataforma.PlataformaError:
        return SedeCEP(uid)
