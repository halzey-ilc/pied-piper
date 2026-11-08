import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('communications', '0004_merge_templates_whatsapp'),
    ]

    operations = [
        migrations.CreateModel(
            name='MessageAttachmentRecord',
            fields=[
                ('message', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='attachment', serialize=False, to='communications.messagerecord')),
                ('kind', models.CharField(max_length=10)),
                ('content_type', models.CharField(max_length=100)),
                ('file_name', models.CharField(max_length=255)),
                ('size', models.PositiveIntegerField()),
                ('content', models.BinaryField()),
            ],
            options={
                'db_table': 'communications_message_attachment',
            },
        ),
    ]
