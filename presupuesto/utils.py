from django.conf import settings
from authentication import directorio, plataforma
from authentication.roles import ROL_COMPRA_RCO, ROL_COMPRA_RSD, ROL_COMPRA_RSS
from emails.mailer import send_email
from presupuesto.models import Configuracion
from enum import Enum

class FrontendRequest(Enum):
    VIEW = "/request/{id}/"
    EDIT = "/request/{id}/edit"
    CONFIRM = "/request/{id}/confirm"

    def url(self, request, solicitud_id):
        """
        Construye la URL inyectando el prefijo de settings.
        Requiere el objeto 'request' para obtener el dominio automáticamente.
        """
        # 1. Obtenemos el path relativo del Enum (ej: /request/8/)
        path_del_enum = self.value.format(id=solicitud_id)

        # 2. Limpiamos el prefijo de settings para evitar dobles barras
        prefijo = settings.PREFIX_URL.rstrip('/')

        # 3. Combinamos: /presupuesto + /request/8/
        full_path = f"{prefijo}{path_del_enum}"
        print(full_path)
        # 4. Django le añade el protocolo y dominio: https://gtsst.uapa.edu.do/presupuesto/request/8/
        return request.build_absolute_uri(full_path)


def enviar_email_solicitud_creada(context):
    template='presupuesto/nueva_solicitud.html'
    usuarios_value = Configuracion.get_value('CORREOS_NUEVAS_SOLICITUDES')
    if usuarios_value:
        send_to_list = [email.strip() for email in usuarios_value.replace(';', ',').split(',') if email.strip()]
    else:
        send_to_list = []
    send_email(
                subject='Nueva Solicitud de Presupuesto',
                send_to_list=send_to_list,
                template=template,
                context=context
            )

def roles_compras(sede_codigo):
    """Roles de Compra que atienden una sede: Compra RSS siempre, más el de la sede si es RCO o RSD."""
    roles_a_notificar = [ROL_COMPRA_RSS]
    if sede_codigo == 'RCO':
        roles_a_notificar.append(ROL_COMPRA_RCO)
    elif sede_codigo == 'RSD':
        roles_a_notificar.append(ROL_COMPRA_RSD)
    return roles_a_notificar


def correos_compras(solicitud):
    """Correos de quienes tienen los roles de Compra de la sede de la solicitud (los dice la plataforma)."""
    correos = []
    for rol in roles_compras(solicitud.ubicacion.codigo):
        for usuario in directorio.buscar_usuarios(app=plataforma.app_slug(), rol=rol, limit=500):
            if usuario.get('email') and usuario['email'] not in correos:
                correos.append(usuario['email'])
    return correos


def enviar_email_a_compras(request, solicitud):
    email_template = 'presupuesto/emision_certificado_a_compra.html'
    sede = solicitud.ubicacion

    send_to_list = [solicitud.colaborador.email]
    # Quién tiene cada rol de Compra en CEP lo dice la plataforma (directorio).
    for email in correos_compras(solicitud):
        if email not in send_to_list:
            send_to_list.append(email)

    context={
        'solicitante': solicitud.colaborador.get_full_name(),
        'titulo': solicitud.titulo,
        'monto_a_ejecutar': solicitud.monto_a_ejecutar,
        'sede': sede.nombre,
        'url_solicitud': FrontendRequest.CONFIRM.url(request, solicitud.id)
    }
    send_email(
        subject='Presupuesto Aprobado',
        send_to_list=send_to_list,
        template=email_template,
        context=context,
    ) 