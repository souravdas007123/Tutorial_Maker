from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recorder", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="tutorialjob",
            name="demo_mode",
            field=models.BooleanField(default=False),
        ),
    ]
