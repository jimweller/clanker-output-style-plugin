#!/usr/bin/env bash
set -euo pipefail
export ARM=baseline
exec "$(dirname "${BASH_SOURCE[0]}")/answer-then-comply.sh" "$@"
