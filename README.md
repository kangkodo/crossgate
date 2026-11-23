# Crossgate

Crossgate is a development pipeline where two AI models check each other's work. Claude plans and reviews. Codex writes the code and runs QA. Nothing merges until a model that did not write it has approved it.

The name comes from *cross* (review by a different model) and *gate* (each stage needs a passing verdict before the next one starts).

> The rule book, role cards, prompts and CLI output are written in Korean. The commands and config keys are in English. You can talk to the Master in any language.

## How it works

- You talk only to the Master, a Claude Code session. The Master calls Codex and Claude in separate roles.
- Default pairing: Claude writes the plan and the wiki, and Codex reviews them. Codex develops, and Claude reviews. Codex runs QA.
- Reviewers are always read-only. Codex runs in a read-only sandbox. Claude reviewers get `Read`, `Grep` and `Glob` only. Resumed review sessions keep the same limits.
- A reviewer who rejects code for a runtime failure has to reproduce it. `probe` runs the claim in a throwaway copy of the repository, sandboxed on macOS.
- Every verdict is appended to `ai-log/<date>/<run>/verdicts.jsonl`. When all conditions hold, the change merges automatically. Deploys happen only when you ask.

## Requirements

- Python 3.9 or later. The CLI uses the standard library only.
- git
- [Claude Code](https://claude.com/claude-code)
- The Codex CLI, signed in
- macOS for the `probe` sandbox (`sandbox-exec`). On other systems `probe` refuses to run until you set `probe.sandbox` to `false`. It then runs unconfined and records `sandbox: none`, so the Master reads the reproduction patch first.

## Install

Clone this repository, then install the kit into a project:

```bash
git clone https://github.com/kangkodo/crossgate.git
```

```bash
sh crossgate/install.sh /path/to/your/project
```

The installer copies the latest tagged version. Pass `--ref <tag>` to pin one, or `--dev` to install the working tree as it is.

It creates:

| Path | Contents |
|---|---|
| `.crossgate/kit/` | A sealed copy of the kit. Overwritten on upgrade. |
| `.crossgate/config.json` | Project settings. Created only if missing. |
| `.claude/skills/crossgate/` | The `/crossgate` skill, which is the Master's playbook |
| `.claude/agents/crossgate-reviewer.md` | The read-only reviewer subagent |

It also adds one paragraph to `AGENTS.md` and `CLAUDE.md`, and a few lines to `.gitignore`.

After installing, edit `gate`, `forbidden` and `deploy` in `.crossgate/config.json` to match your project. The default gate is `npm test`. To turn on the CI check, copy `templates/crossgate.yml` into your workflows.

### Workspaces

If you have several repositories under a root folder that is not itself a git repository, install in workspace mode:

```bash
sh crossgate/install.sh --workspace /path/to/workspace
```

This installs the engine and the `/crossgate` skill and leaves your workspace rules, `.gitignore` and CI alone. List each repository's `gate` and `protected` paths under `repos` in `config.json`, and pass `--repo` to `codex`, `gate` and `verdict`. Your workspace rules win where they say something. The skill covers what they leave open. See section 11 of [CROSSGATE.md](CROSSGATE.md).

### Global setup skill

```bash
sh crossgate/install.sh --global
```

After this, `/crossgate-setup` installs or upgrades Crossgate from any folder.

## Usage

Open Claude Code in the project and type `/crossgate` followed by the task:

```
/crossgate change the save button label to "Save draft", light tier
```

The Master reads the code first, recommends a tier, and asks you every open decision in one batch. After you approve, it asks again only if it finds a new conflict, hits the retry cap, or needs to do something irreversible that you did not approve.

| Tier | Use it for | Stages |
|---|---|---|
| Light | Copy, styling, layout. One repository. | request → develop, with a before and after check of the screen → code review → merge |
| Standard | Changes to existing behavior that leave contracts and permissions alone | request and decision review → TODO → develop and update docs → code review → merge |
| Full | New or removed features, several repositories, changes to data, APIs or permissions | request and decision review → plan and review → develop and review → independent QA → wiki and review → audit of the records → merge |

Each request also gets a work type: feature, bug, refactor, performance, UI or other. The type sets what has to happen first and what counts as proof. A bug fix needs a test that fails before the fix and passes after. A refactor needs pinned tests first. If running something can settle a decision, the Master runs the experiment and does not ask you.

To pick up an interrupted run, type `/crossgate continue`. The Master checks the state with `resume`, rereads the request, and carries on. It does not repeat questions you already answered.

## Commands

The Master runs these for you. To run one yourself, prefix it with `python3 .crossgate/kit/bin/crossgate`.

| Command | What it does |
|---|---|
| `codex <role> --prompt-file <file>` / `claude <role> --prompt-file <file>` | Call a role. Reviews continue the last successful session. `--fresh` starts a new one. |
| `gate`, `verdict`, `merge-check` | Run the project's checks, record a verdict, and test the merge conditions |
| `probe --finding <ID> --cmd <command> --expect <failure text>` | Reproduce a reviewer's runtime finding in a throwaway copy. A code rejection is recorded only with a reproduction or a contrast finding. |
| `parallel --task <file> --task <file> --run <run>` | Develop independent tasks at the same time, then review the combined result once |
| `discuss open\|round\|issue\|close\|status` | Structured debate between models. See below. |
| `resume [--run <run>]`, `resume --list` | Show the stage, round, open findings, interrupted calls and next step |
| `status`, `close-run` | Show a run, or close it. `close-run --reopen` opens it again. |
| `retro [--days <n>]` | Read-only report across runs: reviews per stage, first-pass rate, rejections, findings that survived two rounds, call volume |
| `docs-check [<folder>]` | Check the docs index, relative links and anchors. Changes nothing. |
| `audit-calls [--days <n>]` | Report Codex calls made outside Crossgate. Does not block verdicts or merges. |
| `deploy` | Run the configured deploy command. Only on your instruction. |

Calls time out after 20 minutes, or 40 for development and QA. A Codex call that produces no event in 180 seconds counts as stalled. You can change these, the parallel limit, the probe sandbox and the rest in `config.json`. [templates/config.json](templates/config.json) lists every key with its default. Keys missing from an older config fall back to those defaults.

## Discussions

A discussion is an optional step for design choices that split, bugs with an unknown cause, reviews that keep bouncing on the same finding, or whenever you ask for one. The Master runs it in this order: `open`, an opening round, `issue` entries for each point of disagreement, rebuttal rounds, experiments, `close`.

- The default participants are one Codex and one Claude, both read-only. Add more under `discussion.participants`, including different models on the same engine.
- In round one, everyone answers at the same time in a new session and nobody sees the others' answers. In later rounds each participant gets the others' statements in full, plus the issue ledger.
- No code changes during a discussion. The decision goes into the request, plan or TODO, and the normal stages build and review it.
- Rebuttal rounds are capped at 3 by default. Going past the cap takes `--extend`.
- A run with an open discussion does not pass `merge-check`.

Records live in `<run>/05-discussion/<NN_slug>/`. Section 14 of [CROSSGATE.md](CROSSGATE.md) has the full rules.

## What's in this repository

| Path | Contents |
|---|---|
| [CROSSGATE.md](CROSSGATE.md) | The rule book: verdicts, rubric, response format, caps, merge and deploy |
| `roles/` | One card per role: master, planner, developer, QA, the reviewers, auditor, discussant |
| `templates/` | Request, TODO, task and verification templates, the default `config.json`, the CI check |
| `bin/crossgate` | CLI entry point. The code is in `bin/cg/`. |
| `claude/` | The `/crossgate` skill, the reviewer subagent, the global setup skill |
| `install.sh` | Installs or upgrades a project |

## Changing the rules

Do not edit `.crossgate/kit/` inside a project. The copy is sealed with a checksum manifest. Change this repository, bump `VERSION` and `CHANGELOG.md`, tag the release, and run `install.sh` again. If you find a defect after tagging, ship a patch version. Do not move the tag.

Versions are `0.MINOR.PATCH`. MINOR goes up only when users have something new to learn.

## Tests

```bash
python3 tests/test_crossgate.py
```

The tests run in temporary repositories and never touch a real project.

## Credits

Several methods were adapted from pstack (MIT). The [changelog](CHANGELOG.md) says which.
