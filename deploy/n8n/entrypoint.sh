#!/bin/sh
# n8n entrypoint for MDEIS. Imports the committed workflows, publishes them, then hands over to
# the image's own entrypoint. Importing is repeatable: n8n matches workflows by id.
#
# Mounts (see deploy/compose/n8n.yml):  /workflows = ./workflows   /mdeis = ./deploy/n8n
#
# Every step prints its own output, so `docker logs n8n-mdeis | grep mdeis:` shows what happened.
# A workflow that is imported but not published answers 404 on its webhook.

SRC=/workflows
if ! ls "$SRC"/*.json >/dev/null 2>&1; then
  echo "mdeis: no workflow files in $SRC, importing the echo workflow instead"
  SRC=/mdeis/echo
fi

if n8n import:workflow --separate --input="$SRC"; then
  echo "mdeis: imported workflows from $SRC"
  # Publish each workflow by id. The command name changed between n8n versions, so try both.
  for f in "$SRC"/*.json; do
    id="$(node -e 'process.stdout.write(String(JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")).id || ""))' "$f")"
    if [ -z "$id" ]; then echo "mdeis: $f has no id, cannot publish it" >&2; continue; fi
    if n8n publish:workflow --id="$id"; then
      echo "mdeis: published workflow $id (publish:workflow)"
    elif n8n update:workflow --id="$id" --active=true; then
      echo "mdeis: activated workflow $id (update:workflow)"
    else
      echo "mdeis: could not publish workflow $id from the command line (see the lines above)" >&2
    fi
  done
else
  echo "mdeis: workflow import failed, starting n8n anyway" >&2
fi

exec tini -- /docker-entrypoint.sh
