"""Datos del usuario de la sesión que necesita la SPA de CEP. El rol sale de los roles de la plataforma
(authentication/roles.py)."""
from . import plataforma
from .roles import ROL_COMPRA_RCO, ROL_COMPRA_RSD, ROL_COMPRA_RSS, es_administrador, es_supervisor, tiene_rol

# Orden de prioridad de los roles de compra (el mismo que usaban los grupos de Django).
ROLES = (
    (ROL_COMPRA_RSS, 'COMPRA RSS'),
    (ROL_COMPRA_RSD, 'COMPRA RSD'),
    (ROL_COMPRA_RCO, 'COMPRA RCO'),
)


def rol_de(user):
    if es_supervisor(user):
        return 'SUPERVISOR'
    for nombre, rol in ROLES:
        if tiene_rol(user, nombre):
            return rol
    return 'COLABORADOR'


def usuario_plataforma(request):
    sesion = getattr(request, 'session', None)
    return (sesion.get(plataforma.SESION_USUARIO) if sesion is not None else None) or {}


def datos_cep(request):
    """{id, name, email, role, administrador, picture} del usuario con sesión, o None sin sesión."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    if not hasattr(request, '_datos_cep'):
        usuario = usuario_plataforma(request)
        request._datos_cep = {
            'id': user.pk,
            'name': usuario.get('full_name') or user.get_full_name() or user.get_username(),
            'email': user.email,
            'role': rol_de(user),
            # Administrador de CEP en la plataforma: parámetros y admin técnico de Django.
            'administrador': es_administrador(user),
            'picture': usuario.get('foto_url') or '',
        }
    return request._datos_cep
