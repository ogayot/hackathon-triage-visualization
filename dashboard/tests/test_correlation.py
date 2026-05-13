from datetime import datetime, timezone
from django.test import TestCase, Client

from dashboard.correlation import extract_keywords, match_score, find_duplicates
from dashboard.models import Bug, BugSource, BugCorrelation


class CorrelationEngineTest(TestCase):
    def test_extract_keywords(self):
        assert extract_keywords("Crash during installation on Ubuntu") == {"crash", "during", "installation", "ubuntu"}

    def test_match_score_identical(self):
        bug = Bug.objects.create(external_id="lp:1", title="Fix network config", status="New", last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc))
        score, reason = match_score(bug, bug)
        assert score == 1.0

    def test_find_duplicates(self):
        bugs = [
            Bug.objects.create(external_id="lp:1", title="Network config broken", status="New", last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            Bug.objects.create(external_id="gh:2", title="network configuration is broken", status="open", last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc)),
            Bug.objects.create(external_id="bz:3", title="completely unrelated title here", status="New", last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc)),
        ]
        results = find_duplicates(bugs, threshold=0.6)
        assert len(results) == 1
        assert results[0]["score"] > 0.6
        bug_ids = {b.external_id for b in results[0]["bugs"]}
        assert bug_ids == {"lp:1", "gh:2"}


class CorrelationApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.source1 = BugSource.objects.create(name="lp", source_type="launchpad", identifier="subiquity")
        self.source2 = BugSource.objects.create(name="gh", source_type="github", identifier="canonical/subiquity")
        self.bug1 = Bug.objects.create(external_id="lp:1", title="network broken", status="New", last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.bug2 = Bug.objects.create(external_id="gh:2", title="network is broken", status="open", last_updated=datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.bug1.sources.add(self.source1)
        self.bug2.sources.add(self.source2)
        self.corr = BugCorrelation.objects.create(confidence_score=0.88, match_reason="title match")
        self.corr.bugs.add(self.bug1, self.bug2)

    def test_correlations_list(self):
        resp = self.client.get("/correlations/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["correlations"]) == 1
        assert data["correlations"][0]["score"] == 0.88

    def test_bug_correlated_endpoint(self):
        resp = self.client.get("/bugs/lp:1/correlated/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["correlated_bugs"]) == 1
        assert data["correlated_bugs"][0]["external_id"] == "gh:2"

    def test_bug_detail_includes_correlations(self):
        resp = self.client.get("/bugs/lp:1/")
        assert resp.status_code == 200
        data = resp.json()
        assert "correlations" in data
        assert len(data["correlations"]) == 1
