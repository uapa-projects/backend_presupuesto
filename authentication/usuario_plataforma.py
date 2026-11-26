"""
Usuario de la plataforma SIN tabla local de usuarios.

CEP ya no guarda usuarios (auth_user queda vacía): quien entra es el usuario de services_auth_db y `request.user`
es un `UsuarioPlataforma` armado en memoria con lo que devolvió POST /auth/authenticate (acceso.guardar_respuesta
lo deja en la sesión). Su `id` / `pk` es el UUID de la plataforma: es lo que guardan `colaborador_id` y
`supervisor_id` (UUIDField, sin ForeignKey). Nombre y correo de otros usuarios salen de authentication/directorio.py.

- `PlataformaAuthMiddleware` reemplaza a AuthenticationMiddleware (es subclase: el admin de Django lo exige).
- `iniciar_sesion(request, datos)` reemplaza a django.contrib.auth.login(); `cerrar_sesion(request)` a logout().
- Roles: los de CEP en la plataforma (authentication/roles.py). is_superuser = «Administrador»;
  is_staff = «Administrador» o «Supervisor» (para el admin técnico de Django).
"""
import uuid

from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth.models import AnonymousUser
from django.utils.functional import SimpleLazyObject

from . import plataforma
from .roles import ROL_ADMINISTRADOR, ROL_SUPERVISOR, roles_cep


class UsuarioPlataforma:
    """Lo que las vistas, plantillas y el admin usaban de request.user, sin base de datos."""

    is_authenticated = True
    is_anonymous = False

    def __init__(self, datos, roles=frozenset()):
        self.id = self.pk = uuid.UUID(str(datos['id']))
        self.email = (datos.get('email') or '').strip().lower()
        self.username = self.email
        self.full_name = (datos.get('full_name') or '').strip()
        partes = self.full_name.split(' ', 1)
        self.first_name = partes[0] if partes else ''
        self.last_name = partes[1] if len(partes) > 1 else ''
        self.is_active = bool(datos.get('is_active', True))
        self.foto_url = datos.get('foto_url')
        self.es_colaborador = bool(datos.get('es_colaborador'))
        self.roles_cep = frozenset(roles)  # authentication/roles.py los lee de aquí
        self.is_superuser = ROL_ADMINISTRADOR in self.roles_cep
        self.is_staff = self.is_superuser or ROL_SUPERVISOR in self.roles_cep

    def __str__(self):
        return self.get_full_name()

    def __eq__(self, otro):
        return isinstance(otro, UsuarioPlataforma) and otro.id == self.id

    def __hash__(self):
        return hash(self.id)

    def get_full_name(self):
        return self.full_name or self.email

    def get_short_name(self):
        return self.first_name or self.email

    def get_username(self):
        return self.username

    # Permisos de Django (admin técnico): el Administrador puede todo; nadie más tiene permisos por modelo.
    def has_perm(self, perm, obj=None):
        return self.is_active and self.is_superuser

    def has_perms(self, perm_list, obj=None):
        return self.is_active and self.is_superuser

    def has_module_perms(self, app_label):
        return self.is_active and self.is_superuser

    def get_all_permissions(self, obj=None):
        return set()


def usuario_de_la_sesion(request):
    session = getattr(request, 'session', None)
    datos = session.get(plataforma.SESION_USUARIO) if session is not None else None
    if not isinstance(datos, dict) or not datos.get('id'):
        return AnonymousUser()
    try:
        usuario = UsuarioPlataforma(datos, roles_cep(session))
    except (KeyError, ValueError):
        return AnonymousUser()
    return usuario if usuario.is_active else AnonymousUser()


def iniciar_sesion(request, datos):
    """Reemplaza a login(): si la sesión era de otra persona se vacía; si recién entra, se cambia su llave
    (evita la fijación de sesión). Lo que guarda la sesión lo escribe luego acceso.guardar_respuesta."""
    actual = (request.session.get(plataforma.SESION_USUARIO) or {}).get('id')
    nuevo = str((datos.get('user') or {}).get('id'))
    if actual and str(actual) != nuevo:
        request.session.flush()
    elif str(actual) != nuevo:
        request.session.cycle_key()


def cerrar_sesion(request):
    """Reemplaza a logout()."""
    request.session.flush()
    request.user = AnonymousUser()


class PlataformaAuthMiddleware(AuthenticationMiddleware):
    """request.user desde la sesión (UsuarioPlataforma o AnonymousUser), sin tocar la base."""

    def process_request(self, request):
        request.user = SimpleLazyObject(lambda: usuario_de_la_sesion(request))

        async def auser():
            return usuario_de_la_sesion(request)

        request.auser = auser
