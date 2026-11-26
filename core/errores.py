"""Errores en JSON: Django solo es la API; las páginas de error (y la de «no encontrada») las dibuja React."""
from django.http import JsonResponse


def error_400(request, exception=None):
    return JsonResponse({'error': 'Solicitud no válida.'}, status=400)


def error_403(request, exception=None):
    return JsonResponse({'error': 'No tienes permiso para realizar esta acción.'}, status=403)


def error_404(request, exception=None):
    return JsonResponse({'error': 'No encontrado.'}, status=404)


def error_500(request):
    return JsonResponse({'error': 'Algo salió mal. Inténtalo de nuevo más tarde.'}, status=500)
