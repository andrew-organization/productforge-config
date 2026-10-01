#!/usr/bin/env python3
"""Serves build/web with SPA-style fallback routing, for testing a release build locally.

python3 -m http.server has no way to do this — an unknown path (e.g. a
verification/invite link's own /verify-email) 404s instead of falling
back to index.html, since it only knows about real files on disk. This
mirrors the rewrite rule any real static host needs for a path-based
single-page-app router (see CLAUDE.md's "Local environment overrides").
"""

import http.server
import socketserver
import sys
from pathlib import Path

BUILD_DIR = Path(__file__).resolve().parent.parent / "build" / "web"


class SpaFallbackHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BUILD_DIR), **kwargs)

    def translate_path(self, path: str) -> str:
        requested = super().translate_path(path)
        if Path(requested).is_file():
            return requested
        return super().translate_path("/index.html")


def main() -> None:
    if not BUILD_DIR.is_dir():
        sys.exit(f"{BUILD_DIR} doesn't exist — run `flutter build web` first.")

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 6106
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), SpaFallbackHandler) as server:
        print(f"Serving {BUILD_DIR} at http://0.0.0.0:{port} (SPA fallback to index.html)")
        server.serve_forever()


if __name__ == "__main__":
    main()
