# Step 16: the public website

Branch: `step-16-website` (from `step-15-class-view`). Status: files written, **not deployed, not
run**. I cannot reach your server or a domain from here, so nothing below has been tried over a
network. The page and the proxy settings have static tests that pass without running anything.

**Goal.** A stranger, such as a judge, can open one address, read what MDEIS is in plain words, and
reach the demo. Only one container is public.

## What is public, and what is not

| Address | What | How |
| --- | --- | --- |
| `https://APP_HOST/` | Open WebUI (the assistant). Sign-up is closed; you create the accounts | the proxy forwards to `webui:8080` |
| `https://APP_HOST/mdeis-about/` | `site/index.html`: what MDEIS does and will not do, the eight promises, the repository link. No script, no outside request | served from the `site/` folder |
| anything else | nothing. The gateway, orchestrator, toolbox, lookup server, n8n, the database and the upload page have no published port and are not named in the proxy file | `deploy/caddy/Caddyfile` |

## The plan

1. The page, held to the project's own rules (made-up-data notice, no label, no test result claimed).
2. A reverse proxy (Caddy) in a Compose profile that is off by default, with automatic certificates.
3. `make prod-config`, `make prod-up`, `make prod-down`, and `make prod-check` (run from another machine).
4. Instructions for putting it on your server.

## Decisions I took (you agreed to my defaults; change any of them and tell me)

| ID | Decision | Why |
| --- | --- | --- |
| D16-1 | Caddy, image `caddy:2.8.4-alpine` (override with `CADDY_IMAGE`), in the `prod` profile | One small file, automatic certificates, WebSockets and streaming work without extra settings. It is a new third-party image, which the deploy rules make a decision for you: say if you prefer another |
| D16-2 | The proxy binds to `127.0.0.1` unless `PROXY_BIND` says otherwise. **Making it public is a step you do on purpose**: `PROXY_BIND=0.0.0.0` in the server's `.env` | Nothing becomes public by accident. A public port is a decision for you |
| D16-3 | One host name, with the page under `/mdeis-about/` | Open WebUI owns the root of the site, and `/mdeis-about/` is not one of its routes |
| D16-4 | The page is plain HTML with its own small style sheet, no script, no font or image from elsewhere, and a strict content policy from the proxy | Nothing to break, nothing to track visitors |
| D16-5 | The page links to the repository and to `EVALS.md` and quotes no result | Results are in the repository file as real runs; the page must not run ahead of them |
| D16-6 | `WEBUI_SESSION_COOKIE_SECURE` (default `false`, `true` on the public server) | The sign-in cookie should only travel over https once there is https. Check the name against the Open WebUI reference for the tag you use |
| D16-7 | `make prod-check HOST=...` is run from another machine and checks the page, the headers, the redirect from http, and that nine private ports are closed | What a stranger sees is what counts |

## What was built

| File | What |
| --- | --- |
| `site/index.html` | the page |
| `deploy/caddy/Caddyfile` | the proxy settings |
| `deploy/compose/proxy.yml` | the `proxy` service, profile `prod` |
| `deploy/compose/webui.yml` | the cookie setting |
| `scripts/check_public.py`, `Makefile` | `make prod-config`, `prod-up`, `prod-down`, `prod-check` |
| `tests/unit/test_site.py` | static checks of the page and the proxy files |

## Put it on the server (a human step)

1. A domain name pointing at the server (an `A` record). Open ports 80 and 443 in the firewall and
   nothing else from outside.
2. In the server's `.env` (not committed): `APP_HOST=demo.example.org`, `PROXY_BIND=0.0.0.0`,
   `WEBUI_SESSION_COOKIE_SECURE=true`. Optional: `CADDY_IMAGE`, `PROXY_HTTP_PORT`, `PROXY_HTTPS_PORT`.
3. `make prod-config` (expect "OK: compose files merge with the prod profile" and Caddy's
   "Valid configuration").
4. `make dev-up && make load-criteria`, then `make prod-up`. The first start asks Let's Encrypt for a
   certificate; `make dev-logs SERVICE=proxy` shows it.
5. In Open WebUI (sign in as the admin from `.env`): create the demo accounts as ordinary users with
   strong passwords. Give them to the judges privately; never put them in the repository or the page.
6. From your laptop, not the server: `make prod-check HOST=demo.example.org`.
7. In your OpenRouter account set a spending limit on the key. Anyone with an account can start model
   calls, and the proxy has no rate limit of its own.

## Server test process

```bash
git fetch && git checkout step-16-website && git pull
make test ARGS="tests/unit/test_site.py -q"
make lint
make prod-config                       # on the server, with APP_HOST set
```

Then the steps above. **What "right" looks like:** the test file is green; `make prod-config` is
clean; after `make prod-up` the page opens over https at `/mdeis-about/`; `make prod-check` prints
`PUBLIC CHECK OK`; `make dev-health` still passes (the proxy is not in its list of services).

**What to paste back if something fails:** `make prod-config` output; `make dev-logs SERVICE=proxy`
(it holds no cookies or keys); `make prod-check` output.

## Limits

- No rate limiting and no web application firewall in front of the sign-in page. Caddy's core has no
  rate limit; a plug-in would be a new dependency.
- The page has no screenshot or video: I have none. Adding one means a file in `site/` that you
  made and checked for real data.
- Certificates need a public name and open ports. On a machine without them, use `APP_HOST=localhost`
  (Caddy makes its own local certificate, which a browser will warn about).
- `docs/PUBLISH.md` (yours, local) is where the repository goes public; this step does not do that.

## Needs your edit (contract docs are not edited by me)

- `docs/CONTAINERS.md`: the `proxy` container, the `prod` profile, and the new variables `APP_HOST`,
  `PROXY_BIND`, `PROXY_HTTP_PORT`, `PROXY_HTTPS_PORT`, `CADDY_IMAGE`, `WEBUI_SESSION_COOKIE_SECURE`.
- `.env.example`: I cannot edit it. Suggested: `APP_HOST=localhost`, `PROXY_BIND=127.0.0.1`,
  `WEBUI_SESSION_COOKIE_SECURE=false`.
