"""Running a PDF import job (``Job.type == "import"``): a scan task, page tasks, a build task.

``import_scan`` sorts the pages (free; a page with no text and no image is rendered, and one
that is not blank, e.g. text drawn as outlines, is a scan) and makes one ``import_pages`` task
per 25 pages. With
the AI engine and pages to OCR it also puts an estimate on the job and marks it
``awaiting_estimate``: the job's ``max_cost`` is 0, so the first AI page pauses it at the cost
cap until the owner confirms a ``max_cost`` (``confirm_import``) or turns AI down
(``use_tesseract``).

An ``import_pages`` task reads the pages of its range that have no saved result, one page at a
time: rendered, read and saved before the next one, so no more than one page image is held.
The free pages come first (text layers, pictures, blank pages, and scans when Tesseract is the
engine), then the AI pages. Text pages are never held back by the cost cap: a task that meets
the cap at its first AI page while another queued page task has not read its free pages trades
ranges with that task and reads those free pages; the job pauses only when no free page is
left unread. Once the owner has confirmed, the cap never pauses the job: every AI page met
after it was reached is read by Tesseract (``ai_fallbacks["cost_cap"]``). An AI page the model
cannot read (a refusal, an unusable or empty reply, an error) is read by Tesseract too. A page that
cannot be read at all is saved as ``failed``; a scanned page with nothing on it but dust is
saved as ``blank`` without asking either engine. Why the AI did not read a page is counted in
``stats["ai_fallbacks"]``. Pictures are kept as JPEGs of at most 1600 px. The last page task
makes ``import_build``.

``import_build`` assembles the saved pages into a DOCX and fills the book from it (for a
translation: a hidden source book, and the translated book made from it), then completes the
job. A task that keeps failing fails the import (see ``jerome.jobs.worker._record_failure``).
"""

import io
import logging
import threading
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from jerome.core.books import populate_from_docx
from jerome.core.translation import make_translation
from jerome.jobs import worker as w
from jerome.models import ModelRefusal, Provider, Registry
from jerome.models.base import BilledError
from jerome.models.registry import PassConfig
from jerome.orm import ACTIVE_JOB_STATUSES, Book, Job, JobTask
from jerome.pdfimport.assemble import assemble, report
from jerome.pdfimport.docx_build import build_docx
from jerome.pdfimport.model import PageBlock, PageKind, PageResult, from_json, to_json
from jerome.pdfimport.ocr_ai import ai_page, estimate_ocr
from jerome.pdfimport.ocr_tesseract import TesseractMissing, tesseract_page
from jerome.pdfimport.reader import PageReadError, classify_pages, read_text_page, run_isolated
from jerome.pdfimport.render import render_page
from jerome.storage import Storage

log = logging.getLogger(__name__)

PAGES_PER_TASK = 25
OCR_PASS = "ocr"
PICTURE_PX = 1600  # the long side of a kept picture, at most
PICTURE_QUALITY = 85
# Blank page test (see is_blank), tuned for the 300 dpi render: ink is this much darker than the
# paper; a mark of at most DUST_PX pixels within DUST_SIZE x DUST_SIZE (under 0.85 mm) is
# dust. A 10 pt letter or hyphen has more pixels; a hairline is longer.
_INK_STEP = 25
_LIGHT_PAPER = 200  # a page whose median grey is darker than this is not blank paper
DUST_PX = 24
DUST_SIZE = 10
_MAX_INK = 60_000  # more ink than this is never a blank page
_CELL = 8
Chosen = tuple[Provider, PassConfig]


def ocr_model(registry: Registry, local_only: bool) -> Chosen | None:
    """The model that reads scanned pages: the ocr pass's, else the ai pass's. None for a
    local-only book, which never uses the AI engine."""
    if local_only:
        return None
    return registry.for_pass("ocr", local_only=False) or registry.for_pass("ai", local_only=False)


def configured_ocr_model(registry: Registry) -> str:
    """The configured name of the model that reads pages, to price a billed call by."""
    for pass_name in ("ocr", "ai"):
        model = (registry.settings.models.get(pass_name) or {}).get("model")
        if model:
            return model
    return ""


def needs_ocr(kind: PageKind, reocr: bool) -> bool:
    return kind == "scan" or (kind == "scan_with_layer" and reocr)


def _engine(job: Job, registry: Registry, local_only: bool) -> Chosen | None:
    """The AI model the job reads scans with now, or None when Tesseract reads them."""
    if job.passes[:1] != ["ai"]:
        return None
    return ocr_model(registry, local_only)


def run_import_task(
    sf: w.SessionFactory,
    task_id: int,
    plan: w._Plan,
    deps: w.WorkerDeps,
    paid: w._Paid,
    stop: threading.Event | None = None,
) -> None:
    storage = deps.storage
    if storage is None:
        raise RuntimeError("this worker has no storage, so it cannot run import jobs")
    if plan.kind == "import_scan":
        _scan(sf, task_id, plan, deps, storage)
    elif plan.kind == "import_pages":
        _pages(sf, task_id, plan, deps, paid, storage, stop)
    else:
        _build(sf, task_id, plan, deps, storage)


def _requeue(s: Session, task_id: int) -> None:
    s.execute(
        update(JobTask)
        .where(JobTask.id == task_id, JobTask.status != "done")
        .values(status="queued", not_before=None)
    )


# --- scan -----------------------------------------------------------------------------


def _scan(
    sf: w.SessionFactory, task_id: int, plan: w._Plan, deps: w.WorkerDeps, storage: Storage
) -> None:
    pdf = storage.read_source_pdf(plan.book_id)
    with sf() as s:
        pages = int(s.get(Job, plan.job_id).stats.get("pages") or 0)
    try:
        kinds: list[PageKind] = run_isolated(classify_pages, pdf, timeout=120 + pages)
    except PageReadError as exc:  # each page is still rendered and read on its own
        log.warning("the pages of book %s could not be sorted (%s)", plan.book_id, exc)
        kinds = []
    if len(kinds) != pages:  # pdfminer and pdfium disagree: every page pdfium counts is read
        log.warning("book %s: %s pages sorted of %s", plan.book_id, len(kinds), pages)
        kinds = (kinds + ["scan"] * pages)[:pages]
    kinds = [_seen_blank(pdf, page) if kind == "blank" else kind for page, kind in enumerate(kinds)]
    del pdf
    note = None
    with sf() as s:
        job, task = w._lock_job_task(s, plan.job_id, task_id)
        if task.status == "done" or job.status not in w.ACTIVE:  # cancelled while it read
            _requeue(s, task_id)
            s.commit()
            return
        stats = dict(job.stats or {})
        ocr_pages = sum(1 for kind in kinds if needs_ocr(kind, bool(stats.get("reocr"))))
        stats.update(kinds=list(kinds), ocr_pages=ocr_pages)
        chosen = _engine(job, deps.registry, plan.local_only)
        if chosen is not None and ocr_pages:
            amount = estimate_ocr(deps.registry, ocr_pages, model=chosen[1].model)
            job.estimate = {"ocr_pages": ocr_pages, "cost": str(amount)}
            stats["awaiting_estimate"] = True
        job.stats = stats
        for first in range(0, len(kinds), PAGES_PER_TASK):
            last = min(first + PAGES_PER_TASK, len(kinds)) - 1
            s.add(JobTask(job_id=job.id, kind="import_pages", pages=[first, last]))
        if not kinds:
            s.add(JobTask(job_id=job.id, kind="import_build"))
        note = w._complete_task(s, job, task, deps)
        s.commit()
    w._send(note, deps)


def _seen_blank(pdf: bytes, page: int) -> PageKind:
    """A page with no text and no image is blank only if it looks blank: text drawn as
    shapes (outlines) is a scan to read. A page that cannot be rendered is left to the
    page task, which reports it."""
    try:
        png = render_page(pdf, page)
    except PageReadError:
        return "scan"
    return "blank" if is_blank(png) else "scan"


# --- pages ----------------------------------------------------------------------------


def _pages(
    sf: w.SessionFactory,
    task_id: int,
    plan: w._Plan,
    deps: w.WorkerDeps,
    paid: w._Paid,
    storage: Storage,
    stop: threading.Event | None,
) -> None:
    pdf = storage.read_source_pdf(plan.book_id)
    while True:  # once per range: a task that trades ranges goes on with its new one
        with sf() as s:
            job = s.get(Job, plan.job_id)
            first, last = s.get(JobTask, task_id).pages
            kinds: list[PageKind] = list(job.stats["kinds"])
            reocr = bool(job.stats.get("reocr"))
            variant = job.stats["variant"]
            ai = _engine(job, deps.registry, plan.local_only) is not None
        todo = [p for p in range(first, last + 1) if not storage.has_page(plan.book_id, p)]
        free = [p for p in todo if not (ai and needs_ocr(kinds[p], reocr))]
        paid_pages = [p for p in todo if p not in free]
        for page in free:
            if stop is not None and stop.is_set():
                w._requeue_for_stop(sf, task_id, plan)
                return
            result = _read_free(pdf, page, kinds[page], reocr, variant)
            if not _save(sf, task_id, plan, deps, paid, storage, result, None):
                return
        verdict = _read_paid(sf, task_id, plan, deps, paid, storage, stop, pdf, paid_pages)
        if verdict == "stop":
            return
        if verdict == "done":
            _finish_pages(sf, task_id, plan, deps)
            return


def _read_paid(
    sf: w.SessionFactory,
    task_id: int,
    plan: w._Plan,
    deps: w.WorkerDeps,
    paid: w._Paid,
    storage: Storage,
    stop: threading.Event | None,
    pdf: bytes,
    pages: list[int],
) -> Literal["done", "stop", "swapped"]:
    """Read the pages that go to the AI engine; see the module docstring."""
    first = True
    for page in pages:
        if stop is not None and stop.is_set():
            w._requeue_for_stop(sf, task_id, plan)
            return "stop"
        deps = w.refreshed(deps)
        paid.deps = deps
        verdict, chosen, variant = _gate(sf, task_id, plan, deps, first)
        if verdict in ("stop", "swapped"):
            return verdict
        first = False
        fallback = None
        if verdict == "ai":
            result, fallback = _read_ai(pdf, page, chosen, variant, paid)
        elif verdict == "capped":
            result = _read_scan(pdf, page, variant, after="the job's cost cap was reached")
            fallback = "cost_cap" if result.source != "blank" else None
        else:  # the engine became Tesseract while the job ran (use_tesseract)
            result = _read_scan(pdf, page, variant)
        if not _save(sf, task_id, plan, deps, paid, storage, result, fallback):
            return "stop"
    return "done"


def _gate(
    sf: w.SessionFactory, task_id: int, plan: w._Plan, deps: w.WorkerDeps, first: bool
) -> tuple[str, Chosen | None, str]:
    """May the next AI page be read? Returns (verdict, model, the PDF's variant):
    ``ai``; ``tesseract`` (the job no longer uses AI); ``capped`` (the cost cap was reached
    after the owner confirmed, or in this task: Tesseract reads the page); ``swapped`` (before
    the confirm, the cap met at the task's first AI page, so the task took a range whose free
    pages are unread); ``stop`` (the job is not running, or it paused for the estimate: the
    task goes back to the queue)."""
    note = None
    with sf() as s:
        job = w._lock_job(s, plan.job_id)
        variant = job.stats["variant"]
        if job.status not in w.ACTIVE:
            _requeue(s, task_id)
            s.commit()
            return "stop", None, variant
        chosen = _engine(job, deps.registry, plan.local_only)
        if chosen is None:
            s.commit()
            return "tesseract", None, variant
        if job.cost_actual < job.max_cost:
            s.commit()
            return "ai", chosen, variant
        if not first or not job.stats.get("awaiting_estimate"):
            s.commit()  # confirmed: the cap never pauses the import, Tesseract reads on
            return "capped", None, variant
        unread = s.scalars(
            select(JobTask)
            .where(
                JobTask.job_id == job.id,
                JobTask.id != task_id,
                JobTask.kind == "import_pages",
                JobTask.status == "queued",
                JobTask.chunks_done == 0,
            )
            .order_by(JobTask.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        task = s.scalars(
            select(JobTask)
            .where(JobTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        if unread is not None:  # chunks_done 1: the range's free pages are read
            task.pages, unread.pages = list(unread.pages), list(task.pages)
            task.chunks_done, unread.chunks_done = 0, 1
            s.commit()
            return "swapped", None, variant
        task.chunks_done = 1
        job.status = "paused_cost_cap"
        note = w._notification(job, "job.paused")
        _requeue(s, task_id)
        s.commit()
    w._send(note, deps)
    return "stop", None, variant


def _failed(page: int, error: str) -> PageResult:
    return PageResult(page, "failed", [], error=error[:500])


def is_blank(png: bytes) -> bool:
    """True for a scanned page with no ink but dust. It errs towards "not blank": a page kept
    by mistake costs one white picture, a page dropped by mistake loses text. The pixel
    thresholds assume the 300 dpi render (render_page's default).

    Paper is the median grey, so grey paper is paper too; a page whose median is darker than
    _LIGHT_PAPER (a black cover or part-title page) is never blank. Ink is any pixel clearly
    darker than the paper. Ink pixels are joined into 8-connected marks;
    a mark of at most DUST_PX pixels within DUST_SIZE x DUST_SIZE is dust. The page is blank
    only when every mark is dust. A 1 px hairline or one 10 pt letter is not dust; a page
    with more than _MAX_INK ink pixels is not blank without looking further."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        gray = image.convert("L")
    histogram = gray.histogram()
    half, seen = sum(histogram) / 2, 0
    paper = 255
    for value, n in enumerate(histogram):
        seen += n
        if seen >= half:
            paper = value
            break
    if paper < _LIGHT_PAPER:  # dark paper: its light marks would not count as ink
        return False
    limit = paper - _INK_STEP
    ink_pixels = sum(histogram[:limit])
    if ink_pixels == 0:
        return True
    if ink_pixels > _MAX_INK:
        return False
    mask = gray.point(lambda v: 255 if v < limit else 0)
    return all(_is_dust(mark) for mark in _marks(mask))


def _ink_points(mask) -> set[tuple[int, int]]:
    """The (x, y) of every ink pixel: cells of 8 x 8 with any ink are found in C, then only
    those cells are read pixel by pixel."""
    cells = mask.reduce(_CELL)  # any ink in a cell leaves at least 255 / 64, so 4
    points: set[tuple[int, int]] = set()
    width = cells.width
    for i, value in enumerate(cells.tobytes()):  # mode L: one byte per pixel
        if not value:
            continue
        cx, cy = (i % width) * _CELL, (i // width) * _CELL
        box = (cx, cy, min(cx + _CELL, mask.width), min(cy + _CELL, mask.height))
        cell = mask.crop(box)
        for j, pixel in enumerate(cell.tobytes()):
            if pixel:
                points.add((cx + j % cell.width, cy + j // cell.width))
    return points


def _marks(mask):
    """The 8-connected marks of ink, one set of points each; it stops early at the first
    mark that is not dust, as the caller does."""
    points = _ink_points(mask)
    while points:
        start = points.pop()
        mark, todo = [start], [start]
        while todo:
            x, y = todo.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    near = (x + dx, y + dy)
                    if near in points:
                        points.remove(near)
                        mark.append(near)
                        todo.append(near)
            if len(mark) > DUST_PX:
                break
        yield mark


def _is_dust(mark: list[tuple[int, int]]) -> bool:
    if len(mark) > DUST_PX:
        return False
    xs, ys = [x for x, _ in mark], [y for _, y in mark]
    return max(xs) - min(xs) < DUST_SIZE and max(ys) - min(ys) < DUST_SIZE


def _render(pdf: bytes, page: int, *, scan: bool = True) -> tuple[bytes | None, PageResult | None]:
    """The page's image, or instead the page's result when it is failed, or (a ``scan``
    only) blank. A picture page is never tested for blankness: pdfminer saw a picture there,
    and a pale one must not be lost."""
    try:
        png = render_page(pdf, page)
    except PageReadError as exc:
        return None, _failed(page, str(exc))
    if scan and is_blank(png):
        return None, PageResult(page, "blank", [])
    return png, None


def _read_free(pdf: bytes, page: int, kind: PageKind, reocr: bool, variant: str) -> PageResult:
    if kind == "blank":
        return PageResult(page, "blank", [])
    if kind == "text" or (kind == "scan_with_layer" and not reocr):
        try:
            return run_isolated(read_text_page, pdf, page)
        except PageReadError as exc:
            return _failed(page, str(exc))
    png, done = _render(pdf, page, scan=kind != "image")
    if done is not None:
        return done
    if kind == "image":
        block = PageBlock("image", image=png, image_type="image/png", top=0.0, bottom=1.0)
        return PageResult(page, "image", [block])
    return _tesseract(png, page, variant)


def _tesseract(png: bytes, page: int, variant: str, *, after: str | None = None) -> PageResult:
    """Tesseract's reading of a page. ``after`` says why the AI did not read it; then
    Tesseract is the safety net and a page it cannot read either is failed. As the engine, a
    missing Tesseract fails the task (every page would fail)."""
    language = "de" if variant.startswith("de") else "en"
    try:
        return tesseract_page(png, page, language)
    except TesseractMissing as exc:
        if after is None:
            raise
        return _failed(page, f"{after}; {exc}")
    except Exception as exc:  # noqa: BLE001 - one page Tesseract cannot read fails alone
        detail = f"{type(exc).__name__}: {exc}"
        return _failed(page, detail if after is None else f"{after}; Tesseract: {detail}")


def _read_scan(pdf: bytes, page: int, variant: str, *, after: str | None = None) -> PageResult:
    png, done = _render(pdf, page)
    if done is not None:
        return done
    return _tesseract(png, page, variant, after=after)


def _fallback_reason(exc: Exception) -> str:
    if isinstance(exc, ModelRefusal):
        return "refusal"
    if isinstance(exc, BilledError):
        return "billed_error"
    return "error"


def _read_ai(
    pdf: bytes, page: int, chosen: Chosen, variant: str, paid: w._Paid
) -> tuple[PageResult, str | None]:
    """The AI's reading of a page, or Tesseract's when the AI gives nothing usable, with the
    reason it fell back (None when it did not). Every billed call goes to ``paid.calls``
    before anything else can raise."""
    png, done = _render(pdf, page)
    if done is not None:
        return done, None
    provider, cfg = chosen
    try:
        result, usage = ai_page(provider, cfg, png, page, variant)
    except Exception as exc:  # noqa: BLE001 - refused, unusable, or errors after retries
        billed = w.billed_from(exc)
        if billed is not None:
            paid.calls.append(billed)
        log.warning("the AI could not read page %s (%s); Tesseract reads it", page + 1, exc)
        after = f"the AI failed ({type(exc).__name__})"
        return _tesseract(png, page, variant, after=after), _fallback_reason(exc)
    paid.calls.append(w.Billed(usage, cfg.model, cfg.provider))
    if not result.blocks:
        return _tesseract(png, page, variant, after="the AI found nothing on the page"), "empty"
    return result, None


def _as_jpeg(data: bytes) -> bytes:
    """A picture as an RGB JPEG whose long side is at most PICTURE_PX; transparency on white.
    Bytes Pillow cannot read are kept as they are."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.mode in ("RGBA", "LA", "P", "PA"):
                rgba = image.convert("RGBA")
                picture = Image.new("RGB", rgba.size, "white")
                picture.paste(rgba, mask=rgba.getchannel("A"))
            else:
                picture = image.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return data
    picture.thumbnail((PICTURE_PX, PICTURE_PX), Image.Resampling.LANCZOS)  # never enlarges
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=PICTURE_QUALITY)
    return out.getvalue()


def _compact(result: PageResult) -> PageResult:
    """The result with every picture re-encoded by _as_jpeg, so the build holds no
    full-page PNGs."""
    for block in result.blocks:
        if block.kind == "image" and block.image is not None:
            jpeg = _as_jpeg(block.image)
            if jpeg is not block.image:
                block.image, block.image_type = jpeg, "image/jpeg"
    return result


def _save(
    sf: w.SessionFactory,
    task_id: int,
    plan: w._Plan,
    deps: w.WorkerDeps,
    paid: w._Paid,
    storage: Storage,
    result: PageResult,
    fallback: str | None,
) -> bool:
    """Keep one page's result with the spend of its model calls, and why the AI did not read
    it (``fallback``). False: stop the task (the job was cancelled or failed; the task goes
    back to the queue for a resume)."""
    configured = configured_ocr_model((paid.deps or deps).registry)
    result = _compact(result)
    with sf() as s:
        job, task = w._lock_job_task(s, plan.job_id, task_id)
        for billed in paid.calls:
            w._usage_row(s, job, billed, (paid.deps or deps).registry, configured, OCR_PASS)
        if not storage.has_page(plan.book_id, result.page):
            storage.save_page(plan.book_id, result.page, to_json(result))
            job.stats = w._bump(job.stats, pages_done=1)
            if fallback is not None:
                counts = w._bump(job.stats.get("ai_fallbacks") or {}, **{fallback: 1})
                job.stats = {**job.stats, "ai_fallbacks": counts}
        go = job.status in ACTIVE_JOB_STATUSES  # a pause does not stop free pages
        if not go and task.status != "done":
            task.status = "queued"
            task.not_before = None
        s.commit()
    paid.clear()
    return go


def _finish_pages(sf: w.SessionFactory, task_id: int, plan: w._Plan, deps: w.WorkerDeps) -> None:
    """Mark the page task done; the last one makes the build task."""
    with sf() as s:
        job, task = w._lock_job_task(s, plan.job_id, task_id)
        if task.status == "done":
            s.commit()
            return
        task.status = "done"
        task.finished_at = deps.now()
        task.error = None
        task.chunks_done = 1
        s.flush()
        left = s.scalar(
            select(func.count())
            .select_from(JobTask)
            .where(
                JobTask.job_id == job.id,
                (JobTask.kind == "import_build")
                | ((JobTask.kind == "import_pages") & (JobTask.status != "done")),
            )
        )
        if left == 0:
            s.add(JobTask(job_id=job.id, kind="import_build"))
        s.commit()


# --- build ----------------------------------------------------------------------------


def _build(
    sf: w.SessionFactory, task_id: int, plan: w._Plan, deps: w.WorkerDeps, storage: Storage
) -> None:
    with sf() as s:
        job = s.get(Job, plan.job_id)
        count = int(job.stats.get("pages") or len(job.stats.get("kinds") or []))
        variant = job.stats["variant"]
        target = job.stats.get("target_variant")
        fallbacks = dict(job.stats.get("ai_fallbacks") or {})
        title = s.get(Book, plan.book_id).title
    pages: list[PageResult] = []
    for page in range(count):
        saved = storage.read_page(plan.book_id, page)
        if saved is None:
            raise RuntimeError(f"page {page + 1} of the PDF was never read")
        pages.append(from_json(saved))
    blocks = assemble(pages)
    no_text = not any(b.kind in ("heading", "paragraph", "footnote") for b in blocks)
    summary = {**report(pages), "ai_fallbacks": fallbacks, "no_text": no_text}
    docx = build_docx(blocks, title=title, language=variant)
    del blocks
    del pages
    note = None
    with sf() as s:
        job, task = w._lock_job_task(s, plan.job_id, task_id)
        if task.status == "done" or job.status not in w.ACTIVE:  # cancelled while it built
            _requeue(s, task_id)
            s.commit()
            return
        book = s.get(Book, plan.book_id)
        if book.work == "translate" and target:
            source = Book(
                title=book.title,
                format="docx",
                local_only=book.local_only,
                original_filename=book.original_filename,
                auto_apply=dict(book.auto_apply or {}),
                is_source=True,
            )
            s.add(source)
            s.flush()
            populate_from_docx(s, storage, source, docx, variant)
            make_translation(
                s, storage, source=source, book=book, target_variant=target, keep_existing=True
            )
            book = s.get(Book, plan.book_id)
            book.title = book.translated_title or book.title  # as import_for_translation
        else:
            populate_from_docx(s, storage, book, docx, variant, keep_existing=True)
        book = s.get(Book, plan.book_id)
        book.import_status = None
        book.import_error = None
        book.import_report = summary
        note = w._complete_task(s, job, task, deps)
        s.commit()
    w._send(note, deps)
