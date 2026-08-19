"""Smoke-test a live deployment — the checks a curl script can't make.

    pip install websocket-client   # not a dependency of the app itself
    python scripts/verify_deployment.py https://pme-care.onrender.com

Needs a Chromium/Chrome binary on PATH: this drives the actual rendered page
over the DevTools protocol, at a real phone-sized viewport, rather than just
asking the API for JSON. That is the only way to catch what a curl-only check
would miss — this is exactly how the session that wrote this script caught the
demo banner failing to render and a table clipping at the edge of a phone
screen, neither of which shows up in an HTTP status code.

Deliberately lives outside backend/ and is never shipped in the Docker image
(see backend/Dockerfile) — it drives a browser against a URL from the outside,
it doesn't run inside the container.

Exits non-zero if anything fails, so it can be used as a manual post-deploy
gate today and wired into a pipeline later without changes.
"""

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request

try:
    import websocket
except ImportError:
    sys.exit("Needs websocket-client: pip install websocket-client")

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """Record one pass/fail line; never raises, so the whole suite always runs."""
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{f' — {detail}' if detail and not condition else ''}")
    if not condition:
        FAILURES.append(label)


# --- plain HTTP checks -------------------------------------------------------

def http_checks(base_url: str) -> dict:
    """Hit the API directly; return the parsed /health body for later use."""
    print("HTTP")

    def get(path: str) -> tuple[int, str]:
        req = urllib.request.Request(base_url + path)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    status, body = get("/api/v1/health")
    check("GET /api/v1/health is 200", status == 200, f"got {status}")
    health = json.loads(body) if status == 200 else {}
    check("reports a status", health.get("status") == "ok", repr(health))

    status, _ = get("/docs")
    check("GET /docs is 404 outside development", status == 404, f"got {status}")
    status, _ = get("/openapi.json")
    check("GET /openapi.json is 404 outside development", status == 404, f"got {status}")

    status, _ = get("/api/v1/me")
    check("unauthenticated GET /api/v1/me is 401", status == 401, f"got {status}")

    return health


# --- real browser, driven over the DevTools protocol -------------------------

class Browser:
    """A minimal CDP client — just enough to navigate, run JS, and screenshot."""

    def __init__(self, port: int = 9333):
        """Launch headless Chromium and open a DevTools websocket to it."""
        self.port = port
        self.proc = subprocess.Popen(
            ["chromium", "--headless=new", "--no-sandbox", "--disable-gpu",
             "--hide-scrollbars", "--remote-allow-origins=*",
             f"--remote-debugging-port={port}", "--user-data-dir=/tmp/pme-care-verify-profile",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        target = self._wait_for_target()
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=30)
        self._seq = 0
        self.send("Page.enable")
        self.send("Runtime.enable")

    def _wait_for_target(self) -> dict:
        for _ in range(40):
            try:
                targets = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list"))
                return next(t for t in targets if t["type"] == "page")
            except Exception:
                time.sleep(0.5)
        raise RuntimeError("chromium never came up")

    def send(self, method: str, **params):
        """Call one CDP method and block for its matching response."""
        self._seq += 1
        self.ws.send(json.dumps({"id": self._seq, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._seq:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expression: str):
        """Evaluate JS in the page; returns its value, or None on exception."""
        r = self.send("Runtime.evaluate", expression=expression,
                      awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: int = 30) -> bool:
        """Poll a JS boolean expression, so a slow cold start doesn't race a
        fixed sleep — the whole reason a free-tier instance needs this."""
        for _ in range(timeout):
            if self.js(expression):
                return True
            time.sleep(1)
        return False

    def screenshot(self, path: str) -> None:
        """Save a PNG of the current viewport to ``path``."""
        data = self.send("Page.captureScreenshot", captureBeyondViewport=True)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def close(self) -> None:
        """Kill the browser process; the profile directory is left behind."""
        self.proc.kill()


def browser_checks(base_url: str, is_demo: bool) -> None:
    """Drive the real page and assert what only a rendered browser can show."""
    print("\nBROWSER (390×844, a real phone viewport)")
    browser = Browser()
    try:
        browser.send("Emulation.setDeviceMetricsOverride",
                     width=390, height=844, deviceScaleFactor=2, mobile=True)
        browser.send("Emulation.setUserAgentOverride", userAgent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"))
        browser.send("Page.navigate", url=base_url)

        # Render's free tier can sit behind its own "Application loading"
        # interstitial for a cold start; poll for the real title rather than
        # guessing how long that takes.
        loaded = browser.wait_for('document.title === "PME Care"', timeout=90)
        check("page loads within 90s (cold start included)", loaded,
             f'title was {browser.js("document.title")!r}')

        no_scroll = browser.js(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        check("page never scrolls horizontally at phone width", bool(no_scroll))

        has_buttons = browser.wait_for(
            "[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Doctor')",
            timeout=15)

        if is_demo:
            check("demo notice shown on the login screen",
                 bool(browser.js("!!document.querySelector('.demo-notice')")))
            check("one-click role buttons present (demo mode)", has_buttons)

            browser.js(
                "[...document.querySelectorAll('button')]"
                ".find(b => b.textContent.trim() === 'Doctor').click()")
            signed_in = browser.wait_for("location.hash !== ''", timeout=15)
            check("signing in via a role button lands on a route", signed_in,
                 f'hash is {browser.js("location.hash")!r}')

            banner_visible = browser.js(
                "document.getElementById('demo-banner')?.classList.contains('visible')")
            check("persistent demo banner shows once signed in", bool(banner_visible))
        else:
            # The inverse of the demo checks: production must never expose a
            # one-click way into any role — that is the whole point of gating
            # these buttons on ENV.
            check("no one-click role buttons outside demo/development", not has_buttons)

        browser.screenshot("/tmp/pme-care-verify-screenshot.png")
        print("  (screenshot saved to /tmp/pme-care-verify-screenshot.png)")
    finally:
        browser.close()


def main() -> int:
    """Run every check against the given URL; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="Base URL of the deployment, e.g. https://pme-care.onrender.com")
    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    health = http_checks(base_url)
    browser_checks(base_url, is_demo=bool(health.get("demo")))

    print(f"\n{len(FAILURES)} failure(s)" if FAILURES else "\nAll checks passed.")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
