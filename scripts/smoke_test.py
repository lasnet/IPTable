"""Check a running test installation using its configured administrator account."""

import argparse
from http.cookiejar import CookieJar
import os
import re
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, HTTPSHandler, Request, build_opener


def check_application(url: str, ca_file: str | None = None, http_url: str | None = None,
                      https_port_http_url: str | None = None) -> None:
    context = ssl.create_default_context(cafile=ca_file)
    opener = build_opener(HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=context))

    def request(path: str, data: dict | None = None) -> tuple[int, str]:
        payload = urlencode(data).encode() if data is not None else None
        try:
            response = opener.open(Request(url.rstrip("/") + path, data=payload), timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            if re.search(r"nginx/[\d.]", response.headers.get("Server", ""), re.IGNORECASE):
                raise RuntimeError("Nginx version is exposed")
            return response.status, response.read().decode()

    def expect(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    redirect_client = build_opener(NoRedirect())
    for source in (http_url, https_port_http_url):
        if source:
            path = "/login?redirect_probe=1&next=inventory"
            try:
                response = redirect_client.open(source.rstrip("/") + path, timeout=15)
            except HTTPError as exc:
                response = exc
            with response:
                expect(response.status == 308, "HTTP did not redirect with status 308")
                expect(response.headers.get("Location") == url.rstrip("/") + path,
                       "HTTP redirect lost the public origin, port, path or query")
                evidence = response.headers.get("Server", "") + response.read().decode()
                expect(not re.search(r"nginx/[\d.]", evidence, re.IGNORECASE), "Nginx version is exposed")

    health_status, _ = request("/health")
    expect(health_status == 200, f"Health check failed: HTTP {health_status}")
    for path in ("/docs", "/redoc", "/openapi.json"):
        expect(request(path)[0] == 404, "OpenAPI should be disabled")
    status, login = request("/login")
    expect(status == 200, "Login page failed")
    assets = re.findall(r'<(?:link|script)\b[^>]*(?:href|src)="([^"]+)"', login)
    expect(any("styles.css" in asset for asset in assets), "Stylesheet link missing")
    expect(any("app.js" in asset for asset in assets), "Application script missing")
    origin = urlsplit(url)
    for asset in assets:
        target = urlsplit(urljoin(url.rstrip("/") + "/login", asset))
        expect((target.scheme, target.netloc) == (origin.scheme, origin.netloc),
               "Asset does not use the application's origin (possible mixed content)")
        with opener.open(target.geturl(), timeout=15) as resource:
            expected_type = "text/css" if target.path.endswith(".css") else "javascript"
            expect(resource.status == 200 and expected_type in resource.headers.get("Content-Type", ""),
                   "Stylesheet or script could not be loaded")
            expect(bool(resource.read()), "Empty application asset")
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
    print("Application checks passed: assets, startup, login, session, CSRF and disabled OpenAPI.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--ca-file", help="Trusted local test CA certificate; TLS verification stays enabled")
    parser.add_argument("--http-url", help="HTTP origin expected to redirect to --url")
    parser.add_argument("--https-port-http-url", help="Plain HTTP sent to the TLS port must also redirect")
    arguments = parser.parse_args()
    check_application(arguments.url, arguments.ca_file, arguments.http_url, arguments.https_port_http_url)
