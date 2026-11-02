"""Configuration: defaults, overridable in ~/.config/kfix/config.toml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

HOME = Path.home()
STATE_DIR = Path(os.environ.get('XDG_STATE_HOME', HOME / '.local' / 'state')) / 'kfix'
CONFIG_FILE = Path(os.environ.get('XDG_CONFIG_HOME', HOME / '.config')) / 'kfix' / 'config.toml'


@dataclass
class Config:
    problems_dir: Path = HOME / 'problems'
    org: str = 'kattis-problems'
    issue_repo: str = 'kattis-problems/issue-tracker'
    branch: str = 'main'
    error_log: Path = HOME / 'problems' / 'kfix-errors.log'
    claude_concurrency: int = 3
    test_concurrency: int = 1
    claude_model: str | None = None
    focus_context: int = 60
    # name -> argv; "{dir}" is replaced by the problem directory
    verifiers: dict[str, list[str]] = field(
        default_factory=lambda: {
            'verifyproblem': ['verifyproblem', '-j', '3', '{dir}'],
            'verifyproblem_llm': [str(HOME / '.local/bin/problemtools_llm/bin/verifyproblem.sh'), '{dir}', '-j', '3'],
        }
    )
    no_push: bool = False  # KFIX_NO_PUSH=1: push becomes `git push --dry-run`

    @property
    def jobs_dir(self) -> Path:
        return STATE_DIR / 'jobs'

    @property
    def logs_dir(self) -> Path:
        return STATE_DIR / 'logs'

    @property
    def render_dir(self) -> Path:
        return STATE_DIR / 'render'

    @property
    def baseline_dir(self) -> Path:
        return STATE_DIR / 'baseline'


def load() -> Config:
    cfg = Config()
    if CONFIG_FILE.is_file():
        data = tomllib.loads(CONFIG_FILE.read_text())
        for key, value in data.items():
            if not hasattr(cfg, key):
                raise SystemExit(f'{CONFIG_FILE}: unknown key {key!r}')
            if key in ('problems_dir', 'error_log'):
                value = Path(os.path.expanduser(value))
            setattr(cfg, key, value)
    if os.environ.get('KFIX_NO_PUSH') not in (None, '', '0'):
        cfg.no_push = True
    for d in (cfg.jobs_dir, cfg.logs_dir, cfg.render_dir, cfg.baseline_dir):
        d.mkdir(parents=True, exist_ok=True)
    return cfg
