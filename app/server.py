"""Web UI backend. It runs the same functions as the CLI and streams what really happens.

Run: uv run uvicorn app.server:app --port 8000
"""

import json
import logging
import queue
import re
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import cli, config, crustdata, cv_files, finder, parse_cv, parse_tender, report
from app.models import FirmResult

STATIC_DIR = config.ROOT_DIR / "static"
MAX_CV_CHARACTERS = 200_000
MAX_TENDER_CHARACTERS = 1_000_000
MAX_FORM_OVERHEAD_BYTES = 64 * 1024
PASTED_CV_ID = "pasted_cv"
MAX_BID_FILES = 6
MAX_FIRM_NAME_CHARACTERS = 200
UPLOAD_EMPLOYMENT = {"full_time", "associate"}
_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")
_LINKEDIN_PROFILE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[^/\s?#]+/?", re.IGNORECASE)
SERVICE_NAMES = {
    "/person/enrich": "Crustdata /person/enrich",
    "/company/identify": "Crustdata /company/identify",
    "/person/search": "Crustdata /person/search",
}

logger = logging.getLogger("truenorth.server")
app = FastAPI(title="TrueNorth")

# Lookup counts, listeners and the report file are shared, so only one run happens at a time.
_run_lock = threading.Lock()

# Proposals awaiting a person's decision. Approval sends only a token, so the browser cannot alter the numbers.
_proposals: dict[str, dict[str, Any]] = {}


class CheckCvRequest(BaseModel):
    cv_text: str = Field(max_length=MAX_CV_CHARACTERS)
    profile_url: str | None = None
    position: str | None = None
    fresh: bool = False


class AuditRequest(BaseModel):
    bid: str = "data/bid/bid.json"
    fresh: bool = False
    use_approved_tender: bool = False


@dataclass
class BidUpload:
    firm: dict[str, str | None]
    uploads: list[cli.UploadedCv]
    fresh: bool


class ReadTenderRequest(BaseModel):
    text: str = Field(max_length=MAX_TENDER_CHARACTERS)
    fresh: bool = False


class FindExpertsRequest(BaseModel):
    position: str
    fresh: bool = False


class ApproveTenderRequest(BaseModel):
    token: str


def _source_label(call: dict[str, Any]) -> str:
    return "live" if call["source"] == "live" else f"cache (fetched {call['fetched_at']})"


def _crustdata_call_event(call: dict[str, Any], expert: str | None) -> dict[str, Any]:
    if call["source"] == "live":
        credits_used = report.ledger_credits(call["path"], call["sha1"])
    else:
        credits_used = 0
    return {
        "service": SERVICE_NAMES.get(call["path"], f"Crustdata {call['path']}"),
        "source": _source_label(call),
        "credits": credits_used,
        "ms": call["ms"],
        "status": call["status"],
        "expert": expert,
    }


def _llm_call_event(call: dict[str, Any], expert: str | None) -> dict[str, Any]:
    return {
        "service": call.get("service", "Claude CV reading"),
        "source": _source_label(call),
        "credits": None,
        "ms": call["ms"],
        "status": call["status"],
        "expert": expert,
    }


def _overall_status(counts: dict[str, int]) -> str | None:
    if not sum(counts.values()):
        return None
    if counts["QUESTIONS"]:
        return "QUESTIONS"
    return "CANNOT_CHECK" if counts["CANNOT_CHECK"] else "CONSISTENT"


def _lookup_summary() -> dict[str, Any]:
    stats = crustdata.run_stats()
    return {"live": stats["live"], "from_cache": stats["from_cache"], "cv_readings": parse_cv.run_stats()}


def _audit_result(run: cli.AuditRun) -> dict[str, Any]:
    counts = report.status_counts(run.entries)
    return {
        "ok": True,
        "kind": "audit",
        "tender_id": run.tender_id,
        "headline": report.headline(run.entries, run.firm_result),
        "counts": counts,
        "overall": _overall_status(counts),
        "questions_total": report.total_questions(run.entries, run.firm_result),
        "firm": report.firm_view(run.bid, run.firm_result, run.full_time),
        "experts": [report.expert_view(entry) for entry in run.entries],
        "letter": report.draft_letter(run.bid, run.entries, run.firm_result),
        "report_url": "/api/report.html",
        "credits": report.credits_since(run.ledger_start),
        "lookups": _lookup_summary(),
        "errors": run.errors,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _cv_result(run: cli.CvRun) -> dict[str, Any]:
    no_firm = FirmResult(status="CONSISTENT", records=[], headcount_range=None, findings=[])
    entries = [run.entry]
    counts = report.status_counts(entries)
    return {
        "ok": True,
        "kind": "check_cv",
        "headline": report.headline(entries, no_firm),
        "counts": counts,
        "overall": _overall_status(counts),
        "questions_total": report.total_questions(entries, no_firm),
        "firm": None,
        "experts": [report.expert_view(run.entry)],
        "letter": report.draft_letter({"firm": {"name": "[Bidder]"}}, entries, no_firm),
        "report_url": None,
        "credits": report.credits_since(run.ledger_start),
        "lookups": _lookup_summary(),
        "errors": [run.lookup_error] if run.lookup_error else [],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _run_job(job: Callable[[cli.Progress], dict[str, Any]], events: "queue.Queue[str | None]") -> None:
    current: dict[str, str | None] = {"expert": None}

    def stage(name: str, expert: str | None, detail: str, **extra: Any) -> None:
        events.put(_sse("stage", {"stage": name, "expert": expert, "detail": detail, **extra}))

    def progress(name: str, expert: str | None, detail: str, external: bool = True) -> None:
        current["expert"] = expert
        if name == "looking_up":
            stage(name, expert, detail, external=external)
        else:
            stage(name, expert, detail)

    crustdata.reset_run()
    parse_cv.reset_run()
    crustdata.call_listener = lambda call: events.put(_sse("call", _crustdata_call_event(call, current["expert"])))
    parse_cv.call_listener = lambda call: events.put(_sse("call", _llm_call_event(call, current["expert"])))
    try:
        result = job(progress)
        stage("done", None, result["headline"], status=result["overall"])
        events.put(_sse("result", result))
    except cli.InputError as exc:
        _report_problem(stage, events, str(exc))
    except parse_tender.TenderParseError as exc:
        _report_problem(stage, events, f"The tender could not be read: {exc}.")
    except crustdata.CrustdataError as exc:
        _report_problem(stage, events, str(exc))
    except cli.CV_READ_ERRORS as exc:
        _report_problem(stage, events, f"CV could not be read: {cli._cv_problem(exc)}.")
    except Exception as exc:  # The browser gets a clean message; the details stay in the server log.
        logger.exception("Run stopped unexpectedly")
        _report_problem(stage, events, f"The run stopped unexpectedly ({type(exc).__name__}). See the server log.")
    finally:
        crustdata.call_listener = None
        parse_cv.call_listener = None
        _run_lock.release()
        events.put(None)


def _report_problem(stage: Callable[..., None], events: "queue.Queue[str | None]", message: str) -> None:
    stage("error", None, message)
    events.put(_sse("result", {"ok": False, "error": message}))


def _stream(job: Callable[[cli.Progress], dict[str, Any]]) -> Response:
    if not _run_lock.acquire(blocking=False):
        return JSONResponse({"error": "Another run is in progress. Please wait for it to finish."}, status_code=409)
    events: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=_run_job, args=(job, events), daemon=True).start()

    def generate() -> Iterator[str]:
        while (item := events.get()) is not None:
            yield item

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _resolve_bid(bid: str) -> Path:
    """Only JSON files directly inside data/bid may be audited."""
    path = (config.ROOT_DIR / bid).resolve() if not Path(bid).is_absolute() else Path(bid).resolve()
    if path.parent != cli.BID_DIR.resolve() or path.suffix != ".json":
        raise cli.InputError(f"{bid}: a bid must be a JSON file directly inside data/bid.")
    return path


def _bid_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(config.ROOT_DIR).as_posix()
    except ValueError:
        return path.name


def _is_bid_file(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "firm" in data and "experts" in data


def _is_cross_site(request: Request) -> bool:
    """Any website can post a form to localhost, so uploads must come from this page."""
    origin = request.headers.get("origin")
    return bool(origin) and urlsplit(origin).netloc != request.headers.get("host")


async def _read_upload(request: Request) -> CheckCvRequest | Response:
    if _is_cross_site(request):
        return JSONResponse({"error": "Uploads are only accepted from this page."}, status_code=403)
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        return JSONResponse({"error": "The upload did not state its size."}, status_code=411)
    if int(length) > cv_files.MAX_FILE_BYTES + MAX_FORM_OVERHEAD_BYTES:
        return JSONResponse({"error": "This file is larger than the 5 MB limit."}, status_code=413)
    async with request.form(max_files=1, max_fields=8) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            return JSONResponse({"error": "No CV file was received."}, status_code=422)
        data = await upload.read(cv_files.MAX_FILE_BYTES + 1)
        if len(data) > cv_files.MAX_FILE_BYTES:
            return JSONResponse({"error": "This file is larger than the 5 MB limit."}, status_code=413)
        try:
            text = await run_in_threadpool(cv_files.extract_text, upload.filename or "", data)
        except cv_files.CvFileError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return CheckCvRequest(
            cv_text=text,
            profile_url=str(form.get("profile_url") or "") or None,
            position=str(form.get("position") or "") or None,
            fresh=form.get("fresh") == "true",
        )


@app.post("/api/check-cv")
async def check_cv(http_request: Request) -> Response:
    """Accepts JSON with cv_text, or a multipart form with a CV file."""
    content_type = http_request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        parsed = await _read_upload(http_request)
        if isinstance(parsed, Response):
            return parsed
        request = parsed
    elif content_type.startswith("application/json"):
        try:
            request = CheckCvRequest.model_validate_json(await http_request.body())
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False, include_context=False)) from exc
    else:
        return JSONResponse({"error": "Send JSON with cv_text, or a multipart form with a file."}, status_code=415)

    def job(progress: cli.Progress) -> dict[str, Any]:
        run = cli.check_one_cv(
            request.cv_text,
            PASTED_CV_ID,
            request.position or None,
            (request.profile_url or "").strip() or None,
            True if request.fresh else None,
            progress,
        )
        progress("writing", PASTED_CV_ID, "Preparing the questions and draft letter")
        return _cv_result(run)

    return _stream(job)


@app.post("/api/audit")
def audit(request: AuditRequest) -> Response:
    def job(progress: cli.Progress) -> dict[str, Any]:
        path = _resolve_bid(request.bid)
        tender_path = parse_tender.APPROVED_PATH if request.use_approved_tender else None
        return _audit_result(cli.audit_bid(True if request.fresh else None, progress, path, tender_path))

    return _stream(job)


def _clean_domain(raw: str) -> str | None:
    """A bare domain from what a person typed, or None if it is not one."""
    text = re.sub(r"^https?://", "", raw.strip().lower()).split("/")[0]
    text = text.removeprefix("www.")
    return text if _DOMAIN.fullmatch(text) else None


def _upload_error(message: str, status: int = 422) -> Response:
    return JSONResponse({"error": message}, status_code=status)


def _form_values(form: Any, name: str, count: int) -> list[str] | None:
    """One value per file, in file order. A field left out entirely means blank for every file."""
    values = form.getlist(name)
    if not values:
        return [""] * count
    if len(values) != count or not all(isinstance(value, str) for value in values):
        return None
    return [value.strip() for value in values]


async def _read_bid_files(form: Any) -> list[tuple[str, bytes]] | Response:
    uploads = form.getlist("file")
    if not uploads or not all(isinstance(upload, UploadFile) for upload in uploads):
        return _upload_error("Please add at least one CV file.")
    for number, upload in enumerate(uploads, 1):
        if Path(upload.filename or "").suffix.lower() not in cv_files.ALLOWED_SUFFIXES:
            return _upload_error(f"File {number}: please upload a .pdf, .docx, .txt or .md file.")
    files = []
    for number, upload in enumerate(uploads, 1):
        data = await upload.read(cv_files.MAX_FILE_BYTES + 1)
        if len(data) > cv_files.MAX_FILE_BYTES:
            return _upload_error(f"File {number} is larger than the 5 MB limit.", 413)
        files.append((upload.filename or "", data))
    return files


def _extract_upload(filename: str, data: bytes) -> tuple[str | None, str | None]:
    """(text, None), or (None, problem) so one unreadable CV does not stop the rest of the bid."""
    try:
        return cv_files.extract_text(filename, data), None
    except cv_files.CvFileError as exc:
        return None, str(exc)


async def _parse_bid_form(form: Any) -> BidUpload | Response:
    firm_name = form.get("firm_name")
    firm_name = firm_name.strip() if isinstance(firm_name, str) else ""
    if not firm_name:
        return _upload_error("Please enter the firm name.")
    if len(firm_name) > MAX_FIRM_NAME_CHARACTERS:
        return _upload_error("The firm name is too long.")
    raw_domain = form.get("firm_domain")
    raw_domain = raw_domain.strip() if isinstance(raw_domain, str) else ""
    domain = _clean_domain(raw_domain) if raw_domain else None
    if raw_domain and domain is None:
        return _upload_error("The firm domain does not look like a domain, for example example.com.")

    files = await _read_bid_files(form)
    if isinstance(files, Response):
        return files
    count = len(files)
    urls, positions, employments = (_form_values(form, name, count) for name in ("profile_url", "position", "employment"))
    if urls is None or positions is None or employments is None:
        return _upload_error("Each file needs its own profile URL, position and employment fields.")
    try:
        known_positions = cli._load_tender()["positions"]
    except cli.InputError as exc:
        return _upload_error(str(exc), 500)
    for number, (url, position, employment) in enumerate(zip(urls, positions, employments), 1):
        if url and not _LINKEDIN_PROFILE.fullmatch(url):
            return _upload_error(f"File {number}: the profile URL must look like https://www.linkedin.com/in/name.")
        if position and position not in known_positions:
            return _upload_error(f"File {number}: unknown position.")
        if employment and employment not in UPLOAD_EMPLOYMENT:
            return _upload_error(f"File {number}: employment must be full-time or associate.")

    uploads = []
    for (filename, data), url, position, employment in zip(files, urls, positions, employments):
        text, problem = await run_in_threadpool(_extract_upload, filename, data)
        uploads.append(cli.UploadedCv(text, problem, url or None, position or None, employment or "full_time"))
    return BidUpload({"name": firm_name, "domain": domain}, uploads, form.get("fresh") == "true")


@app.post("/api/audit-upload")
async def audit_upload(http_request: Request) -> Response:
    """Audit firm details and 1-6 CV files. The bid exists only in memory, for this run."""
    if _is_cross_site(http_request):
        return _upload_error("Uploads are only accepted from this page.", 403)
    if not http_request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        return _upload_error("Send a multipart form with the firm details and CV files.", 415)
    length = http_request.headers.get("content-length", "")
    if not length.isdigit():
        return _upload_error("The upload did not state its size.", 411)
    if int(length) > MAX_BID_FILES * cv_files.MAX_FILE_BYTES + MAX_FORM_OVERHEAD_BYTES:
        return _upload_error(f"This upload is larger than {MAX_BID_FILES} files of 5 MB.", 413)
    try:
        async with http_request.form(max_files=MAX_BID_FILES, max_fields=4 + 3 * MAX_BID_FILES) as form:
            parsed = await _parse_bid_form(form)
    except StarletteHTTPException as exc:
        if "files" in str(exc.detail).lower():
            return _upload_error(f"Please upload at most {MAX_BID_FILES} CV files.")
        return _upload_error("The upload form could not be read.", 400)
    if isinstance(parsed, Response):
        return parsed

    def job(progress: cli.Progress) -> dict[str, Any]:
        run = cli.audit_uploaded(parsed.firm, parsed.uploads, True if parsed.fresh else None, progress)
        return _audit_result(run)

    return _stream(job)


@app.post("/api/find-experts")
def find_experts(http_request: Request, request: FindExpertsRequest) -> Response:
    """Preview: public records that appear to meet a position's minimums. Nobody is contacted."""
    if _is_cross_site(http_request):
        return JSONResponse({"error": "Searches are only accepted from this page."}, status_code=403)
    try:
        known = cli._load_tender()["positions"]
    except cli.InputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    if request.position not in known:
        return JSONResponse({"error": "Unknown position."}, status_code=422)

    def job(progress: cli.Progress) -> dict[str, Any]:
        ledger_start = report.ledger_row_count()
        run = finder.find_candidates(request.position, fresh=True if request.fresh else None, progress=progress)
        progress("writing", None, "Preparing the candidate cards")
        return {
            **finder.result_view(run),
            "credits": report.credits_since(ledger_start),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    return _stream(job)


@app.post("/api/read-tender")
def read_tender(request: ReadTenderRequest) -> Response:
    """Propose positions only. Nothing is used until /api/tender/approve is called."""

    def job(progress: cli.Progress) -> dict[str, Any]:
        progress("reading_tender", None, f"Reading the tender text ({len(request.text)} characters)")
        positions = parse_tender.parse_tender(request.text, True if request.fresh else None)
        progress("writing", None, "Preparing the proposed positions for approval")
        token = uuid.uuid4().hex
        _proposals.clear()
        _proposals[token] = positions
        count = len(positions)
        noun = "position" if count == 1 else "positions"
        return {
            "ok": True,
            "kind": "tender",
            "headline": f"{count} proposed {noun}. Nothing is used until you approve.",
            "overall": None,
            "token": token,
            "positions": positions,
        }

    return _stream(job)


@app.post("/api/tender/approve")
def approve_tender(request: ApproveTenderRequest) -> Response:
    positions = _proposals.get(request.token)
    if positions is None:
        return JSONResponse({"error": "That proposal is no longer available. Please read the tender again."}, status_code=404)
    path = parse_tender.approve(positions, "pasted tender text")
    _proposals.clear()
    return JSONResponse({"ok": True, "approved": _approved_info(path)})


def _approved_info(path: Path) -> dict[str, Any] | None:
    """A summary of the approved tender on disk, if there is a valid one."""
    if not path.exists():
        return None
    try:
        tender = cli._load_approved_tender(path)
    except cli.InputError:
        return None
    return {"positions": len(tender["positions"]), "approved_at": tender.get("_approved_at")}


@app.get("/api/options")
def options() -> Response:
    """Positions and bids for the page's dropdowns, read from the real data files."""
    try:
        tender = cli._load_tender()
    except cli.InputError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    positions = [{"key": key, "name": value.get("name", key)} for key, value in tender["positions"].items()]
    bids = [_bid_label(path) for path in sorted(cli.BID_DIR.glob("*.json")) if _is_bid_file(path)]
    return JSONResponse(
        {
            "positions": positions,
            "bids": bids,
            "approved_tender": _approved_info(parse_tender.APPROVED_PATH),
            "finder_credits": round(finder.DEFAULT_LIMIT * finder.CREDITS_PER_RESULT, 2),
        }
    )


@app.get("/api/report.html")
def report_html() -> Response:
    path = report.OUT_PATH.with_suffix(".html")
    if not path.exists():
        return JSONResponse({"error": "No report yet. Run an audit first."}, status_code=404)
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.get("/")
def index() -> Response:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return JSONResponse({"error": "static/index.html is missing."}, status_code=404)
    return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-store"})
