from pathlib import Path

from numeralform_gold.config import load_config, resolve_runtime_paths


def test_config_paths_are_relative_to_config(tmp_path: Path):
    repo = tmp_path / "numeralform-gold"
    repo.mkdir()
    config_path = repo / "config.toml"
    config_path.write_text(
        "[paths]\n"
        'source_cache = "../numeralform-gold-source-cache"\n'
        'work = "../numeralform-gold-work"\n',
        encoding="utf-8",
    )
    config = load_config(config_path, explicit=True)
    paths = resolve_runtime_paths(config=config, environ={})
    assert paths.source_cache == (tmp_path / "numeralform-gold-source-cache").resolve()
    assert paths.work_root == (tmp_path / "numeralform-gold-work").resolve()


def test_environment_overrides_config(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[paths]\nwork = "base-work"\n', encoding="utf-8")
    config = load_config(config_path, explicit=True)
    paths = resolve_runtime_paths(
        config=config,
        environ={"NUMERALFORM_GOLD_WORK": str(tmp_path / "env-work")},
    )
    assert paths.work_root == (tmp_path / "env-work").resolve()
