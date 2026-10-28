"""Check a running test installation using its configured administrator account."""

import argparse
from http.cookiejar import CookieJar
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


def check_application(url: str) -> None:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request(path: str, data: dict | None = None) -> tuple[int, str]:
        payload = urlencode(data).encode() if data is not None else None
        try:
            response = opener.open(Request(url.rstrip("/") + path, data=payload), timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.read().decode()

    def expect(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    expect(request("/health")[0] == 200, "Health check failed")
    for path in ("/docs", "/redoc", "/openapi.json"):
        expect(request(path)[0] == 404, "OpenAPI should be disabled")
    status, login = request("/login")
    expect(status == 200, "Login page failed")
    token = re.search(r'name="_csrf_token" value="([^"]+)"', login)
    expect(token is not None, "Login CSRF token missing")
    expect(request("/login", {})[0] == 403, "POST without CSRF was accepted")
    status, _ = request("/login", {
        "username": os.environ.get("INITIAL_ADMIN_USERNAME", "admin"),
        "password": os.environ["INITIAL_ADMIN_PASSWORD"],
        "_csrf_token": token.group(1),
    })
    expect(status == 200, "Login failed")
    status, admin = request("/admin/users")
    expect(status == 200 and 'action="/login"' not in admin, "Authenticated session failed")
    print("Application checks passed: startup, login, session, CSRF and disabled OpenAPI.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    check_application(parser.parse_args().url)
