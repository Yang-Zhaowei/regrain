"""Shared production error; preserves the render API exception contract."""
from pathlib import Path

class RenderError(Exception):
    """A preflight or run-level fault raises this.

    Preflight faults (before any model starts) carry no ``run_dir``. Once a run
    has started, ``run_dir`` points at the run directory holding the partial
    artifacts and report so a caller can recover them.
    """

    def __init__(self, message: str, *, run_dir: Path | None = None) -> None:
        super().__init__(message)
        self.run_dir = run_dir
