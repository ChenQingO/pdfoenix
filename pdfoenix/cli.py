"""pdfoenix command-line orchestrator.

The CLI is a thin control-flow layer: every subcommand resolves a :class:`Book`,
calls one deterministic stage (or one model adapter), persists the result, and
returns an exit code.  It never keeps pipeline state in memory across commands.
Run ``pdfoenix <command> --help`` for the per-command options.
"""
import argparse
import dataclasses
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz

from pdfoenix import expt as expt_mod
from pdfoenix.assets.crop import crop_figures, render_verify_pages
from pdfoenix.calibrate import calibration_report, format_report
from pdfoenix.core.book import Book
from pdfoenix.core.build import build_book
from pdfoenix.core.check import check_book
from pdfoenix.core.merge import merge_book, merge_chapter
from pdfoenix.core.pages import batch_ranges, parse_pages
from pdfoenix.core.render import render_pages
from pdfoenix.core.scout import scout_book
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.inspect import inspect_epub
from pdfoenix.report import summarize_costs
from pdfoenix.scaffold import scaffold_book
from pdfoenix.transcribe.opencode import OpenCodeTranscriber
from pdfoenix.transcribe.protocol import Correction
from pdfoenix.verify.audit import apply_hits, global_audit
from pdfoenix.verify.content import content_for_pages
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
    parser.add_argument("-C", "--book", default=".", help="book directory (default: .)")
    parser.add_argument("--debug", action="store_true", help="show a traceback on failure")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="scaffold a new book")
    init.add_argument("target", nargs="?", help="book directory (overrides -C)")
    init.add_argument("--pdf", help="source PDF to copy into source/")
    init.add_argument("--title")
    init.add_argument("--author")
    init.add_argument("--page-offset", type=int, default=0,
                      help="PDF page = printed page + offset")
    init.add_argument("--combo", nargs="*", type=int, metavar="ID",
                      help="adopt existing combos or create missing ones")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    scout = sub.add_parser("scout", help="scan the PDF text layer into state/scout.json")
    scout.set_defaults(func=cmd_scout)

    render = sub.add_parser("render", help="render transcription pages at render_dpi")
    render.add_argument("--pages", help="page range, e.g. 19-30,40")
    render.add_argument("--force", action="store_true")
    render.set_defaults(func=cmd_render)

    transcribe = sub.add_parser("transcribe", help="transcribe page batches")
    transcribe.add_argument("--chapter", help="only this chapter, e.g. ch09")
    transcribe.add_argument("--pages", help="explicit page range, e.g. 19-30,40")
    transcribe.add_argument("--force", action="store_true", help="redo existing batches")
    transcribe.set_defaults(func=cmd_transcribe)

    merge = sub.add_parser("merge", help="merge batches into the manuscript")
    merge.add_argument("--chapter", help="only this chapter")
    merge.set_defaults(func=cmd_merge)

    verify = sub.add_parser("verify", help="sample and verify transcribed pages")
    verify.add_argument("--full", action="store_true", help="verify every candidate page")
    verify.add_argument("--chapter", help="only this chapter")
    verify.add_argument("--pages", help="explicit page range, bypassing sampling")
    verify.add_argument("--force", action="store_true")
    verify.add_argument("--no-audit", action="store_true")
    verify.add_argument("--apply-audit", action="store_true",
                        help="apply global-audit replacements (otherwise dry-run)")
    verify.set_defaults(func=cmd_verify)

    assets = sub.add_parser("assets", help="crop registered figures")
    assets.add_argument("--force", action="store_true")
    assets.add_argument("--no-auto", action="store_true",
                        help="do not auto-detect missing crop boxes")
    assets.set_defaults(func=cmd_assets)

    check = sub.add_parser("check", help="validate the manuscript")
    check.set_defaults(func=cmd_check)

    build = sub.add_parser("build", help="assemble Markdown and build the EPUB")
    build.add_argument("--out", default="book.epub")
    build.set_defaults(func=cmd_build)

    inspect = sub.add_parser("inspect", help="run structural EPUB checks")
    inspect.add_argument("--epub", help="EPUB path (default: newest in build/)")
    inspect.set_defaults(func=cmd_inspect)

    watch = sub.add_parser("watch", help="read-only live dashboard")
    watch.add_argument("--interval", type=float, default=1.0)
    watch.add_argument("--once", action="store_true")
    watch.set_defaults(func=cmd_watch)

    report = sub.add_parser("report", help="cost summary")
    report.add_argument("--by", choices=("model", "chapter"), default="model")
    report.set_defaults(func=cmd_report)

    expt = sub.add_parser(
        "expt", help="model-combination experiments (display; --run to spend)")
    expt.add_argument("--batch", type=int, help="select one batch (detail view)")
    expt.add_argument("--combo", nargs="*", type=int, metavar="ID",
                      help="select combos; with --run they form the batch")
    expt.add_argument("-a", "--all", action="store_true",
                      help="select every declared combo")
    expt.add_argument("--report", action="store_true",
                      help="print the comparison and write expt/report.md")
    expt.add_argument("-m", "--message", default="", help="batch note")
    expt.add_argument("--run", action="store_true", help="execute (spend)")
    expt.add_argument("--force", action="store_true", help="re-run finished cells")
    expt.set_defaults(func=cmd_expt)

    calibrate = sub.add_parser("calibrate", help="report risk distributions")
    calibrate.set_defaults(func=cmd_calibrate)
    return parser


# ── Commands ────────────────────────────────────────────────────────────
def cmd_init(args) -> int:
    root = args.target or args.book
    book = scaffold_book(
        root, pdf=args.pdf, title=args.title, author=args.author,
        page_offset=args.page_offset, force=args.force)
    print(f"initialized {book.root}")
    for combo_id in args.combo or []:
        expt_mod.ensure_combo(book, combo_id)
        print(f"combo {combo_id}: ready (active)")
    return 0


def cmd_scout(args) -> int:
    book = _book(args)
    data = scout_book(book)
    print(f"scout: {len(data['chapters'])} chapters, {len(data['sections'])} sections, "
          f"{len(data['figures'])} figures, {len(data['tables'])} tables")
    return 0


def cmd_render(args) -> int:
    book = _book(args)
    total = _pdf_pages(book)
    pages = parse_pages(args.pages, total) if args.pages else list(range(1, total + 1))
    missing = [page for page in pages
               if args.force or not book.page_image(page).exists()]
    if missing:
        render_pages(book.pdf(), missing, book.config["pipeline"]["render_dpi"],
                     book.pages_dir)
    print(f"rendered {len(missing)} page(s); {len(pages)} selected")
    return 0


def cmd_transcribe(args) -> int:
    book = _book(args)
    ranges = _chapter_ranges(book)
    if args.chapter:
        ranges = {name: value for name, value in ranges.items() if name == args.chapter}
    if not ranges:
        raise ValueError(f"chapter not found: {args.chapter}")
    wanted = set(parse_pages(args.pages, _pdf_pages(book))) if args.pages else None
    if wanted is not None and not wanted:
        raise ValueError(f"no pages match: {args.pages}")
    size = book.config["pipeline"]["batch_pages"]
    telemetry = _production_ledger(book)
    transcriber = _make_transcriber(book, telemetry)
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
                pages = list(range(first, last + 1))
                _render_missing(book, pages)
                images = [book.page_image(page) for page in pages]
                unit = target.stem
                task = (f"Chapter {chapter}, PDF pages {first}-{last}. "
                        "Transcribe these pages in order.")
                telemetry.emit_event("dispatch", stage="transcribe",
                                     chapter=chapter, unit=unit)
                telemetry.emit_event("agent_start", agent=f"{chapter}/{unit}")
                try:
                    text = transcriber.transcribe(images, task)
                except Exception:
                    telemetry.emit_event("agent_fail", agent=f"{chapter}/{unit}")
                    _set_progress(book, f"transcribe/{chapter}/{unit}",
                                  status="failed", path=str(target))
                    raise
                target.write_text(text.rstrip() + "\n", encoding="utf-8")
                telemetry.emit_event("agent_done", agent=f"{chapter}/{unit}")
                _set_progress(book, f"transcribe/{chapter}/{unit}",
                              status="done", path=str(target))
                done += 1
                print(f"transcribed {chapter}/{target.name}")
    finally:
        _close(transcriber)
        telemetry.close()
    print(f"transcribed {done} batch(es)")
    return 0


def cmd_merge(args) -> int:
    book = _book(args)
    telemetry = Telemetry(book.root)
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
    return 0


def cmd_verify(args) -> int:
    book = _book(args)
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
    render_verify_pages(book, kept, force=args.force)
    images = [book.verify_dir / f"pdfpage_{page:03d}.png" for page in kept]

    telemetry = _production_ledger(book)
    transcriber = _make_transcriber(book, telemetry)
    try:
        corrections = transcriber.verify(images, content)
        _save_corrections(book, telemetry, corrections)
        for page in kept:
            _set_progress(book, f"verify/p{page}", status="done")
        print(f"verified {len(kept)} page(s); {len(corrections)} correction(s)")
    finally:
        _close(transcriber)
        telemetry.close()

    if not args.no_audit and book.config["review"].get("global_audit", True):
        _run_audit(book, apply=args.apply_audit)
    return 0


def cmd_assets(args) -> int:
    book = _book(args)
    summary = crop_figures(book, force=args.force, auto=not args.no_auto)
    print(f"cropped {len(summary.cropped)}, skipped {len(summary.skipped)}, "
          f"{len(summary.warnings)} warning(s)")
    for warning in summary.warnings:
        print(f"  warning: {warning}")
    return 0


def cmd_check(args) -> int:
    book = _book(args)
    report = check_book(book)
    for issue in report.issues:
        print(f"{issue.severity}: {issue.location}: {issue.message}")
    print(f"check: {len(report.issues)} issue(s), {report.footnotes} footnote(s)")
    return 0 if report.ok else 1


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


def cmd_watch(args) -> int:
    run_watch(_book(args), interval=args.interval, iterations=1 if args.once else None)
    return 0


def cmd_report(args) -> int:
    book = _book(args)
    rows = summarize_costs(book, by=args.by)
    _print_table(rows, ("key", "calls", "input_text_tokens",
                        "input_vision_tokens", "output_tokens", "cost_usd"))
    return 0


def cmd_expt(args) -> int:
    book = _book(args)
    expt_mod.ensure_workspace(book)
    declared = expt_mod.combos(book)
    if not declared:
        print("no combos yet; add them with 'pdfoenix init --combo <id>'")
        return 0
    combo_ids = [str(value) for value in (args.combo or [])]
    if args.all:
        combo_ids = [str(value) for value in sorted(declared)]
    if args.run:
        return expt_mod.run_expt(book, batch_no=args.batch, combo_ids=combo_ids,
                                 message=args.message, force=args.force)
    if args.report:
        _print_table(expt_mod.report_rows(book, include_hidden=args.all),
                     expt_mod.CELL_COLUMNS)
        print(f"report: {expt_mod.write_report(book)}")
        return 0
    if args.batch is not None and (combo_ids or args.all):
        print(f"preview: batch_{args.batch} would use combos "
              f"{' '.join(combo_ids)} (config unchanged; --run applies)")
        _print_table(expt_mod.combo_rows(book, combo_ids), expt_mod.CELL_COLUMNS)
        return 0
    if args.batch is not None:
        _print_table(expt_mod.cell_rows(book, args.batch), expt_mod.CELL_COLUMNS)
        return 0
    if combo_ids:
        _print_table(expt_mod.combo_rows(book, combo_ids), expt_mod.CELL_COLUMNS)
        return 0
    _print_table(expt_mod.overview_rows(book), expt_mod.OVERVIEW_COLUMNS)
    return 0


def cmd_calibrate(args) -> int:
    book = _book(args)
    print(format_report(calibration_report(book)))
    return 0


# ── Helpers ─────────────────────────────────────────────────────────────
def _book(args) -> Book:
    return Book(args.book)


def _production_ledger(book: Book) -> Telemetry:
    """Production ledger, tagged with the active combo for provenance."""
    entry = expt_mod.active_combo(book)
    return Telemetry(book.root, combo_id=entry["id"] if entry else None)


def _make_transcriber(book: Book, telemetry: Telemetry):
    """Adapter factory kept separate so tests can inject a fake model."""
    entry = expt_mod.active_combo(book)
    config = expt_mod.pipeline_config(book, entry) if entry else book.config
    return OpenCodeTranscriber(book.root, config, telemetry=telemetry,
                               presets_dir=book.root / "presets")


def _pdf_pages(book: Book) -> int:
    with fitz.open(book.pdf()) as doc:
        return doc.page_count


def _chapter_ranges(book: Book) -> dict[str, tuple[int, int]]:
    scout = book.state.load("scout")
    if not scout.get("chapters"):
        scout_book(book)
        scout = book.state.load("scout")
    chapters = sorted(scout.get("chapters", []), key=lambda item: item["pdf_page"])
    if not chapters:
        return {}
    total = _pdf_pages(book)
    ranges = {}
    for index, chapter in enumerate(chapters):
        start = int(chapter["pdf_page"])
        end = (int(chapters[index + 1]["pdf_page"]) - 1
               if index + 1 < len(chapters) else total)
        ranges[f"ch{int(chapter['number']):02d}"] = (start, max(start, end))
    return ranges


def _render_missing(book: Book, pages) -> None:
    missing = [page for page in pages if not book.page_image(page).exists()]
    if missing:
        render_pages(book.pdf(), missing, book.config["pipeline"]["render_dpi"],
                     book.pages_dir)


def _write_task_file(book: Book, chapter: str, start: int, end: int) -> None:
    path = book.batches_dir / chapter / "TASK.md"
    if path.exists():
        return
    path.write_text(
        f"# {chapter} transcription task\n\n"
        f"PDF pages {start}-{end}. Use the preset task book and emit "
        "`<!-- page:N -->` before every page.\n",
        encoding="utf-8")


def _set_progress(book: Book, unit: str, *, status: str, path: str | None = None) -> None:
    def mutate(data):
        entry = data["units"].get(unit, {})
        entry["status"] = status
        entry["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        if path:
            entry["path"] = path
        data["units"][unit] = entry

    book.state.update("progress", mutate)


def _save_corrections(book: Book, telemetry: Telemetry,
                      corrections: list[Correction]) -> None:
    if not corrections:
        return

    def mutate(data):
        data["corrections"].extend(dataclasses.asdict(item) for item in corrections)

    book.state.update("corrections", mutate)
    for correction in corrections:
        telemetry.record_correction(
            verify_call_id=correction.call_id,
            unit_id=correction.unit_id,
            formula_id=correction.formula_id,
            severity=correction.severity,
            pattern=correction.pattern,
            source=correction.source,
        )


def _verify_pages(book: Book, args) -> list[int]:
    risks = compute_page_risks(book)
    if args.pages:
        selected = parse_pages(args.pages, _pdf_pages(book))
    elif args.full:
        selected = sorted(risks)
    else:
        selected = sample_pages(book).selected_pages
    if args.chapter:
        bounds = _chapter_ranges(book).get(args.chapter)
        if bounds is None:
            raise ValueError(f"chapter not found: {args.chapter}")
        selected = [page for page in selected if bounds[0] <= page <= bounds[1]]
    if not args.force:
        done = _verified_pages(book)
        selected = [page for page in selected if page not in done]
    return selected


def _verified_pages(book: Book) -> set[int]:
    progress = book.state.load("progress")
    pages = set()
    for unit, entry in progress.get("units", {}).items():
        if not str(unit).startswith("verify/p") or entry.get("status") != "done":
            continue
        match = re.search(r"verify/p(\d+)", str(unit))
        if match:
            pages.add(int(match.group(1)))
    return pages


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


def _print_table(rows: list[dict], columns) -> None:
    if not rows:
        print("(no rows)")
        return
    widths = {}
    for column in columns:
        width = max([len(str(column))] + [len(_cell(row.get(column))) for row in rows])
        widths[column] = width
    print("  ".join(str(column).ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(_cell(row.get(column)).ljust(widths[column])
                        for column in columns))


def _cell(value) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value if value is not None else "")


def _close(resource: Any) -> None:
    """Close ``resource`` when it offers close(); test doubles may omit it."""
    close = getattr(resource, "close", None)
    if close is not None:
        close()


if __name__ == "__main__":
    sys.exit(main())
