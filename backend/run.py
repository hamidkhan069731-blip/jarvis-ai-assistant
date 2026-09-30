"""J.A.R.V.I.S. launcher.

Usage:
    python run.py            # start server and open the console in your browser
    python run.py --no-open  # start server only
    python run.py --host 0.0.0.0 --port 9000

The console is served at http://127.0.0.1:<port>/ by default.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

# make the package importable when run from the repo root or backend/
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    from jarvis.config import settings

    parser = argparse.ArgumentParser(description="Start the JARVIS assistant.")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--no-open", action="store_true", help="don't open the browser")
    parser.add_argument("--reload", action="store_true", help="dev auto-reload")
    args = parser.parse_args()

    url = f"http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{args.port}/"

    if not args.no_open:
        def _open() -> None:
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    print("=" * 60)
    print("  J.A.R.V.I.S.  —  online")
    print(f"  Console : {url}")
    print(f"  Data    : {settings.data_dir}")
    print(f"  Provider: {settings.get('ai_provider', 'local')}")
    print("  Press Ctrl+C to shut down.")
    print("=" * 60)

    import uvicorn
    uvicorn.run(
        "jarvis.server:app",
        host=args.host, port=args.port,
        reload=args.reload, log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
