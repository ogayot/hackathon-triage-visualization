import json
import os
import threading
import time
from datetime import datetime

STATUS_FILE = "/tmp/dashboard_ops.json"
PROGRESS_FILE = "/tmp/dashboard_progress.json"

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

def read_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"current": 0, "total": 0, "phase": "", "item": "", "message": "", "last_update": None}

def write_progress(current=0, total=0, phase="", item="", message=""):
    data = {
        "current": current,
        "total": total,
        "phase": phase,
        "item": item,
        "message": message,
        "last_update": datetime.now().isoformat()
    }
    with open(PROGRESS_FILE, "w") as f:
        json.dump(data, f)

def clear_progress():
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


def request_cancel():
    data = read_status()
    data["cancelled"] = True
    data["cancelled_at"] = time.time()
    write_status(data)

def clear_cancel():
    data = read_status()
    data.pop("cancelled", None)
    data.pop("cancelled_at", None)
    write_status(data)

def check_cancelled():
    data = read_status()
    if data.get("cancelled"):
        data["running"] = False
        data["error"] = "Cancelled"
        data.pop("cancelled", None)
        data.pop("cancelled_at", None)
        write_status(data)
        return True
    return False

def reap_stale_cancel():
    data = read_status()
    if data.get("cancelled"):
        if data.get("cancelled_at") is None or time.time() - data["cancelled_at"] > 30:
            data.pop("cancelled", None)
            data.pop("cancelled_at", None)
            data["running"] = False
            write_status(data)
            return True
    return False