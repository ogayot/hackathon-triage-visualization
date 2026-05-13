import logging
import re
import subprocess
import os

from django.core.management.base import BaseCommand
from django.conf import settings

from dashboard.models import Bug, BugSource, GitHubPR
from dashboard.presets import PRESETS

logger = logging.getLogger(__name__)

LP_PATTERN = re.compile(r"(?:LP|Bug|Launchpad)\s*[#:]\s*#?\s*(\d{4,})", re.IGNORECASE)
LP_URL_PATTERN = re.compile(r"bugs\.launchpad\.net/(?:.*?/)?\+bug/(\d+)", re.IGNORECASE)
SQUASH_PR_PATTERN = re.compile(r"\(#(\d+)\)")
MERGE_PR_PATTERN = re.compile(r"Merge pull request #(\d+)\b")


def git(*args, cwd=None):
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, cwd=cwd,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def extract_lp_ids(text):
    if not text:
        return []
    ids = set()
    ids.update(LP_PATTERN.findall(text))
    ids.update(LP_URL_PATTERN.findall(text))
    return list(ids)


def get_default_branch(repo_dir):
    try:
        head = git("-C", repo_dir, "symbolic-ref", "refs/remotes/origin/HEAD")
        return head.strip().replace("refs/remotes/origin/", "")
    except RuntimeError:
        for branch in ["main", "master"]:
            try:
                git("-C", repo_dir, "rev-parse", f"origin/{branch}")
                return branch
            except RuntimeError:
                continue
        return "main"


def ensure_repo_clone(repo):
    repo_dir = os.path.join(settings.BASE_DIR, ".github_repos", repo.replace("/", "_"))
    if not os.path.exists(repo_dir):
        os.makedirs(os.path.dirname(repo_dir), exist_ok=True)
        logger.info(f"Cloning {repo} (bare)...")
        git("clone", "--bare", "--filter=blob:none",
            f"https://github.com/{repo}.git", repo_dir)
    logger.info(f"Fetching latest refs for {repo}...")
    branch = get_default_branch(repo_dir)
    git("-C", repo_dir, "fetch", "origin", f"+{branch}", "+refs/pull/*/head:refs/pull/*", "--quiet")
    return repo_dir


def scan_squash_repo(repo_dir, branch, max_commits=5000):
    """For repos that use squash merges (cloud-init style)."""
    raw = git("-C", repo_dir, "log", branch,
              f"--max-count={max_commits}",
              "--format=>>>COMMIT_START<<<\n%H\n>>>PARENTS<<<\n%P\n>>>SUBJECT<<<\n%s\n>>>BODY_START<<<\n%b\n>>>BODY_END<<<",
              )

    results = []
    entries = raw.split(">>>COMMIT_START<<<\n")[1:]

    for entry in entries:
        sha = ""
        subject = ""
        body_lines = []
        in_body = False

        for line in entry.split("\n"):
            if line == ">>>PARENTS<<<":
                continue
            elif line == ">>>SUBJECT<<<":
                continue
            elif line == ">>>BODY_START<<<":
                in_body = True
                continue
            elif line == ">>>BODY_END<<<":
                break
            elif not in_body:
                if not sha:
                    sha = line.strip()
                else:
                    subject = line.strip()
            else:
                body_lines.append(line)

        body = "\n".join(body_lines)
        full_text = f"{subject}\n{body}"
        lp_ids = extract_lp_ids(full_text)
        if not lp_ids:
            continue

        m = SQUASH_PR_PATTERN.search(subject)
        pr_num = int(m.group(1)) if m else None

        if pr_num:
            results.append({
                "sha": sha,
                "pr_num": pr_num,
                "subject": subject,
                "lp_ids": lp_ids,
            })
    return results


def scan_merge_repo(repo_dir, branch, max_commits=500):
    """For repos that use merge commits. Walk each merge's PR commits to find LP refs."""
    raw = git("-C", repo_dir, "log", branch, "--merges",
              f"--max-count={max_commits}",
              "--format=>>>MERGE_COMMIT<<<\n%H\n>>>SUBJECT<<<\n%s\n>>>BODY_START<<<\n%b\n>>>BODY_END<<<",
              )

    results = []
    entries = raw.split(">>>MERGE_COMMIT<<<\n")[1:]

    for entry in entries:
        sha = ""
        subject = ""
        in_body = False
        body_lines = []

        for line in entry.split("\n"):
            if line == ">>>SUBJECT<<<":
                continue
            elif line == ">>>BODY_START<<<":
                in_body = True
                continue
            elif line == ">>>BODY_END<<<":
                break
            elif not in_body:
                if not sha:
                    sha = line.strip()
                else:
                    subject = line.strip()
            else:
                body_lines.append(line)

        body = "\n".join(body_lines)
        full_text = f"{subject}\n{body}"

        m = MERGE_PR_PATTERN.search(subject)
        pr_num = int(m.group(1)) if m else None
        if not pr_num:
            continue

        lp_ids = extract_lp_ids(full_text)

        # Also check the PR's actual commits (second parent range)
        try:
            pr_commits = git("-C", repo_dir, "log", f"{sha}^1..{sha}^2",
                             "--format=%s%n%b", "--max-count=50")
            lp_ids.extend(extract_lp_ids(pr_commits))
        except RuntimeError:
            pass

        if lp_ids:
            results.append({
                "sha": sha,
                "pr_num": pr_num,
                "subject": subject,
                "lp_ids": list(set(lp_ids)),
            })
    return results


def get_merged_pr_numbers(repo_dir, branch, max_commits=5000):
    """Get all PR numbers that appear in main branch commits (merged PRs)."""
    merged = set()
    raw = git("-C", repo_dir, "log", branch,
              f"--max-count={max_commits}",
              "--format=%s")
    for line in raw.splitlines():
        m = SQUASH_PR_PATTERN.search(line)
        if m:
            merged.add(int(m.group(1)))
        m = MERGE_PR_PATTERN.search(line)
        if m:
            merged.add(int(m.group(1)))
    return merged


def scan_open_prs(repo_dir, merged_pr_nums, branch="main"):
    """Scan open (non-merged) PR refs for LP references in all PR commits."""
    raw = git("-C", repo_dir, "for-each-ref",
              "--format=%(refname:strip=2)||%(objectname)",
              "refs/pull/")

    results = []
    for line in raw.splitlines():
        if "||" not in line:
            continue
        parts = line.split("||", 1)
        if len(parts) < 2 or not parts[0].strip():
            continue
        try:
            pr_num = int(parts[0].strip())
        except ValueError:
            continue
        if pr_num in merged_pr_nums:
            continue

        pr_ref = f"refs/pull/{pr_num}"
        try:
            merge_base = git("-C", repo_dir, "merge-base", pr_ref, branch).strip()
            pr_commits = git("-C", repo_dir, "log", f"{merge_base}..{pr_ref}",
                             "--format=:::COMMIT:::%n%H%n%s%n%b",
                             "--max-count=50")
        except RuntimeError:
            continue

        all_ids = set()
        first_subject = ""
        entries = pr_commits.split(":::COMMIT:::\n")[1:]
        for entry in entries:
            lines = entry.strip().split("\n", 2)
            if len(lines) < 2:
                continue
            sha = lines[0].strip()
            subject = lines[1].strip()
            body = lines[2] if len(lines) > 2 else ""
            full_text = f"{subject}\n{body}"
            ids = extract_lp_ids(full_text)
            all_ids.update(ids)
            if not first_subject:
                first_subject = subject

        if all_ids:
            results.append({
                "pr_num": pr_num,
                "subject": first_subject,
                "lp_ids": list(all_ids),
            })
    return results


def get_github_repos(preset_name=None):
    repos = set()
    targets = {preset_name: PRESETS[preset_name]} if preset_name else PRESETS
    for name, preset in targets.items():
        for source_type, identifier in preset["sources"]:
            if source_type == "github":
                repos.add(identifier)
    return sorted(repos)


class Command(BaseCommand):
    help = "Correlate GitHub PRs with Launchpad bugs via git (bypasses API rate limit)"

    def add_arguments(self, parser):
        parser.add_argument("--preset", type=str, choices=list(PRESETS.keys()),
                            help="Only scan repos for a specific preset")
        parser.add_argument("--max-commits", type=int, default=5000,
                            help="Max commits to scan per repo")

    def handle(self, *args, **options):
        preset = options["preset"]
        max_commits = options["max_commits"]

        repos = get_github_repos(preset)
        if not repos:
            self.stdout.write(self.style.WARNING("No GitHub repos found in any preset"))
            return

        self.stdout.write(f"Scanning {len(repos)} repos: {', '.join(repos)}")

        total_prs = 0
        total_linked = 0

        for repo in repos:
            self.stdout.write(f"\n--- {repo} ---")
            try:
                repo_dir = ensure_repo_clone(repo)
            except RuntimeError as e:
                self.stdout.write(self.style.ERROR(f"  Clone failed: {e}"))
                continue

            try:
                branch = get_default_branch(repo_dir)
                merge_results = scan_merge_repo(repo_dir, branch, 500)
                squash_results = scan_squash_repo(repo_dir, branch, max_commits)

                merged_nums = get_merged_pr_numbers(repo_dir, branch, max_commits)
                open_results = scan_open_prs(repo_dir, merged_nums, branch)
            except RuntimeError as e:
                self.stdout.write(self.style.ERROR(f"  Scan failed: {e}"))
                continue

            all_results = merge_results + squash_results + open_results

            pr_map = {}
            for c in all_results:
                key = c["pr_num"]
                if key not in pr_map:
                    pr_map[key] = c
                else:
                    pr_map[key]["lp_ids"].extend(
                        i for i in c["lp_ids"] if i not in pr_map[key]["lp_ids"]
                    )

            merged_count = sum(1 for c in all_results if c["pr_num"] in merged_nums)
            self.stdout.write(
                f"  {len(all_results)} PRs with LP refs "
                f"({len(pr_map)} unique: {merged_count} merged, "
                f"{len(all_results) - merged_count} open)"
            )

            linked = 0
            for pr_num, info in sorted(pr_map.items()):
                is_merged = pr_num in merged_nums
                GitHubPR.objects.update_or_create(
                    repo=repo,
                    pr_number=pr_num,
                    defaults={
                        "title": info["subject"][:500],
                        "url": f"https://github.com/{repo}/pull/{pr_num}",
                        "state": "merged" if is_merged else "open",
                    },
                )

                for lp_id in info["lp_ids"]:
                    ext_id = f"lp:{lp_id}"
                    try:
                        bug = Bug.objects.get(external_id=ext_id)
                        pr_obj = GitHubPR.objects.get(repo=repo, pr_number=pr_num)
                        pr_obj.bugs.add(bug)
                        linked += 1
                        self.stdout.write(f"  PR #{pr_num} → LP #{lp_id} ({bug.title[:50]})")
                    except Bug.DoesNotExist:
                        pass

            total_prs += len(pr_map)
            total_linked += linked

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {total_prs} PRs reference LP bugs across {len(repos)} repos, "
            f"{total_linked} linked to bugs in DB"
        ))
