from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='SesionPlataforma',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(db_index=True, max_length=254, verbose_name='Email')),
                ('session_key', models.CharField(max_length=40, verbose_name='Llave de sesión')),
                ('creada', models.DateTimeField(auto_now_add=True, verbose_name='Creada')),
            ],
            options={
                'verbose_name': 'Sesión de la plataforma',
                'verbose_name_plural': 'Sesiones de la plataforma',
                'db_table': 'SesionPlataforma',
            },
        ),
    ]
