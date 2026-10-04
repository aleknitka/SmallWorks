"""Config loader tests: pass on shipped YAMLs, fail fast on bad group/tier."""

from pathlib import Path

import pytest
import yaml

from smallworks.cli import cmd_validate_config
from smallworks.config import default_config_dir, load_configs, load_workers

REPO = Path(__file__).resolve().parents[1]


def _shipped() -> tuple[Path, Path]:
    cfg = default_config_dir()
    return cfg / "models.yaml", cfg / "workers.yaml"


def test_shipped_configs_load_and_map_roles():
    models_path, workers_path = _shipped()
    loaded = load_configs(models_path, workers_path)
    mapping = loaded.role_mapping()
    assert mapping["developer"] == "coder_fast"
    assert mapping["engineer"] == "engineering"
    assert mapping["reviewer"] == "reviewer"
    # Disabled Phase-2 architect is excluded from the active mapping.
    assert "architect" not in mapping


def test_unknown_group_fails_fast(tmp_path: Path):
    models_path, _ = _shipped()
    bad_workers = tmp_path / "workers.yaml"
    bad_workers.write_text(
        yaml.safe_dump({"workers": {"developer": {"model_group": "nope", "tier": "small", "max_concurrent": 1}}}),
        encoding="utf-8",
    )
    groups = set(yaml.safe_load(models_path.read_text(encoding="utf-8"))["models"])
    with pytest.raises(ValueError, match="unknown model group"):
        load_workers(bad_workers, known_groups=groups)


def test_unknown_tier_rejected(tmp_path: Path):
    models_path, _ = _shipped()
    bad_workers = tmp_path / "workers.yaml"
    bad_workers.write_text(
        yaml.safe_dump({"workers": {"developer": {"model_group": "coder_fast", "tier": "huge", "max_concurrent": 1}}}),
        encoding="utf-8",
    )
    groups = set(yaml.safe_load(models_path.read_text(encoding="utf-8"))["models"])
    with pytest.raises(ValueError):
        load_workers(bad_workers, known_groups=groups)


def test_validate_config_pass_and_bad_group(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    models_path, _ = _shipped()
    assert cmd_validate_config(models_path, REPO / "configs" / "workers.yaml") == 0
    assert "developer -> coder_fast" in capsys.readouterr().out

    bad_workers = tmp_path / "workers.yaml"
    bad_workers.write_text(
        yaml.safe_dump({"workers": {"developer": {"model_group": "nope", "tier": "small", "max_concurrent": 1}}}),
        encoding="utf-8",
    )
    assert cmd_validate_config(models_path, bad_workers) == 1
