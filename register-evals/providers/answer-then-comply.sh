#!/usr/bin/env bash
# Chat Register eval. A scenario prompt goes in, a writer answers it, and a judge
# grades the reply against the Chat Register catalog. There is no rewrite and no
# original text: this measures generation only, one live chat turn, not an edit.
#
# Two arms, selected by $ARM (with|baseline), both --bare so neither picks up this
# machine's own CLAUDE.md. The baseline arm never loads the plugin, so any
# difference between arms is the plugin's own contribution (output style + hook),
# isolated the same way the prose-contract loop isolates the judge with --bare.
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

NO_TOOLS="Read Write Edit Bash Glob Grep WebFetch WebSearch NotebookEdit Task"

# --disallowedTools is a variadic option (commander <tools...>): it greedily
# consumes every following bare argument until the next --flag. It must never
# be the last flag before the trailing prompt positional, or the prompt text
# itself gets swallowed as more "tool names" and denied with a typo warning.
# --no-session-persistence closes it unconditionally, regardless of arm.
args=(-p --strict-mcp-config --output-format text --bare
      --mcp-config '{"mcpServers":{}}'
      --disallowedTools "$NO_TOOLS"
      --no-session-persistence)
[[ "$ARM" == "with" ]] && args+=(--plugin-dir "$PLUGIN_ROOT" --settings '{"outputStyle":"Clanker"}')
[[ -n "${EVAL_MODEL:-}" ]] && args+=(--model "$EVAL_MODEL")

judge_args=(-p --output-format text --strict-mcp-config --bare
            --mcp-config '{"mcpServers":{}}'
            --disallowedTools "$NO_TOOLS"
            --no-session-persistence)
JM="${JUDGE_MODEL:-${EVAL_MODEL:-}}"
[[ -n "$JM" ]] && judge_args+=(--model "$JM")
judge_args+=(--effort "${JUDGE_EFFORT:-medium}")

strip_glyph() { perl -CSD -pe 'if ($. == 1) { s/^(?:[^\x00-\x7F]+\s*)+// }'; }

# Same catalog rule as the prose-contract loop: rebuild on a stale mtime unless
# JUDGE_CATALOG pins a copy for an A/B, in which case that copy is authoritative.
CONTRACT="${CONTRACT_FILE:-$EVAL_ROOT/../../../configs/claude-code/claude_md.md}"
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
