"""Serve the QQQ Trader dashboard on port 5000.

Regenerates the dashboard from S3 (or local logs) on every page load
so the view is always current after a bot run.
"""
import os
import sys
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Path setup — mirror what main.py does so dashboard imports work
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))                          # qqq_trader/
sys.path.insert(0, str(HERE.parent))                   # TradingAutomation/
sys.path.insert(0, str(HERE.parent.parent))            # workspace root (strategy.py)

from dotenv import load_dotenv
load_dotenv(HERE / ".env", override=False)             # Replit Secrets take precedence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("dashboard-server")

from dashboard import build, OUT_PATH


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        try:
            build()
        except Exception as e:
            log.warning("Dashboard rebuild failed: %s — serving cached file", e)

        try:
            with open(OUT_PATH, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            msg = b"<h2>No dashboard yet. The bot hasn't run today.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg)

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise; errors still surface via log.warning


if __name__ == "__main__":
    port = 5000
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    log.info("Dashboard server running on http://0.0.0.0:%d", port)
    server.serve_forever()
