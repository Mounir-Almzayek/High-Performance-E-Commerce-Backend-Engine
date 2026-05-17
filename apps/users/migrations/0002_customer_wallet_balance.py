from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="wallet_balance",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
