import json
import os
import urllib.request

from datetime import datetime, timezone

from dashboard.models import AnalysisConfig, Bug

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("AI_MODEL", "deepseek/deepseek-v4-flash:free")


def _openrouter_request(messages, max_tokens, api_key):
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    data = json.dumps({
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    resp = urllib.request.urlopen(req, timeout=60)
    result = json.loads(resp.read().decode())
    return result["choices"][0]["message"]["content"]


def _fetch_lp_bug_context(external_id):
    from launchpadlib.launchpad import Launchpad

    bug_id = external_id.replace("lp:", "")
    launchpad = Launchpad.login_anonymously("bug-dashboard", "production", version="devel")
    lp_bug = launchpad.bugs[int(bug_id)]

    comments_text = []
    total_bytes = len(lp_bug.description or "")

    try:
        for comment in lp_bug.comments:
            text = comment.content or ""
            comments_text.append({
                "author": comment.owner.display_name if comment.owner else "unknown",
                "text": text,
            })
            total_bytes += len(text)
    except Exception:
        pass

    attachments_info = []
    try:
        for att in lp_bug.attachments:
            size = att.data.size if hasattr(att, "data") and hasattr(att.data, "size") else 0
            attachments_info.append({
                "name": att.title,
                "size_bytes": size,
            })
            total_bytes += size
    except Exception:
        pass

    return {
        "title": lp_bug.title,
        "description": lp_bug.description or "",
        "tags": list(lp_bug.tags),
        "comments": comments_text,
        "attachments": attachments_info,
        "total_bytes": total_bytes,
        "url": lp_bug.web_link,
    }


def _build_prompt(context, include_attachments):
    parts = [f"## Bug: {context['title']}\n"]
    parts.append(f"**URL:** {context['url']}\n")
    if context["tags"]:
        parts.append(f"**Tags:** {', '.join(context['tags'])}\n")
    parts.append(f"\n### Description\n{context['description']}\n")

    if context["comments"]:
        parts.append("### Comments\n")
        for i, c in enumerate(context["comments"], 1):
            parts.append(f"**{i}. {c['author']}:**\n{c['text']}\n")

    if context["attachments"]:
        parts.append("### Attachments\n")
        for a in context["attachments"]:
            if include_attachments and a["size_bytes"] > 0:
                parts.append(f"- {a['name']} ({_fmt_size(a['size_bytes'])})\n")
            else:
                parts.append(f"- {a['name']} ({_fmt_size(a['size_bytes'])}) — content not included\n")

    return "\n".join(parts)


def _fmt_size(b):
    if b < 1024:
        return f"{b} B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1024 / 1024:.1f} MB"


def analyze_bug(external_id, decision=None):
    bug = Bug.objects.filter(external_id=external_id).first()
    if not bug:
        return {"status": "error", "error": "Bug not found"}

    if not external_id.startswith("lp:"):
        return {"status": "error", "error": "AI analysis currently only supports Launchpad bugs (lp:...)"}

    config = AnalysisConfig.objects.first()
    if not config:
        config = AnalysisConfig.objects.create()

    try:
        context = _fetch_lp_bug_context(external_id)
    except Exception as e:
        return {"status": "error", "error": f"Failed to fetch bug data from Launchpad: {e}"}

    if not config.api_key:
        return {"status": "error", "error": "OpenRouter API key not configured. Set it in ⚙️ AI Analysis Settings."}

    api_key = config.api_key

    include_attachments = True
    if decision == "skip_attachments":
        include_attachments = False
    elif decision is None and context["total_bytes"] > config.auto_analyze_max_bytes:
        return {
            "status": "needs_decision",
            "total_bytes": context["total_bytes"],
            "attachment_count": len(context["attachments"]),
            "comment_count": len(context["comments"]),
            "attachments": [
                {"name": a["name"], "size_bytes": a["size_bytes"]}
                for a in context["attachments"]
            ],
        }

    prompt = _build_prompt(context, include_attachments)

    try:
        summary = _openrouter_request(
            [
                {"role": "system", "content": config.system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=config.max_tokens,
            api_key=api_key,
        )
    except Exception as e:
        return {"status": "error", "error": str(e)}

    bug.analysis = summary
    bug.analysis_updated_at = datetime.now(timezone.utc)
    bug.save(update_fields=["analysis", "analysis_updated_at"])

    return {
        "status": "ok",
        "summary": summary,
        "cached": False,
        "total_bytes": context["total_bytes"],
    }
