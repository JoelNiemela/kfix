"""Character-level diff of a unified git diff.

Statement paragraphs are often a single (long) line, so a plain `git diff`
shows a one-character typo fix as a pair of near-identical 500-character lines.
This module pairs removed/added lines and diffs them token by token, then
character by character inside replaced tokens, producing segments that the
TUI renders with the changed characters highlighted.

Pure Python (difflib only); rendering to Rich lives in `render_rich`.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r'\w+|\s+|[^\w\s]', re.UNICODE)

# Segment kinds
EQ, DEL, INS, ELIDE = 'eq', 'del', 'ins', 'elide'


@dataclass
class Seg:
    text: str
    kind: str  # EQ / DEL / INS / ELIDE


@dataclass
class DiffLine:
    kind: str  # 'ctx' | 'del' | 'add' | 'change' | 'meta'
    old_no: int | None
    new_no: int | None
    segs: list[Seg]


@dataclass
class Hunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    meta: list[str] = field(default_factory=list)  # e.g. "new file", "Binary files differ"
    hunks: list[Hunk] = field(default_factory=list)


def tokenize(s: str) -> list[str]:
    return TOKEN_RE.findall(s)


def _merge(segs: list[Seg]) -> list[Seg]:
    out: list[Seg] = []
    for s in segs:
        if not s.text:
            continue
        if out and out[-1].kind == s.kind:
            out[-1] = Seg(out[-1].text + s.text, s.kind)
        else:
            out.append(Seg(s.text, s.kind))
    return out


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def intraline(old: str, new: str) -> list[Seg]:
    """Diff two versions of one line into EQ/DEL/INS segments.

    Token-level first (so word replacements read as words), then
    character-level inside a replaced run when the two sides are similar
    (so "arrangment" -> "arrangement" highlights just the inserted "e").
    """
    ta, tb = tokenize(old), tokenize(new)
    segs: list[Seg] = []
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        a, b = ''.join(ta[i1:i2]), ''.join(tb[j1:j2])
        if op == 'equal':
            segs.append(Seg(a, EQ))
        elif op == 'delete':
            segs.append(Seg(a, DEL))
        elif op == 'insert':
            segs.append(Seg(b, INS))
        else:  # replace
            if similarity(a, b) >= 0.5:
                csm = difflib.SequenceMatcher(None, a, b, autojunk=False)
                for cop, k1, k2, l1, l2 in csm.get_opcodes():
                    if cop == 'equal':
                        segs.append(Seg(a[k1:k2], EQ))
                    else:
                        segs.append(Seg(a[k1:k2], DEL))
                        segs.append(Seg(b[l1:l2], INS))
            else:
                segs.append(Seg(a, DEL))
                segs.append(Seg(b, INS))
    return _merge(segs)


def token_opcodes(old: str, new: str) -> list[tuple[str, list[str], list[str], int, int, int, int]]:
    """Token-level opcodes with the token lists, used by the commit message heuristics."""
    ta, tb = tokenize(old), tokenize(new)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    return [(op, ta, tb, i1, i2, j1, j2) for op, i1, i2, j1, j2 in sm.get_opcodes()]


def _pair_block(dels: list[tuple[int, str]], adds: list[tuple[int, str]]) -> list[DiffLine]:
    """Pair a block of removed lines with the following block of added lines."""
    out: list[DiffLine] = []
    i = j = 0
    while i < len(dels) or j < len(adds):
        if i < len(dels) and j < len(adds):
            (ono, o), (nno, n) = dels[i], adds[j]
            if similarity(o, n) >= 0.3:
                out.append(DiffLine('change', ono, nno, intraline(o, n)))
                i += 1
                j += 1
                continue
            # Not similar: emit whichever side has no good partner ahead.
            best_j = max(range(j, len(adds)), key=lambda k: similarity(o, adds[k][1]))
            if similarity(o, adds[best_j][1]) >= 0.3:
                for k in range(j, best_j):
                    out.append(DiffLine('add', None, adds[k][0], [Seg(adds[k][1], INS)]))
                j = best_j
                continue
            out.append(DiffLine('del', ono, None, [Seg(o, DEL)]))
            i += 1
        elif i < len(dels):
            out.append(DiffLine('del', dels[i][0], None, [Seg(dels[i][1], DEL)]))
            i += 1
        else:
            out.append(DiffLine('add', None, adds[j][0], [Seg(adds[j][1], INS)]))
            j += 1
    return out


_HUNK_RE = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')


def parse_unified(diff: str) -> list[FileDiff]:
    """Parse `git diff` output (no color) into files/hunks with paired lines."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    dels: list[tuple[int, str]] = []
    adds: list[tuple[int, str]] = []
    old_no = new_no = 0

    def flush():
        nonlocal dels, adds
        if hunk is not None and (dels or adds):
            hunk.lines.extend(_pair_block(dels, adds))
        dels, adds = [], []

    for raw in diff.splitlines():
        if raw.startswith('diff --git '):
            flush()
            m = re.match(r'diff --git a/(.*) b/(.*)$', raw)
            cur = FileDiff(path=m.group(2) if m else raw[11:])
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if hunk is None or raw.startswith('@@'):
            m = _HUNK_RE.match(raw)
            if m:
                flush()
                old_no, new_no = int(m.group(1)), int(m.group(2))
                hunk = Hunk(header=raw)
                cur.hunks.append(hunk)
                continue
            if raw.startswith(('index ', '--- ', '+++ ')):
                continue
            cur.meta.append(raw)
            continue
        if raw.startswith('-'):
            if adds:  # a '-' after '+' starts a new block
                flush()
            dels.append((old_no, raw[1:]))
            old_no += 1
        elif raw.startswith('+'):
            adds.append((new_no, raw[1:]))
            new_no += 1
        elif raw.startswith('\\'):
            flush()
            hunk.lines.append(DiffLine('meta', None, None, [Seg(raw, EQ)]))
        else:
            flush()
            text = raw.removeprefix(' ')
            hunk.lines.append(DiffLine('ctx', old_no, new_no, [Seg(text, EQ)]))
            old_no += 1
            new_no += 1
    flush()
    return files


def focus(segs: list[Seg], context: int = 60) -> list[Seg]:
    """Trim long unchanged stretches of a changed line to `context` chars around changes."""
    if all(s.kind == EQ for s in segs):
        return segs
    out: list[Seg] = []
    n = len(segs)
    for idx, s in enumerate(segs):
        if s.kind != EQ:
            out.append(s)
            continue
        t = s.text
        first, last = idx == 0, idx == n - 1
        if first and len(t) > context:
            out += [Seg('…', ELIDE), Seg(t[-context:], EQ)]
        elif last and len(t) > context:
            out += [Seg(t[:context], EQ), Seg('…', ELIDE)]
        elif not first and not last and len(t) > 2 * context + 1:
            out += [Seg(t[:context], EQ), Seg(' … ', ELIDE), Seg(t[-context:], EQ)]
        else:
            out.append(s)
    return out


def visible_ws(text: str) -> str:
    """Make whitespace visible inside changed segments (a changed space is otherwise invisible)."""
    return text.replace(' ', '·').replace('\t', '→')


def stats(files: list[FileDiff]) -> dict[str, int]:
    st = {'files': len(files), 'changed': 0, 'added': 0, 'removed': 0}
    for f in files:
        for h in f.hunks:
            for ln in h.lines:
                if ln.kind == 'change':
                    st['changed'] += 1
                elif ln.kind == 'add':
                    st['added'] += 1
                elif ln.kind == 'del':
                    st['removed'] += 1
    return st


def render_rich(files: list[FileDiff], focus_mode: bool = True, context: int = 60, untracked: list[str] | None = None):
    """Render parsed diff as a Rich `Text` (imported lazily so tests don't need Rich)."""
    from rich.text import Text

    out = Text()
    for f in files:
        out.append(f'━━ {f.path}', style='bold cyan')
        out.append('\n')
        for m in f.meta:
            out.append(f'   {m}\n', style='italic yellow')
        for h in f.hunks:
            out.append(f'{h.header}\n', style='magenta')
            for ln in h.lines:
                o = '' if ln.old_no is None else str(ln.old_no)
                n = '' if ln.new_no is None else str(ln.new_no)
                if ln.kind == 'meta':
                    out.append(f'{"":>11}{ln.segs[0].text}\n', style='dim')
                    continue
                marker = {'ctx': ' ', 'del': '-', 'add': '+', 'change': '~'}[ln.kind]
                mstyle = {'ctx': 'dim', 'del': 'red', 'add': 'green', 'change': 'yellow bold'}[ln.kind]
                out.append(f'{o:>5}{n:>5} ', style='dim')
                out.append(f'{marker} ', style=mstyle)
                segs = focus(ln.segs, context) if (focus_mode and ln.kind == 'change') else ln.segs
                for s in segs:
                    if s.kind == EQ:
                        out.append(s.text, style='dim' if ln.kind == 'ctx' else '')
                    elif s.kind == ELIDE:
                        out.append(s.text, style='dim italic')
                    elif ln.kind == 'change':
                        style = 'bold white on red3 strike' if s.kind == DEL else 'bold black on green3'
                        out.append(visible_ws(s.text), style=style)
                    else:
                        out.append(s.text, style='red' if s.kind == DEL else 'green')
                out.append('\n')
        out.append('\n')
    for path in untracked or []:
        out.append(f'━━ {path}  (new, untracked — will be committed)\n', style='bold yellow')
    return out
