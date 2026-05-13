from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from .models import Bug, BugSource
from .presets import PRESETS


def get_preset_data(preset_name):
    preset = PRESETS.get(preset_name)
    if not preset:
        return [], []

    sources = []
    for source_type, identifier in preset["sources"]:
        source, _ = BugSource.objects.get_or_create(
            source_type=source_type,
            identifier=identifier,
            defaults={"name": identifier.split("/")[-1]},
        )
        sources.append(source)

    bugs = Bug.objects.filter(sources__in=sources).distinct()
    return bugs, sources


def dashboard(request):
    preset_name = request.GET.get("preset", "Subiquity")
    bugs, sources = get_preset_data(preset_name)

    status_counts = {}
    for bug in bugs:
        s = bug.status
        status_counts[s] = status_counts.get(s, 0) + 1

    context = {
        "presets": PRESETS,
        "current_preset": preset_name,
        "bugs": bugs,
        "sources": sources,
        "status_counts": status_counts,
        "total_bugs": bugs.count(),
    }
    return render(request, "dashboard/dashboard.html", context)


def bug_detail(request, external_id):
    bug = get_object_or_404(Bug, external_id=external_id)
    return JsonResponse({
        "id": bug.external_id,
        "title": bug.title,
        "description": bug.description,
        "status": bug.status,
        "priority": bug.priority,
        "url": bug.url,
        "last_updated": bug.last_updated.isoformat(),
        "sources": [
            {
                "name": s.name,
                "source_type": s.source_type,
                "identifier": s.identifier,
            }
            for s in bug.sources.all()
        ],
        "github_prs": [
            {
                "pr_number": pr.pr_number,
                "repo": pr.repo,
                "title": pr.title,
                "url": pr.url,
                "state": pr.state,
            }
            for pr in bug.github_prs.all()
        ],
    })


def presets_data(request):
    data = {}
    for preset_name, preset in PRESETS.items():
        bugs, sources = get_preset_data(preset_name)
        data[preset_name] = {
            "source_count": len(sources),
            "bug_count": bugs.count(),
            "status_counts": {},
        }
        for bug in bugs:
            s = bug.status
            data[preset_name]["status_counts"][s] = (
                data[preset_name]["status_counts"].get(s, 0) + 1
            )
    return JsonResponse(data)
