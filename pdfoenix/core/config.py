"""Configuration loading: defaults, per-book ``config.toml`` overrides, validation.

Design
------
Every book ships a single ``config.toml`` that only lists values that differ from
the built-in defaults.  Loading deep-merges the file over ``DEFAULT_CONFIG``, so
missing keys are always filled and sibling keys under the same table survive a
partial override.  Unknown keys are allowed (forward compatibility); known keys
are type-checked and range-checked to fail fast on typos.
"""
import copy
import tomllib

from pdfoenix.core.risk import DEFAULT_WEIGHTS


class ConfigError(ValueError):
    """Raised when the configuration is malformed or contains invalid values."""


DEFAULT_CONFIG = {
    "book": {"title": "", "author": "", "pdf": "", "page_offset": 0},
    "pipeline": {
        "batch_pages": 5,          # pages per transcription batch
        "render_dpi": 150,         # DPI used for transcription page images
        "verify_dpi": 300,         # DPI used for verification page images
        "verify_chunk_size": 5,    # pages per single verification call
    },
    "transcriber": {
        "provider": "opencode",
        "opencode_config": "presets/opencode.jsonc",
    },
    "models": {
        "transcribe": {"model": "", "thinking": "high"},
        "verify": {"model": "", "thinking": "high"},
        "classify": {"model": "", "thinking": "low"},
    },
    "sampling": {
        "strategy": "risk_distribution",
        "risk_weights": dict(DEFAULT_WEIGHTS),
        "page_risk": "max",        # "max" or "sum" of the page's formula risks
        "budget": "20%",           # hard ceiling: page fraction or absolute count
        "low_max": 50,             # page risk <= low_max is low risk
        "high_min": 125,           # page risk > high_min is high risk
        "low_prob": 0.01,          # per-page sampling probabilities by band
        "medium_prob": 0.09,
        "high_prob": 0.60,
        "seed": 0,                 # deterministic sampling by default
    },
    "review": {"global_audit": True},
    "logging": {"level": "info", "verbose": False},
}

_LOG_LEVELS = {"debug", "info", "warning", "error"}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into ``base`` and return a new dict.

    Tables are merged key-by-key; every other value (including lists) replaces
    the base value wholesale.  Neither input is mutated.
    """
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _require_positive_int(cfg: dict, section: str, key: str) -> None:
    value = cfg[section][key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{section}.{key} must be a positive integer, got {value!r}")


def validate_config(cfg: dict) -> None:
    """Validate types and value ranges of the known configuration keys."""
    if not isinstance(cfg, dict):
        raise ConfigError("configuration root must be a table")

    for key in ("batch_pages", "render_dpi", "verify_dpi", "verify_chunk_size"):
        _require_positive_int(cfg, "pipeline", key)
    if cfg["pipeline"]["verify_dpi"] < cfg["pipeline"]["render_dpi"]:
        raise ConfigError("pipeline.verify_dpi must not be lower than render_dpi")

    sampling = cfg["sampling"]
    if sampling["page_risk"] not in {"max", "sum"}:
        raise ConfigError("sampling.page_risk must be 'max' or 'sum'")

    for key in ("low_max", "high_min", "seed"):
        value = sampling[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"sampling.{key} must be a non-negative integer")
    if sampling["low_max"] >= sampling["high_min"]:
        raise ConfigError("sampling.low_max must be smaller than sampling.high_min")
    for key in ("low_prob", "medium_prob", "high_prob"):
        value = sampling[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ConfigError(f"sampling.{key} must be a number")
        if not 0 <= value <= 1:
            raise ConfigError(f"sampling.{key} must be between 0 and 1")

    weights = cfg["sampling"]["risk_weights"]
    if not isinstance(weights, dict) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v >= 0
        for v in weights.values()
    ):
        raise ConfigError("sampling.risk_weights must be {construct: non-negative int}")

    budget = cfg["sampling"]["budget"]
    if not isinstance(budget, (int, str)):
        raise ConfigError(
            "sampling.budget must be an integer (page count) or a percentage string")

    if cfg["logging"]["level"] not in _LOG_LEVELS:
        raise ConfigError(f"logging.level must be one of {sorted(_LOG_LEVELS)}")

    models = cfg["models"]
    if not isinstance(models, dict):
        raise ConfigError("models must be a table")
    for role, spec in models.items():
        if not isinstance(spec, dict) or "model" not in spec or "thinking" not in spec:
            raise ConfigError(f"models.{role} must define both 'model' and 'thinking'")


def load_config(path=None, defaults=None) -> dict:
    """Load ``config.toml`` (optional), merge it over defaults, validate, return.

    ``path`` may be a ``str`` or ``pathlib.Path``.  Passing ``None`` returns a
    validated copy of the defaults.
    """
    base = copy.deepcopy(defaults or DEFAULT_CONFIG)
    if path is not None:
        from pathlib import Path

        text = Path(path).read_text(encoding="utf-8")
        try:
            override = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"failed to parse config.toml: {exc}") from exc
        if not isinstance(override, dict):
            raise ConfigError("config.toml root must be a table")
        cfg = deep_merge(base, override)
    else:
        cfg = base
    validate_config(cfg)
    return cfg


def risk_weights(cfg: dict) -> dict:
    """Return the effective risk weights (config values over defaults)."""
    return {**DEFAULT_WEIGHTS, **cfg["sampling"]["risk_weights"]}
