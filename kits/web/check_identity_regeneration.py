#!/usr/bin/env python3
"""Proves that `make identity` regenerates from product.yaml, and only from it.

Changes product.yaml's own name and short name to sentinel values, reruns
generate_identity.py, and checks that each of the three files it generates —
lib/config/product.dart, web/index.html and web/manifest.json — comes out
changed and now carries the sentinels. Restores product.yaml and every
regenerated file to what it was before, whichever way the check goes.

It works in any product: the lines it replaces are whatever `name:` and
`short_name:` product.yaml has now, and the files are compared with what they
held before, so it needs neither git nor a particular name. Run by CI (`make
check-identity-regeneration`), not by the git hook. A change to
generate_identity.py that narrows what it touches, or a name hard-coded
instead of read from product.yaml, shows up here as a failing check rather
than shipping quietly.
"""

import html
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_identity  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_YAML_PATH = REPO_ROOT / "product.yaml"
GENERATED_PATHS = (
    "lib/config/product.dart",
    "web/index.html",
    "web/manifest.json",
)
# A name with every character each generated format has to escape: an
# apostrophe and a dollar sign for Dart, quotes and an ampersand for HTML and
# JSON. The sentinel lines are how product.yaml writes them, double-quoted for
# the name and single-quoted for the short name.
SENTINEL_NAME = 'Andy\'s "Identity" & $Check'
SENTINEL_NAME_LINE = 'name: "Andy\'s \\"Identity\\" & $Check"'
SENTINEL_SHORT_NAME = "Andy's & $Short"
SENTINEL_SHORT_NAME_LINE = "short_name: 'Andy''s & $Short'"

_NAME_LINE_RE = re.compile(r"^name:.*$", re.MULTILINE)
_SHORT_NAME_LINE_RE = re.compile(r"^short_name:.*$", re.MULTILINE)


def _with_sentinels(product_yaml: str) -> str:
    """product.yaml with its own name and short name lines swapped for the sentinels."""

    for pattern, line, key in (
        (_NAME_LINE_RE, SENTINEL_NAME_LINE, "name"),
        (_SHORT_NAME_LINE_RE, SENTINEL_SHORT_NAME_LINE, "short_name"),
    ):
        if not pattern.search(product_yaml):
            sys.exit(f"check-identity-regeneration: product.yaml has no top-level `{key}:` line to change.")
        product_yaml = pattern.sub(lambda _, line=line: line, product_yaml, count=1)
    return product_yaml


def main() -> None:
    original_product_yaml = PRODUCT_YAML_PATH.read_text()
    sentinel_product_yaml = _with_sentinels(original_product_yaml)
    if sentinel_product_yaml == original_product_yaml:
        sys.exit("check-identity-regeneration: product.yaml already carries the sentinel names.")
    before = {path: (REPO_ROOT / path).read_text() for path in GENERATED_PATHS}

    try:
        PRODUCT_YAML_PATH.write_text(sentinel_product_yaml)
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "generate_identity.py")], check=True)

        after = {path: (REPO_ROOT / path).read_text() for path in GENERATED_PATHS}
        unchanged = [path for path in GENERATED_PATHS if after[path] == before[path]]
        if unchanged:
            sys.exit(
                "check-identity-regeneration: changing product.yaml's name and running `make identity` "
                f"left {unchanged} as they were."
            )

        expected = {
            "lib/config/product.dart": (
                f"name = '{generate_identity.dart_string(SENTINEL_NAME)}';",
                f"shortName = '{generate_identity.dart_string(SENTINEL_SHORT_NAME)}';",
            ),
            "web/index.html": (
                f"<title>{html.escape(SENTINEL_NAME)}</title>",
                f'content="{html.escape(SENTINEL_SHORT_NAME)}"',
            ),
        }
        missing_sentinel = [path for path, texts in expected.items() if not all(text in after[path] for text in texts)]
        manifest = json.loads(after["web/manifest.json"])
        if manifest["name"] != SENTINEL_NAME or manifest["short_name"] != SENTINEL_SHORT_NAME:
            missing_sentinel.append("web/manifest.json")
        if missing_sentinel:
            sys.exit(
                f"check-identity-regeneration: the new name is missing from {missing_sentinel} after regenerating."
            )
    finally:
        PRODUCT_YAML_PATH.write_text(original_product_yaml)
        for path, text in before.items():
            (REPO_ROOT / path).write_text(text)

    print("check-identity-regeneration: passed — restored product.yaml and every regenerated file.")


if __name__ == "__main__":
    main()
