"""Claude integration: prompt, headless (`claude -p`, stream-json) runs and parsing."""

from __future__ import annotations

import json
from pathlib import Path

# Read-only shell commands are allowed so that compound inspection commands (`cat a; wc -l b`) aren't
# refused in -p mode. No `find` (it has -exec/-delete) and no interpreters.
ALLOWED_TOOLS = ','.join([
    'Read', 'Edit', 'Write', 'Glob', 'Grep',
    'Bash(problem2pdf:*)', 'Bash(problem2html:*)', 'Bash(pdftotext:*)',
    'Bash(ls:*)', 'Bash(cat:*)', 'Bash(head:*)', 'Bash(tail:*)', 'Bash(wc:*)', 'Bash(grep:*)',
    'Bash(diff:*)', 'Bash(file:*)', 'Bash(sort:*)', 'Bash(uniq:*)', 'Bash(echo:*)',
])

STYLE_EXAMPLES = '''\
Fix typo in problem statement: "arrangment" to "arrangement"
Fix typo in problem statement: "How they know" to "How do they know"
Fix LaTeX rendering error in problem statement
Un-comment figure \\caption{}
Replace transparent with white in the Danish flag figure, to fix dark-mode issues'''


def build_prompt(problem_id: str, problem_dir: Path, issue: dict | None, render_dir: Path) -> str:
    if issue:
        issue_txt = (
            f'GitHub issue #{issue["number"]} ({issue["url"]})\n'
            f'Title: {issue["title"]}\n'
            f'Labels: {", ".join(issue.get("labels", [])) or "-"}\n\n'
            f'{issue.get("body") or "(no body)"}'
        )
    else:
        issue_txt = '(no GitHub issue given — the user will describe the problem)'
    return f'''You are fixing a reported issue in the Kattis problem package `{problem_id}`
(legacy Kattis problem package format), checked out at {problem_dir}.

{issue_txt}

Rules:
- Make the smallest change that fixes the issue. Keep the author's wording and LaTeX style elsewhere.
- If the statement exists in several languages (problem_statement/problem.*.tex) and the same
  error is present in the other languages, fix it there too.
- Only edit files inside {problem_dir}. Do not create new files there, and do not run git.
- If you change LaTeX, check that it renders, writing output ONLY under {render_dir}:
    problem2pdf -o {render_dir}/check.pdf {problem_dir}
    problem2html -d {render_dir}/html {problem_dir}
  The HTML statement uses MathJax for math: text inside \\texttt/\\mbox/\\text in math mode is shown literally.
- If the issue is invalid, already fixed, or needs a decision from a human, change nothing and explain why.

Finish your reply with exactly one line of the form
COMMIT: <commit message>
— a single-line commit message in this repository's style, e.g.:
{STYLE_EXAMPLES}
'''


def headless_cmd(prompt: str, render_dir: Path, resume: str | None = None, model: str | None = None) -> list[str]:
    cmd = ['claude', '-p', prompt, '--output-format', 'stream-json', '--verbose',
           '--permission-mode', 'acceptEdits', '--allowedTools', ALLOWED_TOOLS, '--add-dir', str(render_dir)]
    if resume:
        cmd += ['--resume', resume]
    if model:
        cmd += ['--model', model]
    return cmd


def interactive_cmd(prompt: str, render_dir: Path, model: str | None = None) -> list[str]:
    # The prompt goes first: --add-dir is variadic and would swallow a trailing positional argument.
    cmd = ['claude', prompt, '--add-dir', str(render_dir)]
    if model:
        cmd += ['--model', model]
    return cmd


def _short(value, n: int = 100) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = s.replace('\n', '⏎ ')
    return s if len(s) <= n else s[: n - 1] + '…'


def describe_event(ev: dict) -> tuple[str | None, dict]:
    """Turn one stream-json event into a log line (or None) plus extracted facts
    (session_id, result, is_error, cost)."""
    facts: dict = {}
    t = ev.get('type')
    if 'session_id' in ev:
        facts['session_id'] = ev['session_id']
    if t == 'system' and ev.get('subtype') == 'init':
        return f'claude: session {ev.get("session_id")} model {ev.get("model")}', facts
    if t == 'assistant':
        parts = []
        for block in ev.get('message', {}).get('content', []):
            if block.get('type') == 'text' and block.get('text', '').strip():
                parts.append('claude: ' + _short(block['text'], 300))
            elif block.get('type') == 'tool_use':
                inp = block.get('input', {})
                arg = inp.get('file_path') or inp.get('command') or inp.get('pattern') or inp
                parts.append(f'tool: {block.get("name")}({_short(arg)})')
        return ('\n'.join(parts) or None), facts
    if t == 'user':
        for block in ev.get('message', {}).get('content', []) if isinstance(ev.get('message', {}).get('content'), list) else []:
            if block.get('type') == 'tool_result' and block.get('is_error'):
                return f'tool error: {_short(block.get("content"), 200)}', facts
        return None, facts
    if t == 'result':
        facts['result'] = ev.get('result')
        facts['is_error'] = bool(ev.get('is_error')) or ev.get('subtype') != 'success'
        facts['cost'] = ev.get('total_cost_usd')
        return f'claude finished: {ev.get("subtype")} in {ev.get("num_turns")} turns, ${ev.get("total_cost_usd") or 0:.3f}', facts
    return None, facts
