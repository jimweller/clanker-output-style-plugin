#!/usr/bin/env python3
"""Summarizes a Clanker Register eval run per promptfoo column (with-plugin, baseline).

Reads both shapes of run. A graded row carries the promptfoo judge's GradingResult, with
namedScores and one componentResults entry per finding, verdict, and judge run. A legacy
row carries the old exec provider's text artifact, which is read as a single judge pass.

For graded rows it recomputes every rate twice: once from each row's findings and
verdicts, checked against that row's namedScores, and once as column sums, checked
against promptfoo's derived metrics. It exits 1 on any mismatch, a cached row, a judge
error, or a writer or isolation error, because a number built on any of those is wrong.

Findings default to the per-row majority, the keys at least floor(P/2)+1 passes reported.
--findings union counts a key any single pass reported.

Usage
    tools/register-report.py RESULT_JSON [--findings maj|union]
"""

import argparse
import collections
import json
import re
import sys

DERIVED = {
    "clean_maj_rate": ("reg_clean_maj", "reg_judged"),
    "clean_union_rate": ("reg_clean_union", "reg_judged"),
    "clean_pass_rate": ("reg_pass_clean", "reg_passes"),
    "pass_agree_rate": ("reg_pass_agree", "reg_judged"),
    "correct_maj_rate": ("cor_pass_maj", "cor_judged"),
}
ROW_ERRORS = ("ISOLATION_BREACH", "WRITER_ERROR")

SECTION = re.compile(r"<<<(REPLY|FINDINGS|CORRECTNESS)>>>")
VERDICT = re.compile(r"VERDICT\s+violations=(\d+)", re.I)
FINDING = re.compile(r"^FINDING\s*\|\s*([^|]+)\|", re.MULTILINE)
CORRECTNESS = re.compile(r"CORRECTNESS\s+(pass|fail)", re.I)


def label_of(row):
    p = row.get("provider") or {}
    return (p.get("label") or p.get("id") or "unknown") if isinstance(p, dict) else str(p)


def split_artifact(text):
    parts, last, pos = {}, None, 0
    for m in SECTION.finditer(text):
        if last:
            parts[last] = text[pos:m.start()].strip()
        last, pos = m.group(1), m.end()
    if last:
        parts[last] = text[pos:].strip()
    return parts


def legacy(row):
    parts = split_artifact((row.get("response") or {}).get("output") or "")
    block = parts.get("FINDINGS", "")
    vm = VERDICT.search(block)
    cm = CORRECTNESS.search(parts.get("CORRECTNESS", ""))
    rules = {m.group(1).strip() for m in FINDING.finditer(block)}
    clean = int(vm is not None and int(vm.group(1)) == 0)
    scores = {}
    if vm:
        scores.update({"reg_judged": 1, "reg_passes": 1, "reg_pass_clean": clean, "reg_clean_maj": clean, "reg_clean_union": clean, "reg_pass_agree": 1})
    if cm:
        ok = int(cm.group(1).lower() == "pass")
        scores.update({"cor_judged": 1, "cor_passes": 1, "cor_pass_votes": ok, "cor_pass_maj": ok})
    return scores, {"maj": rules, "union": rules}, []


def graded(row):
    g = row.get("gradingResult") or {}
    scores = g.get("namedScores") or {}
    comps = g.get("componentResults") or []
    findings = [c["metadata"] for c in comps if (c.get("metadata") or {}).get("role") == "finding" and c["metadata"].get("judge_kind") == "register"]
    verdicts = [c["metadata"] for c in comps if (c.get("metadata") or {}).get("role") == "verdict" and c["metadata"].get("judge_kind") == "correctness"]
    rules = {"maj": {f["key"] for f in findings if f["majority"]}, "union": {f["key"] for f in findings}}
    problems = []
    if scores.get("reg_judged"):
        p = scores["reg_passes"]
        m = p // 2 + 1
        dirty = {x["pass_index"] for f in findings for x in f["passes"]}
        pass_clean = p - len(dirty)
        expect = {"reg_pass_clean": pass_clean, "reg_clean_maj": int(pass_clean >= m), "reg_clean_union": int(pass_clean == p),
                  "reg_pass_agree": int(pass_clean in (0, p))}
        for f in findings:
            expect[f"R:{f['key']}"] = int(f["votes"] >= m)
        for k, v in expect.items():
            if scores.get(k, 0) != v:
                problems.append(f"INCONSISTENT {k}={scores.get(k)} but the findings give {v}")
    if scores.get("cor_judged"):
        votes = sum(1 for v in verdicts if v["verdict"] == "pass")
        if len(verdicts) != scores["cor_passes"] or votes != scores["cor_pass_votes"]:
            problems.append(f"INCONSISTENT cor_pass_votes={scores['cor_pass_votes']} but {len(verdicts)} verdicts give {votes}")
    return scores, rules, problems


def is_graded(row):
    comps = (row.get("gradingResult") or {}).get("componentResults") or []
    return any((c.get("metadata") or {}).get("role") == "judge" for c in comps)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result_json")
    ap.add_argument("--findings", choices=("maj", "union"), default="maj")
    args = ap.parse_args(argv)

    with open(args.result_json) as f:
        data = json.load(f)
    rows = data["results"]["results"]
    prompts = {p.get("provider"): (p.get("metrics") or {}).get("namedScores") or {} for p in data["results"].get("prompts") or []}

    cols = collections.OrderedDict()
    problems = []
    for i, row in enumerate(rows):
        label = label_of(row)
        col = cols.setdefault(label, {"rows": 0, "adapters": set(), "sums": collections.Counter(), "rules": collections.Counter(),
                                      "meta": collections.Counter(), "meta_rows": 0, "writer_cost": 0.0, "judge_cost": 0.0})
        col["rows"] += 1
        resp = row.get("response") or {}
        # promptfoo also fills row.error with an assertion's failure reason, so only a row
        # that never reached grading is a provider error, whatever its text says.
        err = str(row.get("error") or resp.get("error") or "")
        if err and (not row.get("gradingResult") or row.get("failureReason") == 2):
            kind = next((e for e in ROW_ERRORS if err.startswith(e)), "PROVIDER_ERROR")
            problems.append(f"{kind} row {i} ({label}): {err[:300]}")
            continue
        if resp.get("cached"):
            problems.append(f"CACHED row {i} ({label})")
        adapter = "graded" if is_graded(row) else "legacy"
        col["adapters"].add(adapter)
        scores, rules, row_problems = graded(row) if adapter == "graded" else legacy(row)
        problems += [f"{p} (row {i}, {label})" for p in row_problems]
        for k, v in scores.items():
            if k.endswith("_judge_error") and v:
                problems.append(f"JUDGE_ERROR row {i} ({label}): {(row.get('gradingResult') or {}).get('reason', '')}")
        col["sums"].update({k: v for k, v in scores.items() if not k.startswith("R:")})
        col["rules"].update(rules[args.findings])
        col["writer_cost"] += resp.get("cost") or 0
        for c in (row.get("gradingResult") or {}).get("componentResults") or []:
            md = c.get("metadata") or {}
            if md.get("role") == "judge":
                col["judge_cost"] += md.get("cost_total") or 0
        meta = resp.get("metadata") or {}
        if "arm" in meta:
            col["meta_rows"] += 1
            for k in ("self_name_anywhere", "contract_loaded"):
                col["meta"][k] += int(bool(meta.get(k)))

    for label, col in cols.items():
        s = col["sums"]
        rates = {name: (s[num] / s[den] if s[den] else None) for name, (num, den) in DERIVED.items()}
        if "graded" in col["adapters"]:
            derived = prompts.get(label, {})
            for name, value in rates.items():
                if value is None:
                    continue
                got = derived.get(name)
                if got is None or abs(got - value) > 1e-9:
                    problems.append(f"MISMATCH {label} {name}: recomputed {value:.6f}, promptfoo derived {got}")
        adapter = "+".join(sorted(col["adapters"])) or "none"
        fmt = " ".join(f"{k}={'n/a' if v is None else f'{v:.3f}'}" for k, v in rates.items())
        metas = " ".join(f"{k}={col['meta'][k]}/{col['meta_rows']}" for k in ("self_name_anywhere", "contract_loaded")) if col["meta_rows"] else ""
        print(f"== {label} ==  adapter={adapter} rows={col['rows']} judged={s['reg_judged']} {fmt} {metas} "
              f"writer_cost=${col['writer_cost']:.2f} judge_cost=${col['judge_cost']:.2f}")
        print(f"  findings ({args.findings}):")
        for rule, n in sorted(col["rules"].items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {rule:24s} {n}")
        print()

    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
