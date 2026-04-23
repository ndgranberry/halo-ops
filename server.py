import subprocess
import sys
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

PYTHON = str(Path(sys.executable))
SCRIPT = str(Path(__file__).parent / "agent_scout.py")


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
    cmd = [PYTHON, SCRIPT]

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

    proc = subprocess.Popen(
        cmd,
        cwd=str(Path(__file__).parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    return {"status": "started", "pid": proc.pid, "cmd": " ".join(cmd)}


@app.get("/health")
def health():
    return {"status": "ok"}
