import json
import os
import pathlib
import sys
import tempfile
import unittest

SUITE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SUITE / "providers"))
import writer  # noqa: E402

RULES = str(SUITE.parent / "rules" / "clanker-register.md")
STYLE = "clanker-chat:Clanker"


def init(plugins=("clanker-chat",), style=STYLE, tools=("Read",), mcp=()):
    return {"type": "system", "subtype": "init", "plugins": [{"name": p, "path": "/x"} for p in plugins],
            "output_style": style, "tools": list(tools), "mcp_servers": list(mcp), "skills": []}


def hook(event, code=0):
    return [{"type": "system", "subtype": "hook_started", "hook_event": event, "hook_name": event},
            {"type": "system", "subtype": "hook_response", "hook_event": event, "hook_name": event, "exit_code": code}]


def read(path=RULES, tid="t1", error=False):
    return [{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Read", "input": {"file_path": path}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": "..."}]}}]


def result(text="🤖CLANKER answers.", cost=0.12, is_error=False, subtype="success"):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": text, "total_cost_usd": cost, "duration_ms": 4200,
            "num_turns": 2, "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 50},
            "modelUsage": {"claude-opus-5-5[1m]": {"costUSD": cost}}}


def stream(*parts):
    lines = []
    for p in parts:
        lines.extend(p if isinstance(p, list) else [p])
    return "\n".join(json.dumps(e) for e in lines) + "\n"


def with_arm_ok(text="🤖CLANKER answers."):
    return stream(hook("SessionStart"), init(), hook("UserPromptSubmit"), read(), result(text))


def baseline_ok(text="It rewrites history."):
    return stream(init(plugins=(), style="default", tools=()), result(text))


class FakeRunner:
    def __init__(self, stdout, code=0, timed_out=False):
        self.stdout, self.code, self.timed_out = stdout, code, timed_out
        self.calls = []

    def __call__(self, argv, *, input, cwd, env, timeout_s):
        self.calls.append({"argv": argv, "input": input, "cwd": cwd, "env": env, "timeout_s": timeout_s, "cwd_existed": os.path.isdir(cwd)})
        return self.code, self.stdout, "", self.timed_out


def call(arm, runner, environ=None, **config):
    options = {"config": {"arm": arm, **config}}
    return writer.call_api("What does git rebase -i do?", options, {"vars": {}}, runner=runner,
                           environ=environ if environ is not None else {"PATH": "/bin", "HOME": "/h"}, settings_path="/no/such/settings.json")


class ArgvTest(unittest.TestCase):
    def test_with_arm_loads_the_plugin_style_and_may_read_the_contract(self):
        argv = writer.build_argv("with", "opus", "/plugin", STYLE)
        self.assertNotIn("--bare", argv)
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertEqual(argv[argv.index("--plugin-dir") + 1], "/plugin")
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]), {"outputStyle": STYLE})
        self.assertEqual(argv[argv.index("--tools") + 1], "Bash,Read")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read")
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        for flag in ("--include-hook-events", "--strict-mcp-config", "--no-session-persistence", "--verbose"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-1], "--no-session-persistence")

    def test_baseline_arm_has_the_same_tools_and_no_plugin_or_style(self):
        # An empty tool set made the baseline writer print fake tool-call markup on an
        # action request and repeat it until the output limit, so both arms get the same
        # Bash and Read under dontAsk, which runs read-only commands and denies the rest.
        argv = writer.build_argv("baseline", "opus", "/plugin", STYLE)
        self.assertNotIn("--plugin-dir", argv)
        self.assertNotIn("--settings", argv)
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "Bash,Read")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")

    def test_unknown_arm_is_refused(self):
        with self.assertRaises(ValueError):
            writer.build_argv("both", "opus", "/plugin", STYLE)

    def test_only_aliases_are_models(self):
        self.assertEqual(writer.check_alias("sonnet"), "sonnet")
        with self.assertRaises(ValueError):
            writer.check_alias("claude-opus-5-5[1m]")


class EnvTest(unittest.TestCase):
    def test_allowlist_takes_auth_from_parent_and_routing_from_settings(self):
        parent = {"PATH": "/bin", "HOME": "/h", "SECRET": "s", "ANTHROPIC_FOUNDRY_API_KEY": "k", "ENABLE_PROMPT_CACHING_1H": "1",
                  "CLAUDE_CODE_USE_FOUNDRY": "0", "CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/s"}
        env = writer.child_env(parent, {"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-5-5[1m]", "DISABLE_TELEMETRY": "1"})
        self.assertEqual(env["ANTHROPIC_FOUNDRY_API_KEY"], "k")
        self.assertEqual(env["CLAUDE_CODE_USE_FOUNDRY"], "1")
        self.assertEqual(env["ANTHROPIC_DEFAULT_OPUS_MODEL"], "claude-opus-5-5[1m]")
        self.assertEqual(env["CLAUDE_CODE_PROMPT_CACHE_TTL"], "5m")
        for k in ("SECRET", "ENABLE_PROMPT_CACHING_1H", "DISABLE_TELEMETRY", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET"):
            self.assertNotIn(k, env)

    def test_missing_settings_file_is_empty(self):
        self.assertEqual(writer.read_settings_env("/no/such/file.json"), {})


class WithArmTest(unittest.TestCase):
    def test_clean_run_returns_output_cost_usage_and_metadata(self):
        runner = FakeRunner(with_arm_ok())
        r = call("with", runner)
        self.assertNotIn("error", r)
        self.assertEqual(r["output"], "🤖CLANKER answers.")
        self.assertEqual(r["cost"], 0.12)
        self.assertEqual(r["latencyMs"], 4200)
        self.assertEqual(r["tokenUsage"], {"prompt": 1110, "completion": 50, "total": 1160, "numRequests": 1})
        m = r["metadata"]
        self.assertEqual(m["arm"], "with")
        self.assertTrue(m["style_marker"])
        self.assertTrue(m["contract_loaded"])
        self.assertEqual(m["output_style"], STYLE)
        self.assertEqual(m["plugins"], ["clanker-chat"])
        self.assertEqual(m["tools"], ["Read"])
        self.assertEqual(m["mcp_servers"], [])
        self.assertEqual(m["hook_fired"], {"SessionStart": 1, "UserPromptSubmit": 1})
        self.assertEqual(m["model"], "opus")
        self.assertEqual(m["models"], ["claude-opus-5-5[1m]"])
        self.assertEqual(m["breaches"], [])
        self.assertNotIn("What does git rebase", " ".join(m["argv"]))
        call0 = runner.calls[0]
        self.assertEqual(call0["input"], "What does git rebase -i do?")
        self.assertTrue(call0["cwd_existed"])
        self.assertFalse(os.path.exists(call0["cwd"]))
        self.assertEqual(call0["env"]["CLAUDE_CODE_PROMPT_CACHE_TTL"], "5m")

    def assertBreach(self, stdout, pattern, arm="with"):
        r = call(arm, FakeRunner(stdout))
        self.assertIn("error", r)
        self.assertRegex(r["error"], r"^ISOLATION_BREACH")
        self.assertRegex(r["error"], pattern)
        self.assertIn("metadata", r)
        return r

    def test_missing_plugin(self):
        self.assertBreach(stream(hook("SessionStart"), init(plugins=()), hook("UserPromptSubmit"), read(), result()), "plugin")

    def test_missing_session_start_hook(self):
        self.assertBreach(stream(init(), hook("UserPromptSubmit"), read(), result()), "SessionStart")

    def test_failed_session_start_hook(self):
        self.assertBreach(stream(hook("SessionStart", code=1), init(), hook("UserPromptSubmit"), read(), result()), "SessionStart")

    def test_missing_reminder_hook(self):
        self.assertBreach(stream(hook("SessionStart"), init(), read(), result()), "UserPromptSubmit")

    def test_wrong_style(self):
        self.assertBreach(stream(hook("SessionStart"), init(style="default"), hook("UserPromptSubmit"), read(), result()), "output style")

    def test_an_unread_contract_is_recorded_not_a_breach(self):
        # The style and the per-turn reminder still carry the register, and whether the
        # writer follows the SessionStart pointer is plugin behavior the eval measures.
        r = call("with", FakeRunner(stream(hook("SessionStart"), init(), hook("UserPromptSubmit"), result())))
        self.assertNotIn("error", r)
        self.assertFalse(r["metadata"]["contract_loaded"])

    def test_a_failed_contract_read_is_recorded_not_a_breach(self):
        r = call("with", FakeRunner(stream(hook("SessionStart"), init(), hook("UserPromptSubmit"), read(error=True), result())))
        self.assertNotIn("error", r)
        self.assertFalse(r["metadata"]["contract_loaded"])

    def test_leading_starter_glyph(self):
        self.assertBreach(with_arm_ok("✳️ 🤖CLANKER answers."), "✳")


class BaselineArmTest(unittest.TestCase):
    def test_self_name_mid_reply_is_recorded_separately_from_the_opening(self):
        r = call("with", FakeRunner(with_arm_ok("The flag deletes the branch. 🤖CLANKER has no further finding.")))
        self.assertFalse(r["metadata"]["style_marker"])
        self.assertTrue(r["metadata"]["self_name_anywhere"])

    def test_clean_baseline(self):
        r = call("baseline", FakeRunner(baseline_ok()))
        self.assertNotIn("error", r)
        self.assertFalse(r["metadata"]["style_marker"])
        self.assertFalse(r["metadata"]["self_name_anywhere"])
        self.assertFalse(r["metadata"]["contract_loaded"])
        self.assertEqual(r["metadata"]["hook_fired"], {})

    def test_baseline_with_a_plugin(self):
        r = call("baseline", FakeRunner(stream(init(style="default", tools=()), result("x"))))
        self.assertRegex(r["error"], "plugin")

    def test_baseline_with_a_hook(self):
        r = call("baseline", FakeRunner(stream(hook("SessionStart"), init(plugins=(), style="default", tools=()), result("x"))))
        self.assertRegex(r["error"], "hook")

    def test_baseline_with_a_style(self):
        r = call("baseline", FakeRunner(stream(init(plugins=(), style=STYLE, tools=()), result("x"))))
        self.assertRegex(r["error"], "output style")

    def test_baseline_leading_starter_glyph(self):
        r = call("baseline", FakeRunner(baseline_ok("✳️ It rewrites history.")))
        self.assertRegex(r["error"], "✳")


class WriterErrorTest(unittest.TestCase):
    def test_nonzero_exit(self):
        r = call("baseline", FakeRunner("", code=1))
        self.assertRegex(r["error"], r"^WRITER_ERROR: exit 1")

    def test_timeout(self):
        r = call("baseline", FakeRunner("", code=None, timed_out=True))
        self.assertRegex(r["error"], r"^WRITER_ERROR: timeout")

    def test_is_error(self):
        r = call("baseline", FakeRunner(stream(init(plugins=(), style="default", tools=()), result("rate limited", is_error=True))))
        self.assertRegex(r["error"], r"^WRITER_ERROR: is_error")

    def test_no_result_event(self):
        r = call("baseline", FakeRunner(stream(init(plugins=(), style="default", tools=()))))
        self.assertRegex(r["error"], r"^WRITER_ERROR: no result")

    def test_bad_model_alias(self):
        r = call("baseline", FakeRunner(baseline_ok()), environ={"EVAL_MODEL": "gpt-6"})
        self.assertRegex(r["error"], r"^WRITER_ERROR: .*alias")

    def test_a_runner_exception_is_a_writer_error(self):
        def boom(argv, **kw):
            raise OSError("claude not found")
        r = call("baseline", boom)
        self.assertRegex(r["error"], r"^WRITER_ERROR: .*claude not found")

    def test_run_claude_returns_text_on_timeout(self):
        code, out, err, timed_out = writer.run_claude(["-c", "printf partial; sleep 5"], input="", cwd=tempfile.mkdtemp(), env={"PATH": "/bin:/usr/bin"},
                                                     timeout_s=0.5, binary="sh")
        self.assertTrue(timed_out)
        self.assertIsInstance(out, str)
        self.assertEqual(out, "partial")

    def test_writer_error_keeps_the_stream_tail(self):
        r = call("baseline", FakeRunner(stream(init(plugins=(), style="default", tools=())), code=1))
        self.assertIn("system", r["metadata"]["stream_tail"])

    def test_unknown_arm(self):
        r = call("sideways", FakeRunner(baseline_ok()))
        self.assertRegex(r["error"], r"^WRITER_ERROR: .*arm")


if __name__ == "__main__":
    unittest.main()
