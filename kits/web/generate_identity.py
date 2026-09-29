#!/usr/bin/env python3
"""Regenerates this product's own identity from product.yaml, at the repository's root.

Reads product.yaml's name, short_name, email_from and domain and writes them
into lib/config/product.dart, web/index.html's title and
apple-mobile-web-app-title, and web/manifest.json's name and short_name — the
three files Flutter can't fill at run time from a single source. `make
check-generated` reruns this and fails on drift, so these three files always
match product.yaml.
"""

import html
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRODUCT_YAML_PATH = REPO_ROOT / "product.yaml"
PRODUCT_DART_PATH = REPO_ROOT / "lib" / "config" / "product.dart"
INDEX_HTML_PATH = REPO_ROOT / "web" / "index.html"
MANIFEST_JSON_PATH = REPO_ROOT / "web" / "manifest.json"

_FIELDS = ("name", "short_name", "email_from", "domain")


def _unquote(value: str) -> str:
    """A YAML scalar's own text: a quoted one unwrapped and unescaped, a plain one without its comment."""

    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", lambda escaped: escaped.group(1), value[1:-1])
    return value.split(" #", 1)[0].rstrip()


def _read_product_yaml() -> dict[str, str]:
    """Reads product.yaml's flat `key: value` pairs into a plain dict.

    product.yaml only ever holds this fixed, flat set of string fields (see
    its own comment), so a hand-rolled parser covers it without a YAML
    dependency — this script, like every other script in this
    repository, stays dependency-free. A value may be plain, single-quoted or
    double-quoted, as YAML allows.
    """

    values: dict[str, str] = {}
    for line in PRODUCT_YAML_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key in _FIELDS:
            values[key] = _unquote(value.strip())

    missing = [field for field in _FIELDS if not values.get(field)]
    if missing:
        sys.exit(f"product.yaml is missing: {', '.join(missing)}")
    return values


def dart_string(value: str) -> str:
    """Returns `value` as the body of a single-quoted Dart string literal."""

    return value.replace("\\", "\\\\").replace("'", "\\'").replace("$", "\\$")


def _write_product_dart(product: dict[str, str]) -> None:
    """Writes lib/config/product.dart, `AppConstants.appName`'s own source."""

    PRODUCT_DART_PATH.write_text(
        "// GENERATED CODE - DO NOT MODIFY BY HAND\n"
        "//\n"
        "// Regenerated from product.yaml (this repository's root) by `make\n"
        "// identity` (.productforge/generate_identity.py). Edit product.yaml, then run\n"
        "// `make identity`, rather than editing this file directly —\n"
        "// `make check-generated` fails on drift between the two.\n"
        "\n"
        "/// This repository's own product identity, read from `product.yaml`\n"
        "/// at generation time.\n"
        "abstract final class Product {\n"
        f"  static const String name = '{dart_string(product['name'])}';\n"
        f"  static const String shortName = '{dart_string(product['short_name'])}';\n"
        f"  static const String emailFrom = '{dart_string(product['email_from'])}';\n"
        f"  static const String domain = '{dart_string(product['domain'])}';\n"
        "}\n"
    )


def _write_index_html(product: dict[str, str]) -> None:
    """Rewrites web/index.html's <title> and apple-mobile-web-app-title in place.

    Only these two values come from product.yaml; every other line (the meta
    description included) is the repository's own copy, edited by
    hand.
    """

    page = INDEX_HTML_PATH.read_text()
    title = html.escape(product["name"])
    short_name = html.escape(product["short_name"])
    page = re.sub(r"<title>.*?</title>", lambda _: f"<title>{title}</title>", page, count=1)
    page = re.sub(
        r'(<meta name="apple-mobile-web-app-title" content=")[^"]*(">)',
        lambda match: f"{match.group(1)}{short_name}{match.group(2)}",
        page,
        count=1,
    )
    INDEX_HTML_PATH.write_text(page)


def _write_manifest_json(product: dict[str, str]) -> None:
    """Rewrites web/manifest.json's name and short_name in place.

    Only these two fields come from product.yaml; the description and every
    other field are the repository's own copy, edited by hand.
    """

    manifest = json.loads(MANIFEST_JSON_PATH.read_text())
    manifest["name"] = product["name"]
    manifest["short_name"] = product["short_name"]
    MANIFEST_JSON_PATH.write_text(json.dumps(manifest, indent=4, ensure_ascii=False) + "\n")


def main() -> None:
    product = _read_product_yaml()
    _write_product_dart(product)
    _write_index_html(product)
    _write_manifest_json(product)


if __name__ == "__main__":
    main()
