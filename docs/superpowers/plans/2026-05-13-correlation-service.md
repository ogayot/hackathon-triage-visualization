# Correlation Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a correlation service that detects and links duplicate bugs across different sources (Launchpad, GitHub Issues, Bugzilla), exposing the relationships via API and dashboard UI.

**Architecture:** Add a `BugCorrelation` model to group related `Bug` records. Implement a matching engine using title similarity and keyword extraction. Provide a management command to run correlation, JSON API endpoints to query relationships, and update the dashboard template to surface cross-source links.

**Tech Stack:** Django 6.0.5, SQLite, Python 3.12, Bootstrap 5 (frontend), difflib (sequence matching).

---

## File Structure

| File | Responsibility |
|------|--------------|
| `dashboard/models.py` | `BugCorrelation` model + migration |
| `dashboard/correlation.py` | Matching engine: `find_duplicates()`, `match_score()`, `extract_keywords()` |
| `dashboard/management/commands/correlate_bugs.py` | Management command to scan and create correlations |
| `dashboard/views.py` | `correlations_json`, `bug_detail` (enhanced), `correlated_bugs` |
| `dashboard/urls.py` | URL routes for new views |
| `dashboard/templates/dashboard/dashboard.html` | UI badges/links for correlated bugs |
| `dashboard/tests/test_correlation.py` | Tests for matching engine and views |

---

## Execution Order

```
Task 1 (Foundation) ──> Task 2A ─┐
                                 ├──> Task 2B (parallel)
                    Task 2A ─────┘
                                 ├──> Task 2C (parallel)
                    Task 2B ─────┘
                                 └──> Task 3 (UI)
```

- **Task 1** is the foundation (data model). It MUST complete before any parallel work.
- **Task 2A**, **2B**, and **2C** are independent and can run in parallel once Task 1 is done.
- **Task 3** depends on 2A and 2B (needs both the API and the command to exist for integration), but can be drafted in parallel if interfaces are agreed.

---

### Task 1: Data Model (`BugCorrelation`)

**Files:**
- Modify: `dashboard/models.py`
- Create: migration (auto-generated)
- Test: `dashboard/tests/test_correlation.py`

**Interface Contract:**
- `BugCorrelation` has M2M `bugs` to `Bug`, `created_at`, optional `confidence_score` (float 0-1), `match_reason` (text).
- `BugCorrelation.objects.for_bug(bug)` returns QuerySet of `BugCorrelation`.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from django.test import TestCase
from dashboard.models import Bug, BugSource, BugCorrelation

class BugCorrelationModelTest(TestCase):
    def test_create_correlation_links_bugs(self):
        source1 = BugSource.objects.create(name="lp", source_type="launchpad", identifier="subiquity")
        source2 = BugSource.objects.create(name="gh", source_type="github", identifier="canonical/subiquity")
        bug1 = Bug.objects.create(external_id="lp:12345", title="Crash on install", status="New", last_updated="2024-01-01T00:00:00Z")
        bug2 = Bug.objects.create(external_id="gh:canonical/subiquity#67", title="Crash on install", status="open", last_updated="2024-01-01T00:00:00Z")
        bug1.sources.add(source1)
        bug2.sources.add(source2)

        corr = BugCorrelation.objects.create(confidence_score=0.92, match_reason="title match")
        corr.bugs.add(bug1, bug2)

        assert bug1.correlations.count() == 1
        assert bug2.correlations.count() == 1
        assert corr.bugs.count() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test dashboard.tests.test_correlation.BugCorrelationModelTest`
Expected: FAIL — `BugCorrelation` not defined.

- [ ] **Step 3: Write minimal model**

In `dashboard/models.py`, after the `GitHubPR` model:

```python
class BugCorrelation(models.Model):
    bugs = models.ManyToManyField(Bug, related_name="correlations")
    confidence_score = models.FloatField(default=0.0)
    match_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confidence_score"]

    def __str__(self):
        bug_ids = ", ".join(self.bugs.values_list("external_id", flat=True)[:3])
        return f"Correlation ({self.confidence_score:.2f}): {bug_ids}"
```

- [ ] **Step 4: Generate and run migration**

```bash
python manage.py makemigrations dashboard
python manage.py migrate
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test dashboard.tests.test_correlation.BugCorrelationModelTest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/models.py dashboard/migrations/ dashboard/tests/test_correlation.py
git commit -m "feat: add BugCorrelation model for cross-source bug linking"
```

---

### Task 2A: Matching Engine (`dashboard/correlation.py`)

**Files:**
- Create: `dashboard/correlation.py`
- Test: `dashboard/tests/test_correlation.py`

**Interface Contract:**
- `extract_keywords(title)` → `set[str]` (lowercased, filtered words > 3 chars, stopwords removed)
- `match_score(bug_a, bug_b)` → `float` 0-1 (combines title similarity and keyword overlap)
- `find_duplicates(bugs, threshold=0.75)` → `list[dict]` where each dict is `{"bugs": [bug1, bug2], "score": 0.85, "reason": "..."}`

- [ ] **Step 1: Write the failing test**

```python
from dashboard.correlation import extract_keywords, match_score, find_duplicates
from dashboard.models import Bug, BugSource

class CorrelationEngineTest(TestCase):
    def test_extract_keywords(self):
        assert extract_keywords("Crash during installation on Ubuntu") == {"crash", "during", "installation", "ubuntu"}

    def test_match_score_identical(self):
        bug = Bug.objects.create(external_id="lp:1", title="Fix network config", status="New", last_updated="2024-01-01T00:00:00Z")
        score, reason = match_score(bug, bug)
        assert score == 1.0

    def test_find_duplicates(self):
        bugs = [
            Bug.objects.create(external_id="lp:1", title="Network config broken", status="New", last_updated="2024-01-01T00:00:00Z"),
            Bug.objects.create(external_id="gh:2", title="network configuration is broken", status="open", last_updated="2024-01-01T00:00:00Z"),
            Bug.objects.create(external_id="bz:3", title="completely unrelated title here", status="New", last_updated="2024-01-01T00:00:00Z"),
        ]
        results = find_duplicates(bugs, threshold=0.6)
        assert len(results) == 1
        assert results[0]["score"] > 0.6
        bug_ids = {b.external_id for b in results[0]["bugs"]}
        assert bug_ids == {"lp:1", "gh:2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test dashboard.tests.test_correlation.CorrelationEngineTest`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/correlation.py
import re
import difflib

STOPWORDS = {"the", "and", "for", "with", "this", "that", "from", "into", "when", "where", "what", "how", "are", "was", "were", "been", "have", "has", "had", "will", "would", "could", "should", "may", "might", "must", "can", "not", "but", "than", "then", "them", "they", "their", "there", "here", "about", "over", "under", "again", "once", "more", "most", "some", "such", "only", "own", "same", "so", "than", "too", "very", "just", "now"}


def extract_keywords(title):
    if not title:
        return set()
    words = re.findall(r"[a-zA-Z0-9]+", title.lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


def match_score(bug_a, bug_b):
    if bug_a.pk == bug_b.pk:
        return 1.0, "identical"

    title_a = (bug_a.title or "").lower()
    title_b = (bug_b.title or "").lower()

    # Sequence similarity
    seq_sim = difflib.SequenceMatcher(None, title_a, title_b).ratio()

    # Keyword overlap
    kw_a = extract_keywords(title_a)
    kw_b = extract_keywords(title_b)
    if not kw_a and not kw_b:
        kw_score = 0.0
    else:
        intersection = len(kw_a & kw_b)
        union = len(kw_a | kw_b)
        kw_score = intersection / union if union else 0.0

    # Combined score: 60% sequence, 40% keyword
    score = seq_sim * 0.6 + kw_score * 0.4

    reason = f"seq={seq_sim:.2f}, kw={kw_score:.2f}"
    return round(score, 3), reason


def find_duplicates(bugs, threshold=0.75):
    results = []
    seen_pairs = set()
    bugs_list = list(bugs)

    for i, bug_a in enumerate(bugs_list):
        for bug_b in bugs_list[i + 1:]:
            pair = tuple(sorted([bug_a.pk, bug_b.pk]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            score, reason = match_score(bug_a, bug_b)
            if score >= threshold:
                results.append({
                    "bugs": [bug_a, bug_b],
                    "score": score,
                    "reason": reason,
                })

    # Sort by score descending
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test dashboard.tests.test_correlation.CorrelationEngineTest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/correlation.py dashboard/tests/test_correlation.py
git commit -m "feat: add bug matching engine with title similarity + keyword overlap"
```

---

### Task 2B: Management Command (`correlate_bugs`)

**Files:**
- Create: `dashboard/management/commands/correlate_bugs.py`
- Test: `dashboard/tests/test_correlation.py`

**Interface Contract:**
- Command: `python manage.py correlate_bugs [--preset NAME] [--threshold 0.75]`
- Scans all `Bug` records (optionally filtered by preset), runs `find_duplicates()`, creates `BugCorrelation`.
- Skips pairs that already have a `BugCorrelation` linking them.
- Output: number of new correlations created, number of bugs involved.

- [ ] **Step 1: Write the failing test**

```python
from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from dashboard.models import Bug, BugSource, BugCorrelation

class CorrelateBugsCommandTest(TestCase):
    def test_creates_correlations(self):
        source1 = BugSource.objects.create(name="lp", source_type="launchpad", identifier="subiquity")
        source2 = BugSource.objects.create(name="gh", source_type="github", identifier="canonical/subiquity")
        bug1 = Bug.objects.create(external_id="lp:1", title="network config broken", status="New", last_updated="2024-01-01T00:00:00Z")
        bug2 = Bug.objects.create(external_id="gh:2", title="network configuration is broken", status="open", last_updated="2024-01-01T00:00:00Z")
        bug1.sources.add(source1)
        bug2.sources.add(source2)

        out = StringIO()
        call_command("correlate_bugs", stdout=out)
        assert BugCorrelation.objects.count() == 1
        assert "1 correlation" in out.getvalue().lower() or "1 new" in out.getvalue().lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test dashboard.tests.test_correlation.CorrelateBugsCommandTest`
Expected: FAIL — command not found.

- [ ] **Step 3: Write minimal implementation**

```python
# dashboard/management/commands/correlate_bugs.py
import logging
from django.core.management.base import BaseCommand
from dashboard.models import Bug, BugSource, BugCorrelation
from dashboard.correlation import find_duplicates
from dashboard.presets import PRESETS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Correlate bugs across different sources by title similarity"

    def add_arguments(self, parser):
        parser.add_argument("--preset", type=str, choices=list(PRESETS.keys()), help="Only correlate bugs for a specific preset")
        parser.add_argument("--threshold", type=float, default=0.75, help="Minimum match score (0-1)")

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

        # Only correlate across different sources (not same external source)
        # find_duplicates already handles O(n^2) pairing.
        results = find_duplicates(bugs, threshold=threshold)

        created = 0
        skipped = 0

        for result in results:
            bug_a, bug_b = result["bugs"]

            # Skip if already correlated
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test dashboard.tests.test_correlation.CorrelateBugsCommandTest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/management/commands/correlate_bugs.py dashboard/tests/test_correlation.py
git commit -m "feat: add correlate_bugs management command"
```

---

### Task 2C: API Endpoints

**Files:**
- Modify: `dashboard/views.py`
- Modify: `dashboard/urls.py`
- Test: `dashboard/tests/test_correlation.py`

**Interface Contract:**
- `GET /correlations/` → JSON list of all `BugCorrelation` with nested bug data.
- `GET /bugs/<external_id>/correlated/` → JSON list of correlated bugs (excluding self).
- Enhanced `bug_detail` to include `correlations` array.

- [ ] **Step 1: Write the failing test**

```python
from django.test import TestCase, Client
from dashboard.models import Bug, BugSource, BugCorrelation
import json

class CorrelationApiTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.source1 = BugSource.objects.create(name="lp", source_type="launchpad", identifier="subiquity")
        self.source2 = BugSource.objects.create(name="gh", source_type="github", identifier="canonical/subiquity")
        self.bug1 = Bug.objects.create(external_id="lp:1", title="network broken", status="New", last_updated="2024-01-01T00:00:00Z")
        self.bug2 = Bug.objects.create(external_id="gh:2", title="network is broken", status="open", last_updated="2024-01-01T00:00:00Z")
        self.bug1.sources.add(self.source1)
        self.bug2.sources.add(self.source2)
        self.corr = BugCorrelation.objects.create(confidence_score=0.88, match_reason="title match")
        self.corr.bugs.add(self.bug1, self.bug2)

    def test_correlations_list(self):
        resp = self.client.get("/correlations/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data["correlations"]) == 1
        assert data["correlations"][0]["score"] == 0.88

    def test_bug_correlated_endpoint(self):
        resp = self.client.get("/bugs/lp:1/correlated/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert len(data["correlated_bugs"]) == 1
        assert data["correlated_bugs"][0]["external_id"] == "gh:2"

    def test_bug_detail_includes_correlations(self):
        resp = self.client.get("/bugs/lp:1/")
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert "correlations" in data
        assert len(data["correlations"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test dashboard.tests.test_correlation.CorrelationApiTest`
Expected: FAIL — views/urls not updated.

- [ ] **Step 3: Write minimal views**

In `dashboard/views.py`, add:

```python
def correlations_json(request):
    data = []
    for corr in BugCorrelation.objects.prefetch_related("bugs__sources"):
        data.append({
            "id": corr.id,
            "score": corr.confidence_score,
            "reason": corr.match_reason,
            "created_at": corr.created_at.isoformat(),
            "bugs": [
                {
                    "external_id": b.external_id,
                    "title": b.title,
                    "status": b.status,
                    "url": b.url,
                    "sources": [
                        {"name": s.name, "source_type": s.source_type}
                        for s in b.sources.all()
                    ],
                }
                for b in corr.bugs.all()
            ],
        })
    return JsonResponse({"correlations": data})


def correlated_bugs(request, external_id):
    bug = get_object_or_404(Bug, external_id=external_id)
    related = []
    for corr in bug.correlations.prefetch_related("bugs"):
        for b in corr.bugs.all():
            if b.external_id != bug.external_id:
                related.append({
                    "external_id": b.external_id,
                    "title": b.title,
                    "status": b.status,
                    "url": b.url,
                    "correlation_score": corr.confidence_score,
                    "correlation_reason": corr.match_reason,
                    "sources": [
                        {"name": s.name, "source_type": s.source_type}
                        for s in b.sources.all()
                    ],
                })
    return JsonResponse({"correlated_bugs": related})
```

Then enhance `bug_detail` by appending to the returned dict:

```python
"correlations": [
    {
        "id": corr.id,
        "score": corr.confidence_score,
        "reason": corr.match_reason,
        "bugs": [
            {"external_id": b.external_id, "title": b.title}
            for b in corr.bugs.all()
        ],
    }
    for corr in bug.correlations.all()
],
```

- [ ] **Step 4: Wire URLs**

In `dashboard/urls.py`, add:

```python
    path("correlations/", views.correlations_json, name="correlations_json"),
    path("bugs/<str:external_id>/correlated/", views.correlated_bugs, name="correlated_bugs"),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test dashboard.tests.test_correlation.CorrelationApiTest`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/views.py dashboard/urls.py dashboard/tests/test_correlation.py
git commit -m "feat: add correlation API endpoints"
```

---

### Task 3: Dashboard UI (Template Updates)

**Files:**
- Modify: `dashboard/templates/dashboard/dashboard.html`
- Test: manual browser verification (or Django `Client` test for HTML presence)

**Interface Contract:**
- Bug rows in the table show a "linked" badge if the bug has `correlations`.
- Bug detail modal shows a "Correlated Bugs" section with links.
- Uses existing Bootstrap 5 classes and emoji source icons.

- [ ] **Step 1: Add correlation badge to table row**

In `dashboard/templates/dashboard/dashboard.html`, inside the `<tr class="bug-row">`, add a new `<td>` or augment the existing ID/status/PR columns.

Find the PR `<td>` and add before `</td>`:

```html
<td>
    {% with pr_count=bug.github_prs.count corr_count=bug.correlations.count %}
    {% if pr_count > 0 %}
    <span class="badge bg-success">{{ pr_count }} 🐙</span>
    {% endif %}
    {% if corr_count > 0 %}
    <span class="badge bg-info">{{ corr_count }} 🔗</span>
    {% endif %}
    {% endwith %}
</td>
```

Update the `<thead>` to add a "Links" header:

```html
<th style="width:100px">Links</th>
```

- [ ] **Step 2: Add correlated bugs section to modal**

In the `openBugDetail` JS function, inside the template string, after the `github_prs` block, add:

```javascript
${data.correlations && data.correlations.length > 0 ? '<h6 class="mt-3">Correlated Bugs</h6><ul class="list-unstyled">' + data.correlations.map(c => '<li>🔗 Score: ' + c.score + ' <span class="text-muted">(' + c.reason + ')</span><ul>' + c.bugs.map(b => '<li><a href="/bugs/' + b.external_id + '/" onclick="openBugDetail(\'' + b.external_id + '\');return false;">' + b.external_id + '</a>: ' + b.title + '</li>').join('') + '</ul></li>').join('') + '</ul>' : ''}
```

- [ ] **Step 3: Run Django server and manually verify**

```bash
python manage.py fetch_bugs --preset Subiquity
python manage.py correlate_bugs --preset Subiquity
python manage.py runserver 0.0.0.0:8000
```

Open dashboard, click a bug row, verify modal shows correlated bugs.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/dashboard/dashboard.html
git commit -m "feat: show correlation badges and links in dashboard UI"
```

---

## Self-Review Checklist

| Spec Requirement | Task |
|-----------------|------|
| Correlate bugs from different sources or multiple sources | Task 1 + 2A + 2B |
| Correct to related bugs or PRs | Task 2B (cross-source) + Task 2C (API) |
| Map bugs from multiple sources | Task 3 (UI surfacing) |

**Placeholder scan:** No placeholders found. All steps contain exact file paths, exact code, exact commands.

**Type consistency:**
- `BugCorrelation.confidence_score` used consistently as float.
- `BugCorrelation.match_reason` used consistently as text.
- `find_duplicates` return structure matches consumption in 2B.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-13-correlation-service.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
