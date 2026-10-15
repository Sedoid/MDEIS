#!/usr/bin/env python3
"""Does the upload page's sign-in check work with the real Open WebUI?

Run on the server, after `make dev-up`:   python3 scripts/probe_signin.py
Standard library only. It signs in as WEBUI_ADMIN_EMAIL from .env, then prints:
  1. the cookie Open WebUI sets (name and flags, never the value),
  2. what Open WebUI's session endpoint returns (field names, role; no token),
  3. whether Open WebUI allows being framed (the headers that decide it),
  4. what Open WebUI says an ordinary "user" may do (the permission settings in webui.yml),
  5. what the portal says when that cookie is sent to it, and with no cookie.
It prints no password and no token. Paste the whole output back.
"""
import json
import sys
import urllib.error
import urllib.request

from envfile import ROOT, parse_env


def call(url, headers=None, body=None):
    request = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, reply.headers, reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()
    except OSError as error:
        return None, {}, str(error).encode()


def main() -> int:
    if not (ROOT / ".env").exists():
        print("FAIL: .env is missing")
        return 1
    env = parse_env()
    host = (env.get("HOST_BIND") or "127.0.0.1").strip()
    chat = f"http://{host}:{(env.get('WEBUI_PORT') or '3000').strip()}"
    portal = f"http://{host}:{(env.get('PORTAL_PORT') or '8080').strip()}"
    login = json.dumps({"email": env.get("WEBUI_ADMIN_EMAIL", ""),
                        "password": env.get("WEBUI_ADMIN_PASSWORD", "")}).encode()

    status, headers, body = call(chat + "/api/v1/auths/signin",
                                 {"Content-Type": "application/json"}, login)
    print(f"1. sign in at {chat}/api/v1/auths/signin -> HTTP {status}")
    if status != 200:
        print("   FAIL: could not sign in; check WEBUI_ADMIN_EMAIL and WEBUI_ADMIN_PASSWORD")
        return 1
    signin = json.loads(body)
    print("   fields in the answer:", sorted(signin))
    token = signin.get("token", "")
    cookies = headers.get_all("Set-Cookie") or []
    if not cookies:
        print("   no Set-Cookie header on the sign-in answer")
    for cookie in cookies:
        name, _, rest = cookie.partition("=")
        flags = [part.strip() for part in rest.split(";")[1:]]
        print(f"   cookie '{name}' flags: {flags}")

    status, _, body = call(chat + "/api/v1/auths/", {"Authorization": f"Bearer {token}"})
    print(f"2. session check {chat}/api/v1/auths/ with the token -> HTTP {status}")
    if status == 200:
        data = json.loads(body)
        print("   fields:", sorted(data), "| role:", data.get("role"))

    status, headers, _ = call(chat + "/")
    print(f"3. framing: GET {chat}/ -> HTTP {status}")
    print("   X-Frame-Options:", headers.get("X-Frame-Options"))
    csp = headers.get("Content-Security-Policy") or ""
    print("   frame-ancestors in CSP:", [p.strip() for p in csp.split(";") if "frame-anc" in p])

    status, _, body = call(chat + "/api/v1/users/default/permissions",
                           {"Authorization": f"Bearer {token}"})
    print(f"4. what a 'user' may do, from Open WebUI: HTTP {status}")
    if status == 200:
        for group, items in json.loads(body).items():
            print(f"   {group}: " + ", ".join(f"{k}={v}" for k, v in items.items()))

    cookie_name = (cookies[0].partition("=")[0] if cookies else "token")
    status, _, body = call(portal + "/api/me", {"Cookie": f"{cookie_name}={token}"})
    print(f"5. portal {portal}/api/me with the cookie -> HTTP {status} {body.decode()[:200]}")
    status, _, _ = call(portal + "/api/me")
    print(f"   portal /api/me with no cookie -> HTTP {status} (expect 401)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
