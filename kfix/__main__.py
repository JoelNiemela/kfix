"""kfix CLI.

  kfix                      start the TUI
  kfix add 349 355 xorsist  queue issues (number/URL) or problem ids; "kfix add 349 diffrun" = issue + problem id
  kfix list                 list jobs
  kfix doctor               check toolchains needed for a zero-error verifyproblem
  kfix drop <problem>       forget a job (the clone is kept)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from . import config, doctor, jobs
from .github import parse_add_input


def cmd_add(cfg, targets: list[str]) -> int:
    from .pipeline import Pipeline

    class _Quiet:
        def changed(self, job): pass
        def log(self, job, line): pass
        def failed(self, job): pass
        def attention(self, job, message): pass

    async def go() -> int:
        pipeline = Pipeline(cfg, _Quiet())
        rc = 0
        for target, override in parse_add_input(' '.join(targets)):
            try:
                job = await pipeline.create_job(target, override, jobs.load_all(cfg.jobs_dir))
            except Exception as e:  # noqa: BLE001
                print(f'{target}: {e}', file=sys.stderr)
                rc = 1
                continue
            title = f' — {job.issue["title"]}' if job.issue else ''
            print(f'queued {job.problem} {job.issue_label}{title}')
        return rc

    rc = asyncio.run(go())
    print('(a running kfix picks these up within a few seconds; otherwise start `kfix`)')
    return rc


def cmd_list(cfg) -> int:
    all_jobs = sorted(jobs.load_all(cfg.jobs_dir).values(), key=lambda j: j.created)
    if not all_jobs:
        print('no jobs')
    for j in all_jobs:
        stage = j.stage if j.stage != jobs.FAILED else f'failed@{j.failed_stage}'
        age = time.strftime('%m-%d %H:%M', time.localtime(j.created))
        print(f'{j.problem:<28} {j.issue_label:<6} {stage:<20} {age}  {j.status}')
    return 0


def cmd_drop(cfg, problem: str) -> int:
    p = cfg.jobs_dir / f'{problem}.json'
    if not p.exists():
        print(f'no job {problem}', file=sys.stderr)
        return 1
    p.unlink()
    print(f'dropped {problem} (clone kept in {cfg.problems_dir / problem})')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='kfix', description='Pipeline for fixing Kattis problem issues.')
    sub = ap.add_subparsers(dest='cmd')
    a = sub.add_parser('add', help='queue issues / problems')
    a.add_argument('targets', nargs='+')
    sub.add_parser('list', help='list jobs')
    sub.add_parser('doctor', help='check toolchains and tools')
    d = sub.add_parser('drop', help='forget a job')
    d.add_argument('problem')
    args = ap.parse_args(argv)
    cfg = config.load()
    if args.cmd == 'add':
        return cmd_add(cfg, args.targets)
    if args.cmd == 'list':
        return cmd_list(cfg)
    if args.cmd == 'drop':
        return cmd_drop(cfg, args.problem)
    if args.cmd == 'doctor':
        text, ok = doctor.check()
        print(text)
        return 0 if ok else 1
    try:
        from .app import run
    except ImportError as e:
        print(f'cannot start the TUI: {e}\n  install it with: sudo pacman -S python-textual', file=sys.stderr)
        return 1
    run(cfg)
    return 0


if __name__ == '__main__':
    sys.exit(main())
