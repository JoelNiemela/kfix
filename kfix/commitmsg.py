"""Standardized commit messages, matching the existing kattis-problems history, e.g.

    Fix typo in problem statement: "arrangment" to "arrangement"
    Fix LaTeX rendering error in problem statement

Priority: user-typed > auto-detected typo > Claude's COMMIT: line > from issue title.
"""

from __future__ import annotations

import re

from .chardiff import FileDiff, token_opcodes

_WORDY = re.compile(r"^[\w][\w '’\-]*$", re.UNICODE)
_STATEMENT = re.compile(r'(^|/)problem_statement/problem(\.[a-z]{2,3})?\.tex$')


def _is_word(tok: str) -> bool:
    return bool(re.match(r'\w', tok))


def typo_pair(old: str, new: str) -> tuple[str, str] | None:
    """If `old`->`new` is a single small word-level change, return the ("X", "Y") pair."""
    ops = [o for o in token_opcodes(old, new) if o[0] != 'equal']
    if len(ops) != 1:
        return None
    _, ta, tb, i1, i2, j1, j2 = ops[0]

    def has_word(toks):
        return any(_is_word(t) for t in toks)

    # Outside the single changed run, both sides are identical and aligned, so the
    # window can be widened symmetrically. Grow it until both sides contain a word:
    # covers pure insertions/deletions like "a an" -> "an", "How they" -> "How do they".
    while not (has_word(ta[i1:i2]) and has_word(tb[j1:j2])):
        if i2 < len(ta) and j2 < len(tb):
            i2, j2 = i2 + 1, j2 + 1
        elif i1 > 0 and j1 > 0:
            i1, j1 = i1 - 1, j1 - 1
        else:
            return None

    # Drop identical whitespace at the window edges (keeps both sides aligned), so that the
    # whole-word expansion below only follows tokens that are directly attached.
    while i1 < i2 and j1 < j2 and ta[i1].isspace() and ta[i1] == tb[j1]:
        i1, j1 = i1 + 1, j1 + 1
    while i2 > i1 and j2 > j1 and ta[i2 - 1].isspace() and ta[i2 - 1] == tb[j2 - 1]:
        i2, j2 = i2 - 1, j2 - 1

    # Expand to whole words ("don't", "square-free"): absorb attached word/apostrophe/hyphen tokens.
    def attached(tok: str) -> bool:
        return _is_word(tok) or tok in "'’-"

    while i1 > 0 and j1 > 0 and attached(ta[i1 - 1]):
        i1, j1 = i1 - 1, j1 - 1
    while i2 < len(ta) and j2 < len(tb) and attached(ta[i2]):
        i2, j2 = i2 + 1, j2 + 1
    x, y = ''.join(ta[i1:i2]).strip(), ''.join(tb[j1:j2]).strip()
    if not x or not y or x == y or len(x) > 40 or len(y) > 40:
        return None
    if not (_WORDY.match(x) and _WORDY.match(y)):
        return None  # LaTeX, math, punctuation-only: not a "typo" message
    return x, y


def typo_message(files: list[FileDiff]) -> str | None:
    """`Fix typo in problem statement: "X" to "Y"` if the whole diff is one small word change
    in a problem statement (possibly the same change in several languages is NOT handled)."""
    if len(files) != 1 or not _STATEMENT.search(files[0].path) or files[0].meta:
        return None
    changes = [ln for h in files[0].hunks for ln in h.lines if ln.kind in ('change', 'add', 'del')]
    if len(changes) != 1 or changes[0].kind != 'change':
        return None
    segs = changes[0].segs
    old = ''.join(s.text for s in segs if s.kind in ('eq', 'del'))
    new = ''.join(s.text for s in segs if s.kind in ('eq', 'ins'))
    pair = typo_pair(old, new)
    if pair is None:
        return None
    return f'Fix typo in problem statement: "{pair[0]}" to "{pair[1]}"'


_COMMIT_LINE = re.compile(r'^\s*COMMIT:\s*(.+?)\s*$', re.MULTILINE)


def from_claude(result_text: str | None) -> str | None:
    if not result_text:
        return None
    found = _COMMIT_LINE.findall(result_text)
    if not found:
        return None
    msg = found[-1].strip().strip('`').strip()
    if len(msg) >= 2 and msg[0] == msg[-1] and msg[0] in '"\'':
        msg = msg[1:-1]
    return msg or None


_VERBS = ('fix', 'add', 'remove', 'update', 'replace', 'correct', 'clarify', 'change', 'rename', 'improve', 'un-comment', 'uncomment')


def from_issue_title(title: str | None, problem_id: str) -> str:
    if not title:
        return f'Fix problem statement of {problem_id}'
    t = re.sub(rf'^\s*{re.escape(problem_id)}\s*:\s*', '', title.strip(), flags=re.IGNORECASE).strip().rstrip('.')
    if not t:
        return f'Fix problem statement of {problem_id}'
    if t.lower().startswith(_VERBS):
        return t[0].upper() + t[1:]
    # Lower-case the first letter unless it starts an acronym/brand like "LaTeX" or "TLE"
    if len(t) > 1 and t[0].isupper() and t[1].islower():
        t = t[0].lower() + t[1:]
    return f'Fix {t}'


def choose(user: str | None, files: list[FileDiff], claude_result: str | None, issue_title: str | None, problem_id: str) -> tuple[str, str]:
    """Return (message, source)."""
    if user and user.strip():
        return user.strip(), 'yours'
    if (m := typo_message(files)):
        return m, 'auto (typo)'
    if (m := from_claude(claude_result)):
        return m, 'claude'
    return from_issue_title(issue_title, problem_id), 'issue title'
