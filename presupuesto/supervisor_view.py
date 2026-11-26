from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView
from authentication.roles import es_supervisor
from .models import SolicitudPresupuesto

class PanelSupervisorView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = SolicitudPresupuesto
    template_name = 'presupuesto/panel_supervisor.html'
    context_object_name = 'solicitudes'

    def test_func(self):
        # Solo Supervisor o Administrador en la plataforma
        return es_supervisor(self.request.user)

    def get_queryset(self):
        # El supervisor ve TODO, pero ponemos las PENDIENTES primero
        return SolicitudPresupuesto.objects.all().order_by('estado', '-fecha_solicitud')