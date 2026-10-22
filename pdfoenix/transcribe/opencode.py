"""OpenCode-backed implementation of the model-layer protocols.

One call is a single headless ``opencode run`` process with a prompt and zero
or more attached page images.  The adapter owns three concerns the rest of the
pipeline must not know about:

* **Prompt assembly.**  The task books in ``presets/transcribe.md`` and
  ``presets/verify.md`` are the stable part of the prompt; the caller supplies
  only the batch-specific instructions or the content under review.
* **Fan-out.**  Transcription is split into ``pipeline.batch_pages`` chunks and
  verification into ``pipeline.verify_chunk_size`` chunks.  Chunks run in
  parallel, but the results are re-joined in input order.
* **Accounting.**  Every process attempt is written to the SQLite ledger, even
  when it fails.  Token counts are read back from ``opencode session export``
  because ``opencode run --format json`` only streams content.

The subprocess is invoked through an injectable ``runner`` so tests never need
network access or a real model.  The runner signature is intentionally the
small subset of :func:`subprocess.run` that this module uses.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdfoenix.core.config import load_config
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.transcribe.protocol import Correction

#: Agent ids defined by ``presets/opencode.jsonc``.
TRANSCRIBE_AGENT = "pdfoenix-transcribe"
VERIFY_AGENT = "pdfoenix-verify"

#: Provider label stored in the ledger (the adapter, not the model vendor).
PROVIDER_NAME = "opencode"

_DEFAULT_COMMAND = ("npx", "opencode")
_PAGE_ANCHOR_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_OUTER_FENCE_RE = re.compile(
    r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL | re.IGNORECASE)
_DIGITS_RE = re.compile(r"\d+")


class OpenCodeError(RuntimeError):
    """Raised when an OpenCode call fails or violates its output contract."""


@dataclass
class _CallOutcome:
    """Raw result of one ``opencode run`` attempt, before it is interpreted."""

    text: str = ""
    session_id: str | None = None
    returncode: int = 0
    stderr: str = ""
    latency_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    call_id: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0


@dataclass(frozen=True)
class _Chunk:
    """A group of page images plus the content that belongs with them."""

    images: list[Path]
    content: str
    unit_id: str


def _chunked(items: Sequence[Any], size: int) -> list[list[Any]]:
    """Split ``items`` into consecutive chunks of at most ``size`` items."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [list(items[start:start + size]) for start in range(0, len(items), size)]


def _as_paths(images: Iterable[str | Path]) -> list[Path]:
    """Normalize image arguments to absolute paths and fail fast if missing."""
    paths = [Path(image).expanduser().resolve() for image in images]
    if not paths:
        raise OpenCodeError("at least one page image is required")
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise OpenCodeError(f"page image(s) not found: {', '.join(missing)}")
    return paths


def _image_page(image: Path) -> int | None:
    """Last run of digits in an image stem, e.g. ``pdfpage_399`` -> ``399``."""
    matches = _DIGITS_RE.findall(image.stem)
    return int(matches[-1]) if matches else None


def _default_unit_id(images: Sequence[Path]) -> str:
    """Human-readable unit id derived from page numbers (``p399-403``)."""
    pages = sorted(page for page in (_image_page(image) for image in images)
                   if page is not None)
    if not pages:
        return "+".join(image.stem for image in images)
    if len(pages) == 1:
        return f"p{pages[0]}"
    return f"p{pages[0]}-{pages[-1]}"


def _page_rows(images: Sequence[Path]) -> str:
    """Bullet list that tells the model which page number each attachment is."""
    rows = []
    for image in images:
        page = _image_page(image)
        label = f"page {page}" if page is not None else "unnumbered page"
        rows.append(f"- {label}: {image.name}")
    return "\n".join(rows)


def _strip_outer_fence(text: str) -> str:
    """Drop a Markdown fence that a model wrapped around its whole answer."""
    match = _OUTER_FENCE_RE.match(text.strip())
    return match.group(1).strip() if match else text.strip()


def _parse_run_output(stdout: str) -> tuple[str, str | None, str | None]:
    """Extract streamed text, the session id and any error from JSON output."""
    text_parts: list[str] = []
    session_id: str | None = None
    error: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if session_id is None:
            candidate = event.get("sessionID") or event.get("session_id")
            if isinstance(candidate, str) and candidate:
                session_id = candidate
        if event.get("type") == "error" and error is None:
            detail = event.get("error")
            if isinstance(detail, dict):
                error = str(detail.get("message") or detail)
            else:
                error = str(detail or "unknown opencode error")
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict):
            chunk = part.get("text")
            if isinstance(chunk, str):
                text_parts.append(chunk)
    return "".join(text_parts), session_id, error


def _extract_json_payload(text: str) -> Any:
    """Find and decode the JSON value embedded in a verifier response."""
    candidates = [match.group(1).strip() for match in _JSON_FENCE_RE.finditer(text)]
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    start, end = stripped.find("["), stripped.rfind("]")
    if start != -1 and end > start:
        candidates.append(stripped[start:end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise OpenCodeError("verifier response contains no valid JSON")


def _page_sections(content: str) -> tuple[str, dict[int, str]] | None:
    """Split anchored content into ``(preamble, {page: section_text})``.

    Returns ``None`` when there are no page anchors, which tells the caller to
    fall back to a single verification call for the whole unit.
    """
    matches = list(_PAGE_ANCHOR_RE.finditer(content))
    if not matches:
        return None
    preamble = content[:matches[0].start()]
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        page = int(match.group(1))
        sections[page] = sections.get(page, "") + content[match.start():end]
    return preamble, sections


class OpenCodeTranscriber:
    """Run page images through the locally installed OpenCode CLI.

    Parameters
    ----------
    book_root:
        Directory of one book.  It is the root for ``config.toml``,
        ``logs/pipeline.db`` and relative preset paths.  The subprocess runs
        from the directory holding ``opencode.jsonc`` so OpenCode discovers the
        project configuration; page images are passed as absolute paths.
    config:
        Already-loaded configuration.  When omitted, ``config.toml`` is loaded
        from ``book_root`` (falling back to built-in defaults).
    command:
        CLI prefix.  Defaults to ``("npx", "opencode")``; pass ``("opencode")``
        when the binary is installed globally.
    runner:
        Callable with the same keyword arguments as :func:`subprocess.run`.
        Injected by tests to avoid starting a real agent.
    telemetry:
        Ledger to write to.  A private one is opened when omitted.
    timeout:
        Per-process timeout in seconds.  Defaults to ``transcriber.timeout`` or
        one hour.
    max_workers:
        Maximum parallel OpenCode processes for chunk fan-out.
    presets_dir:
        Directory holding ``transcribe.md`` and ``verify.md``.  Defaults to the
        directory of the configured ``opencode.jsonc`` or the packaged presets.
    env:
        Extra environment variables for the subprocess.
    """

    def __init__(
        self,
        book_root: str | Path,
        config: dict | None = None,
        *,
        command: Sequence[str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        telemetry: Telemetry | None = None,
        timeout: float | None = None,
        max_workers: int = 4,
        presets_dir: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.book_root = Path(book_root)
        if config is None:
            config_path = self.book_root / "config.toml"
            config = load_config(config_path if config_path.exists() else None)
        self.config = config
        self.command = list(command or _DEFAULT_COMMAND)
        self.batch_pages = int(config["pipeline"]["batch_pages"])
        self.verify_chunk_size = int(config["pipeline"]["verify_chunk_size"])
        configured_timeout = config.get("transcriber", {}).get("timeout")
        self.timeout = float(timeout if timeout is not None else configured_timeout or 3600)
        self.max_workers = max(1, int(max_workers))
        self._runner = runner or self._default_runner
        self._extra_env = dict(env or {})
        self._owns_telemetry = telemetry is None
        self.telemetry = telemetry or Telemetry(self.book_root)

        self.presets_dir = (Path(presets_dir) if presets_dir is not None
                            else self._default_presets_dir())
        self.transcribe_book, self.transcribe_version = self._load_task_book("transcribe.md")
        self.verify_book, self.verify_version = self._load_task_book("verify.md")
        self.opencode_config = self._resolve_opencode_config()
        # OpenCode discovers project configuration by walking up from the
        # working directory; it does not reliably honor OPENCODE_CONFIG across
        # its shared server.  Running from the config's directory makes the
        # project-local file authoritative while tool access stays disabled.
        self.workdir = (self.opencode_config.parent if self.opencode_config is not None
                        else self.book_root)

    # ── Lifecycle ───────────────────────────────────────────────────────
    def close(self) -> None:
        """Close the ledger if this instance created it."""
        if self._owns_telemetry:
            self.telemetry.close()

    def __enter__(self) -> "OpenCodeTranscriber":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── Public protocol ─────────────────────────────────────────────────
    def transcribe(self, images: list[Path], task: str) -> str:
        """Transcribe ``images`` in page-sized parallel batches and join them."""
        paths = _as_paths(images)
        chunks = _chunked(paths, self.batch_pages)
        prompts = [self._transcribe_prompt(chunk, task) for chunk in chunks]
        unit_ids = [_default_unit_id(chunk) for chunk in chunks]
        outcomes = self._run_many("transcribe", chunks, prompts, unit_ids,
                                  self.transcribe_version)
        texts = []
        for outcome in outcomes:
            if not outcome.ok:
                raise OpenCodeError(
                    f"transcription failed: {outcome.error or outcome.stderr.strip()}")
            text = _strip_outer_fence(outcome.text)
            if not text:
                raise OpenCodeError("transcription returned empty output")
            texts.append(text)
        return "\n\n".join(texts)

    def verify(self, images: list[Path], content: str) -> list[Correction]:
        """Audit ``content`` against ``images`` and return structured corrections."""
        paths = _as_paths(images)
        chunks = self._verify_chunks(paths, content)
        prompts = [self._verify_prompt(chunk.images, chunk.content) for chunk in chunks]
        outcomes = self._run_many(
            "verify", [chunk.images for chunk in chunks], prompts,
            [chunk.unit_id for chunk in chunks], self.verify_version)
        corrections: list[Correction] = []
        for chunk, outcome in zip(chunks, outcomes, strict=True):
            if not outcome.ok:
                raise OpenCodeError(
                    f"verification failed: {outcome.error or outcome.stderr.strip()}")
            corrections.extend(self._parse_corrections(
                outcome.text, chunk.unit_id, call_id=outcome.call_id))
        return corrections

    # ── Chunk planning ──────────────────────────────────────────────────
    def _verify_chunks(self, images: list[Path], content: str) -> list[_Chunk]:
        """Split images and anchored content into aligned verification chunks."""
        image_chunks = _chunked(images, self.verify_chunk_size)
        if len(image_chunks) == 1:
            return [_Chunk(images, content, _default_unit_id(images))]

        sections = _page_sections(content)
        if sections is None:
            return [_Chunk(images, content, _default_unit_id(images))]
        preamble, by_page = sections
        if any(_image_page(image) not in by_page for image in images):
            return [_Chunk(images, content, _default_unit_id(images))]

        chunks = []
        for index, chunk_images in enumerate(image_chunks):
            parts = [preamble] if index == 0 else []
            for image in chunk_images:
                page = _image_page(image)
                parts.append(by_page.get(page, "") if page is not None else "")
            chunks.append(
                _Chunk(chunk_images, "\n".join(part for part in parts if part.strip()),
                       _default_unit_id(chunk_images)))
        return chunks

    # ── Prompt construction ─────────────────────────────────────────────
    def _transcribe_prompt(self, images: list[Path], task: str) -> str:
        parts = [self.transcribe_book.strip()]
        if task.strip():
            parts.append("## Batch-specific instructions\n" + task.strip())
        parts.append("## Attached page images\n" + _page_rows(images))
        parts.append(
            "Emit one page anchor before every page, in the order listed above. "
            "Return only the Markdown transcription.")
        return "\n\n".join(parts)

    def _verify_prompt(self, images: list[Path], content: str) -> str:
        parts = [
            self.verify_book.strip(),
            "## Pages under review\n" + _page_rows(images),
            "## Transcription under review\n" + content.strip(),
            "Return only the JSON array described in the task book.",
        ]
        return "\n\n".join(parts)

    # ── Process execution ───────────────────────────────────────────────
    def _run_many(
        self,
        stage: str,
        image_chunks: Sequence[Sequence[Path]],
        prompts: Sequence[str],
        unit_ids: Sequence[str],
        prompt_version: str,
    ) -> list[_CallOutcome]:
        """Execute all chunks (in parallel) and record every attempt."""
        jobs = list(zip(image_chunks, prompts, unit_ids, strict=True))
        outcomes = self._execute_jobs(stage, jobs)
        for outcome, images, unit_id in zip(outcomes, image_chunks, unit_ids, strict=True):
            outcome.call_id = self._record(
                stage, outcome, len(images), unit_id, prompt_version)
        return outcomes

    def _execute_jobs(
        self,
        stage: str,
        jobs: Sequence[tuple[Sequence[Path], str, str]],
    ) -> list[_CallOutcome]:
        """Run jobs serially or through a bounded thread pool, preserving order."""
        if len(jobs) == 1 or self.max_workers == 1:
            return [self._call_once(stage, list(images), prompt)
                    for images, prompt, _ in jobs]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = [
                pool.submit(self._call_once, stage, list(images), prompt)
                for images, prompt, _ in jobs
            ]
            return [future.result() for future in futures]

    def _call_once(self, stage: str, images: list[Path], prompt: str) -> _CallOutcome:
        """Run one OpenCode process and read its token usage back from the session."""
        argv = self._run_argv(stage, images, prompt)
        started = time.perf_counter()
        try:
            result = self._runner(
                argv, cwd=self.workdir, env=self._subprocess_env(),
                timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return _CallOutcome(
                returncode=-1, latency_ms=self._elapsed_ms(started),
                error=f"timeout after {self.timeout:g}s")
        except OSError as exc:
            return _CallOutcome(
                returncode=-1, latency_ms=self._elapsed_ms(started),
                error=f"could not start opencode: {exc}")

        latency_ms = self._elapsed_ms(started)
        text, session_id, run_error = _parse_run_output(result.stdout or "")
        stderr = result.stderr or ""
        if run_error:
            return _CallOutcome(
                text=text, session_id=session_id, returncode=result.returncode,
                stderr=stderr, latency_ms=latency_ms, error=run_error)
        if result.returncode != 0:
            return _CallOutcome(
                text=text, session_id=session_id, returncode=result.returncode,
                stderr=stderr, latency_ms=latency_ms,
                error=f"exit code {result.returncode}: {stderr.strip()}")
        usage = self._session_usage(session_id)
        return _CallOutcome(
            text=text, session_id=session_id, returncode=result.returncode,
            stderr=stderr, latency_ms=latency_ms, usage=usage)

    def _run_argv(self, stage: str, images: Sequence[Path], prompt: str) -> list[str]:
        agent = TRANSCRIBE_AGENT if stage == "transcribe" else VERIFY_AGENT
        argv = [*self.command, "run", "--format", "json", "--agent", agent]
        model = self._model_spec(stage)
        if model:
            argv.extend(["--model", model])
        for image in images:
            argv.extend(["--file", str(image)])
        argv.append(prompt)
        return argv

    def _session_usage(self, session_id: str | None) -> dict[str, Any]:
        """Export one session and return its aggregate token usage and model."""
        if not session_id:
            return {}
        argv = [*self.command, "session", "export", session_id]
        try:
            result = self._runner(
                argv, cwd=self.workdir, env=self._subprocess_env(), timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode != 0:
            return {}
        try:
            data = json.loads(result.stdout or "")
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        info = data.get("info")
        if not isinstance(info, dict):
            return {}
        tokens = info.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}
        return {
            "input": _as_int(tokens.get("input")),
            "output": _as_int(tokens.get("output")),
            "reasoning": _as_int(tokens.get("reasoning")),
            "model": info.get("model") if isinstance(info.get("model"), dict) else {},
        }

    def _record(
        self,
        stage: str,
        outcome: _CallOutcome,
        n_images: int,
        unit_id: str,
        prompt_version: str,
    ) -> str:
        """Write one call attempt (success or failure) to the cost ledger."""
        model_info = outcome.usage.get("model") or {}
        configured = self.config.get("models", {}).get(stage, {})
        model = str(model_info.get("id") or configured.get("model") or "")
        provider = str(model_info.get("providerID") or PROVIDER_NAME)
        thinking = str(configured.get("thinking") or "")
        status = "ok" if outcome.ok else "failed"
        # OpenCode's session export exposes one aggregate input count, not a
        # text/image split.  Record it as text and leave vision at zero rather
        # than inventing a split; the image count is preserved for later
        # re-pricing if a provider ever reports vision tokens separately.
        return self.telemetry.record_call(
            stage=stage,
            role=stage,
            model=model,
            provider=provider,
            thinking_mode=thinking,
            prompt_version=prompt_version,
            unit_id=unit_id,
            n_images=n_images,
            input_text_tokens=_as_int(outcome.usage.get("input")),
            input_vision_tokens=0,
            output_tokens=_as_int(outcome.usage.get("output"))
            + _as_int(outcome.usage.get("reasoning")),
            latency_ms=outcome.latency_ms,
            status=status,
            error=outcome.error,
        )

    # ── Helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _parse_corrections(text: str, unit_id: str,
                           *, call_id: str = "") -> list[Correction]:
        """Decode the verifier's JSON contract into correction objects."""
        payload = _extract_json_payload(text)
        if isinstance(payload, dict):
            payload = payload.get("corrections", [])
        if not isinstance(payload, list):
            raise OpenCodeError("verifier JSON must be an array or {'corrections': [...]}")
        corrections = []
        for item in payload:
            try:
                corrections.append(
                    Correction.from_mapping(
                        item, default_unit_id=unit_id, default_call_id=call_id))
            except ValueError as exc:
                raise OpenCodeError(f"invalid correction entry: {exc}") from exc
        return corrections

    def _model_spec(self, stage: str) -> str:
        """Build ``provider/model#variant`` from the configured model + thinking."""
        spec = self.config.get("models", {}).get(stage, {})
        model = str(spec.get("model") or "").strip()
        thinking = str(spec.get("thinking") or "").strip()
        if model and thinking not in {"", "default", "none"}:
            return f"{model}#{thinking}"
        return model

    def _subprocess_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self._extra_env)
        if self.opencode_config is not None:
            env["OPENCODE_CONFIG"] = str(self.opencode_config)
        return env

    def _default_presets_dir(self) -> Path:
        configured = self.config.get("transcriber", {}).get("opencode_config", "")
        if configured:
            candidate = Path(configured)
            if not candidate.is_absolute():
                candidate = self.book_root / candidate
            if candidate.parent.exists():
                return candidate.parent
        return Path(__file__).resolve().parent.parent / "presets"

    def _resolve_opencode_config(self) -> Path | None:
        """Find the configured ``opencode.jsonc`` and expose it via the environment."""
        raw = str(self.config.get("transcriber", {}).get("opencode_config") or "").strip()
        if not raw:
            return None
        configured = Path(raw)
        if configured.is_absolute():
            candidates = [configured]
        else:
            package_root = Path(__file__).resolve().parent.parent.parent
            candidates = [
                self.book_root / configured,
                self.presets_dir / configured.name,
                package_root / configured,
                Path(__file__).resolve().parent.parent / configured,
            ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise OpenCodeError(f"opencode config not found: {raw}")

    def _load_task_book(self, name: str) -> tuple[str, str]:
        """Read one task book and return its text plus a stable version hash."""
        path = self.presets_dir / name
        if not path.exists():
            raise OpenCodeError(f"task book not found: {path}")
        text = path.read_text(encoding="utf-8")
        version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        return text, version

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _default_runner(
        argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, text=True,
            timeout=timeout, check=False)


def _as_int(value: Any) -> int:
    """Coerce a possibly-missing token count to a non-negative integer."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
