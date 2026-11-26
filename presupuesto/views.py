from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.cache import never_cache

# Lo genera `npm run build` en el frontend (vite.config.ts → build.outDir).
INDEX_HTML = Path(settings.REACT_APP_DIR) / 'index.html'


@never_cache
def render_app(request):
    """Entrega la SPA tal cual sale del build: Django no dibuja nada. React decide qué mostrar (portada, ventana
    de acceso, pantallas) con GET /api/sesion/. Sin caché, para que un build nuevo se vea al recargar."""
    try:
        html = INDEX_HTML.read_text(encoding='utf-8')
    except FileNotFoundError:
        return HttpResponse('Falta el build del frontend: ejecuta `npm run build`.', status=503,
                            content_type='text/plain; charset=utf-8')
    return HttpResponse(html, content_type='text/html; charset=utf-8')
