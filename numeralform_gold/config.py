"""Project-local runtime path configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

CONFIG_FILENAME = "config.toml"
PATH_KEYS = {"source_cache", "work"}


class ConfigError(ValueError):
    """Raised for invalid or incomplete runtime configuration."""


@dataclass(frozen=True)
class PathsConfig:
    source_cache: Path | None
    work: Path | None


@dataclass(frozen=True)
class ProjectConfig:
    path: Path | None
    paths: PathsConfig


@dataclass(frozen=True)
class RuntimePaths:
    source_cache: Path | None
    work_root: Path | None


def default_config_path() -> Path:
    return Path.cwd() / CONFIG_FILENAME


def _normalise_path(value: str | Path, *, base: Path) -> Path:
    if isinstance(value, str) and not value.strip():
        raise ConfigError("configured path must not be empty")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


def load_config(path: Path | None, *, explicit: bool = False) -> ProjectConfig:
    if path is None:
        return ProjectConfig(path=None, paths=PathsConfig(None, None))
    config_path = path.expanduser().resolve(strict=False)
    if not config_path.exists():
        if explicit:
            raise ConfigError(f"config file not found: {config_path}")
        return ProjectConfig(path=None, paths=PathsConfig(None, None))
    if not config_path.is_file():
        raise ConfigError(f"config path is not a file: {config_path}")
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not load config file {config_path}: {exc}") from exc
    raw_paths = payload.get("paths", {})
    if not isinstance(raw_paths, dict):
        raise ConfigError(f"[paths] must be a table in {config_path}")
    unknown = sorted(set(raw_paths) - PATH_KEYS)
    if unknown:
        raise ConfigError(f"unknown key(s) under [paths]: {', '.join(unknown)}")
    values: dict[str, Path | None] = {}
    for key in PATH_KEYS:
        value = raw_paths.get(key)
        if value is None:
            values[key] = None
        elif not isinstance(value, str):
            raise ConfigError(f"[paths].{key} must be a string")
        else:
            values[key] = _normalise_path(value, base=config_path.parent)
    return ProjectConfig(
        path=config_path,
        paths=PathsConfig(values["source_cache"], values["work"]),
    )


def _override(value: Path | str | None) -> Path | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _normalise_path(value, base=Path.cwd())


def _select(
    cli_value: Path | None,
    env_name: str,
    config_value: Path | None,
    environ: Mapping[str, str],
) -> Path | None:
    if cli_value is not None:
        return _override(cli_value)
    if environ.get(env_name):
        return _override(environ[env_name])
    return config_value


def resolve_runtime_paths(
    *,
    config: ProjectConfig,
    source_cache: Path | None = None,
    work_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimePaths:
    env = os.environ if environ is None else environ
    return RuntimePaths(
        source_cache=_select(
            source_cache,
            "NUMERALFORM_GOLD_SOURCE_CACHE",
            config.paths.source_cache,
            env,
        ),
        work_root=_select(
            work_root,
            "NUMERALFORM_GOLD_WORK",
            config.paths.work,
            env,
        ),
    )


def require_runtime_paths(paths: RuntimePaths) -> RuntimePaths:
    if paths.source_cache is None:
        raise ConfigError(
            "source cache is not configured; use --source-cache, "
            "NUMERALFORM_GOLD_SOURCE_CACHE, or [paths].source_cache"
        )
    if paths.work_root is None:
        raise ConfigError(
            "work root is not configured; use --work-root, "
            "NUMERALFORM_GOLD_WORK, or [paths].work"
        )
    return paths
