#!/bin/sh
# n8n entrypoint for MDEIS. Imports the committed workflows, then hands over to the image's own
# entrypoint. Importing is repeatable: n8n matches workflows by id, so a restart updates them.
#
# Mounts (see deploy/compose/n8n.yml):  /workflows  = ./workflows   /mdeis = ./deploy/n8n

SRC=/workflows
if ! ls "$SRC"/*.json >/dev/null 2>&1; then
  echo "mdeis: no workflow files in $SRC, importing the echo workflow instead"
  SRC=/mdeis/echo
fi

if n8n import:workflow --separate --input="$SRC"; then
  echo "mdeis: imported workflows from $SRC"
  # Switch them on so their webhooks answer. The command was renamed between n8n versions,
  # so try both. If neither works, activate the workflow in the editor (this is spike T-024).
  n8n publish:workflow --all 2>/dev/null \
    || n8n update:workflow --all --active=true 2>/dev/null \
    || echo "mdeis: could not activate workflows from the command line"
else
  echo "mdeis: workflow import failed, starting n8n anyway" >&2
fi

exec tini -- /docker-entrypoint.sh
