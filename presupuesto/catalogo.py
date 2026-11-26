import json
from django.core import serializers
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse, JsonResponse
from authentication import directorio
from authentication.plataforma import PlataformaError
from .models import CuentaContable, CuentaAnalitica
from core.serializer import BaseSerializer
from core.utils.login_required import login_required_json

@login_required_json
def ubicaciones_list(request):
    # Las sedes son los recintos de la plataforma: {id (UUID), codigo, nombre}, ordenadas por nombre.
    try:
        return JsonResponse(directorio.sedes(), safe=False)
    except PlataformaError as exc:
        return JsonResponse({'error': exc.mensaje}, status=503)

@login_required_json
def cuentas_analiticas_list(request):
    # Traemos las cuentas analíticas
    qs = CuentaAnalitica.objects.all().order_by('nombre')

    # El serializer procesará 'id', 'codigo' y 'nombre'
    serializer = BaseSerializer(qs)
    return JsonResponse(serializer.serialize(), safe=False)

@login_required_json
def cuentas_contables_list(request):
    # Traemos las cuentas analíticas
    qs = CuentaContable.objects.all().order_by('nombre')

    # El serializer procesará 'id', 'codigo' y 'nombre'
    serializer = BaseSerializer(qs)
    return JsonResponse(serializer.serialize(), safe=False)