"""
Roles de CEP: salen de la plataforma (auth_service, tabla user_app_roles de la app cep). En CEP no se administran.

- «Supervisor»    (antes is_staff y el grupo Supervisor): ve y revisa todas las solicitudes. No administra.
- «Administrador» (antes is_superuser): todo lo del Supervisor, la pantalla Administración de React (catálogos y
                  parámetros) y el admin técnico de Django.
- «Compra RSS», «Compra RSD», «Compra RCO» y «Colaborador»: los antiguos grupos de Django.

La plataforma manda los roles al iniciar sesión y en cada revalidación (cada 5 minutos, PlataformaMiddleware) y
CEP los guarda en la sesión; los permisos salen siempre de ahí. request.user es un UsuarioPlataforma
(authentication/usuario_plataforma.py) que ya trae esos roles en `roles_cep` y, a partir de ellos, is_staff e
is_superuser (para el admin técnico de Django). CEP no tiene usuarios ni grupos locales: quién tiene un rol (p. ej.
para el correo a Compras) se pregunta a la plataforma (directorio.buscar_usuarios(app='cep', rol=...)).
"""
from . import plataforma

ROL_SUPERVISOR = 'Supervisor'
ROL_ADMINISTRADOR = 'Administrador'
ROL_COLABORADOR = 'Colaborador'
ROL_COMPRA_RSS = 'Compra RSS'
ROL_COMPRA_RSD = 'Compra RSD'
ROL_COMPRA_RCO = 'Compra RCO'
ROLES_CEP = (ROL_ADMINISTRADOR, ROL_SUPERVISOR, ROL_COMPRA_RSS, ROL_COMPRA_RSD, ROL_COMPRA_RCO, ROL_COLABORADOR)


def roles_en(apps):
    """Roles de la app de CEP dentro de la lista `apps` que devuelve la plataforma."""
    slug = plataforma.app_slug()
    for app in apps or []:
        if isinstance(app, dict) and app.get('app_slug') == slug:
            return frozenset(str(r) for r in (app.get('roles') or []))
    return frozenset()


def roles_cep(session):
    """Roles del usuario en CEP según lo último que respondió la plataforma (guardado en la sesión)."""
    return roles_en(session.get(plataforma.SESION_APPS)) if session is not None else frozenset()


def _roles(user):
    return getattr(user, 'roles_cep', None) or frozenset()


def tiene_rol(user, *nombres):
    return bool(user and user.is_authenticated) and bool(set(nombres) & _roles(user))


def es_administrador(user):
    return tiene_rol(user, ROL_ADMINISTRADOR)


def es_supervisor(user):
    """Supervisor o Administrador."""
    return tiene_rol(user, ROL_SUPERVISOR, ROL_ADMINISTRADOR)
