#!/usr/bin/env python3
"""Packs taplo's own arm64 Linux release binary as the pip wheel the `taplo` pre-commit hook installs.

The hook asks pip for `taplo==VERSION`. PyPI has no Linux arm64 wheel for it and its source distribution does not
build, so on an arm64 host the install fails. A wheel in the directory `PIP_FIND_LINKS` names satisfies it.

usage: taplo_wheel.py VERSION BINARY OUTPUT-DIRECTORY
"""

import base64
import hashlib
import sys
import zipfile
from pathlib import Path

TAG = "py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64"


def record_line(*, name: str, data: bytes) -> str:
    """One RECORD row: the file's path, its sha256 and its size."""
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{name},sha256={digest},{len(data)}"


def main() -> int:
    """Writes the wheel and returns the exit status."""
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    version, binary, output = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    info = f"taplo-{version}.dist-info"
    wheel_file = f"Wheel-Version: 1.0\nGenerator: taplo_wheel\nRoot-Is-Purelib: false\nTag: {TAG}\n"
    files = {
        f"taplo-{version}.data/scripts/taplo": (binary.read_bytes(), 0o755),
        f"{info}/METADATA": (f"Metadata-Version: 2.1\nName: taplo\nVersion: {version}\n".encode(), 0o644),
        f"{info}/WHEEL": (wheel_file.encode(), 0o644),
    }  # fmt: skip
    record = "".join(record_line(name=n, data=d) + "\n" for n, (d, _) in files.items()) + f"{info}/RECORD,,\n"
    files[f"{info}/RECORD"] = (record.encode(), 0o644)
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output / f"taplo-{version}-{TAG}.whl", "w", zipfile.ZIP_DEFLATED) as wheel:
        for name, (data, mode) in files.items():
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (0o100000 | mode) << 16
            wheel.writestr(entry, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
