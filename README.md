# clanker-chat

Claude Code plugin delivering the Clanker Register: findings before
recommendation, "🤖CLANKER" self-reference, no first- or second-person
pronouns, no praise of the operator.

## Overview

A custom output style carries no per-turn reminder field in its frontmatter, so
its instructions can fade over a long session the way built-in styles do not.
This plugin delivers the register three ways: a `SessionStart` hook points a
new session at the rules file, a `UserPromptSubmit` hook reinjects a one-line
reminder every turn, and the `Clanker` output style sets the terminal-turn
voice.

## Usage

```bash
claude plugin marketplace add https://github.com/jimweller/claude-marketplace.git
claude plugin install clanker-chat
```

Select the style with `/config`, or set `"outputStyle": "Clanker"` in a
settings file.

## Architecture

| Path                                    | Role                                                                                                                             |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `rules/clanker-register.md`             | The register, wrapped in `<clanker-register>`; every rule carries a `CR-` id                                                     |
| `hooks/hooks.json`, `hooks/inject.sh`   | `SessionStart` hook, points the session at the rules file                                                                        |
| `hooks/hooks.json`, `hooks/reminder.sh` | `UserPromptSubmit` hook, reinjects a one-line reminder every turn                                                                |
| `output-styles/clanker.md`              | The `Clanker` output style; `keep-coding-instructions: true` keeps Claude Code's default engineering behavior layered underneath |

## Testing

`register-evals/` grades a writer's reply against the register with and
without the plugin loaded. `evals/smoke-test/` checks the style against a
conversational-tone case.
