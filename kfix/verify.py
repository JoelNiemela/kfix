"""Running and parsing verifyproblem / verifyproblem_llm output."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

_ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
_SUMMARY = re.compile(r'([\w.-]+) tested: (\d+) errors?, (\d+) warnings?')  # \w excludes the table's box chars
_TOOLCHAIN = re.compile(r'could not find (?:compiler|interpreter|program) "([^"]+)"')
_TABLE_CHARS = '╭│├╰┌└┃┏┗┡┣╞'


@dataclass
class VerifyResult:
    name: str
    returncode: int | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    n_errors: int | None = None
    n_warnings: int | None = None
    table: str = ''
    missing_toolchains: list[str] = field(default_factory=list)
    preexisting: list[bool] | None = None  # parallel to `errors`, filled by the baseline diagnostic
    note: str = ''

    @property
    def passed(self) -> bool:
        return self.n_errors == 0 and self.returncode == 0

    def summary(self) -> str:
        if self.n_errors is None:
            return f'{self.name}: no summary line (exit {self.returncode})'
        s = f'{self.name}: {self.n_errors} errors, {self.n_warnings} warnings'
        if self.returncode not in (0, None):
            s += f' (exit {self.returncode})'
        return s

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> VerifyResult:
        return cls(**d)


def strip_ansi(s: str) -> str:
    return _ANSI.sub('', s)


def parse_output(name: str, text: str, returncode: int | None) -> VerifyResult:
    text = strip_ansi(text)
    res = VerifyResult(name=name, returncode=returncode)
    cur: list[str] | None = None
    table_lines: list[str] = []
    for line in text.splitlines():
        m = _SUMMARY.search(line)
        if m:
            res.n_errors, res.n_warnings = int(m.group(2)), int(m.group(3))
            before = line[: m.start()]
            if before.strip():
                table_lines.append(before)
            cur = None
            continue
        if line.startswith('ERROR'):
            res.errors.append(line)
            cur = res.errors
            continue
        if line.startswith('WARNING'):
            res.warnings.append(line)
            cur = res.warnings
            continue
        stripped = line.lstrip()
        if stripped[:1] in _TABLE_CHARS or (table_lines and line.strip() and '@' in line and 'time limit' in line):
            table_lines.append(line)
            cur = None
            continue
        # problemtools indents an error's details by 8 spaces; shallower lines are progress output
        if cur is not None and line.startswith((' ' * 8, '\t')) and line.strip():
            cur[-1] += '\n' + line  # continuation of a multi-line ERROR/WARNING
            continue
        cur = None
    res.table = '\n'.join(table_lines)
    res.missing_toolchains = sorted({m.group(1) for e in res.errors for m in _TOOLCHAIN.finditer(e)})
    return res


def normalize_error(err: str, problem_dir: str) -> str:
    """Normalize an error for baseline comparison (paths, timings, temp dirs)."""
    s = err.replace(problem_dir, '<dir>')
    s = re.sub(r'/tmp/[^\s"\']+', '<tmp>', s)
    s = re.sub(r'\d+\.\d+s?', '<num>', s)
    return s.split('\n', 1)[0]
