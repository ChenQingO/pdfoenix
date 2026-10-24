"""pdfoenix command-line orchestrator.

The CLI is a thin control-flow layer: every subcommand resolves a :class:`Book`,
calls one deterministic stage (or one model adapter), persists the result, and
returns an exit code.  It never keeps pipeline state in memory across commands.

Command groups
--------------
* pipeline stages: ``init scout render transcribe merge assets verify check
  build inspect`` — plain verbs, one stage each;
* status: ``watch`` (read-only dashboard);
* reports: ``report cost|quality|risk`` (read-only analysis);
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
from pathlib import Path

from pdfoenix import combo as combo_mod
from pdfoenix import expt as expt_mod
from pdfoenix import pipeline
from pdfoenix.assets.crop import crop_figures
from pdfoenix.core.book import Book
from pdfoenix.core.build import build_book
from pdfoenix.core.check import check_book
from pdfoenix.core.merge import merge_book, merge_chapter
from pdfoenix.core.pages import batch_ranges, parse_pages
from pdfoenix.core.render import render_pages
from pdfoenix.core.scout import scout_book
from pdfoenix.display import print_table
from pdfoenix.inspect import inspect_epub
from pdfoenix.report import format_risk_report, risk_report, summarize_costs
from pdfoenix.scaffold import scaffold_book
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

    scout = sub.add_parser("scout", help="scan the PDF text layer into state/scout.json")
    scout.set_defaults(func=cmd_scout)

    render = sub.add_parser("render", help="render transcription pages at render_dpi")
    _add_scope(render, pages=True)
    _add_force(render, "re-render existing page images")
    render.set_defaults(func=cmd_render)

    transcribe = sub.add_parser("transcribe", help="transcribe page batches")
    _add_scope(transcribe, pages=True)
    _add_force(transcribe, "redo existing batches")
    transcribe.set_defaults(func=cmd_transcribe)

    merge = sub.add_parser("merge", help="merge batches into the manuscript")
    _add_scope(merge)
    merge.set_defaults(func=cmd_merge)

    assets = sub.add_parser("assets", help="crop registered figures")
    _add_force(assets, "overwrite existing crops")
    assets.add_argument("--no-auto", action="store_true",
                        help="do not auto-detect missing crop boxes")
    assets.set_defaults(func=cmd_assets)

    verify = sub.add_parser("verify", help="sample and verify transcribed pages")
    _add_scope(verify, pages=True)
    verify.add_argument("--full", action="store_true",
                        help="verify every candidate page (bypass sampling)")
    _add_force(verify, "redo verified pages (and their page images)")
    verify.add_argument("--audit", choices=("off", "dry", "apply"),
                        help="global-audit pass (default: dry, or [review].global_audit)")
    verify.set_defaults(func=cmd_verify)

    check = sub.add_parser("check", help="validate the manuscript")
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

    # ── Reports (read-only) ─────────────────────────────────────────────
    report = sub.add_parser("report", help="read-only cost/quality/risk reports")
    report_sub = report.add_subparsers(dest="report_command", required=True)

    cost = report_sub.add_parser("cost", help="token and cost totals")
    cost.add_argument("--by", choices=("model", "chapter"), default="model")
    cost.set_defaults(func=cmd_report_cost)

    quality = report_sub.add_parser(
        "quality", help="experiment cost-quality comparison (writes expt/report.md)")
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


def cmd_scout(args) -> int:
    book = _book(args)
    data = scout_book(book)
    print(f"scout: {len(data['chapters'])} chapters, {len(data['sections'])} sections, "
          f"{len(data['figures'])} figures, {len(data['tables'])} tables")
    return 0


def cmd_render(args) -> int:
    book = _book(args)
    total = book.pdf_page_count()
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
    ranges = pipeline.ensure_chapter_ranges(book)
    if args.chapter:
        ranges = {name: value for name, value in ranges.items() if name == args.chapter}
    if not ranges:
        raise ValueError(f"chapter not found: {args.chapter}")
    wanted = set(parse_pages(args.pages, book.pdf_page_count())) if args.pages else None
    if wanted is not None and not wanted:
        raise ValueError(f"no pages match: {args.pages}")
    size = book.config["pipeline"]["batch_pages"]
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
                telemetry.emit_event("dispatch", stage="transcribe",
                                     chapter=chapter, unit=unit)
                telemetry.emit_event("agent_start", agent=f"{chapter}/{unit}")
                try:
                    text = pipeline.transcribe_window(
                        book, transcriber, first=first, last=last, chapter=chapter)
                except Exception:
                    telemetry.emit_event("agent_fail", agent=f"{chapter}/{unit}")
                    pipeline.set_progress(book, f"transcribe/{chapter}/{unit}",
                                          status="failed", path=str(target))
                    raise
                target.write_text(text.rstrip() + "\n", encoding="utf-8")
                telemetry.emit_event("agent_done", agent=f"{chapter}/{unit}")
                pipeline.set_progress(book, f"transcribe/{chapter}/{unit}",
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
    images = pipeline.verify_images(book, kept, force=args.force)

    telemetry = pipeline.production_ledger(book)
    transcriber = pipeline.make_transcriber(book, telemetry)
    telemetry.emit_event("dispatch", stage="verify", unit=f"p{kept[0]}-{kept[-1]}")
    telemetry.emit_event("agent_start", agent="verify")
    try:
        corrections = transcriber.verify(images, content)
    except Exception:
        telemetry.emit_event("agent_fail", agent="verify")
        raise
    else:
        telemetry.emit_event("agent_done", agent="verify")
        pipeline.save_corrections(book, telemetry, corrections)
        for page in kept:
            pipeline.set_progress(book, f"verify/p{page}", status="done")
        print(f"verified {len(kept)} page(s); {len(corrections)} correction(s)")
    finally:
        _close(transcriber)
        telemetry.close()

    audit = args.audit or ("dry" if book.config["review"].get("global_audit", True)
                           else "off")
    if audit != "off":
        _run_audit(book, apply=audit == "apply")
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
