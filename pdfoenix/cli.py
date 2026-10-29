"""pdfoenix command-line orchestrator.

The CLI is a thin control-flow layer: every subcommand resolves a :class:`Book`,
calls one deterministic stage (or one model adapter), persists the result, and
returns an exit code.  It never keeps pipeline state in memory across commands.

Command groups
--------------
* pipeline stages: ``init transcribe merge verify check build inspect`` —
  plain verbs, one outcome each;
* status: ``watch`` (read-only dashboard);
* reports: ``report cost|quality|risk`` (``quality`` writes ``expt/report.md``);
* experiments: ``expt show|run`` (only ``run`` spends money).

Cross-cutting rules (documented in the README handbook):
* scope: ``--chapter`` / ``--pages`` restrict work to that scope and never
  touch anything outside it;
* idempotency: every stage skips completed units; ``--force`` redoes them
  (including the cached inputs of those units);
* outputs: ``--out`` names build artifacts; inputs are overridden by
  positionals (``inspect EPUB``, ``init TARGET``);
* exit codes: 0 ok, 1 "ran fine but found issues" (check/inspect/expt run),
  2 usage or runtime error.
"""
import argparse
import sys
import time
from pathlib import Path

from pdfoenix import __version__, pipeline
from pdfoenix import combo as combo_mod
from pdfoenix import expt as expt_mod
from pdfoenix.assets.crop import crop_figures
from pdfoenix.core.batches import validate_batch_text
from pdfoenix.core.book import Book
from pdfoenix.core.build import build_book
from pdfoenix.core.check import check_book
from pdfoenix.core.merge import merge_book, merge_chapter
from pdfoenix.core.pages import batch_ranges, parse_pages
from pdfoenix.core.render import render_pages
from pdfoenix.core.scout import scout_book
from pdfoenix.display import print_table
from pdfoenix.doctor import collect_findings
from pdfoenix.inspect import inspect_epub
from pdfoenix.report import chapter_for_unit, format_risk_report, risk_report, summarize_costs
from pdfoenix.scaffold import scaffold_book
from pdfoenix.transcribe.opencode import OpenCodeError
from pdfoenix.verify.audit import apply_hits, global_audit
from pdfoenix.verify.content import content_for_pages, plan_verify
from pdfoenix.verify.sampling import compute_page_risks, sample_pages
from pdfoenix.watch import watch as run_watch


# ── Entry point ─────────────────────────────────────────────────────────
def main(argv=None) -> int:
    """Parse arguments, dispatch one subcommand, and return an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except Exception as exc:
        print(f"pdfoenix: {exc}", file=sys.stderr)
        if getattr(args, "debug", False):
            raise
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdfoenix")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("-C", "--book", default=".", help="book directory (default: .)")
    parser.add_argument("--debug", action="store_true", help="show a traceback on failure")
    sub = parser.add_subparsers(dest="command", required=True)

    # ── Pipeline stages ─────────────────────────────────────────────────
    init = sub.add_parser("init", help="scaffold a new book")
    init.add_argument("target", nargs="?", help="book directory to create (overrides -C)")
    init.add_argument("--pdf", help="source PDF to copy into source/")
    init.add_argument("--title")
    init.add_argument("--author")
    init.add_argument("--page-offset", type=int, default=0,
                      help="PDF page = printed page + offset")
    init.add_argument("--force", action="store_true",
                      help="overwrite existing scaffold files")
    init.set_defaults(func=cmd_init)

    transcribe = sub.add_parser("transcribe", help="transcribe page batches")
    _add_scope(transcribe, pages=True)
    _add_force(transcribe, "redo existing batches")
    transcribe.add_argument("--rescan", action="store_true",
                            help="rebuild the chapter layout cache first")
    transcribe.add_argument("--rerender", action="store_true",
                            help="re-render selected page images first")
    transcribe.set_defaults(func=cmd_transcribe)

    merge = sub.add_parser("merge", help="merge batches into the manuscript")
    _add_scope(merge)
    merge.add_argument("--no-assets", action="store_true",
                       help="skip figure cropping after the merge")
    merge.add_argument("--recrop", action="store_true",
                       help="crop existing figures again")
    merge.set_defaults(func=cmd_merge)

    verify = sub.add_parser("verify", help="sample and verify transcribed pages")
    _add_scope(verify, pages=True)
    verify.add_argument("--full", action="store_true",
                        help="verify every candidate page (bypass sampling)")
    _add_force(verify, "redo verified pages (and their page images)")
    verify.add_argument("--audit", choices=("off", "dry", "apply"),
                        help="global-audit pass (default: dry, or [review].global_audit)")
    verify.set_defaults(func=cmd_verify)

    check = sub.add_parser("check", help="validate the manuscript")
    check.add_argument("--all", action="store_true",
                       help="also audit batches, figures, progress and link targets")
    check.set_defaults(func=cmd_check)

    build = sub.add_parser("build", help="assemble Markdown and build the EPUB")
    build.add_argument("--out", default="book.epub", help="output name in build/")
    build.set_defaults(func=cmd_build)

    inspect = sub.add_parser("inspect", help="run structural EPUB checks")
    inspect.add_argument("epub", nargs="?", help="EPUB path (default: newest in build/)")
    inspect.set_defaults(func=cmd_inspect)

    # ── Status ──────────────────────────────────────────────────────────
    watch = sub.add_parser("watch", help="read-only live dashboard")
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--once", action="store_true", help="print one frame and exit")
    watch.set_defaults(func=cmd_watch)

    # ── Reports ─────────────────────────────────────────────────────────
    report = sub.add_parser("report", help="cost, quality and risk reports")
    report_sub = report.add_subparsers(dest="report_command", required=True)

    cost = report_sub.add_parser("cost", help="token and cost totals")
    cost.add_argument("--by", choices=("model", "chapter"), default="model")
    cost.set_defaults(func=cmd_report_cost)

    quality = report_sub.add_parser(
        "quality", help="write experiment cost-quality comparison to expt/report.md")
    quality.add_argument("--all", action="store_true",
                         help="include combos no longer in their batch")
    quality.set_defaults(func=cmd_report_quality)

    risk = report_sub.add_parser("risk", help="formula/page risk distributions")
    risk.set_defaults(func=cmd_report_risk)

    # ── Experiments (only ``run`` spends) ───────────────────────────────
    expt = sub.add_parser("expt", help="model-combination experiments")
    expt_sub = expt.add_subparsers(dest="expt_command", required=True)

    show = expt_sub.add_parser("show", help="display batches and combos (read-only)")
    _add_expt_selection(show)
    show.set_defaults(func=cmd_expt_show)

    run = expt_sub.add_parser("run", help="execute one experiment round (spends)")
    _add_expt_selection(run)
    run.add_argument("-m", "--message", default="", help="batch note")
    _add_force(run, "re-run finished cells")
    run.set_defaults(func=cmd_expt_run)
    return parser


def _add_scope(parser, *, pages: bool = False) -> None:
    """The uniform scope options: restrict work, never touch outside."""
    parser.add_argument("--chapter", help="only this chapter, e.g. ch09")
    if pages:
        parser.add_argument("--pages", help="explicit page range, e.g. 19-30,40")


def _add_force(parser, help_text: str) -> None:
    parser.add_argument("--force", action="store_true", help=help_text)


def _add_expt_selection(parser) -> None:
    parser.add_argument("--batch", type=int, help="select one batch")
    parser.add_argument("--combo", nargs="*", type=int, metavar="ID",
                        help="select combos; with 'run' they form the batch")
    parser.add_argument("--all", action="store_true",
                        help="select every declared combo")


# ── Pipeline stage commands ─────────────────────────────────────────────
def cmd_init(args) -> int:
    root = args.target or args.book
    book = scaffold_book(
        root, pdf=args.pdf, title=args.title, author=args.author,
        page_offset=args.page_offset, force=args.force)
    print(f"initialized {book.root}")
    return 0




def cmd_transcribe(args) -> int:
    book = _book(args)
    if getattr(args, "rescan", False):
        scout_book(book)
    ranges = pipeline.ensure_chapter_ranges(book)
    if args.chapter:
        ranges = {name: value for name, value in ranges.items() if name == args.chapter}
    if not ranges:
        raise ValueError(f"chapter not found: {args.chapter}")
    wanted = set(parse_pages(args.pages, book.pdf_page_count())) if args.pages else None
    if wanted is not None and not wanted:
        raise ValueError(f"no pages match: {args.pages}")
    if getattr(args, "rerender", False):
        pages = [
            page for start, end in ranges.values()
            for page in range(start, end + 1)
            if wanted is None or page in wanted
        ]
        render_pages(book.pdf(), pages,
                     book.config["pipeline"]["render_dpi"], book.pages_dir)
    size = book.config["pipeline"]["batch_pages"]
    attempts = int(book.config["transcriber"]["attempts"])
    delay = float(book.config["transcriber"]["retry_delay"])
    telemetry = pipeline.production_ledger(book)
    transcriber = pipeline.make_transcriber(book, telemetry)
    done = 0
    try:
        for chapter, (start, end) in ranges.items():
            directory = book.batches_dir / chapter
            directory.mkdir(parents=True, exist_ok=True)
            _write_task_file(book, chapter, start, end)
            for first, last in batch_ranges(start, end, size, wanted):
                target = directory / f"p{first}-{last}.md"
                if target.exists() and not args.force:
                    continue
                unit = target.stem
                agent = f"{chapter}/{unit}"
                telemetry.emit_event("dispatch", stage="transcribe",
                                     chapter=chapter, unit=unit)
                telemetry.emit_event("agent_start", agent=agent)
                failure = None
                for attempt in range(1, attempts + 1):
                    try:
                        text = pipeline.transcribe_window(
                            book, transcriber, first=first, last=last,
                            chapter=chapter)
                        candidate = text.rstrip() + "\n"
                        issues = validate_batch_text(
                            candidate, first, last,
                            location=f"{chapter}/{target.name}")
                    except OpenCodeError as exc:
                        failure = str(exc)
                    else:
                        errors = [issue for issue in issues
                                  if issue.severity == "error"]
                        if not errors:
                            target.write_text(candidate, encoding="utf-8")
                            telemetry.emit_event("agent_done", agent=agent)
                            pipeline.set_progress(
                                book, f"transcribe/{chapter}/{unit}",
                                status="done", path=str(target))
                            done += 1
                            print(f"transcribed {chapter}/{target.name}")
                            break
                        failure = "; ".join(
                            f"{issue.kind} {issue.location}: {issue.message}"
                            for issue in errors)
                    if attempt < attempts:
                        telemetry.emit_event(
                            "agent_retry", agent=agent, attempt=attempt,
                            error=failure)
                        if delay > 0:
                            time.sleep(delay * attempt)
                else:
                    telemetry.emit_event("agent_fail", agent=agent,
                                         error=failure)
                    pipeline.set_progress(
                        book, f"transcribe/{chapter}/{unit}", status="failed",
                        path=str(target), error=failure)
                    raise ValueError(
                        f"transcription failed validation for {agent}: {failure}")
    finally:
        _close(transcriber)
        telemetry.close()
    print(f"transcribed {done} batch(es)")
    return 0


def cmd_merge(args) -> int:
    book = _book(args)
    telemetry = pipeline.production_ledger(book)
    try:
        if args.chapter:
            summaries = {args.chapter: merge_chapter(
                book, args.chapter, telemetry=telemetry)}
        else:
            summaries = merge_book(book, telemetry=telemetry)
    finally:
        telemetry.close()
    for chapter, summary in summaries.items():
        print(f"{chapter}: {len(summary.sections)} sections, "
              f"{summary.formulas} formulas, {summary.figures} figures, "
              f"{summary.tables} tables")
        for warning in summary.warnings:
            print(f"  warning: {warning}")

    if not args.no_assets:
        crops = crop_figures(book, force=args.recrop, chapter=args.chapter)
        if crops.cropped or crops.warnings:
            print(f"assets: cropped {len(crops.cropped)}, "
                  f"skipped {len(crops.skipped)}, "
                  f"{len(crops.warnings)} warning(s)")
            for warning in crops.warnings:
                print(f"  warning: {warning}")
    return 0


def cmd_verify(args) -> int:
    book = _book(args)
    if not any(book.manuscript_dir.glob("ch*")):
        print("warning: no merged manuscript; run 'pdfoenix merge' first "
              "(sampling cannot use formula risk and the audit scans nothing)")
    selected = _verify_pages(book, args)
    if not selected:
        print("no pages selected for verification")
        return 0
    content, kept = content_for_pages(book, selected)
    if not kept:
        raise ValueError("no transcribed batch content for the selected pages; "
                         "run 'pdfoenix transcribe' first")
    if len(kept) != len(selected):
        missing = sorted(set(selected) - set(kept))
        print(f"warning: no batch content for pages {missing}; skipping them")
    images = pipeline.verify_images(book, kept, force=args.force)
    chunks = plan_verify(images, content,
                         int(book.config["pipeline"]["verify_chunk_size"]))
    ranges = pipeline.ensure_chapter_ranges(book)
    attempts = int(book.config["transcriber"]["attempts"])
    delay = float(book.config["transcriber"]["retry_delay"])

    telemetry = pipeline.production_ledger(book)
    transcriber = pipeline.make_transcriber(book, telemetry)
    pipeline.clear_verify_plan(book)

    plan = []
    for chunk in chunks:
        chapter = chapter_for_unit(chunk.unit_id, ranges)
        unit = f"verify/{chapter}/{chunk.unit_id}"
        pipeline.set_progress(book, unit, status="pending", pages=chunk.pages)
        plan.append((unit, chunk))

    verified = corrections_count = 0
    try:
        for unit, chunk in plan:
            telemetry.emit_event("dispatch", stage="verify", unit=unit)
            telemetry.emit_event("agent_start", agent=unit)
            pipeline.set_progress(book, unit, status="running", pages=chunk.pages)
            try:
                corrections = _verify_chunk_with_retries(
                    transcriber, chunk, unit=unit, attempts=attempts,
                    delay=delay, telemetry=telemetry)
            except Exception as exc:
                telemetry.emit_event("agent_fail", agent=unit, error=str(exc))
                pipeline.set_progress(book, unit, status="failed", pages=chunk.pages)
                raise
            telemetry.emit_event("agent_done", agent=unit)
            pipeline.save_corrections(book, telemetry, corrections)
            for page in chunk.pages:
                pipeline.set_progress(book, f"verify/p{page}", status="done")
            pipeline.set_progress(book, unit, status="done", pages=chunk.pages)
            verified += len(chunk.pages)
            corrections_count += len(corrections)
            print(f"verified {unit} ({len(chunk.pages)} page(s), "
                  f"{len(corrections)} correction(s))")
    finally:
        _close(transcriber)
        telemetry.close()

    print(f"verified {verified} page(s) in {len(plan)} batch(es); "
          f"{corrections_count} correction(s)")

    audit = args.audit or ("dry" if book.config["review"].get("global_audit", True)
                           else "off")
    if audit != "off":
        _run_audit(book, apply=audit == "apply")
    return 0



def cmd_check(args) -> int:
    book = _book(args)
    report = check_book(book)
    doctor_errors = 0
    if args.all:
        findings = collect_findings(book)
        # Manuscript link suggestions are extra information; plain dangling
        # links are already reported by check_book below.
        shown = [finding for finding in findings
                 if finding.area in {"batch", "figure", "progress"}
                 or (finding.area == "link"
                     and ("resolvable as" in finding.message
                          or "candidates:" in finding.message))]
        for finding in shown:
            print(f"{finding.severity}: {finding.area}: {finding.location}: "
                  f"{finding.message}")
        doctor_errors = sum(1 for finding in findings
                            if finding.severity == "error")
    for issue in report.issues:
        print(f"{issue.severity}: {issue.location}: {issue.message}")
    if args.all:
        print(f"doctor: {doctor_errors} error(s)")
    if report.normalized:
        print(f"normalized: {len(report.normalized)} safe math rewrite(s)")
    print(f"check: {len(report.issues)} issue(s), {report.footnotes} footnote(s)")
    return 0 if report.ok and doctor_errors == 0 else 1



def cmd_build(args) -> int:
    book = _book(args)
    path = build_book(book, out_name=args.out)
    print(f"built {path}")
    return 0


def cmd_inspect(args) -> int:
    book = _book(args)
    epub = Path(args.epub) if args.epub else _latest_epub(book)
    report = inspect_epub(epub)
    print(f"inspect {report.path}")
    for key, value in sorted(report.counts.items()):
        print(f"  {key}: {value}")
    for issue in report.issues:
        print(f"  issue: {issue}")
    print("inspect: ok" if report.ok else f"inspect: {len(report.issues)} issue(s)")
    return 0 if report.ok else 1


# ── Status ──────────────────────────────────────────────────────────────
def cmd_watch(args) -> int:
    run_watch(_book(args), interval=args.interval, iterations=1 if args.once else None)
    return 0


# ── Reports (read-only) ─────────────────────────────────────────────────
def cmd_report_cost(args) -> int:
    rows = summarize_costs(_book(args), by=args.by)
    print_table(rows, ("key", "calls", "input_text_tokens",
                       "input_vision_tokens", "output_tokens", "cost_usd"))
    return 0


def cmd_report_quality(args) -> int:
    book = _book(args)
    expt_mod.ensure_workspace(book)
    if not combo_mod.combos(book):
        print("no combos yet; add them with 'pdfoenix init --combo <id>'")
        return 0
    print_table(expt_mod.report_rows(book, include_hidden=args.all),
                expt_mod.CELL_COLUMNS)
    print(f"report: {expt_mod.write_report(book)}")
    return 0


def cmd_report_risk(args) -> int:
    print(format_risk_report(risk_report(_book(args))))
    return 0


# ── Experiments ─────────────────────────────────────────────────────────
def cmd_expt_show(args) -> int:
    book = _book(args)
    expt_mod.ensure_workspace(book)
    if not _declared_combos(book):
        return 0
    combo_ids = _selected_combos(args, book)
    if args.batch is not None and (combo_ids or args.all):
        print(f"preview: batch_{args.batch} would use combos "
              f"{' '.join(combo_ids)} (config unchanged; 'expt run' applies)")
        print_table(expt_mod.combo_rows(book, combo_ids), expt_mod.CELL_COLUMNS)
        return 0
    if args.batch is not None:
        print_table(expt_mod.cell_rows(book, args.batch), expt_mod.CELL_COLUMNS)
        return 0
    if combo_ids:
        print_table(expt_mod.combo_rows(book, combo_ids), expt_mod.CELL_COLUMNS)
        return 0
    print_table(expt_mod.overview_rows(book), expt_mod.OVERVIEW_COLUMNS)
    return 0


def cmd_expt_run(args) -> int:
    book = _book(args)
    expt_mod.ensure_workspace(book)
    if not _declared_combos(book):
        return 0
    return expt_mod.run_expt(book, batch_no=args.batch,
                             combo_ids=_selected_combos(args, book),
                             message=args.message, force=args.force)


def _declared_combos(book: Book) -> bool:
    if combo_mod.combos(book):
        return True
    print("no combos yet; declare [[expt.combo]] in config.toml")
    return False


def _selected_combos(args, book: Book) -> list[str]:
    if args.all:
        return [str(value) for value in sorted(combo_mod.combos(book))]
    return [str(value) for value in (args.combo or [])]


# ── Helpers ─────────────────────────────────────────────────────────────
def _book(args) -> Book:
    return Book(args.book)


def _write_task_file(book: Book, chapter: str, start: int, end: int) -> None:
    path = book.batches_dir / chapter / "TASK.md"
    if path.exists():
        return
    path.write_text(
        f"# {chapter} transcription task\n\n"
        f"PDF pages {start}-{end}. Use the preset task book and emit "
        "`<!-- page:N -->` before every page.\n",
        encoding="utf-8")


def _verify_pages(book: Book, args) -> list[int]:
    risks = compute_page_risks(book)
    if not risks and not args.pages:
        # Sampling is page-image based; transcribe normally rendered them, but
        # verify must stay usable after a cache wipe now that render is internal.
        ranges = pipeline.ensure_chapter_ranges(book)
        pages = [page for start, end in ranges.values()
                 for page in range(start, end + 1)]
        pipeline.render_missing(book, pages)
        risks = compute_page_risks(book)
    if args.pages:
        selected = parse_pages(args.pages, book.pdf_page_count())
    elif args.full:
        selected = sorted(risks)
    else:
        selected = sample_pages(book).selected_pages
    if args.chapter:
        bounds = pipeline.ensure_chapter_ranges(book).get(args.chapter)
        if bounds is None:
            raise ValueError(f"chapter not found: {args.chapter}")
        selected = [page for page in selected if bounds[0] <= page <= bounds[1]]
    if not args.force:
        done = pipeline.verified_pages(book)
        selected = [page for page in selected if page not in done]
    return selected


def _verify_chunk_with_retries(transcriber, chunk, *, unit: str, attempts: int,
                               delay: float, telemetry):
    """Verify one page chunk, retrying transient model/network failures.

    Each attempt is its own billed call and is recorded in the ledger by the
    adapter; ``agent_retry`` events make the retries visible in ``watch``.
    """
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return transcriber.verify(list(chunk.images), chunk.content)
        except OpenCodeError as exc:
            last = exc
            if attempt >= attempts:
                break
            telemetry.emit_event("agent_retry", agent=unit, attempt=attempt,
                                 error=str(exc))
            if delay > 0:
                time.sleep(delay * attempt)
    raise last


def _run_audit(book: Book, *, apply: bool) -> None:
    report = global_audit(book)
    print(f"audit: {report.hit_count} hit(s) for {len(report.patterns)} pattern(s)")
    for hit in report.hits[:20]:
        print(f"  {hit.path}:{hit.line_no}: {hit.matched} -> {hit.replacement}")
    if report.hit_count > 20:
        print(f"  ... {report.hit_count - 20} more")
    if apply and report.hits:
        changed = apply_hits(book, report.hits, dry_run=False)
        print(f"audit: rewrote {len(changed)} file(s)")


def _latest_epub(book: Book) -> Path:
    candidates = sorted(book.build_dir.glob("*.epub"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"no EPUB in {book.build_dir}")
    return candidates[0]


def _close(resource) -> None:
    """Close ``resource`` when it offers close(); test doubles may omit it."""
    close = getattr(resource, "close", None)
    if close is not None:
        close()


if __name__ == "__main__":
    sys.exit(main())
