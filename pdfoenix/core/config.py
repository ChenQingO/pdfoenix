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
        "min_pages": 10,           # absolute floor, even for formula-sparse books
        "min_rate": 0.10,          # minimum fraction of candidate pages to sample
        "seed": 0,                 # deterministic sampling by default
    },
    "review": {"global_audit": True},
    "logging": {"level": "info", "verbose": False},
    # Model-combination experiments (``pdfoenix expt``).  A combo is the model
    # pair behind the two billed agents; combos are append-only: to change one,
    # create a new id or edit the file by hand.  Combo 1 is the quality winner
    # of the 2026-10 benchmark (55 corrections/100 pages on dense math) and is
    # provided by default; ``active`` selects the pair behind plain transcribe
    # and verify.
    "expt": {
        "pages": "",               # benchmark page spec, e.g. "220-224,507-511"
        "max_workers": 2,          # concurrent windows per combo
        "attempts": 4,             # retries per window on transient failures
        "active": 1,
        "provider": [
            {"name": "mimo", "base_url": "https://api.xiaomimimo.com/v1",
             "api_key_env": "MIMO_API_KEY"},
        ],
        "combo": [
            {"id": 1, "transcribe": "mimo/mimo-v2.6-pro-ultraspeed",
             "verify": "mimo/mimo-v2.6-pro"},
        ],
    },
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

    for key in ("low_max", "high_min", "seed", "min_pages"):
        value = sampling[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"sampling.{key} must be a non-negative integer")
    if sampling["low_max"] >= sampling["high_min"]:
        raise ConfigError("sampling.low_max must be smaller than sampling.high_min")
    for key in ("low_prob", "medium_prob", "high_prob", "min_rate"):
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

    _validate_expt(cfg.get("expt", {}))


def parse_model_spec(spec: str) -> tuple[str, str, str]:
    """Split ``"provider/model#thinking"`` into its three parts.

    ``thinking`` is empty when no variant suffix is present; ``model`` keeps
    everything after the first slash so provider-qualified ids survive.
    """
    text = str(spec or "").strip()
    if "#" in text:
        text, thinking = text.split("#", 1)
    else:
        thinking = ""
    if "/" not in text:
        raise ConfigError(f"model spec must be 'provider/model', got {spec!r}")
    provider, model = text.split("/", 1)
    if not provider or not model:
        raise ConfigError(f"model spec must be 'provider/model', got {spec!r}")
    return provider, model, thinking.strip()


def _validate_expt(expt: dict) -> None:
    """Validate the ``[expt]`` table: providers, combos and their references."""
    if not isinstance(expt, dict):
        raise ConfigError("expt must be a table")

    for key in ("max_workers", "attempts"):
        value = expt.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ConfigError(f"expt.{key} must be a positive integer")
    if not isinstance(expt.get("pages", ""), str):
        raise ConfigError("expt.pages must be a page spec string")

    providers = {}
    for entry in expt.get("provider", []):
        if not isinstance(entry, dict):
            raise ConfigError("expt.provider entries must be tables")
        name = str(entry.get("name") or "")
        for key in ("base_url", "api_key_env"):
            if not str(entry.get(key) or ""):
                raise ConfigError(f"expt.provider {name or '?'} needs '{key}'")
        if not name:
            raise ConfigError("expt.provider entries need a 'name'")
        if name in providers:
            raise ConfigError(f"duplicate expt.provider {name!r}")
        providers[name] = entry

    combos = {}
    for entry in expt.get("combo", []):
        if not isinstance(entry, dict):
            raise ConfigError("expt.combo entries must be tables")
        combo_id = entry.get("id")
        if (not isinstance(combo_id, int) or isinstance(combo_id, bool)
                or combo_id <= 0):
            raise ConfigError(f"expt.combo id must be a positive integer, "
                              f"got {combo_id!r}")
        if combo_id in combos:
            raise ConfigError(f"duplicate expt.combo id {combo_id}")
        specs = []
        for role in ("transcribe", "verify"):
            provider, model, _ = parse_model_spec(entry.get(role, ""))
            if provider not in providers:
                raise ConfigError(
                    f"combo {combo_id} references undeclared provider "
                    f"{provider!r}; add [[expt.provider]] for it")
            specs.append(f"{provider}/{model}")
        if specs[0] == specs[1]:
            raise ConfigError(f"combo {combo_id} would judge its own output: "
                              "verify must differ from transcribe")
        combos[combo_id] = entry

    active = expt.get("active", 0)
    if not isinstance(active, int) or isinstance(active, bool) or active < 0:
        raise ConfigError("expt.active must be a non-negative combo id")
    if active and active not in combos:
        raise ConfigError(f"expt.active references unknown combo {active}")


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


# ── Serialization (init and the expt wizard own config.toml writes) ──────
def write_config(path, config: dict) -> None:
    """Write a merged configuration as TOML (tables and arrays of tables)."""
    from pathlib import Path

    Path(path).write_text(dump_table(config) + "\n", encoding="utf-8")


def dump_table(data: dict, prefix: str = "") -> str:
    """Render a nested dict as TOML: scalars, tables and ``[[array]]`` tables."""
    scalars = {}
    tables = {}
    arrays = {}
    for key, value in data.items():
        if isinstance(value, dict):
            tables[key] = value
        elif isinstance(value, list) and value and all(
                isinstance(item, dict) for item in value):
            arrays[key] = value
        else:
            scalars[key] = value
    lines = []
    if prefix:
        lines.append(f"[{prefix}]")
    for key, value in scalars.items():
        lines.append(f"{key} = {toml_value(value)}")
    if scalars and (tables or arrays):
        lines.append("")
    for key, value in tables.items():
        name = f"{prefix}.{key}" if prefix else key
        lines.append(dump_table(value, name))
        lines.append("")
    for key, entries in arrays.items():
        name = f"{prefix}.{key}" if prefix else key
        for entry in entries:
            lines.append(f"[[{name}]]")
            for sub_key, sub_value in entry.items():
                lines.append(f"{sub_key} = {toml_value(sub_value)}")
            lines.append("")
    return "\n".join(lines).rstrip()


def toml_value(value) -> str:
    """Render one TOML scalar or inline array."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    raise ConfigError(f"cannot serialize config value {value!r}")
