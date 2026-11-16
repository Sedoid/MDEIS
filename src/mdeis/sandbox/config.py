"""Load the sandbox rules from `templates/sandbox-v1.yaml`.

Only `mdeis-mcp` reads the file (it needs `./templates:/templates:ro`). The runner gets the rules in
the request, so it holds no YAML reader. The lookup follows the gate file's: SANDBOX_FILE if set,
else the templates folder of the repo, else /templates.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .rules import Rules, SandboxRulesError

SANDBOX_ENV = "SANDBOX_FILE"  # names the rules file, if it is not in the usual place
SANDBOX_NAME = "sandbox-v1.yaml"
FOLDER_NAME = "templates"  # the folder of the repo that holds the file
CONTAINER_FOLDER = "/templates"  # where the compose files mount the templates folder


def rules_path() -> Path:
    named = os.environ.get(SANDBOX_ENV)
    if named:
        return Path(named)
    # Walk up from this file to the first folder that holds templates/<file>: the repo checkout.
    for folder in Path(__file__).resolve().parents:
        beside_source = folder / FOLDER_NAME / SANDBOX_NAME
        if beside_source.is_file():
            return beside_source
    return Path(CONTAINER_FOLDER) / SANDBOX_NAME


def load_rules(path: str | Path | None = None) -> Rules:
    """Read and check the file. Raises SandboxRulesError, with a plain message, for any fault."""
    where = Path(path) if path else rules_path()
    try:
        text = where.read_text(encoding="utf-8")
    except OSError as error:
        raise SandboxRulesError(f"cannot read the sandbox rules at {where}: {error}") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        message = f"the sandbox rules at {where} are not valid YAML: {error}"
        raise SandboxRulesError(message) from error
    return Rules.from_dict(data)
