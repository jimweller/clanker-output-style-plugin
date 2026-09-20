# clanker-output-style-plugin

Claude Code plugin: a `Clanker` output style plus a `UserPromptSubmit` hook that
reinforces its register every turn.

## Why

A custom output style has no per-turn reminder field in its frontmatter (`name`,
`description`, `keep-coding-instructions`, `force-for-plugin` are the full set) so
its instructions can fade over a long session, unlike built-in styles (`Concise`,
`Proactive`), which Claude Code re-injects every turn. This plugin closes that gap
with a `UserPromptSubmit` hook that returns `hookSpecificOutput.additionalContext`
on every turn, the same delivery mechanism the built-in per-turn reminders use.

## What it does

- `output-styles/clanker.md`: findings before recommendation, self-referred to as
  "🤖CLANKER", no first- or second-person pronouns, no praise, terse by default,
  full detail on request. `keep-coding-instructions: true` keeps Claude Code's
  default engineering behavior layered underneath.
- `hooks/hooks.json` + `hooks/reminder.sh`: injects a one-line reminder of the
  register on every `UserPromptSubmit`, regardless of which output style is active.

## Install

```bash
claude plugin marketplace add https://github.com/jimweller/claude-marketplace.git
claude plugin install clanker-output-style-plugin
```

Then select the style for a session with `/output-style Clanker` or `/config`, or
set `"outputStyle": "Clanker"` in a settings file.
