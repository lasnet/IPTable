"""Non-destructive HTTPS/auth checks against an isolated release candidate."""

import argparse
from configparser import ConfigParser
from http.cookiejar import CookieJar
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener


def check_release(url: str, ca: str, env_file: Path) -> None:
    if urlsplit(url).scheme != "https":
        raise ValueError("Release checks require HTTPS")
    # Generated deployment env files contain unquoted, single-line values only.
    config = ConfigParser(interpolation=None)
    config.read_string("[deployment]\n" + env_file.read_text(encoding="utf-8"))
    credentials = config["deployment"]
    jar = CookieJar()
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=ca)), HTTPCookieProcessor(jar))

    def request(path: str, data: dict | None = None) -> tuple[int, str, object]:
        payload = urlencode(data).encode() if data is not None else None
        try:
            response = opener.open(Request(url.rstrip("/") + path, data=payload), timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read().decode(), response.headers

    def expect(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    status, _, headers = request("/health")
    expect(status == 200, "Health endpoint failed")
    expect(headers.get("X-Content-Type-Options") == "nosniff", "Missing security headers")
    expect(bool(headers.get("Strict-Transport-Security")), "Missing HSTS")
    for path in ("/docs", "/redoc", "/openapi.json"):
        expect(request(path)[0] == 404, f"Production endpoint exposed: {path}")
    status, login, _ = request("/login")
    expect(status == 200, "Login page failed")
    match = re.search(r'name="_csrf_token" value="([^"]+)"', login)
    expect(match is not None, "Missing login CSRF token")
    expect(any(cookie.secure for cookie in jar), "Session cookie is not Secure")
    expect(request("/login", {"username": "smoke-check"})[0] == 403, "Missing CSRF accepted")
    status, _, _ = request("/login", {
        "username": credentials["INITIAL_ADMIN_USERNAME"],
        "password": credentials["INITIAL_ADMIN_PASSWORD"],
        "_csrf_token": match.group(1),
    })
    expect(status == 200, "Administrator login failed")
    status, admin, _ = request("/admin/users")
    expect(status == 200 and 'action="/login"' not in admin, "Administrator session failed")
    print("Release smoke checks passed: HTTPS, health, headers, CSRF, login and OpenAPI exposure.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--ca", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    check_release(args.url, args.ca, args.env_file)
