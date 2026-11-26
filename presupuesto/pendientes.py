"""Pendientes de CEP en UAPA Suite (panel «Pendientes en la suite»), en notify_service.

Cada solicitud tiene tres pendientes posibles, según su estado:

1. «Revisar solicitud …» para TODOS los usuarios con rol Supervisor en CEP (los dice la plataforma:
   directorio.buscar_usuarios), clave `cep:solicitud:<id>`. Abierto mientras la solicitud está en PENDIENTE: se crea
   al crearla, se actualiza al editarla y se cierra para todos cuando un supervisor la aprueba, la rechaza o la manda
   a revisión. Vuelve cuando el colaborador resuelve las revisiones.
2. «Corregir solicitud …» para el colaborador que la pidió, clave `cep:solicitud:<id>:revision`. Abierto mientras la
   solicitud está en POR_REVISION, con las observaciones que le faltan por resolver; se cierra cuando las resuelve
   todas (la solicitud vuelve a PENDIENTE) o si un supervisor la aprueba o la rechaza.
3. «Confirmar solicitud …» para Compras de su sede (Compra RSS siempre, más Compra RSD o RCO: utils.roles_compras,
   los mismos del correo «Presupuesto Aprobado»), clave `cep:solicitud:<id>:compras`. Abierto desde que la
   solicitud está APROBADA y tiene certificado hasta que Compras la confirma (`confirmado`). Su enlace es la
   pantalla de confirmar, que confirma al abrirla.

Si se elimina la solicitud, desaparecen todos.

`sincronizar(solicitud)` deja los tres como corresponde al estado actual, así que se puede llamar las veces que haga
falta. Se envía al confirmarse la transacción y NUNCA rompe la petición: si la plataforma no responde, queda en el log
y `python manage.py sincronizar_pendientes` los pone al día (sirve también para la carga inicial).
"""
import logging
import os

import httpx
from django.db import transaction

from authentication import directorio, plataforma
from authentication.roles import ROL_SUPERVISOR

log = logging.getLogger(__name__)

TIMEOUT = 5  # segundos: la petición del usuario espera esto como mucho por cada llamada
ICONO_REVISAR = 'cash-coin'
ICONO_CORREGIR = 'pencil-square'
ICONO_CONFIRMAR = 'bag-check'
SUFIJO_REVISION = ':revision'
SUFIJO_COMPRAS = ':compras'


def url_notify():
    """PLATFORM_NOTIFY_URL o, si está vacío, el /notify hermano de PLATFORM_AUTH_URL."""
    url = plataforma.parametro('PLATFORM_NOTIFY_URL').rstrip('/')
    if url:
        return url
    auth = plataforma.url_base()
    return auth[: -len('/auth')] + '/notify' if auth.endswith('/auth') else ''


def origen(solicitud_id):
    """cep:solicitud:<id>. En pruebas locales (correr-local.ps1 pone DJANGO_APPS_LOCALES) la base es una copia pero
    la plataforma es la de producción: cep:local:solicitud:<id>, para no pisar el pendiente de la solicitud real
    que tenga el mismo id."""
    local = 'local:' if os.environ.get('DJANGO_APPS_LOCALES') else ''
    return f'{plataforma.app_slug()}:{local}solicitud:{solicitud_id}'


def origen_revision(solicitud_id):
    return origen(solicitud_id) + SUFIJO_REVISION


def origen_compras(solicitud_id):
    return origen(solicitud_id) + SUFIJO_COMPRAS


def supervisores():
    """Correos de los usuarios activos con rol Supervisor en CEP."""
    usuarios = directorio.buscar_usuarios(app=plataforma.app_slug(), rol=ROL_SUPERVISOR, limit=500)
    return sorted({(u.get('email') or '').strip().lower() for u in usuarios if u.get('email')})


def _peticion(metodo, ruta, **kwargs):
    base = url_notify()
    if not base:
        raise plataforma.PlataformaError(0, 'service_not_configured', 'Falta PLATFORM_NOTIFY_URL / PLATFORM_AUTH_URL')
    for intento in (1, 2):
        token = plataforma._token_servicio(renovar=intento == 2)
        with httpx.Client(timeout=TIMEOUT, transport=plataforma.transporte) as cliente:
            r = cliente.request(metodo, base + ruta, headers={'Authorization': f'Bearer {token}'}, **kwargs)
        if r.status_code == 401 and intento == 1:
            continue  # token de servicio vencido: se renueva una vez
        r.raise_for_status()
        return r


def _estado(solicitud):
    return (solicitud.estado or '').upper()


def _completar(clave):
    r = _peticion('POST', f'/pendientes/{clave}/completar')
    return f"cerrado ({r.json().get('completados', 0)})"


def _abrir(clave, destinatarios, datos):
    r = _peticion('PUT', f'/pendientes/{clave}', json=dict(datos, destinatarios=destinatarios))
    return f"abierto para {r.json().get('destinatarios', len(destinatarios))}"


def _revisar(solicitud, url):
    """Pendiente de los Supervisores: abierto en PENDIENTE."""
    clave = origen(solicitud.pk)
    if _estado(solicitud) != 'PENDIENTE':
        return _completar(clave)
    destinatarios = supervisores()
    if not destinatarios:
        log.warning('Pendientes: no hay usuarios con rol %s en CEP; la solicitud %s queda sin pendiente.',
                    ROL_SUPERVISOR, solicitud.pk)
        _completar(clave)
        return 'sin supervisores'
    sede = getattr(solicitud.ubicacion, 'nombre', '') or ''
    texto = ' · '.join(p for p in (solicitud.get_nombre_colaborador, sede, f'RD$ {solicitud.monto_a_ejecutar:,.2f}') if p)
    return _abrir(clave, destinatarios, {'titulo': f'Revisar solicitud «{solicitud.titulo}»'[:200],
                                         'texto': texto[:2000], 'url': url, 'icono': ICONO_REVISAR})


def _corregir(solicitud, url):
    """Pendiente del colaborador: abierto en POR_REVISION, con las observaciones que le faltan."""
    clave = origen_revision(solicitud.pk)
    if _estado(solicitud) != 'POR_REVISION':
        return _completar(clave)
    persona = solicitud.colaborador
    email = (getattr(persona, 'email', '') or '').strip().lower()
    if not email:
        log.warning('Pendientes: el colaborador de la solicitud %s no tiene correo en la plataforma.', solicitud.pk)
        _completar(clave)
        return 'sin correo del colaborador'
    faltan = list(solicitud.revisiones.exclude(estado='RESUELTA').order_by('-id').values_list('contenido', flat=True))
    if faltan:
        resumen = f'{len(faltan)} observación(es) por resolver: {faltan[0]}' if len(faltan) > 1 else faltan[0]
    else:
        resumen = 'El supervisor pidió correcciones.'
    return _abrir(clave, [email], {'titulo': f'Corregir solicitud «{solicitud.titulo}»'[:200],
                                   'texto': resumen[:2000], 'url': url, 'icono': ICONO_CORREGIR})


def _confirmar(solicitud, url_confirmar):
    """Pendiente de Compras de la sede: abierto si está APROBADA, tiene certificado y aún no la confirmaron."""
    from .utils import correos_compras  # import tardío: utils importa los modelos

    clave = origen_compras(solicitud.pk)
    if (_estado(solicitud) != 'APROBADA' or solicitud.confirmado
            or not solicitud.adjuntos.filter(es_certificado=True).exists()):
        return _completar(clave)
    destinatarios = sorted({e.strip().lower() for e in correos_compras(solicitud) if e})
    if not destinatarios:
        log.warning('Pendientes: no hay usuarios de Compras para la sede de la solicitud %s.', solicitud.pk)
        _completar(clave)
        return 'sin Compras'
    sede = getattr(solicitud.ubicacion, 'nombre', '') or ''
    texto = ' · '.join(p for p in (solicitud.get_nombre_colaborador, sede, f'RD$ {solicitud.monto_a_ejecutar:,.2f}') if p)
    return _abrir(clave, destinatarios, {'titulo': f'Confirmar solicitud «{solicitud.titulo}»'[:200],
                                         'texto': texto[:2000], 'url': url_confirmar, 'icono': ICONO_CONFIRMAR})


def enviar(solicitud, url, url_confirmar):
    """Deja los tres pendientes de la solicitud como corresponde a su estado. Lanza excepción si la plataforma falla."""
    return (f'supervisores: {_revisar(solicitud, url)}; colaborador: {_corregir(solicitud, url)}; '
            f'compras: {_confirmar(solicitud, url_confirmar)}')


def _seguro(funcion, descripcion):
    try:
        funcion()
    except Exception as exc:  # la plataforma caída no debe romper la solicitud
        log.warning('Pendientes: no se pudo %s (%s). Se pondrá al día con manage.py sincronizar_pendientes.',
                    descripcion, type(exc).__name__)


def sincronizar(request, solicitud):
    """Tras guardar la solicitud: crea, actualiza o cierra sus pendientes cuando se confirme la transacción."""
    from .utils import FrontendRequest  # import tardío: utils importa los modelos

    url = FrontendRequest.VIEW.url(request, solicitud.pk)
    url_confirmar = FrontendRequest.CONFIRM.url(request, solicitud.pk)
    transaction.on_commit(lambda: _seguro(lambda: enviar(solicitud, url, url_confirmar),
                                          f'sincronizar la solicitud {solicitud.pk}'))


def borrar(solicitud_id):
    """La solicitud se eliminó: sus pendientes desaparecen."""
    def _borrar():
        for clave in (origen(solicitud_id), origen_revision(solicitud_id), origen_compras(solicitud_id)):
            _peticion('DELETE', f'/pendientes/{clave}')
    transaction.on_commit(lambda: _seguro(_borrar, f'borrar los pendientes de la solicitud {solicitud_id}'))
