from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class SpaRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str | None = None, **kwargs):
        self._spa_root = Path(directory or ".").resolve()
        super().__init__(*args, directory=str(self._spa_root), **kwargs)

    def do_GET(self):
        requested_path = self.translate_path(self.path)
        candidate = Path(requested_path)
        if candidate.exists():
            return super().do_GET()

        if "." not in Path(self.path.split("?", 1)[0]).name:
            self.path = "/index.html"
        return super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a static SPA with index fallback.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        raise SystemExit(f"SPA root does not exist: {root}")

    handler = partial(SpaRequestHandler, directory=str(root))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
