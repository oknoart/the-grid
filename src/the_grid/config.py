"""Strict non-secret client configuration handling."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Final

from . import terms, ui_text
from .models import ClientConfig, ServerSettings, UiSettings

CONFIG_FILENAME: Final = "config.json"
SUPPORTED_CONFIG_KEYS: Final = (
    "ui.color",
    "ui.plain",
)


class ConfigError(ValueError):
    """Raised when configuration input cannot be safely accepted."""


def default_config_path(
    *,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the approved per-platform client configuration path."""

    platform_name = sys.platform if platform_name is None else platform_name
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home

    if platform_name == "darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / terms.APP_SLUG
            / CONFIG_FILENAME
        )

    if platform_name.startswith("linux") or platform_name == "android":
        xdg_home = environ.get("XDG_CONFIG_HOME", "").strip()
        base = Path(xdg_home).expanduser() if xdg_home else home / ".config"
        return base / terms.APP_SLUG / CONFIG_FILENAME

    raise ConfigError(ui_text.PLATFORM_UNSUPPORTED)


def load_config(path: Path | None = None) -> ClientConfig:
    """Load a client configuration, returning defaults when no file exists."""

    target = default_config_path() if path is None else Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ClientConfig()
    except (OSError, UnicodeError) as exc:
        raise ConfigError(ui_text.CONFIG_INVALID) from exc

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(ui_text.CONFIG_INVALID) from exc

    return config_from_mapping(raw)


def config_from_mapping(raw: object) -> ClientConfig:
    """Validate a decoded JSON object and return an immutable config model."""

    if not isinstance(raw, dict):
        raise ConfigError(ui_text.CONFIG_INVALID)

    _reject_unknown(raw, {"server", "ui"})
    server_raw = raw.get("server", {})
    ui_raw = raw.get("ui", {})

    if not isinstance(server_raw, dict) or not isinstance(ui_raw, dict):
        raise ConfigError(ui_text.CONFIG_INVALID)

    _reject_unknown(server_raw, {"host", "port", "ca_file"})
    _reject_unknown(ui_raw, {"color", "plain"})

    host = server_raw.get("host")
    if host is not None and not isinstance(host, str):
        raise ConfigError(ui_text.CONFIG_INVALID)

    port = server_raw.get("port", 7331)
    if isinstance(port, bool) or not isinstance(port, int):
        raise ConfigError(ui_text.CONFIG_INVALID)

    ca_value = server_raw.get("ca_file")
    if ca_value is not None and not isinstance(ca_value, str):
        raise ConfigError(ui_text.CONFIG_INVALID)
    ca_file = None if ca_value is None else Path(ca_value)

    color = ui_raw.get("color", True)
    plain = ui_raw.get("plain", False)
    if not isinstance(color, bool) or not isinstance(plain, bool):
        raise ConfigError(ui_text.CONFIG_INVALID)

    try:
        return ClientConfig(
            server=ServerSettings(host=host, port=port, ca_file=ca_file),
            ui=UiSettings(color=color, plain=plain),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(ui_text.CONFIG_INVALID) from exc


def config_to_mapping(config: ClientConfig) -> dict[str, object]:
    """Return the stable JSON representation of a client configuration."""

    return {
        "server": {
            "host": config.server.host,
            "port": config.server.port,
            "ca_file": (
                None if config.server.ca_file is None else str(config.server.ca_file)
            ),
        },
        "ui": {
            "color": config.ui.color,
            "plain": config.ui.plain,
        },
    }


def format_config(config: ClientConfig) -> str:
    """Format config deterministically without secret fields."""

    return json.dumps(config_to_mapping(config), indent=2, ensure_ascii=True) + "\n"


def save_config(config: ClientConfig, path: Path | None = None) -> Path:
    """Atomically persist config with owner-only POSIX permissions."""

    target = default_config_path() if path is None else Path(path)
    parent_was_missing = not target.parent.exists()
    try:
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if parent_was_missing:
            os.chmod(target.parent, 0o700)

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{CONFIG_FILENAME}.", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(format_config(config))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
    except OSError as exc:
        raise ConfigError("the configuration file could not be saved") from exc

    return target


def set_config_value(config: ClientConfig, key: str, raw_value: str) -> ClientConfig:
    """Return a copy with one approved CLI-settable field changed."""

    if key not in SUPPORTED_CONFIG_KEYS:
        raise ConfigError("that configuration key is not supported")

    bool_value = _parse_bool(raw_value)
    if key == "ui.color":
        return replace(config, ui=replace(config.ui, color=bool_value))
    return replace(config, ui=replace(config.ui, plain=bool_value))


def _parse_bool(value: str) -> bool:
    normalised = value.strip().lower()
    if normalised in {"true", "yes", "1", "on"}:
        return True
    if normalised in {"false", "no", "0", "off"}:
        return False
    raise ConfigError("the value must be true or false")


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str]) -> None:
    if any(key not in allowed for key in raw):
        raise ConfigError("the configuration file contains an unknown setting")
