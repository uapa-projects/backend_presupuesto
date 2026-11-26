"""CEP sin usuarios ni sedes locales: colaborador_id / supervisor_id guardan el UUID del usuario en services_auth_db
y ubicacion_id el del recinto (sin ForeignKey). Se borra presupuesto_sede.

En una base con datos, ANTES de esta migración se corre (con la app detenida) el script de la plataforma:
    python -m scripts.migrar_apps_a_plataforma --app cep --aplicar --respaldo DIR      (en ServiceServer/auth_service)
que convierte los ids viejos (auth_user / presupuesto_sede) en los UUID de la plataforma. Si las columnas ya son
uuid, esta migración solo actualiza el estado de Django; si siguen siendo enteras y tienen datos, se detiene.
"""
from django.db import migrations, models

COLUMNAS = [
    ('presupuesto_solicitudpresupuesto', 'colaborador_id'),
    ('presupuesto_solicitudpresupuesto', 'ubicacion_id'),
    ('presupuesto_revisionsolicitud', 'supervisor_id'),
]


def a_uuid(apps, schema_editor):
    with schema_editor.connection.cursor() as c:
        for tabla, columna in COLUMNAS:
            c.execute("SELECT data_type FROM information_schema.columns "
                      "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
                      [tabla, columna])
            fila = c.fetchone()
            if fila is None or fila[0] == 'uuid':
                continue  # ya la convirtió scripts.migrar_apps_a_plataforma
            c.execute(f'SELECT count(*) FROM "{tabla}" WHERE "{columna}" IS NOT NULL')
            if c.fetchone()[0]:
                raise RuntimeError(
                    f'{tabla}.{columna} todavía tiene ids locales: corre primero, en ServiceServer/auth_service, '
                    'python -m scripts.migrar_apps_a_plataforma --app cep (ver la cabecera de esta migración).')
            c.execute("SELECT con.conname FROM pg_constraint con "
                      "JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey) "
                      "WHERE con.contype = 'f' AND con.conrelid = %s::regclass AND a.attname = %s",
                      [tabla, columna])
            for (nombre,) in c.fetchall():
                c.execute(f'ALTER TABLE "{tabla}" DROP CONSTRAINT "{nombre}"')
            c.execute(f'ALTER TABLE "{tabla}" ALTER COLUMN "{columna}" TYPE uuid USING NULL')
        c.execute('DROP TABLE IF EXISTS presupuesto_sede')


class Migration(migrations.Migration):

    dependencies = [
        ('presupuesto', '0027_parametros_plataforma'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(a_uuid)],
            state_operations=[
                migrations.RemoveField('solicitudpresupuesto', 'colaborador'),
                migrations.AddField('solicitudpresupuesto', 'colaborador_id', models.UUIDField(db_index=True)),
                migrations.RemoveField('solicitudpresupuesto', 'ubicacion'),
                migrations.AddField('solicitudpresupuesto', 'ubicacion_id', models.UUIDField(db_index=True)),
                migrations.RemoveField('revisionsolicitud', 'supervisor'),
                migrations.AddField('revisionsolicitud', 'supervisor_id', models.UUIDField(db_index=True)),
                migrations.DeleteModel('Sede'),
            ],
        ),
    ]
