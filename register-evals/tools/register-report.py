#!/usr/bin/env python3
"""Summarizes a Chat Register eval run: clean rate, per-rule violation counts,
and correctness pass rate, broken out per provider (with-plugin vs baseline).

The provider script prints one artifact per run:

    <<<REPLY>>>
    ...
    <<<FINDINGS>>>
    FINDING | CR-id | span | why
    VERDICT violations=N
    <<<CORRECTNESS>>>
    CORRECTNESS pass|fail: reason

This reads promptfoo's -o JSON output, pulls each run's provider label and raw
output, parses the FINDINGS and CORRECTNESS blocks, and tallies both. A
register clean rate improvement that costs correctness is not a win; the two
numbers are reported side by side for exactly that reason.

Usage
    tools/register-report.py RESULT_JSON
"""

import collections
import json
import re
import sys

SECTION = re.compile(r"<<<(REPLY|FINDINGS|CORRECTNESS)>>>")
VERDICT = re.compile(r"VERDICT\s+violations=(\d+)", re.I)
FINDING = re.compile(r"^FINDING\s*\|\s*([^|]+)\|", re.MULTILINE)
CORRECTNESS = re.compile(r"CORRECTNESS\s+(pass|fail)", re.I)


def split_artifact(text: str) -> dict:
    parts, last, pos = {}, None, 0
    for m in SECTION.finditer(text):
        if last:
            parts[last] = text[pos : m.start()].strip()
        last, pos = m.group(1), m.end()
    if last:
        parts[last] = text[pos:].strip()
    return parts


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    data = json.load(open(sys.argv[1]))
    results = data.get("results", {}).get("results", data.get("results", []))
    if isinstance(results, dict):
        results = results.get("results", [])

    by_arm = collections.defaultdict(lambda: {
        "runs": 0, "clean": 0, "unparsed": 0, "rules": collections.Counter(),
        "correct": 0, "incorrect": 0, "correctness_unparsed": 0,
    })

    for r in results:
        provider = r.get("provider", {})
        label = provider.get("label") if isinstance(provider, dict) else provider
        label = label or "unknown"
        output = r.get("response", {}).get("output", "") or ""
        arm = by_arm[label]
        arm["runs"] += 1

        parts = split_artifact(output)
        findings_block = parts.get("FINDINGS", output)
        vm = VERDICT.search(findings_block)
        if not vm:
            arm["unparsed"] += 1
        else:
            n = int(vm.group(1))
            if n == 0:
                arm["clean"] += 1
            for m in FINDING.finditer(findings_block):
                arm["rules"][m.group(1).strip()] += 1

        correctness_block = parts.get("CORRECTNESS", "")
        cm = CORRECTNESS.search(correctness_block)
        if not cm:
            arm["correctness_unparsed"] += 1
        elif cm.group(1).lower() == "pass":
            arm["correct"] += 1
        else:
            arm["incorrect"] += 1

    for label, stats in sorted(by_arm.items()):
        runs = stats["runs"]
        clean_pct = 100 * stats["clean"] / runs if runs else 0.0
        graded = stats["correct"] + stats["incorrect"]
        correct_pct = 100 * stats["correct"] / graded if graded else 0.0
        print(f"== {label} ==  runs={runs}  clean={stats['clean']} ({clean_pct:.0f}%)  "
              f"correct={stats['correct']}/{graded} ({correct_pct:.0f}%)  unparsed={stats['unparsed']}")
        for rule, count in stats["rules"].most_common():
            print(f"  {rule:24s} {count}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
