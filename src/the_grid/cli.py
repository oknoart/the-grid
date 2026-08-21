"""Command routing for the incremental implementation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from . import __version__, terms, ui_text
from .config import (
    ConfigError,
    SUPPORTED_CONFIG_KEYS,
    default_config_path,
    format_config,
    load_config,
    save_config,
    set_config_value,
)
from .phrases import PhraseError, WordListError, generate_phrase


def build_parser() -> argparse.ArgumentParser:
    """Build the user-facing parser without any phrase command arguments."""

    parser = argparse.ArgumentParser(
        prog=terms.EXECUTABLE_NAME,
        description="lightweight private terminal messaging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="show connection status")

    config_parser = commands.add_parser(
        "config", help="show or change non-secret client configuration"
    )
    config_commands = config_parser.add_subparsers(dest="config_command")
    config_commands.add_parser("show", help="show the current configuration")

    set_parser = config_commands.add_parser(
        "set", help="set one approved configuration value"
    )
    set_parser.add_argument("key", choices=SUPPORTED_CONFIG_KEYS)
    set_parser.add_argument("value")

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    config_file: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the current command set and return a process exit status."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        return _show_implementation_status(output, errors)

    if args.command == "status":
        print(ui_text.PHASE_2_STATUS_PENDING, file=errors)
        return 2

    path = default_config_path() if config_file is None else Path(config_file)
    if args.config_command is None:
        print("use grid config show or grid config set", file=errors)
        return 2

    try:
        config = load_config(path)
        if args.config_command == "show":
            print(format_config(config), end="", file=output)
            return 0

        updated = set_config_value(config, args.key, args.value)
        save_config(updated, path)
        print(ui_text.CONFIGURATION_SAVED, file=output)
        return 0
    except ConfigError as exc:
        print(str(exc), file=errors)
        return 1


def _show_implementation_status(output: TextIO, errors: TextIO) -> int:
    try:
        generated = generate_phrase()
    except (WordListError, PhraseError):
        print(ui_text.WORDLIST_INVALID, file=errors)
        return 1

    if len(generated.split(" ")) != 4:
        print(ui_text.WORDLIST_INVALID, file=errors)
        return 1

    print(ui_text.THE_GRID, file=output)
    print(file=output)
    print(ui_text.PHASE_2_READY, file=output)
    print(ui_text.PHASE_2_CLIENT_PENDING, file=output)
    return 0
