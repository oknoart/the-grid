"""PyInstaller entry point for the self-contained okno terminal executable."""

from the_grid.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
