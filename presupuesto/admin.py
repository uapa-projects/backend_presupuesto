from django.contrib import admin
from django.contrib.auth.models import Group, User
from authentication.roles import es_administrador, es_supervisor
from .administracion import PATRON_SECRETO
from .models import DriveFolder, SolicitudPresupuesto, CuentaAnalitica, GoogleConfig, Configuracion, AdjuntoSolicitud, CuentaContable

# La administración del día a día está en React (/administracion). Este admin queda como respaldo técnico solo
# para el rol Administrador de la plataforma. CEP no tiene usuarios ni grupos locales: usuarios y roles se
# administran en la plataforma (auth_service).
admin.site.has_permission = lambda request: bool(request.user.is_active and es_administrador(request.user))
admin.site.site_header = 'CEP · Administración técnica'
# Sin «Acciones recientes»: django_admin_log apunta a auth_user, que queda vacía.
admin.site.index_template = 'admin/index_cep.html'
admin.site.unregister(User)
admin.site.unregister(Group)

# El historial del admin (django_admin_log) tiene FK a auth_user: no se escribe.
admin.ModelAdmin.log_addition = lambda self, request, obj, message: None
admin.ModelAdmin.log_change = lambda self, request, obj, message: None
admin.ModelAdmin.log_deletions = lambda self, request, queryset: None

@admin.register(SolicitudPresupuesto)
class SolicitudAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'colaborador', 'estado', 'fecha_solicitud', 'fecha_actividad', 'monto_a_ejecutar', 'updated_at')
    list_filter = ('estado', 'tipo_solicitud')
    search_fields = ('titulo',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Supervisor o Administrador en la plataforma: ve todo
        if es_supervisor(request.user):
            return qs
        # Si no, solo ve lo suyo
        return qs.filter(colaborador_id=request.user.id)

admin.site.register(CuentaAnalitica)
admin.site.register(GoogleConfig)
@admin.register(Configuracion)
class ConfiguracionAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'valor', 'descripcion')

    def get_queryset(self, request):
        # Las contraseñas no salen aquí (el formulario las pondría en el HTML): se cargan con manage.py clave_servicio.
        return super().get_queryset(request).exclude(nombre__iregex=PATRON_SECRETO)


admin.site.register(AdjuntoSolicitud)
admin.site.register(CuentaContable)
admin.site.register(DriveFolder)