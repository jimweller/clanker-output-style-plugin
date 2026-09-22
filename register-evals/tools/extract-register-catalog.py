#!/usr/bin/env python3
"""Pulls the Chat Register section out of the global instruction file.

`<clanker-register>` wraps the section body, the same device `<prose-contract>`
uses and for the same reason: cutting on the tag rather than on the
`## Chat Register` heading string means a renamed heading no longer breaks
extraction. Each rule line opens with a `CR-` id, the same convention `PC-`
ids use for the prose contract, added for this eval so a judge and a report
can name a rule exactly instead of by free text.

Source of truth is the dotfiles repo, a parent of this one. Override with
CONTRACT_FILE when running from somewhere else.

Usage
    tools/extract-register-catalog.py [--ids] [OUT_FILE]
"""

import os
import pathlib
import re
import sys

EVAL_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_CONTRACT = EVAL_ROOT.parents[2] / "configs" / "claude-code" / "claude_md.md"
BLOCK = re.compile(r"<clanker-register>(.*?)</clanker-register>", re.DOTALL)
RULE_ID = re.compile(r"^- `(CR-[a-z0-9-]+)`", re.MULTILINE)


def contract_path() -> pathlib.Path:
    override = os.environ.get("CONTRACT_FILE")
    return pathlib.Path(override) if override else DEFAULT_CONTRACT


def extract(text: str) -> str:
    blocks = BLOCK.findall(text)
    if not blocks:
        raise LookupError("no <clanker-register> block")
    if len(blocks) > 1:
        raise LookupError(f"{len(blocks)} <clanker-register> blocks, expected 1")
    return blocks[0].strip()


def main() -> int:
    argv = sys.argv[1:]
    listing = "--ids" in argv
    argv = [a for a in argv if a != "--ids"]

    path = contract_path()
    if not path.is_file():
        print(f"contract not found at {path}", file=sys.stderr)
        return 1

    try:
        body = extract(path.read_text())
    except LookupError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return 1

    if listing:
        for rule_id in sorted(set(RULE_ID.findall(body))):
            print(rule_id)
        return 0

    if argv:
        pathlib.Path(argv[0]).write_text(body)
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
