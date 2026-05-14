import json
import os
import threading
import time
from io import StringIO

from django.core.management import call_command
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .management.progress import read_progress, request_cancel, clear_cancel, check_cancelled
from .models import Bug, BugSource, Preset, BugCorrelation

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
        clear_cancel()
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
            if status.get("error") != "Cancelled":
                status["error"] = None
            write_status(status)
        except Exception as e:
            status = read_status()
            status["running"] = False
            status["output"] = buf.getvalue()
            if not status.get("cancelled"):
                status["error"] = str(e)
            write_status(status)
        finally:
            clear_cancel()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()


def get_preset_data(preset_name):
    try:
        preset = Preset.objects.get(name=preset_name)
    except Preset.DoesNotExist:
        return [], []

    sources = list(preset.sources.all())
    bugs = Bug.objects.filter(sources__in=sources).distinct()
    return bugs, sources


def presets_data(request):
    presets = [
        {"id": p.id, "name": p.name, "source_count": p.sources.count()}
        for p in Preset.objects.all()
    ]
    return JsonResponse(presets, safe=False)


def dashboard(request):
    preset_name = request.GET.get("preset", "Subiquity")
    bugs, sources = get_preset_data(preset_name)

    status_counts = {}
    for bug in bugs:
        s = bug.status
        status_counts[s] = status_counts.get(s, 0) + 1

    context = {
        "presets": {p.name: p for p in Preset.objects.all()},
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
    })


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


@csrf_exempt
def cancel_operation(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    status = read_status()
    if not status.get("running"):
        return JsonResponse({"error": "No operation running"}, status=409)
    request_cancel()
    return JsonResponse({"status": "cancel_requested"})


def operation_status(request):
    status = read_status()
    progress = read_progress()
    return JsonResponse({"status": status, "progress": progress})


def status_page(request):
    return render(request, "dashboard/status.html")


@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
def manage_presets(request, preset_id=None):
    if request.method == "GET":
        if preset_id:
            try:
                preset = Preset.objects.get(id=preset_id)
                return JsonResponse({
                    "id": preset.id,
                    "name": preset.name,
                    "sources": [
                        {
                            "id": s.id,
                            "source_type": s.source_type,
                            "identifier": s.identifier,
                            "name": s.name,
                        }
                        for s in preset.sources.all()
                    ],
                })
            except Preset.DoesNotExist:
                return JsonResponse({"error": "Preset not found"}, status=404)

        presets_list = []
        for p in Preset.objects.all():
            presets_list.append({
                "id": p.id,
                "name": p.name,
                "source_count": p.sources.count(),
            })
        return JsonResponse(presets_list, safe=False)

    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        name = body.get("name", "").strip()
        if not name:
            return JsonResponse({"error": "Name is required"}, status=400)

        sources_data = body.get("sources", [])
        if not sources_data:
            return JsonResponse({"error": "At least one source is required"}, status=400)

        if preset_id:
            try:
                preset = Preset.objects.get(id=preset_id)
            except Preset.DoesNotExist:
                return JsonResponse({"error": "Preset not found"}, status=404)
            if Preset.objects.filter(name=name).exclude(id=preset_id).exists():
                return JsonResponse({"error": "A preset with this name already exists"}, status=409)
            preset.name = name
            preset.save()
        else:
            if Preset.objects.filter(name=name).exists():
                return JsonResponse({"error": "A preset with this name already exists"}, status=409)
            preset = Preset.objects.create(name=name)

        source_ids = []
        for s in sources_data:
            source_type = s.get("source_type", "").strip()
            identifier = s.get("identifier", "").strip()
            if not source_type or not identifier:
                continue
            source, _ = BugSource.objects.get_or_create(
                source_type=source_type,
                identifier=identifier,
                defaults={"name": identifier.split("/")[-1]},
            )
            source_ids.append(source.id)

        preset.sources.set(BugSource.objects.filter(id__in=source_ids))

        return JsonResponse({
            "id": preset.id,
            "name": preset.name,
            "source_count": preset.sources.count(),
        }, status=201 if not preset_id else 200)

    if request.method == "DELETE":
        if not preset_id:
            return JsonResponse({"error": "Preset ID required"}, status=400)
        try:
            preset = Preset.objects.get(id=preset_id)
            preset.delete()
            return JsonResponse({"status": "deleted"})
        except Preset.DoesNotExist:
            return JsonResponse({"error": "Preset not found"}, status=404)

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
    for corr in bug.correlations.prefetch_related("bugs__sources"):
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
