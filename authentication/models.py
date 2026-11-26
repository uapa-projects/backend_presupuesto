from django.db import models


class SesionPlataforma(models.Model):
    """Sesiones de Django abiertas por cada usuario, para cerrarlas todas cuando la plataforma avisa
    de un logout hecho en otra app (POST /accounts/logout/ con logout_token)."""
    email = models.EmailField(db_index=True, verbose_name='Email')
    session_key = models.CharField(max_length=40, verbose_name='Llave de sesión')
    creada = models.DateTimeField(auto_now_add=True, verbose_name='Creada')

    def __str__(self):
        return f'{self.email} ({self.creada:%d/%m/%Y %H:%M})'

    class Meta:
        db_table = 'SesionPlataforma'
        verbose_name = 'Sesión de la plataforma'
        verbose_name_plural = 'Sesiones de la plataforma'
