from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0002_conversationrecord_lead_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='messagerecord',
            name='failure_code',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddIndex(
            model_name='conversationrecord',
            index=models.Index(fields=['lead_id'], name='conv_lead_idx'),
        ),
    ]
