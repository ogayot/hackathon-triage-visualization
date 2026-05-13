from django.db import migrations

PRESETS = {
    "Subiquity": {
        "sources": [
            ("launchpad", "subiquity"),
            ("launchpad", "ubuntu/+source/subiquity"),
            ("launchpad", "probert"),
            ("launchpad", "curtin"),
            ("github", "canonical/subiquity"),
            ("github", "canonical/probert"),
            ("github", "canonical/curtin"),
        ]
    },
    "cloud-init": {
        "sources": [
            ("launchpad", "cloud-init"),
            ("launchpad", "ubuntu/+source/cloud-init"),
            ("github", "canonical/cloud-init"),
            ("bugzilla", "opensuse/cloud-init"),
        ],
    },
}


def seed_presets(apps, schema_editor):
    BugSource = apps.get_model("dashboard", "BugSource")
    Preset = apps.get_model("dashboard", "Preset")

    for preset_name, preset_data in PRESETS.items():
        sources = []
        for source_type, identifier in preset_data["sources"]:
            source, _ = BugSource.objects.get_or_create(
                source_type=source_type,
                identifier=identifier,
                defaults={"name": identifier.split("/")[-1]},
            )
            sources.append(source)

        preset, _ = Preset.objects.get_or_create(name=preset_name)
        preset.sources.add(*sources)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0002_preset"),
    ]

    operations = [
        migrations.RunPython(seed_presets, migrations.RunPython.noop),
    ]
