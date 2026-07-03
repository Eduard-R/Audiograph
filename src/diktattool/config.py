"""Configuration loading.

Loads defaults from a `Config` dataclass; overlays values from
`%USERPROFILE%\\.diktattool\\config.toml` if that file exists.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    hotkey: str = "f12"
    model_size: str = "small"
    language: str = "de"
    max_recording_seconds: int = 120
    samplerate: int = 16000
    compute_type: str = "int8"
    device: str = "cuda"
    log_level: str = "INFO"
    # Audio input device. Empty string = system default. May be an integer
    # index (as a string, e.g. "1") or a substring of the device name
    # (e.g. "Microphone (Realtek"). Query with:
    #   python -c "import sounddevice as sd; print(sd.query_devices())"
    input_device: str = ""


def user_config_dir() -> Path:
    """`%USERPROFILE%\\.diktattool\\` — created on first read."""
    base = Path(os.environ.get("USERPROFILE") or Path.home())
    return base / ".diktattool"


def config_path() -> Path:
    return user_config_dir() / "config.toml"


def log_path() -> Path:
    return user_config_dir() / "diktattool.log"


def load_config(path: Path | None = None) -> Config:
    """Return a Config: defaults merged with values from `path` if it exists.

    Unknown keys in the file are ignored with a warning. Values with a wrong
    type fall back to the default (also with a warning) — we never crash on a
    bad user config, because there is no UI to report it.
    """
    p = path if path is not None else config_path()
    if not p.exists():
        return Config()

    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("config: could not parse %s (%s) — using defaults", p, e)
        return Config()

    defaults = Config()
    valid = {f.name: _expected_python_type(f.type, getattr(defaults, f.name)) for f in fields(Config)}
    overrides: dict[str, object] = {}
    for key, value in data.items():
        expected = valid.get(key, _MISSING)
        if expected is _MISSING:
            logger.warning("config: unknown key %r ignored", key)
            continue
        # bool is a subclass of int in Python — reject bools where int is expected.
        if expected is int and isinstance(value, bool):
            _warn_type(key, value, expected)
            continue
        if not isinstance(value, expected):
            _warn_type(key, value, expected)
            continue
        overrides[key] = value
    return Config(**overrides)


_MISSING = object()


def _warn_type(key: str, value: object, expected: type) -> None:
    logger.warning(
        "config: %s has wrong type (%s, want %s) — using default",
        key,
        type(value).__name__,
        expected.__name__,
    )


def _expected_python_type(annotation: object, default: object) -> type:
    """Map dataclass annotations to concrete types.

    We use ``from __future__ import annotations``, so annotations arrive as
    strings; fall back to ``type(default)`` when the string isn't recognized.
    """
    if isinstance(annotation, type):
        return annotation
    if isinstance(annotation, str):
        table = {"int": int, "float": float, "str": str, "bool": bool}
        if annotation in table:
            return table[annotation]
    return type(default)
