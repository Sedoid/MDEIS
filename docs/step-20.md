# Step 20: Portal and about-page look

Branch `step-20-portal-ui`, from `step-16b-portal`. Front end only. No tool, schema, contract or
dependency change. Nothing here has been run in a browser or on the server yet.

## What changed

| Path | Change |
| --- | --- |
| `src/portal/static/index.html` | Sign-in screen on its own; app shell with sidebar and three views (Upload, Files, Chat) |
| `src/portal/static/style.css` | Tokens for light and dark, system font stack, buttons, drop zones, table, narrow-screen layout |
| `src/portal/static/app.js` | View switcher on the address hash, theme toggle, show/hide password, drop zones, pills and labelled table cells, details block for review. API calls and the login hand-over are unchanged |
| `site/index.html` | Same look as the portal: neutral greys, system sans, dark by OS setting. Still no script, still one inline style block |

## Decisions

| ID | Decision | Why |
| --- | --- | --- |
| D20-1 | System font stack, no font file | The proxy policy allows only own files; a font would be a new asset |
| D20-2 | One view at a time on `#/upload`, `#/files`, `#/chat`; the shell fills the screen and only the content area scrolls | This removes the page scroll with a second scroll inside the chat frame |
| D20-3 | Chat stays in the iframe, full height in its own view | Owner's choice |
| D20-4 | Theme choice kept in `localStorage` under `mdeis-theme`, wrapped in try/catch | A per-viewer convenience; the page works without it |
| D20-5 | Status shown as a pill with text and a dot, never colour alone | Accessibility |
| D20-6 | The about page follows the OS light or dark setting only | Its policy allows no script, so no toggle |

## Static checks run here (not test runs)

- `node --check src/portal/static/app.js`: syntax ok.
- The page-file rules from `test_the_page_files_have_no_inline_script_or_style`, replayed by hand:
  no `<style`, `onclick=`, `javascript:`, `http://`, `https://` in `index.html`; no `innerHTML` or
  `eval(` in `app.js`; password field and `current-password` still present.
- `python3 scripts/scan_secrets.py`: clean.
- `python3 scripts/check_structure.py` lists `site/index.html`, `skills-lock.json` and `.agents/`
  as missing from `docs/STRUCTURE.md`. None of these is new in this step; the docs-keeper owns that file.

## Server run sheet

1. `git checkout step-20-portal-ui`
2. `pytest tests/unit/test_portal.py` . Expect all pass. Paste the output.
3. Bring the portal up the way step 19 does, open the address in a browser and check, in light
   and dark and at 360px, 768px and 1280px wide:
   - sign-in screen centred; wrong password shows the message under the form and focuses the password field
   - after sign-in the sidebar shows Upload, Files, Chat; Back and the browser address follow the views
   - **no page scroll and no double scrollbar in Chat**; only the content area scrolls elsewhere
   - choosing or dropping a PDF lists its name; upload shows progress under Files
   - the table turns into cards on a phone width; the empty state links to Upload
   - keyboard only: every control reachable, visible focus ring
   - `/mdeis-about/` renders with the new look and has no console errors
4. Paste back a screenshot or a description of anything that looks wrong.

## Open

- Needs docs-keeper: add `docs/step-20.md` to the map if the structure check lists it.

## Open WebUI: fewer buttons (added later)

`deploy/compose/webui.yml` now switches off, for the "user" role: attaching files, the Controls panel
(system prompt, parameters), temporary and shared chats, export, the workspace pages (models,
knowledge, prompts, tools) and the extra features (web search, images, code, notes, folders, API keys).
It also switches off, for everyone, code execution and interpreter, image generation, API keys,
memories, folders, message rating, analytics, user status, calendar and automations. The names are
from the Open WebUI environment reference. A file attached in the chat would reach the model without
the tools and the citation check, which is the reason files go through the upload page.

**Limits, said plainly:**
- Open WebUI shows an **administrator** more of the screen whatever these say. I could not confirm
  from the documentation which buttons an admin keeps; the attach button is the likely one. Check it
  with the administrator login and with a second account made with the "user" role (Admin Panel →
  Users). If the admin still sees it, tell me: the choices are to use a "user"-role account for the
  chat, or to hide it in the portal's chat frame, which is fragile.
- The Admin Panel and Settings screens are the product's own; there is no switch to hide individual
  items. Switching off a feature removes its entry.
- This is tidying, not a safety control. What keeps numbers and citations honest is the tool path,
  not which buttons are visible.

Server run sheet: `git pull`, `make test ARGS="tests/unit/test_webui_settings.py -q"`, then
`make dev-up` (the webui container is recreated), and sign in as the administrator and as a "user"
account. Paste what the message box and the "+" menu show for each.
