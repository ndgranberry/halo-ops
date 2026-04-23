import subprocess
import sys
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

LOGS_DIR = Path(__file__).parent / "run_logs"
LOGS_DIR.mkdir(exist_ok=True)

app = FastAPI()

PYTHON = str(Path(sys.executable))
# Use module invocation so relative imports resolve correctly within packages.
# Scripts are run as: python -m agent_scout.agent_scout [args]
SCRIPT_SCOUT = str(Path(__file__).parent / "agent_scout" / "agent_scout.py")
SCRIPT_ROBOSCOUT = str(Path(__file__).parent / "roboscout" / "roboscout_query_gen.py")
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
    cmd = [PYTHON, SCRIPT_SCOUT]

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
    cmd = [PYTHON, SCRIPT_ROBOSCOUT]

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
