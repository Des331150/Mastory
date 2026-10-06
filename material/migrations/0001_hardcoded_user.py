from django.apps.registry import Apps
from django.conf import settings
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db import migrations


def create_hardcoded_user(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    User = apps.get_model("auth", "User")
    username = "student"
    if not User.objects.filter(pk=settings.HARDCODED_USER_ID).exists():
        User.objects.create(
            pk=settings.HARDCODED_USER_ID,
            username=username,
            first_name="Student",
            is_staff=False,
            is_superuser=False,
            is_active=True,
            date_joined="2024-01-01T00:00:00Z",
        )


def remove_hardcoded_user(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    User = apps.get_model("auth", "User")
    User.objects.filter(pk=settings.HARDCODED_USER_ID).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_hardcoded_user, remove_hardcoded_user),
    ]