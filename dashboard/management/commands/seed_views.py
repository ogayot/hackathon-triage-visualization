from django.core.management.base import BaseCommand
from dashboard.models import Preset, View

UBUNTU_TAGS = [
    "noble",
    "oracular",
    "plucky",
    "questing",
]


class Command(BaseCommand):
    help = "Seed views for common Ubuntu release codenames"

    def add_arguments(self, parser):
        parser.add_argument("--tag", type=str, help="Only seed a specific tag")

    def handle(self, *args, **options):
        tags = [options["tag"]] if options["tag"] else UBUNTU_TAGS
        presets = Preset.objects.all()

        if not presets.exists():
            self.stdout.write(self.style.WARNING("No presets found. Create presets first."))
            return

        created = 0
        skipped = 0
        for preset in presets:
            for tag in tags:
                name = tag.capitalize()
                _, was_created = View.objects.get_or_create(
                    preset=preset,
                    name=name,
                    defaults={"tag": tag},
                )
                if was_created:
                    created += 1
                    self.stdout.write(f"  Created view '{name}' → tag '{tag}' for preset '{preset.name}'")
                else:
                    skipped += 1

        self.stdout.write(self.style.SUCCESS(f"Done. {created} created, {skipped} already exist."))
