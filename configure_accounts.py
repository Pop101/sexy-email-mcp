"""Compatibility entrypoint for the one-time private setup script."""
from pathlib import Path
from runpy import run_path

run_path(str(Path(__file__).parent / "scripts/setup.py"), run_name="__main__")
