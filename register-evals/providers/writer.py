"""promptfoo Python provider: one chat reply from a claude -p writer, with or without the plugin.

config.arm is "with" or "baseline". Both arms run with --setting-sources "" from a fresh temp
directory, so neither sees this machine's CLAUDE.md, settings, hooks, or git state, and both hold
the same Bash and Read tools under dontAsk, which runs read-only commands and denies the rest. The
with arm adds --plugin-dir, the plugin's output style, and permission to Read outside its working
directory, which its SessionStart hook needs to load rules/clanker-register.md. --bare is never
used, because it disables plugin hooks.

An empty tool set is not an option. Given an action request, a tool-less writer printed fake
tool-call markup as text and repeated one line until the output limit.

Whether the writer follows the SessionStart pointer is recorded as contract_loaded and never
treated as a breach. The output style and the per-turn reminder carry the register either way,
and skipping the pointer is plugin behavior the eval exists to measure.

A run whose isolation failed returns {"error", "metadata"}, so promptfoo records an error row and
never grades a reply the writer produced under the wrong conditions.
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

ALIASES = ("haiku", "sonnet", "opus", "fable")
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_STYLE = "clanker-chat:Clanker"
RULES_SUFFIX = "rules/clanker-register.md"
STARTER_GLYPH = "✳"
STYLE_MARKER = "\U0001f916CLANKER"

BASE_KEYS = ("PATH", "HOME", "USER", "LANG", "TMPDIR", "SHELL")
AUTH_KEYS = ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE", "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_API_KEY",
             "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_BEARER_TOKEN_BEDROCK")
ROUTING_KEYS = ("CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL")


def check_alias(model):
    if model not in ALIASES:
        raise ValueError(f"model must be an alias ({', '.join(ALIASES)}), got {model!r}")
    return model


def read_settings_env(path):
    p = pathlib.Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text()).get("env", {})


def child_env(parent, settings_env):
    env = {k: parent[k] for k in BASE_KEYS + AUTH_KEYS if k in parent}
    for k in ROUTING_KEYS:
        v = settings_env.get(k, parent.get(k))
        if v is not None:
            env[k] = v
    env["CLAUDE_CODE_PROMPT_CACHE_TTL"] = "5m"
    return env


def build_argv(arm, model, plugin_root, output_style):
    argv = ["-p", "--setting-sources", "", "--output-format", "stream-json", "--verbose", "--include-hook-events",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", "Bash,Read", "--permission-mode", "dontAsk"]
    if arm == "with":
        argv += ["--plugin-dir", str(plugin_root), "--settings", json.dumps({"outputStyle": output_style}), "--allowedTools", "Read"]
    elif arm == "baseline":
        pass
    else:
        raise ValueError(f"unknown arm {arm!r}, expected 'with' or 'baseline'")
    # --allowedTools is variadic, so a non-variadic flag must follow it.
    return argv + ["--model", check_alias(model), "--no-session-persistence"]


def text_of(value):
    # TimeoutExpired carries bytes even when the process ran with text=True.
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_claude(argv, *, input, cwd, env, timeout_s, binary="claude"):
    try:
        p = subprocess.run([binary, *argv], input=input, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout_s)
        return p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired as e:
        return None, text_of(e.stdout), text_of(e.stderr), True


def parse_events(stdout):
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def inspect(events):
    init, result = None, None
    hook_events, hook_ok = [], {}
    reads, read_ok = {}, set()
    for e in events:
        if e.get("type") == "system" and e.get("subtype") == "init":
            init = e
        elif e.get("type") == "system" and e.get("subtype") == "hook_response":
            name = e.get("hook_event")
            hook_events.append(name)
            if e.get("exit_code") == 0:
                hook_ok[name] = hook_ok.get(name, 0) + 1
        elif e.get("type") == "assistant":
            for c in (e.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use" and c.get("name") == "Read":
                    reads[c.get("id")] = (c.get("input") or {}).get("file_path", "")
        elif e.get("type") == "user":
            content = (e.get("message") or {}).get("content")
            for c in content if isinstance(content, list) else []:
                if c.get("type") == "tool_result" and not c.get("is_error"):
                    read_ok.add(c.get("tool_use_id"))
        elif e.get("type") == "result":
            result = e
    contract_loaded = any(path.endswith(RULES_SUFFIX) and tid in read_ok for tid, path in reads.items())
    return init, result, hook_events, hook_ok, contract_loaded


def breaches_for(arm, meta, output, output_style):
    found = []
    if arm == "with":
        if meta["plugins"] != ["clanker-chat"]:
            found.append(f"plugins {meta['plugins']} is not ['clanker-chat']")
        if not meta["hook_fired"].get("SessionStart"):
            found.append("SessionStart hook did not fire")
        if not meta["hook_fired"].get("UserPromptSubmit"):
            found.append("UserPromptSubmit reminder hook did not fire")
        if meta["output_style"] != output_style:
            found.append(f"output style {meta['output_style']!r} is not {output_style!r}")
    else:
        if meta["plugins"]:
            found.append(f"baseline loaded plugins {meta['plugins']}")
        if meta["hook_events"]:
            found.append(f"baseline hook fired {meta['hook_events']}")
        if meta["output_style"] != "default":
            found.append(f"baseline output style {meta['output_style']!r} is not 'default'")
    if meta["mcp_servers"]:
        found.append(f"mcp servers {meta['mcp_servers']}")
    if output.lstrip().startswith(STARTER_GLYPH):
        found.append(f"reply opens with the {STARTER_GLYPH} starter glyph, so a CLAUDE.md leaked in")
    return found


def call_api(prompt, options, context, runner=None, environ=None, settings_path=None):
    config = (options or {}).get("config") or {}
    environ = os.environ if environ is None else environ
    runner = runner or run_claude
    arm = config.get("arm")
    output_style = config.get("output_style", DEFAULT_STYLE)
    meta = {"arm": arm}
    try:
        model = check_alias(environ.get("EVAL_MODEL") or "opus")
        argv = build_argv(arm, model, config.get("plugin_dir", PLUGIN_ROOT), output_style)
    except ValueError as e:
        return {"error": f"WRITER_ERROR: {e}", "metadata": meta}
    meta.update({"model": model, "argv": argv})
    settings = read_settings_env(settings_path or pathlib.Path.home() / ".claude" / "settings.json")
    env = child_env(environ, settings)
    cwd = tempfile.mkdtemp(prefix="writer-")
    try:
        code, stdout, stderr, timed_out = runner(argv, input=prompt, cwd=cwd, env=env,
                                                 timeout_s=int(config.get("writer_timeout_ms", 540000)) / 1000)
    except Exception as e:
        return {"error": f"WRITER_ERROR: {type(e).__name__}: {e}", "metadata": meta}
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    stdout, stderr = text_of(stdout), text_of(stderr)
    meta["stream_tail"] = stdout[-1500:]

    init, result, hook_events, hook_ok, contract_loaded = inspect(parse_events(stdout))
    init = init or {}
    meta.update({
        "output_style": init.get("output_style"),
        "plugins": [p.get("name") for p in init.get("plugins") or []],
        "tools": init.get("tools") or [],
        "mcp_servers": init.get("mcp_servers") or [],
        "hook_events": hook_events,
        "hook_fired": hook_ok,
        "contract_loaded": contract_loaded,
    })
    if timed_out:
        return {"error": "WRITER_ERROR: timeout", "metadata": meta}
    if code != 0:
        return {"error": f"WRITER_ERROR: exit {code}: {(stderr or '').strip()[:300]}", "metadata": meta}
    if result is None:
        return {"error": "WRITER_ERROR: no result event", "metadata": meta}
    meta["models"] = sorted((result.get("modelUsage") or {}).keys())
    meta["num_turns"] = result.get("num_turns")
    if result.get("is_error") or result.get("subtype") != "success":
        return {"error": f"WRITER_ERROR: is_error subtype={result.get('subtype')}: {str(result.get('result'))[:300]}", "metadata": meta}

    output = result.get("result") or ""
    meta["style_marker"] = output.lstrip().startswith(STYLE_MARKER)
    meta["self_name_anywhere"] = STYLE_MARKER in output
    meta["breaches"] = breaches_for(arm, meta, output, output_style)
    if meta["breaches"]:
        return {"error": "ISOLATION_BREACH: " + "; ".join(meta["breaches"]), "metadata": meta}

    usage = result.get("usage") or {}
    prompt_tokens = sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    completion = usage.get("output_tokens", 0)
    return {
        "output": output,
        "cost": result.get("total_cost_usd", 0),
        "latencyMs": result.get("duration_ms"),
        "tokenUsage": {"prompt": prompt_tokens, "completion": completion, "total": prompt_tokens + completion, "numRequests": 1},
        "metadata": meta,
    }
