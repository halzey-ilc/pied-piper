# Merged duplicates keep a marker of the customer they were merged into (spec 2.10).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0002_customer_archive'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerrecord',
            name='merged_into_id',
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
