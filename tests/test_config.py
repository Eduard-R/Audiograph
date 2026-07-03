from pathlib import Path

from diktattool.config import Config, load_config


def test_defaults_when_no_file(tmp_path: Path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg == Config()


def test_partial_override(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('hotkey = "ctrl+alt+space"\nmax_recording_seconds = 60\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.hotkey == "ctrl+alt+space"
    assert cfg.max_recording_seconds == 60
    # Untouched fields keep defaults.
    assert cfg.model_size == "small"
    assert cfg.language == "de"


def test_unknown_keys_ignored(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('nonsense = "x"\nlanguage = "en"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.language == "en"


def test_wrong_type_falls_back_to_default(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('max_recording_seconds = "not a number"\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.max_recording_seconds == 120


def test_malformed_toml_returns_defaults(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("this is = = not toml", encoding="utf-8")
    cfg = load_config(p)
    assert cfg == Config()
