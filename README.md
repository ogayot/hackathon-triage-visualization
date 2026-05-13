# Bug Aggregator Dashboard

Aggregates bugs from Launchpad, GitHub Issues, and Bugzilla across multiple projects into a single dashboard. Correlates bugs with GitHub PRs by scanning git commit history.

## Quick Start

```bash
# Fetch bugs from all sources
python3 manage.py fetch_bugs

# Correlate GitHub PRs with Launchpad bugs
python3 manage.py correlate_prs

# Start the dashboard
python3 manage.py runserver 0.0.0.0:8000
```

Then open `http://localhost:8000` in your browser.

## Commands

### `fetch_bugs`

Fetches bugs from all configured sources (Launchpad, GitHub Issues, Bugzilla).

```bash
# Fetch all presets
python3 manage.py fetch_bugs

# Fetch a specific preset
python3 manage.py fetch_bugs --preset Subiquity
python3 manage.py fetch_bugs --preset cloud-init
```

Scans the most recent 100 bugs per source.

### `correlate_prs`

Correlates GitHub PRs with Launchpad bugs by scanning git commit history. No GitHub API token needed — works entirely via git (bypasses rate limits).

```bash
# Scan all repos across all presets
python3 manage.py correlate_prs

# Scan repos for a specific preset only
python3 manage.py correlate_prs --preset Subiquity
python3 manage.py correlate_prs --preset cloud-init
```

Scans merged PRs (via commit bodies) and open PRs (via individual commit messages). For full coverage including PR descriptions, set `GITHUB_TOKEN`:

```bash
GITHUB_TOKEN=your_token python3 manage.py correlate_prs
```

### `runserver`

```bash
# Start the web dashboard
python3 manage.py runserver 0.0.0.0:8000
```

## Presets

### Subiquity
- **Launchpad**: subiquity, ubuntu/+source/subiquity, probert, curtin
- **GitHub**: canonical/subiquity, canonical/probert, canonical/curtin

### cloud-init
- **Launchpad**: cloud-init, ubuntu/+source/cloud-init
- **GitHub**: canonical/cloud-init
- **Bugzilla**: opensuse/cloud-init

## Data Model

- **Bug** — external_id (lp:/gh:/bz: prefixed), title, status, priority, sources (M2M)
- **BugSource** — source_type (launchpad/github/bugzilla), identifier
- **GitHubPR** — pr_number, repo, title, url, state (open/merged), bugs (M2M)

## Adding a Preset

Edit `dashboard/presets.py`:

```python
PRESETS["MyProject"] = {
    "sources": [
        ("launchpad", "my-project"),
        ("github", "canonical/my-project"),
    ],
}
```

Then add a fetcher in `dashboard/management/commands/fetch_bugs.py` if needed.
