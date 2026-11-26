"""
URL configuration for core project.

Django es solo el backend (API, acceso y admin). Todo lo demás es la SPA (React): `render_app`, al final,
entrega su index.html y React dibuja la pantalla que corresponda.
"""
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.generic import RedirectView

from authentication import sesiones_locales
from authentication.views import Logout, me
from core.sesion import sesion
from presupuesto.views import render_app

handler400 = 'core.errores.error_400'
handler403 = 'core.errores.error_403'
handler404 = 'core.errores.error_404'
handler500 = 'core.errores.error_500'

urlpatterns = [
    # El admin entra y sale por la plataforma, como el resto de CEP.
    path('admin/login/', RedirectView.as_view(pattern_name='login', query_string=True)),
    path('admin/logout/', Logout.as_view(), name='admin_logout'),
    path('admin/', admin.site.urls),
    path('accounts/', include('authentication.urls')),
    path('api/sesion/', sesion, name='sesion'),
    path('api/me/', me, name='me'),
    path("api/", include('presupuesto.urls')),
    # Logout en cadena entre las apps de este equipo (solo con correr-local.ps1; en el servidor responde 404).
    path('sesiones-locales/cerrar', sesiones_locales.cerrar, name='sesiones_locales_cerrar'),
    path('', render_app, name='inicio'),
    # Todo lo que no es del backend es una pantalla de React (incluida la de «no encontrada»).
    re_path(r'^(?!api/|accounts/|admin/|django_static/).*$', render_app, name='render_app'),
]
