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


def test_providers_defaults_when_file_missing(tmp_path: Path):
    from smallworks.config import PROVIDER_DEFAULT_BASES, load_providers

    providers = load_providers(tmp_path / "no-such-providers.yaml")
    assert set(providers) == set(PROVIDER_DEFAULT_BASES)
    assert providers["github"].api_key_env == "GITHUB_TOKEN"


def test_shipped_providers_load_with_key_envs():
    from smallworks.config import load_configs, load_providers

    # Ships with ONE local slot; the rest resolve from built-in defaults.
    slots = load_providers(REPO / "configs" / "providers.yaml")
    assert set(slots) == {"ollama"}
    assert slots["ollama"].api_key_env == ""  # local: no key sent
    loaded = load_configs(
        REPO / "configs" / "models.yaml",
        REPO / "configs" / "workers.yaml",
        REPO / "configs" / "providers.yaml",
    )
    assert loaded.providers["github"].base_url.startswith("https://")
    assert loaded.providers["github"].api_key_env == "GITHUB_TOKEN"


def test_unknown_provider_in_models_fails_fast(tmp_path: Path):
    from smallworks.config import load_configs

    models = tmp_path / "models.yaml"
    models.write_text(
        yaml.safe_dump(
            {"models": {"g": [{"provider": "nope", "model": "m", "class": "frontier"}]}}
        ),
        encoding="utf-8",
    )
    workers = tmp_path / "workers.yaml"
    workers.write_text(
        yaml.safe_dump(
            {"workers": {"developer": {"model_group": "g", "tier": "small", "max_concurrent": 1}}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown provider"):
        load_configs(models, workers)
