from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='conversationrecord',
            name='lead_id',
            field=models.UUIDField(blank=True, null=True),
        ),
    ]
