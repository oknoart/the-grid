"""Command routing for the okno client and server administration."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence, TextIO

from . import __version__, terms, ui_text
from .access import create_initial_access
from .config import (
    ConfigError,
    SUPPORTED_CONFIG_KEYS,
    default_config_path,
    format_config,
    load_config,
    save_config,
    set_config_value,
)
from .interactive import InteractiveClientApp, apply_ui_overrides
from .models import DEFAULT_SERVER_PORT
from .server_admin import (
    ServerAdminError,
    backup_server,
    check_server_init_target,
    export_client_profile,
    format_server_status,
    initialise_server,
    renew_server_tls,
    rotate_server_access,
    run_server,
    server_status,
)
from .server_config import default_server_state_dir, make_server_config
from .terminal import PosixTerminal, RenderOptions


def build_parser() -> argparse.ArgumentParser:
    """Build the user-facing parser without any phrase command arguments."""

    parser = argparse.ArgumentParser(
        prog=terms.EXECUTABLE_NAME,
        description="okno — terminal access to the grid",
    )
    parser.add_argument(
        "--server",
        metavar="HOST:PORT",
        help="developer server override for this launch",
    )
    parser.add_argument(
        "--ca-file",
        type=Path,
        metavar="PATH",
        help="developer certificate-authority override",
    )
    parser.add_argument("--plain", action="store_true", help="avoid cursor positioning and ornament")
    parser.add_argument("--no-color", action="store_true", help="disable ansi colour")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="show configured client status")

    server_parser = commands.add_parser("server", help="administer the personal grid server")
    server_commands = server_parser.add_subparsers(dest="server_command")

    init_parser = server_commands.add_parser("init", help="initialise this personal grid server")
    init_parser.add_argument("--public-host", required=True, metavar="HOST")
    init_parser.add_argument("--public-port", type=int, default=DEFAULT_SERVER_PORT, metavar="PORT")
    init_parser.add_argument("--listen-host", default="0.0.0.0", metavar="HOST")
    init_parser.add_argument("--listen-port", type=int, default=DEFAULT_SERVER_PORT, metavar="PORT")
    init_parser.add_argument("--state-dir", type=Path, metavar="PATH")

    for name, help_text in (
        ("run", "run the server in the foreground"),
        ("status", "show server status"),
        ("rotate-access", "rotate the shared access phrase"),
        ("renew-tls", "renew the private-ca server certificate"),
    ):
        command_parser = server_commands.add_parser(name, help=help_text)
        command_parser.add_argument("--config", type=Path, metavar="PATH")

    export_parser = server_commands.add_parser(
        "export-client",
        help="export public client deployment material",
    )
    export_parser.add_argument("--config", type=Path, metavar="PATH")
    export_parser.add_argument("--output", type=Path, required=True, metavar="DIR")

    backup_parser = server_commands.add_parser("backup", help="create an operational server backup")
    backup_parser.add_argument("--config", type=Path, metavar="PATH")
    backup_parser.add_argument("--output", type=Path, required=True, metavar="FILE")

    config_parser = commands.add_parser(
        "config",
        help="show or change non-secret client configuration",
    )
    config_commands = config_parser.add_subparsers(dest="config_command")
    config_commands.add_parser("show", help="show the current configuration")

    set_parser = config_commands.add_parser(
        "set",
        help="set one approved user-interface preference",
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
    stdin: TextIO | None = None,
    terminal: object | None = None,
) -> int:
    """Run the current command set and return a process exit status."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    input_stream = sys.stdin if stdin is None else stdin
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "server":
        return _server_main(args, input_stream=input_stream, output=output, errors=errors)

    path = default_config_path() if config_file is None else Path(config_file)
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(str(exc), file=errors)
        return 1

    if args.command is None:
        effective = apply_ui_overrides(
            config,
            plain=args.plain,
            no_color=(args.no_color or "NO_COLOR" in os.environ),
        )
        terminal_obj = terminal
        if terminal_obj is None:
            terminal_obj = PosixTerminal(
                output_stream=output,
                options=RenderOptions(
                    color=effective.ui.color,
                    plain=effective.ui.plain,
                ),
            )
        app = InteractiveClientApp(
            config=effective,
            terminal=terminal_obj,  # type: ignore[arg-type]
            config_path=path,
            server_override=args.server,
            ca_file_override=args.ca_file,
        )
        try:
            if isinstance(terminal_obj, PosixTerminal):
                async def run_with_terminal() -> int:
                    async with terminal_obj:
                        return await app.run()

                return asyncio.run(run_with_terminal())
            return asyncio.run(app.run())
        except KeyboardInterrupt:
            return 0

    if args.command == "status":
        print("status", file=output)
        if config.server.host is None:
            print("server: not configured", file=output)
        else:
            print(f"server: {config.server.host}:{config.server.port}", file=output)
        print("connection: not active", file=output)
        return 0

    if args.command == "config":
        if args.config_command is None:
            print("use okno config show or okno config set", file=errors)
            return 2
        try:
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

    return 2


def _server_main(args, *, input_stream: TextIO, output: TextIO, errors: TextIO) -> int:
    if args.server_command is None:
        print(
            "use okno server init, run, status, rotate-access, renew-tls, backup or export-client",
            file=errors,
        )
        return 2
    old_umask = os.umask(0o077)
    try:
        if args.server_command == "init":
            state_dir = (
                default_server_state_dir()
                if args.state_dir is None
                else Path(args.state_dir).expanduser().resolve(strict=False)
            )
            check_server_init_target(state_dir)
            # Validate all network/configuration inputs before generating or
            # revealing the one-time access phrase.
            make_server_config(
                state_dir,
                public_host=args.public_host,
                public_port=args.public_port,
                listen_host=args.listen_host,
                listen_port=args.listen_port,
            )
            setup = create_initial_access()
            while True:
                print("access phrase:", file=output)
                print(setup.phrase, file=output)
                print("", file=output)
                print("save this phrase.", file=output)
                print("give it only to people you want on the grid.", file=output)
                print("it cannot be recovered from the server.", file=output)
                if _confirm("saved? y/n", input_stream=input_stream, output=output):
                    break
                print("", file=output)
            result = initialise_server(
                state_dir=state_dir,
                public_host=args.public_host,
                public_port=args.public_port,
                listen_host=args.listen_host,
                listen_port=args.listen_port,
                setup=setup,
            )
            print(
                f"server initialised: {result.config.public_host}:{result.config.public_port}",
                file=output,
            )
            print(f"config: {state_dir / 'server.json'}", file=output)
            return 0

        config_path = args.config
        if args.server_command == "run":
            asyncio.run(run_server(config_path))
            return 0
        if args.server_command == "status":
            status = asyncio.run(server_status(config_path))
            print(format_server_status(status), end="", file=output)
            return 0
        if args.server_command == "rotate-access":
            print("rotate access phrase?", file=output)
            print("this clears the current hub and disconnects users", file=output)
            print("using the old phrase.", file=output)
            if not _confirm("continue? y/n", input_stream=input_stream, output=output):
                print("cancelled", file=output)
                return 0
            rotated = asyncio.run(rotate_server_access(config_path))
            print("", file=output)
            print("new access phrase:", file=output)
            print(rotated.phrase, file=output)
            return 0
        if args.server_command == "renew-tls":
            days = renew_server_tls(config_path)
            print(f"tls renewed: {days} days remaining", file=output)
            print("restart the server process to load the renewed certificate", file=output)
            return 0
        if args.server_command == "export-client":
            paths = export_client_profile(args.output, config_path)
            print(f"client profile exported: {args.output}", file=output)
            for exported in paths:
                print(exported.name, file=output)
            return 0
        if args.server_command == "backup":
            backup = backup_server(args.output, config_path)
            print(f"backup created: {backup}", file=output)
            return 0
    except (ServerAdminError, ValueError) as exc:
        print(str(exc), file=errors)
        return 1
    finally:
        os.umask(old_umask)
    return 2


def _confirm(prompt: str, *, input_stream: TextIO, output: TextIO) -> bool:
    while True:
        print(prompt, file=output)
        value = input_stream.readline()
        if value == "":
            raise ServerAdminError("confirmation input ended")
        normalised = value.strip().lower()
        if normalised in {"y", "yes"}:
            return True
        if normalised in {"n", "no"}:
            return False
        print("enter y or n", file=output)
