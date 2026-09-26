import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout

SUITE = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("register_report", SUITE / "tools" / "register-report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

IDS = ["CR-a", "CR-b"]


def finding(key, votes, p=3, kind="register"):
    return {"pass": votes < 2, "score": 0, "reason": "", "metadata": {"role": "finding", "judge_kind": kind, "key": key, "rule": key,
            "votes": votes, "passes_total": p, "majority": votes >= 2,
            "passes": [{"pass_index": i, "span": "s", "why": "w"} for i in range(votes)]}}


def judge_meta(kind, cost=0.05):
    return {"pass": True, "score": 1, "reason": "judge run metadata", "metadata": {"role": "judge", "judge_kind": kind, "kind": kind, "cost_total": cost}}


def reg_scores(pass_clean, p=3, rules_maj=()):
    m = p // 2 + 1
    s = {"reg_judged": 1, "reg_judge_error": 0, "reg_passes": p, "reg_pass_clean": pass_clean,
         "reg_clean_maj": int(pass_clean >= m), "reg_clean_union": int(pass_clean == p), "reg_pass_agree": int(pass_clean in (0, p))}
    for i in IDS:
        s[f"R:{i}"] = int(i in rules_maj)
    return s


def cor_scores(votes, p=3):
    return {"cor_judged": 1, "cor_judge_error": 0, "cor_passes": p, "cor_pass_votes": votes, "cor_pass_maj": int(votes >= p // 2 + 1)}


def graded_row(label, pass_clean, findings=(), cor_votes=3, cached=False, style=True):
    rules_maj = [f["metadata"]["key"] for f in findings if f["metadata"]["majority"]]
    ns = {**reg_scores(pass_clean, rules_maj=rules_maj), **cor_scores(cor_votes), "register_clean": int(pass_clean >= 2), "correct": int(cor_votes >= 2)}
    verdicts = [{"pass": i < cor_votes, "score": int(i < cor_votes), "reason": "",
                 "metadata": {"role": "verdict", "judge_kind": "correctness", "pass_index": i, "verdict": "pass" if i < cor_votes else "fail", "reason": "r"}}
                for i in range(3)]
    comps = [{"pass": True, "score": 1, "reason": "reg"}, judge_meta("register"), *findings, {"pass": True, "score": 1, "reason": "cor"}, judge_meta("correctness", 0.02), *verdicts]
    return {"provider": {"label": label}, "success": pass_clean >= 2 and cor_votes >= 2, "vars": {"prompt": "q"},
            "response": {"output": "reply", "cached": cached, "cost": 0.1, "metadata": {"arm": "with" if label == "with-plugin" else "baseline",
                         "style_marker": style, "self_name_anywhere": style, "contract_loaded": label == "with-plugin"}},
            "gradingResult": {"pass": True, "namedScores": ns, "componentResults": comps}}


def breach_row(label):
    return {"provider": {"label": label}, "success": False, "error": "ISOLATION_BREACH: SessionStart hook did not fire",
            "failureReason": 2, "response": {"error": "ISOLATION_BREACH: SessionStart hook did not fire", "metadata": {}}}


def judge_error_row(label):
    return {"provider": {"label": label}, "success": False, "vars": {},
            "response": {"output": "reply", "cached": False, "cost": 0.1, "metadata": {"arm": "with"}},
            "gradingResult": {"pass": False, "reason": "JUDGE_ERROR: pass 0: timeout", "namedScores": {"reg_judge_error": 1, **cor_scores(3), "register_clean": 0, "correct": 1},
                              "componentResults": [{"pass": False, "score": 0, "reason": "JUDGE_ERROR: pass 0: timeout"}, judge_meta("register")]}}


def derived(rows):
    sums = {}
    for r in rows:
        for k, v in ((r.get("gradingResult") or {}).get("namedScores") or {}).items():
            sums[k] = sums.get(k, 0) + v
    out = dict(sums)
    for name, (num, den) in report.DERIVED.items():
        if sums.get(den):
            out[name] = sums.get(num, 0) / sums[den]
    return out


def doc(rows, tamper=None):
    labels = sorted({r["provider"]["label"] for r in rows})
    prompts = []
    for label in labels:
        ns = derived([r for r in rows if r["provider"]["label"] == label])
        if tamper and label in tamper:
            ns.update(tamper[label])
        prompts.append({"provider": label, "metrics": {"namedScores": ns}})
    return {"results": {"results": rows, "prompts": prompts}}


def run(data, *args):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
    out = io.StringIO()
    with redirect_stdout(out):
        code = report.main([f.name, *args])
    return code, out.getvalue()


class GradedTest(unittest.TestCase):
    def rows(self):
        return [
            graded_row("with-plugin", 3),
            graded_row("with-plugin", 1, findings=[finding("CR-a", 2), finding("CR-b", 1)]),
            graded_row("baseline", 0, findings=[finding("CR-a", 3), finding("CR-b", 2)], style=False),
            graded_row("baseline", 2, findings=[finding("CR-b", 1)], cor_votes=1, style=False),
        ]

    def test_rates_per_column_and_exit_zero_when_derived_metrics_agree(self):
        code, out = run(doc(self.rows()))
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"with-plugin .*clean_maj_rate=0\.500")
        self.assertRegex(out, r"baseline .*clean_maj_rate=0\.500")
        self.assertRegex(out, r"baseline .*correct_maj_rate=0\.500")
        self.assertRegex(out, r"with-plugin .*contract_loaded=2/2")
        self.assertNotIn("style_marker", out)

    def test_majority_findings_by_default(self):
        code, out = run(doc(self.rows()))
        section = out.split("== baseline")[1]
        self.assertRegex(section, r"CR-a\s+1")
        self.assertRegex(section, r"CR-b\s+1")

    def test_union_findings_on_request(self):
        code, out = run(doc(self.rows()), "--findings", "union")
        section = out.split("== baseline")[1]
        self.assertRegex(section, r"CR-b\s+2")

    def test_derived_mismatch_exits_one(self):
        code, out = run(doc(self.rows(), tamper={"with-plugin": {"clean_maj_rate": 0.9}}))
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out)

    def test_row_inconsistent_with_its_own_findings_exits_one(self):
        rows = self.rows()
        rows[0]["gradingResult"]["componentResults"].append(finding("CR-a", 3))
        code, out = run(doc(rows))
        self.assertEqual(code, 1)
        self.assertIn("INCONSISTENT", out)

    def test_cached_row_exits_one(self):
        rows = self.rows()
        rows[1]["response"]["cached"] = True
        code, out = run(doc(rows))
        self.assertEqual(code, 1)
        self.assertIn("CACHED", out)

    def test_judge_error_exits_one(self):
        rows = self.rows() + [judge_error_row("with-plugin")]
        code, out = run(doc(rows))
        self.assertEqual(code, 1)
        self.assertIn("JUDGE_ERROR", out)

    def test_isolation_breach_exits_one(self):
        rows = self.rows() + [breach_row("with-plugin")]
        code, out = run(doc(rows))
        self.assertEqual(code, 1)
        self.assertIn("ISOLATION_BREACH", out)

    def test_any_provider_error_exits_one(self):
        rows = self.rows() + [{"provider": {"label": "with-plugin"}, "success": False, "failureReason": 2,
                               "error": "Error: Python error: startswith first arg must be bytes", "response": {}}]
        code, out = run(doc(rows))
        self.assertEqual(code, 1)
        self.assertIn("PROVIDER_ERROR", out)


class UngradedTest(unittest.TestCase):
    def test_a_row_with_no_judge_result_exits_one(self):
        text = "<<<REPLY>>>\nreply\n<<<FINDINGS>>>\nFINDING | CR-a | span | why\nVERDICT violations=1\n<<<CORRECTNESS>>>\nCORRECTNESS pass: ok\n"
        rows = [graded_row("with-plugin", 3),
                {"provider": {"label": "with-plugin"}, "success": True, "response": {"output": text, "cached": False},
                 "gradingResult": {"pass": True, "namedScores": {}, "componentResults": []}}]
        code, out = run(doc(rows))
        self.assertEqual(code, 1, out)
        self.assertIn("UNGRADED row 1 (with-plugin)", out)
        self.assertNotIn("adapter=", out)
        self.assertRegex(out, r"with-plugin .*judged=1 ")


if __name__ == "__main__":
    unittest.main()
