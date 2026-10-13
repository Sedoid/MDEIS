# Step 19: the upload portal

Branch `step-16b-portal`, made from `step-13-evals`. This is what Step 16 should have been: a page
where a signed-in person uploads report cards (and, if an administrator, the methodology), watches
each file get read, and opens the chat to carry on from there. The public "about" page from
Step 16 is separate and stays as it is.

## What was built

| Piece | Where |
| --- | --- |
| Service: sign-in check, CSRF, upload, status | `src/portal/app.py`, `auth.py`, `security.py`, `settings.py`, `store.py` |
| Page (no framework, no inline script or style) | `src/portal/static/index.html`, `app.js`, `style.css` |
| Tests, written first, no network and no database | `tests/unit/test_portal.py` |
| Compose and env | `deploy/compose/portal.yml`, `.env.example`, `pyproject.toml` (static files in the package) |
| Spike for what I could not check | `scripts/probe_signin.py`, `make probe-signin` |

## How it works

1. You sign in to Open WebUI as before. The browser keeps Open WebUI's login cookie.
2. The portal page (same host name, other port) sends that cookie back to the portal. The portal
   asks Open WebUI, from inside the network, who owns it (`GET /api/v1/auths/`) and reads the id,
   name and role. No cookie, a wrong one, or a role other than `admin` or `user` means 401.
3. An upload is a raw request body (no `python-multipart`, so no new library; D-014 is still open for
   it). The kind is in the address, the file name in a header. The portal checks, in this order:
   signed in (401) → kind (400) → methodology needs admin (403) → CSRF token and same origin (403) →
   extension (415) → size while streaming (413, nothing kept) → empty (400) → first bytes are `%PDF-`
   or valid text (415) → rename into the inbox under a generated name → add the `documents` row as
   `pending`. If the row cannot be added the file is deleted (503).
4. `ingestd` reads it as before. The page shows the status every 2 s while anything is waiting, with
   the failure in plain words and, for a methodology that needs review, the list of changes and a
   button that copies `make ack DOC=D-…`.
5. The portal runs as database role `portal_app` (insert and select on `documents`, select on
   `document_issues`). It holds no model key and not the approver-token secret.

## Decisions I made so you did not have to (change any of them)

- **Accounts are Open WebUI's own.** No second login. Anyone Open WebUI calls `admin` may upload the
  methodology; any other signed-in user may upload report cards and sees only their own files.
- **Acknowledging a methodology stays on the command line** (`make ack`). The page shows the changes
  and gives the command. A button in the page is a later, separate choice: the person who
  acknowledges is named in the log, so it needs to be designed with the approver rule in mind.
- **Bare IP for now.** Open the chat and the portal with the same host name (both
  `http://<server-ip>:…` or both `http://127.0.0.1:…` through an SSH tunnel). Cookies are shared by
  host name, not by port, but `127.0.0.1` and `localhost` are different names.
- **Chat frame is on by default**, with an "Open chat in a new tab" link always shown. If Open WebUI
  refuses to be framed, set `PORTAL_FRAME_CHAT=false` and the page shows only the link.

## What is NOT verified (this is why there is a spike)

I have not seen Open WebUI v0.11.4 answer these, so the code assumes them and the spike checks them:

1. The sign-in cookie is called `token`. (`WEBUI_COOKIE_NAME` changes it.)
2. `GET /api/v1/auths/` with `Authorization: Bearer <token>` returns `id`, `name` and `role`.
3. The cookie set by `:3000` is sent to `:8080` on the same host name.
4. Open WebUI allows itself to be shown in a frame (no `X-Frame-Options: DENY`).

If 2 is wrong, `read_user` in `src/portal/auth.py` is the one place to change. If 3 is wrong, the
fallback is a sign-in form on the portal itself (a separate decision). If 4 is wrong, use the link.

## Server run sheet

On the server, on branch `step-16b-portal`:

```
git fetch && git checkout step-16b-portal && git pull
make lint
make test ARGS="tests/unit/test_portal.py -q"
```

Expect `make lint` clean and the portal tests all passing (about 30). If a test fails, paste the
output; do not edit the test.

Then rebuild and start, and run the spike:

```
make dev-up
make dev-ps
make probe-portal
make probe-signin
```

Expect `make probe-portal` to print `{"status":"ok","problems":[]}`. Paste the whole of
`make probe-signin` back. It prints cookie flags, field names and header names, never a password or
a token.

Then by hand, in a browser (same host name for both):

1. Open `http://<host>:3000`, sign in. Open `http://<host>:8080`. You should see your name, two
   upload boxes (methodology only as administrator) and the chat.
2. Open the portal in a private window, signed out: it should say "Not signed in" and offer no boxes.
3. Upload one card from `data/synthetic/cards/`. It should go Waiting → Being read → Read. Ask the
   chat about that learner.
4. Upload a text file renamed `x.pdf`: it should be refused. Upload the same card twice: the second
   says it is the same file and adds nothing.
5. Upload a methodology Markdown as administrator: expect "Read; a person must check it", the list of
   changes, and the copy button. Run the copied `make ack` command, and the status turns to Read.

Paste back: the lint and test output, the `probe-signin` output, and what you saw in steps 1 to 5.

## Needs from you (I cannot edit these)

- `docs/STRUCTURE.md` has no row for `site/` (it fails `check_structure.py`, from Step 16). The
  portal files are covered by the existing `src/portal/` row.
- `.env`: the three new variables are optional because the compose file has defaults. On a server
  set `WEBUI_PUBLIC_URL=http://<server-ip>:3000` so the chat link goes to the right place, and make
  sure the provider firewall allows ports 3000 and 8080.

## Reaching it on a bare IP (added after the first server run)

`make probe-signin` passed all four checks, so the sign-in design holds. The browser could not open
`ip:3000` or `ip:8080`: only port 8088 (the Caddy proxy from Step 16) answers from outside, and the
other ports are not open to the internet. So the chat and the upload page now go through that one
port, with a new proxy file `deploy/caddy/Caddyfile.ip`:

```
http://13.140.150.214:8088/          the chat (Open WebUI)
http://13.140.150.214:8088/portal/   the upload page
http://13.140.150.214:8088/mdeis-about/
```

One host name and one port also means the sign-in cookie reaches the upload page without any
cross-port question. The page's own addresses are now relative so that it works under `/portal/`.
This is plain http: the sign-in password and chat text are not encrypted. Sign-up is off, so only the
accounts you made can sign in. Treat it as a demo; a domain name and the full Caddyfile fix that.

On the server:

```
git pull
make env        # adds the new names to .env if they are missing
CADDYFILE=Caddyfile.ip WEBUI_PUBLIC_URL=http://13.140.150.214:8088 \
  PORTAL_PUBLIC_URL=http://13.140.150.214:8088 \
  python3 scripts/set_env.py CADDYFILE WEBUI_PUBLIC_URL PORTAL_PUBLIC_URL
make dev-up
make prod-down && make prod-up
```
