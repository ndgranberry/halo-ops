import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

LOGS_DIR = Path(__file__).parent / "run_logs"
LOGS_DIR.mkdir(exist_ok=True)

app = FastAPI()

PYTHON = str(Path(sys.executable))
# Use module invocation so relative imports resolve correctly within packages.
# Scripts are run as: python -m agent_scout.agent_scout [args]
SCRIPT_SCOUT = ["-u", "-m", "agent_scout.agent_scout"]
SCRIPT_ROBOSCOUT = ["-u", "-m", "roboscout.roboscout_query_gen"]
ROOT_DIR = str(Path(__file__).parent)


class RunRequest(BaseModel):
    type: str  # partnering_request | request_with_examples | scraped_list | company_list
    request_id: Optional[int] = None
    request_looking_for: Optional[str] = None
    request_use_case: Optional[str] = None
    request_sois: Optional[str] = None
    request_partner_types: Optional[str] = None
    request_requirements: Optional[str] = None
    request_out_of_scope: Optional[str] = None
    input_sheet: Optional[str] = None
    sheet_tab: Optional[str] = None
    input_csv: Optional[str] = None
    companies: Optional[str] = None
    output_sheet: Optional[str] = None
    output_csv: Optional[str] = None
    min_score: Optional[float] = 0.3
    resume: Optional[str] = None


class ResumeRequest(BaseModel):
    run_id: str


@app.post("/run")
def run(req: RunRequest):
    cmd = [PYTHON] + SCRIPT_SCOUT

    if req.resume:
        cmd += ["--resume", req.resume]
    else:
        cmd += ["--type", req.type]
        if req.request_id:
            cmd += ["--request-id", str(req.request_id)]
        if req.request_looking_for:
            cmd += ["--request-looking-for", req.request_looking_for]
        if req.request_use_case:
            cmd += ["--request-use-case", req.request_use_case]
        if req.request_sois:
            cmd += ["--request-sois", req.request_sois]
        if req.request_partner_types:
            cmd += ["--request-partner-types", req.request_partner_types]
        if req.request_requirements:
            cmd += ["--request-requirements", req.request_requirements]
        if req.request_out_of_scope:
            cmd += ["--request-out-of-scope", req.request_out_of_scope]
        if req.input_sheet:
            cmd += ["--input-sheet", req.input_sheet]
        if req.sheet_tab:
            cmd += ["--sheet-tab", req.sheet_tab]
        if req.input_csv:
            cmd += ["--input-csv", req.input_csv]
        if req.companies:
            cmd += ["--companies", req.companies]
        if req.output_sheet:
            cmd += ["--output-sheet", req.output_sheet]
        if req.output_csv:
            cmd += ["--output-csv", req.output_csv]
        if req.min_score is not None:
            cmd += ["--min-score", str(req.min_score)]

    log_file = LOGS_DIR / f"scout_{req.request_id or 'manual'}.log"
    with open(log_file, "w") as f:
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).parent),
            stdout=f,
            stderr=subprocess.STDOUT,
        )

    return {"status": "started", "pid": proc.pid, "log": str(log_file), "cmd": " ".join(cmd)}


class RoboScoutRequest(BaseModel):
    request_id: Optional[int] = None
    looking_for: Optional[str] = None
    use_case: Optional[str] = None
    sois: Optional[str] = None
    output_sheet: Optional[str] = None
    output_csv: Optional[str] = None


@app.post("/run-roboscout")
def run_roboscout(req: RoboScoutRequest):
    cmd = [PYTHON] + SCRIPT_ROBOSCOUT

    if req.request_id:
        cmd += ["--request-id", str(req.request_id)]
    if req.looking_for:
        cmd += ["--looking-for", req.looking_for]
    if req.use_case:
        cmd += ["--use-case", req.use_case]
    if req.sois:
        cmd += ["--sois", req.sois]
    if req.output_sheet:
        cmd += ["--output-sheet", req.output_sheet]
    if req.output_csv:
        cmd += ["--output-csv", req.output_csv]

    log_file = LOGS_DIR / f"roboscout_{req.request_id or 'manual'}.log"
    with open(log_file, "w") as f:
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path(__file__).parent),
            stdout=f,
            stderr=subprocess.STDOUT,
        )

    return {"status": "started", "pid": proc.pid, "log": str(log_file), "cmd": " ".join(cmd)}


@app.get("/logs/{filename}")
def get_logs(filename: str, lines: int = 50):
    log_file = LOGS_DIR / filename
    if not log_file.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    all_lines = log_file.read_text().splitlines()
    return {"file": filename, "total_lines": len(all_lines), "lines": all_lines[-lines:]}


@app.get("/logs")
def list_logs():
    files = sorted(LOGS_DIR.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {"logs": [f.name for f in files]}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/kill/{pid}")
def kill(pid: int):
    """Kill a specific process by PID."""
    try:
        import os as _os
        import signal
        _os.kill(pid, signal.SIGTERM)
        return {"killed": pid}
    except ProcessLookupError:
        return {"error": f"PID {pid} not found", "killed": None}
    except Exception as e:
        return {"error": str(e), "killed": None}


@app.post("/kill-all")
def kill_all():
    """Kill all running scout and roboscout subprocesses."""
    result = subprocess.run(
        ["pkill", "-f", "-e", "agent_scout.agent_scout|roboscout.roboscout_query_gen"],
        capture_output=True,
        text=True,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@app.get("/processes")
def processes():
    """List currently running scout/roboscout processes."""
    result = subprocess.run(
        ["pgrep", "-af", "agent_scout.agent_scout|roboscout.roboscout_query_gen"],
        capture_output=True,
        text=True,
    )
    return {"processes": result.stdout.strip().splitlines()}


OUTPUT_DIRS = [Path("/tmp"), Path(ROOT_DIR) / "output"]


@app.get("/output/{filename}")
def get_output(filename: str):
    """Download a result file from /tmp or the output directory."""
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    for d in OUTPUT_DIRS:
        path = d / filename
        if path.exists() and path.is_file():
            return FileResponse(path, filename=filename)
    raise HTTPException(status_code=404, detail="File not found")


@app.get("/output")
def list_outputs():
    """List CSV files in known output locations."""
    files = []
    for d in OUTPUT_DIRS:
        if d.exists():
            for p in d.glob("*.csv"):
                files.append({
                    "name": p.name,
                    "path": str(p),
                    "size_bytes": p.stat().st_size,
                    "modified": p.stat().st_mtime,
                })
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"outputs": files}


@app.post("/restart")
def restart():
    """Restart the FastAPI service via systemd. The endpoint returns before the
    kill signal arrives; systemd auto-restarts the unit."""
    subprocess.Popen(["systemctl", "restart", "agent-scout"])
    return {"status": "restarting"}


@app.get("/config")
def config():
    """Return the server's model configuration and key presence (no values)."""
    keys_to_check = [
        "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
        "EXA_API_KEY", "APOLLO_API_KEY", "SEMANTIC_SCHOLAR_API_KEY",
        "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD",
        "N8N_ENRICHMENT_WEBHOOK_URL", "GOOGLE_SERVICE_ACCOUNT_JSON",
    ]
    models = [
        "SCOUT_SCORE_MODEL", "SCOUT_PLANNER_MODEL",
        "ROBOSCOUT_MODEL", "ROBOSCOUT_THINKING_LEVEL",
        "GEPA_TASK_MODEL", "GEPA_REFLECTION_MODEL",
    ]
    git_result = subprocess.run(
        ["git", "log", "-1", "--oneline"],
        cwd=ROOT_DIR, capture_output=True, text=True,
    )
    return {
        "commit": git_result.stdout.strip(),
        "keys_set": {k: bool(os.getenv(k)) for k in keys_to_check},
        "models": {k: os.getenv(k) for k in models},
    }


@app.get("/status/{pid}")
def status(pid: int):
    """Check whether a PID is still running."""
    try:
        os.kill(pid, 0)
        return {"pid": pid, "status": "running"}
    except ProcessLookupError:
        return {"pid": pid, "status": "not_running"}
    except PermissionError:
        return {"pid": pid, "status": "running"}  # exists, owned by another user


@app.delete("/logs")
def clear_logs():
    """Delete all log files in run_logs/."""
    deleted = []
    for f in LOGS_DIR.glob("*.log"):
        f.unlink()
        deleted.append(f.name)
    return {"deleted": deleted}


@app.post("/pull")
def pull():
    """Force a git pull so changes merged to main are picked up without waiting for cron."""
    result = subprocess.run(
        ["git", "pull"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@app.get("/version")
def version():
    """Return the current git commit on the server."""
    result = subprocess.run(
        ["git", "log", "-1", "--oneline"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
    )
    return {"commit": result.stdout.strip()}
