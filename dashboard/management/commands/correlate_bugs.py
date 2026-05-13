import logging

from django.core.management.base import BaseCommand

from dashboard.correlation import find_duplicates
from dashboard.models import Bug, BugSource, BugCorrelation
from dashboard.presets import PRESETS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Correlate bugs across different sources by title similarity"

    def add_arguments(self, parser):
        parser.add_argument("--preset", type=str, choices=list(PRESETS.keys()),
                            help="Only correlate bugs for a specific preset")
        parser.add_argument("--threshold", type=float, default=0.75,
                            help="Minimum match score (0-1)")

    def handle(self, *args, **options):
        preset = options["preset"]
        threshold = options["threshold"]

        if preset:
            preset_data = PRESETS[preset]
            sources = []
            for source_type, identifier in preset_data["sources"]:
                source, _ = BugSource.objects.get_or_create(
                    source_type=source_type,
                    identifier=identifier,
                    defaults={"name": identifier.split("/")[-1]},
                )
                sources.append(source)
            bugs = Bug.objects.filter(sources__in=sources).distinct()
            self.stdout.write(f"Correlating bugs for preset: {preset} ({bugs.count()} bugs)")
        else:
            bugs = Bug.objects.all()
            self.stdout.write(f"Correlating all bugs ({bugs.count()} bugs)")

        results = find_duplicates(bugs, threshold=threshold)

        created = 0
        skipped = 0

        for result in results:
            bug_a, bug_b = result["bugs"]

            existing = BugCorrelation.objects.filter(bugs=bug_a).filter(bugs=bug_b).first()
            if existing:
                skipped += 1
                continue

            corr = BugCorrelation.objects.create(
                confidence_score=result["score"],
                match_reason=result["reason"],
            )
            corr.bugs.add(bug_a, bug_b)
            created += 1
            self.stdout.write(f"  Correlated {bug_a.external_id} <-> {bug_b.external_id} (score: {result['score']:.2f})")

        self.stdout.write(self.style.SUCCESS(
            f"Done. {created} new correlations created, {skipped} already existed."
        ))
