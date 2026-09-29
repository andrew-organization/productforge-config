"""Every file write `update` makes goes through here, so `update --check` can report the
writes it would make, without making them.
"""

import contextlib
from collections.abc import Iterator
from pathlib import Path

_check_only = False


@contextlib.contextmanager
def check_only() -> Iterator[None]:
    """Within this block a write reports whether it would change a file, and leaves it as it is."""
    global _check_only
    previous = _check_only
    _check_only = True
    try:
        yield
    finally:
        _check_only = previous


def write_if_changed(path: Path, text: str) -> bool:
    """Write `text` to `path` unless it already holds exactly that. True when it differs."""
    if path.exists() and path.read_text() == text:
        return False
    if not _check_only:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return True


def remove(path: Path) -> bool:
    """Remove a file. True when it was there."""
    if not path.is_file():
        return False
    if not _check_only:
        path.unlink()
    return True


def prune_empty_dirs(directory: Path) -> None:
    """Remove `directory` and every empty directory beneath it, when they hold nothing."""
    if _check_only or not directory.is_dir():
        return
    for child in sorted(directory.rglob("*"), reverse=True):
        if child.is_dir() and not any(child.iterdir()):
            child.rmdir()
    if not any(directory.iterdir()):
        directory.rmdir()
