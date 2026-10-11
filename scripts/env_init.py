#!/usr/bin/env python3
"""Create .env from .env.example and fill the [generate] values with random strings.

Never overwrites an existing .env. Prints variable names only, never values.
Standard library only.
"""
import secrets
import sys

from envfile import PLACEHOLDER, ROOT, TAG_RE, VAR_RE, parse_example


def main() -> int:
    example = ROOT / ".env.example"
    target = ROOT / ".env"
    if not example.exists():
        print("FAIL: .env.example not found")
        return 1
    if target.exists():
        print(".env already exists. Not touching it. (Delete it first if you want a fresh one.)")
        return 0

    generated, still_yours = [], []
    out_lines = []
    tag = "default"
    for line in example.read_text(encoding="utf-8").splitlines():
        m = TAG_RE.match(line)
        if m:
            tag = m.group(1)
            out_lines.append(line)
            continue
        m = VAR_RE.match(line)
        if m:
            name, value = m.group(1), m.group(2).strip()
            if tag == "generate" and value == PLACEHOLDER:
                line = f"{name}={secrets.token_hex(24)}"
                generated.append(name)
            elif tag == "you" and value == PLACEHOLDER:
                still_yours.append(name)
            tag = "default"
        out_lines.append(line)

    target.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    target.chmod(0o600)
    print("Created .env (permissions 600).")
    print(f"Generated {len(generated)} random values: {', '.join(generated)}")
    if still_yours:
        print("You still need to set these by hand:")
        for name in still_yours:
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
