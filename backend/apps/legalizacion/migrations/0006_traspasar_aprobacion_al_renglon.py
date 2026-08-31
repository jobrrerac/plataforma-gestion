"""Traslada al renglón la aprobación que antes vivía en el día.

Sin esto, los días ya aprobados volverían a la cola: sus renglones nacerían
PENDIENTE y `recalcular_estado` los devolvería a REGISTRADO. Quien ya firmó
tendría que firmar otra vez, y el informe de horas legalizadas se vaciaría de
golpe.

La firma del día se copia tal cual a cada uno de sus renglones. Es una
aproximación —en su momento se aprobó el día entero, no actividad por
actividad— pero es exactamente lo que se decidió entonces, y perderla sería
peor que trasladarla.
"""

from django.db import migrations


def aprobacion_del_dia_a_los_renglones(apps, schema_editor):
    DiaLegalizado = apps.get_model("legalizacion", "DiaLegalizado")
    RegistroHoras = apps.get_model("legalizacion", "RegistroHoras")

    for dia in DiaLegalizado.objects.filter(estado="APROBADO"):
        RegistroHoras.objects.filter(dia=dia).update(
            estado="APROBADO",
            aprobado_por_id=dia.aprobado_por_id,
            aprobado_en=dia.aprobado_en,
        )

    # Los días REGISTRADO y ABIERTO se quedan con renglones PENDIENTE, que es
    # el valor por defecto y ya significa lo correcto.


def volver_al_dia(apps, schema_editor):
    """Marcha atrás: el estado vuelve a leerse del día, así que basta con
    dejar los renglones como estaban antes de existir el campo."""
    RegistroHoras = apps.get_model("legalizacion", "RegistroHoras")
    RegistroHoras.objects.update(estado="PENDIENTE", aprobado_por=None, aprobado_en=None)


class Migration(migrations.Migration):
    dependencies = [
        ("legalizacion", "0005_registrohoras_aprobado_en_registrohoras_aprobado_por_and_more"),
    ]

    operations = [
        migrations.RunPython(aprobacion_del_dia_a_los_renglones, volver_al_dia),
    ]
