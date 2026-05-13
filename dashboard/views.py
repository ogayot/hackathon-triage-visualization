import json
import os
import threading
import time
from io import StringIO

from django.core.management import call_command
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from .models import Bug, BugSource
from .presets import PRESETS

STATUS_FILE = "/tmp/dashboard_ops.json"


def read_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"running": False, "operation": None, "output": "", "error": None}


def write_status(data):
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f)


def run_command_thread(command, args=None):
    def target():
        buf = StringIO()
        try:
            kwargs = {"stdout": buf, "stderr": buf}
            if args:
                kwargs.update(args)
            call_command(command, **kwargs)
            output = buf.getvalue()
            status = read_status()
            status["running"] = False
            status["output"] = output
            status["error"] = None
            write_status(status)
        except Exception as e:
            status = read_status()
            status["running"] = False
            status["output"] = buf.getvalue()
            status["error"] = str(e)
            write_status(status)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()


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


@csrf_exempt
def run_operation(request, operation_name):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    if operation_name not in ("fetch_bugs", "correlate_prs"):
        return JsonResponse({"error": f"Unknown operation: {operation_name}"}, status=400)

    status = read_status()
    if status.get("running"):
        return JsonResponse({"error": "An operation is already running"}, status=409)

    write_status({
        "running": True,
        "operation": operation_name,
        "output": "",
        "error": None,
        "started_at": time.time(),
    })

    args = {}
    if operation_name == "correlate_prs":
        args = {"max_commits": 2000}

    run_command_thread(operation_name, args)

    return JsonResponse({"status": "started", "operation": operation_name})


def operation_status(request):
    status = read_status()
    return JsonResponse(status)
