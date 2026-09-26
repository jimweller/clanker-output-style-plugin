#!/usr/bin/env bash
# Clanker Register eval. A scenario prompt goes in, a writer answers it, and a judge
# grades the reply against the Clanker Register catalog. There is no rewrite and no
# original text: this measures generation only, one live chat turn, not an edit.
#
# Two arms, selected by $ARM (with|baseline), both on --setting-sources "" so
# neither picks up this machine's own CLAUDE.md, user settings, or hooks. Not
# --bare: it disables plugin hooks unconditionally (measured in Phase 0), which
# would stop the with-arm's own SessionStart contract pointer from ever firing.
# The baseline arm never loads the plugin, so any difference between arms is the
# plugin's own contribution (output style + SessionStart pointer + per-turn hook).
#
# promptfoo exec provider. Receives the prompt as $1 and prints one artifact
# carrying the reply and the judge's findings.
set -euo pipefail

EVAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_ROOT="$(cd "$EVAL_ROOT/.." && pwd)"
SRC="$(mktemp)"
MID="$(mktemp)"
trap 'rm -f "$SRC" "$MID"' EXIT
printf '%s' "$1" >"$SRC"

: "${ARM:?set ARM=with or ARM=baseline}"

# Read is disallowed on the baseline arm, which has no plugin and no contract to
# read. It is explicitly allowed on the with arm, because the register's
# SessionStart hook points the session at rules/clanker-register.md and Read is
# the only way to follow that pointer (the register has no Skill wrapper,
# unlike prose). Omitting Read from --disallowedTools is not enough: measured
# directly, a Read merely absent from the deny list still triggers an
# interactive permission check that headless mode cannot answer and fails
# closed, denying the read. --allowedTools Read grants it outright.
NO_TOOLS_BASELINE="Read Write Edit Bash Glob Grep WebFetch WebSearch NotebookEdit Task"
NO_TOOLS_WITH="Write Edit Bash Glob Grep WebFetch WebSearch NotebookEdit Task"

# --disallowedTools and --allowedTools are both variadic options (commander
# <tools...>): each greedily consumes every following bare argument until the
# next --flag. Neither must ever be the last flag before the trailing prompt
# positional, or the prompt text itself gets swallowed as more "tool names" and
# denied with a typo warning. --no-session-persistence is appended last,
# unconditionally, after every variadic flag and after --model, so it always
# terminates the array regardless of arm or whether EVAL_MODEL is set.
args=(-p --strict-mcp-config --output-format text --setting-sources ""
      --mcp-config '{"mcpServers":{}}')
if [[ "$ARM" == "with" ]]; then
  args+=(--plugin-dir "$PLUGIN_ROOT" --settings '{"outputStyle":"Clanker"}'
         --allowedTools "Read"
         --disallowedTools "$NO_TOOLS_WITH")
else
  args+=(--disallowedTools "$NO_TOOLS_BASELINE")
fi
[[ -n "${EVAL_MODEL:-}" ]] && args+=(--model "$EVAL_MODEL")
args+=(--no-session-persistence)

judge_args=(-p --output-format text --strict-mcp-config --bare
            --mcp-config '{"mcpServers":{}}'
            --disallowedTools "$NO_TOOLS_BASELINE"
            --no-session-persistence)
JM="${JUDGE_MODEL:-${EVAL_MODEL:-}}"
[[ -n "$JM" ]] && judge_args+=(--model "$JM")
judge_args+=(--effort "${JUDGE_EFFORT:-medium}")

strip_glyph() { perl -CSD -pe 'if ($. == 1) { s/^(?:[^\x00-\x7F]+\s*)+// }'; }

# Same catalog rule as the prose-contract loop: rebuild on a stale mtime unless
# JUDGE_CATALOG pins a copy for an A/B, in which case that copy is authoritative.
CONTRACT="${CONTRACT_FILE:-$EVAL_ROOT/../rules/clanker-register.md}"
CAT="${JUDGE_CATALOG:-$EVAL_ROOT/corpus/catalog.md}"
if [[ -z "${JUDGE_CATALOG:-}" ]] && { [[ ! -f "$CAT" ]] || [[ "$CONTRACT" -nt "$CAT" ]]; }; then
  TMP_CAT="$(mktemp)"
  python3 "$EVAL_ROOT/tools/extract-register-catalog.py" "$TMP_CAT"
  mv -f "$TMP_CAT" "$CAT"
fi
[[ -f "$CAT" ]] || { printf 'no catalog at %s\n' "$CAT" >&2; exit 1; }

claude "${args[@]}" "$(cat "$SRC")" | strip_glyph >"$MID"

judge_prompt="$(python3 "$EVAL_ROOT/tools/render-template.py" \
  "$EVAL_ROOT/prompts/register-comply.txt" \
  catalog "$CAT" reply "$MID")"

correctness_prompt="$(python3 "$EVAL_ROOT/tools/render-template.py" \
  "$EVAL_ROOT/prompts/correctness-judge.txt" \
  reply "$MID")"

printf '<<<REPLY>>>\n'
cat "$MID"
printf '<<<FINDINGS>>>\n'
claude "${judge_args[@]}" "$judge_prompt" | strip_glyph
printf '<<<CORRECTNESS>>>\n'
claude "${judge_args[@]}" "$correctness_prompt" | strip_glyph
