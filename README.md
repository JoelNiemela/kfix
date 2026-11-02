# kfix

Single-terminal pipeline for fixing issues in `kattis-problems/*` repositories, several problems at a time.

```
new → cloning → fixing → review → committing → testing → pushing → done
                  ↑         │                     │
                  └ feedback/edit ┘      failed (any stage) → R to retry
```

## Setup

```sh
sudo pacman -S python-textual            # TUI
sudo pacman -S mono gcc-go ghc ocaml fpc swi-prolog pypy   # judge toolchains (zero-error gate)
kfix doctor                              # lists anything still missing
```

`bin/kfix` is symlinked to `~/.local/bin/kfix`. Arch ships Node.js as `node`, so
`~/.config/problemtools/languages.yaml` overrides problemtools' `nodejs` commands
(both `verifyproblem` and `problemtools_llm` read that file).

## Use

```sh
kfix add 355 349 xorsist     # issue numbers, issue URLs or problem ids
kfix add 349 diffrun         # issue whose title lacks the "problemid:" prefix
kfix                         # the TUI (also picks up jobs added from another shell)
kfix list | kfix doctor | kfix drop <problem>
KFIX_NO_PUSH=1 kfix          # dry run: push becomes `git push --dry-run`
```

Problems are cloned to `~/problems/<id>` (same as `clone.sh`); an existing clean clone is pulled,
an existing dirty clone is kept as is (its changes go straight to review).

### Keys (only the ones that apply to the selected job are shown)

| key | stage | action |
|---|---|---|
| `a` | any | add issues / problems |
| `c` | fixing, review, failed | headless Claude fix (`claude -p`, runs in parallel; output in the Log tab) |
| `C` | fixing, review, failed | interactive Claude in the problem dir (suspends the TUI) |
| `e` | fixing, review, failed | open `$EDITOR` on `problem_statement/problem*.tex` |
| `r` | fixing, failed | done editing elsewhere → review |
| `f` | fixing, review, failed | feedback to Claude (resumes its session) |
| `y` | review | approve: commit (amends kfix's own unpushed commit), run both verifiers, push |
| `m` | review | edit the commit message (Enter saves, Esc leaves) |
| `o` | any (cloned) | render HTML + PDF of the working tree and open them |
| `z` | any | toggle diff focus mode (trim long unchanged context) |
| `x` | fixing, review, failed | discard all local changes (`reset --hard` to the starting commit + `clean -fd`) |
| `R` | failed | retry the failed stage |
| `d` | any | drop the job (clone is kept) |

### Diff tab

Removed/added line pairs are diffed word by word and then character by character, so a
one-letter typo in a 500-character paragraph shows as one highlighted letter. Changed
whitespace is shown as `·`/`→`. Focus mode (`z`) elides unchanged text more than 60
characters from a change. Outside the TUI: `git diff --word-diff=color --word-diff-regex=.`

### Commit messages

First match wins: what you typed (`m`) → `Fix typo in problem statement: "X" to "Y"` when the
whole change is one word-level replacement in a statement → Claude's `COMMIT:` line → `Fix <issue title>`.

### Test gate

`verifyproblem -j 3` and `problemtools_llm/bin/verifyproblem.sh -j 3` must both report
**0 errors** (warnings are fine). On failure kfix also runs the failing verifier on
`origin/main` in a temporary worktree and tags each error `[pre-existing]` or `[NEW]`
(diagnostic only — the gate stays zero errors). Missing compilers are flagged with a
`kfix doctor` hint.

### Failures

Red row + Error tab + terminal bell + `notify-send`, and a line in `~/problems/kfix-errors.log`.

## Files

- State: `~/.local/state/kfix/jobs/<problem>.json` (survives restarts; running stages become
  "interrupted", retry with `R`)
- Logs: `~/.local/state/kfix/logs/<problem>/{clone,claude-stream,git,verifyproblem,verifyproblem_llm,…}.log`
- Renders: `~/.local/state/kfix/render/<problem>/`
- Config (optional): `~/.config/kfix/config.toml`, e.g.

  ```toml
  claude_concurrency = 3
  test_concurrency = 1
  claude_model = "opus"
  focus_context = 80
  ```

kfix only reads GitHub issues (`gh issue view`); it never comments, labels or closes them.

## Tests

```sh
python -m pytest tests
```
