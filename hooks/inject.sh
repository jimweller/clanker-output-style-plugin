#!/bin/bash
set -euo pipefail
event="${1:?event name required}"
root="${CLAUDE_PLUGIN_ROOT:?CLAUDE_PLUGIN_ROOT not set}"
rules_file="${root}/rules/clanker-register.md"
[ -f "$rules_file" ] || { echo "clanker-chat inject.sh: missing $rules_file" >&2; exit 1; }
python3 -c "
import json, sys
event = sys.argv[1]
rules_path = sys.argv[2]
text = (
    'At the start of this session, read the Clanker Register at ' + rules_path +
    ' and follow it for every reply in this session.'
)
print(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}))
" "$event" "$rules_file"
