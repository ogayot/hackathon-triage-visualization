import os

from django.core.management.base import BaseCommand
from launchpadlib.credentials import UnencryptedFileCredentialStore
from launchpadlib.launchpad import Launchpad

CRED_PATH = os.path.expanduser("~/.launchpadlib/bug-dashboard-credentials")


class Command(BaseCommand):
    help = "Authenticate with Launchpad to access private bugs"

    def handle(self, *args, **options):
        cred_store = UnencryptedFileCredentialStore(CRED_PATH)
        self.stdout.write("Opening Launchpad authorization...")
        launchpad = Launchpad.login_with(
            "bug-dashboard", "production",
            version="devel",
            credential_store=cred_store,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Authenticated as: {launchpad.me.display_name}"
        ))
        self.stdout.write(f"Credentials saved to: {CRED_PATH}")
