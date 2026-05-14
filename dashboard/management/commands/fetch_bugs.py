import logging
from datetime import datetime, timezone

from django.core.management.base import BaseCommand
from django.utils.timezone import make_aware

from dashboard.management.progress import write_progress, check_cancelled
from dashboard.models import Bug, BugSource, Preset

logger = logging.getLogger(__name__)


def to_aware(dt):
    if dt is None:
        return make_aware(datetime.now(timezone.utc))
    if dt.tzinfo is not None:
        return dt
    return make_aware(dt, timezone.utc)


def fetch_launchpad_bugs(source):
    from launchpadlib.launchpad import Launchpad

    launchpad = Launchpad.login_anonymously("bug-dashboard", "production", version="devel")

    bugs = []
    try:
        project = launchpad.projects[source.identifier]
    except KeyError:
        try:
            project = launchpad.distributions[source.identifier]
        except KeyError:
            logger.warning(f"Could not find project or distribution: {source.identifier}")
            return bugs

    statuses = ["New", "Incomplete", "Confirmed", "Triaged", "In Progress", "Fix Committed", "Fix Released"]
    tasks = project.searchTasks(status=statuses)

    for i, task in enumerate(tasks):
        if i >= 100:
            break
        bug = task.bug
        status = task.status
        bugs.append({
            "external_id": f"lp:{bug.id}",
            "title": bug.title.replace(f"Bug #{bug.id} in {source.identifier}: ", ""),
            "description": bug.description or "",
            "status": status,
            "priority": getattr(task, "importance", ""),
            "url": bug.web_link,
            "last_updated": to_aware(bug.date_last_updated),
        })

    return bugs


def fetch_github_issues(source):
    import urllib.request
    import json
    import os

    repo = source.identifier
    api_url = f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100&sort=updated"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "bug-dashboard"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(api_url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.warning(f"GitHub API rate limited for {repo}. Set GITHUB_TOKEN env var to increase limit.")
        else:
            logger.warning(f"GitHub API error for {repo}: {e}")
        return []
    except Exception as e:
        logger.warning(f"Failed to fetch GitHub issues for {repo}: {e}")
        return []

    bugs = []
    for issue in data:
        if "pull_request" in issue:
            continue
        bugs.append({
            "external_id": f"gh:{repo}#{issue['number']}",
            "title": issue["title"],
            "description": issue.get("body", "") or "",
            "status": issue["state"],
            "priority": "",
            "url": issue["html_url"],
            "last_updated": to_aware(datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))),
        })
    return bugs


def fetch_bugzilla_bugs(source):
    import urllib.request
    import json

    base_url = source.identifier
    bugzilla_base = ""
    product = ""

    if "/" in base_url:
        parts = base_url.split("/", 1)
        bugzilla_base = parts[0]
        product = parts[1]
    else:
        bugzilla_base = base_url

    if bugzilla_base == "bugzilla.opensuse.org" or bugzilla_base == "opensuse":
        api_url = f"https://bugzilla.opensuse.org/rest/bug"
        params = []
        if product:
            params.append(f"quicksearch={product}")
        params.append("limit=100")
        api_url += "?" + "&".join(params)
    else:
        xmlrpc_body = """<?xml version="1.0"?>
<methodCall>
  <methodName>Bug.search</methodName>
  <params>
    <param><value><struct>
      <member><name>product</name><value><string>%s</string></value></member>
      <member><name>status</name><value><array><data>
        <value><string>NEW</string></value>
        <value><string>CONFIRMED</string></value>
        <value><string>IN_PROGRESS</string></value>
      </data></array></value></member>
      <member><name>limit</name><value><int>100</int></value></member>
    </struct></value></param>
  </params>
</methodCall>""" % product
        req = urllib.request.Request(
            f"https://{bugzilla_base}/xmlrpc.cgi",
            data=xmlrpc_body.encode(),
            headers={"Content-Type": "text/xml"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
        except Exception as e:
            logger.warning(f"Failed to fetch Bugzilla bugs for {source.identifier}: {e}")
            return []
        data = resp.read().decode()
        import re
        bug_ids = re.findall(r"<member><name>bug_id</name><value><int>(\d+)</int>", data)
        titles = re.findall(r"<member><name>short_desc</name><value><string>(.*?)</string>", data)
        statuses = re.findall(r"<member><name>bug_status</name><value><string>(.*?)</string>", data)
        bugs = []
        for i in range(min(len(bug_ids), len(titles), len(statuses))):
            bugs.append({
                "external_id": f"bz:{bug_ids[i]}",
                "title": titles[i],
                "description": "",
                "status": statuses[i],
                "priority": "",
                "url": f"https://{bugzilla_base}/show_bug.cgi?id={bug_ids[i]}",
                "last_updated": make_aware(datetime.now(timezone.utc)),
            })
        return bugs

    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "bug-dashboard"})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
    except Exception as e:
        logger.warning(f"Failed to fetch Bugzilla bugs for {source.identifier}: {e}")
        return []

    STATUS_NORMALIZE = {
        "NEW": "New", "CONFIRMED": "Confirmed", "IN_PROGRESS": "In Progress",
        "REOPENED": "In Progress", "RESOLVED": "Fix Committed",
        "VERIFIED": "Fix Committed", "CLOSED": "Fix Committed",
    }
    bugs = []
    for b in data.get("bugs", []):
        raw_status = b.get("status", "Unknown")
        status = STATUS_NORMALIZE.get(raw_status, raw_status)
        bugs.append({
            "external_id": f"bz:{b['id']}",
            "title": b.get("summary", ""),
            "description": "",
            "status": status,
            "priority": "",
            "url": f"https://bugzilla.opensuse.org/show_bug.cgi?id={b['id']}",
            "last_updated": to_aware(datetime.fromisoformat(b.get("last_change_time", "").replace("Z", "+00:00"))) if b.get("last_change_time") else make_aware(datetime.now(timezone.utc)),
        })
    return bugs


FETCHERS = {
    "launchpad": fetch_launchpad_bugs,
    "launchpad_package": fetch_launchpad_bugs,
    "github": fetch_github_issues,
    "bugzilla": fetch_bugzilla_bugs,
}


class Command(BaseCommand):
    help = "Fetch bugs from all configured sources (Launchpad, GitHub, Bugzilla)"

    def add_arguments(self, parser):
        parser.add_argument("--preset", type=str, help="Only fetch for a specific preset")

    def handle(self, *args, **options):
        if options["preset"]:
            try:
                presets_to_fetch = [Preset.objects.get(name=options["preset"])]
            except Preset.DoesNotExist:
                self.stderr.write(f"Unknown preset: {options['preset']}")
                return
        else:
            presets_to_fetch = Preset.objects.all()

        total_created = 0
        total_updated = 0

        source_steps = [(p, s) for p in presets_to_fetch for s in p.sources.all()]
        total_steps = len(source_steps)
        step_idx = 0

        for preset, source in source_steps:
            step_idx += 1
            if check_cancelled():
                self.stdout.write(self.style.WARNING("Operation cancelled by user"))
                return
            fetcher = FETCHERS.get(source.source_type)
            if not fetcher:
                self.stdout.write(f"  No fetcher for {source.source_type}: {source.identifier}")
                write_progress(
                    current=step_idx, total=total_steps,
                    phase="fetch_bugs",
                    item=source.identifier,
                    message=f"Skipping {source.source_type}: {source.identifier} (no fetcher)"
                )
                continue

            self.stdout.write(f"[{step_idx}/{total_steps}] Fetching {source.source_type}: {source.identifier}...")
            write_progress(
                current=step_idx, total=total_steps,
                phase="fetch_bugs",
                item=source.identifier,
                message=f"Fetching {source.source_type}: {source.identifier}"
            )
            bugs = fetcher(source)

            created = 0
            updated = 0
            for i, bug_data in enumerate(bugs):
                if check_cancelled():
                    self.stdout.write(self.style.WARNING("Operation cancelled by user"))
                    return
                bug, was_created = Bug.objects.update_or_create(
                    external_id=bug_data["external_id"],
                    defaults={
                        "title": bug_data["title"],
                        "description": bug_data["description"],
                        "status": bug_data["status"],
                        "priority": bug_data["priority"],
                        "url": bug_data["url"],
                        "last_updated": bug_data["last_updated"],
                    },
                )
                bug.sources.add(source)
                if was_created:
                    created += 1
                else:
                    updated += 1

                if (i + 1) % 10 == 0 or i == len(bugs) - 1:
                    write_progress(
                        current=step_idx, total=total_steps,
                        phase="fetch_bugs",
                        item=source.identifier,
                        message=f"Saving bugs from {source.source_type}: {source.identifier} ({i+1}/{len(bugs)})"
                    )

            total_created += created
            total_updated += updated
            self.stdout.write(f"    → {created} created, {updated} updated")

        self.stdout.write(self.style.SUCCESS(f"Done. {total_created} created, {total_updated} updated total"))
