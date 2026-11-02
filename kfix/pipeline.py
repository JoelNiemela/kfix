"""The per-problem pipeline. UI-agnostic: the TUI supplies `Hooks`."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Protocol

from . import chardiff, claude, github, jobs, runner
from .config import Config
from .jobs import Job
from .verify import VerifyResult, normalize_error, parse_output


class Hooks(Protocol):
    def changed(self, job: Job) -> None: ...
    def log(self, job: Job, line: str) -> None: ...
    def failed(self, job: Job) -> None: ...
    def attention(self, job: Job, message: str) -> None: ...


class DiffInfo:
    def __init__(self, text: str, files: list[chardiff.FileDiff], base: str):
        self.text = text
        self.files = files
        self.base = base
        self.digest = hashlib.sha1(text.encode()).hexdigest()


class Pipeline:
    def __init__(self, cfg: Config, hooks: Hooks):
        self.cfg = cfg
        self.hooks = hooks
        self.claude_sem = asyncio.Semaphore(cfg.claude_concurrency)
        self.test_sem = asyncio.Semaphore(cfg.test_concurrency)
        self._git_locks: dict[str, asyncio.Lock] = {}

    def git_lock(self, job: Job) -> asyncio.Lock:
        """Serializes index-writing git commands per problem (diff refresh vs. commit/discard/pull)."""
        return self._git_locks.setdefault(job.problem, asyncio.Lock())

    # ---- helpers -----------------------------------------------------
    def dir(self, job: Job) -> Path:
        return self.cfg.problems_dir / job.problem

    def log_file(self, job: Job, name: str) -> Path:
        d = self.cfg.logs_dir / job.problem
        d.mkdir(parents=True, exist_ok=True)
        return d / f'{name}.log'

    def _log(self, job: Job):
        return lambda line: self.hooks.log(job, line)

    def _set(self, job: Job, stage: str, status: str = '') -> None:
        job.set_stage(stage, status)
        self.hooks.changed(job)

    def _status(self, job: Job, status: str) -> None:
        job.status = status
        self.hooks.changed(job)

    async def _fail(self, job: Job, stage: str, message: str) -> None:
        job.stage = jobs.FAILED
        job.stage_since = time.time()
        job.failed_stage = stage
        job.error = message
        job.status = ''
        self.hooks.log(job, f'FAILED at {stage}: {message}')
        self.hooks.changed(job)
        first = message.strip().splitlines()[0] if message.strip() else '(no message)'
        try:
            with open(self.cfg.error_log, 'a', encoding='utf-8') as f:
                f.write(f'[{time.strftime("%F %T")}] {job.problem} {job.issue_label} failed at {stage}: {first}\n'
                        f'    logs: {self.cfg.logs_dir / job.problem}\n')
        except OSError:
            pass
        if shutil.which('notify-send'):
            try:
                proc = await asyncio.create_subprocess_exec(
                    'notify-send', '-u', 'critical', '-a', 'kfix', f'kfix: {job.problem} failed at {stage}', first[:200],
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await proc.wait()
            except OSError:
                pass
        self.hooks.failed(job)

    async def _head(self, job: Job) -> str | None:
        r = await runner.capture(['git', 'rev-parse', 'HEAD'], cwd=self.dir(job))
        return r.output.strip() if r.ok else None

    async def dirty(self, job: Job) -> bool:
        r = await runner.capture(['git', '--no-optional-locks', 'status', '--porcelain'], cwd=self.dir(job))
        return bool(r.output.strip())

    async def has_fix(self, job: Job) -> bool:
        """Working-tree changes, or our own unpushed commit (re-review after failed tests)."""
        if await self.dirty(job):
            return True
        return bool(job.commit_sha and not job.pushed and await self._head(job) == job.commit_sha)

    # ---- creating jobs ---------------------------------------------------
    async def create_job(self, target: str, problem_override: str | None, existing: dict[str, Job]) -> Job:
        kind, value, repo = github.parse_target(target, self.cfg.issue_repo)
        issue = None
        if kind == 'issue':
            issue = await github.fetch_issue(repo, int(value))
            problem = problem_override or github.problem_id_from_title(issue['title'])
            if not problem:
                raise ValueError(f'cannot tell the problem id from issue title {issue["title"]!r}; '
                                 f'add it as "{target} <problemid>"')
        else:
            problem = str(value)
        old = existing.get(problem)
        if old and old.stage not in jobs.FINISHED:
            raise ValueError(f'{problem} is already in the pipeline (stage {old.stage})')
        job = Job(problem=problem, target=target, issue=issue)
        job.save(self.cfg.jobs_dir)
        return job

    # ---- stages ------------------------------------------------------
    async def start(self, job: Job) -> None:
        """new → (fetch issue) → cloning → fixing"""
        log = self._log(job)
        if job.issue is None and job.issue_ref:
            try:
                job.issue = await github.fetch_issue(job.issue_ref[0], int(job.issue_ref[1]))
            except Exception as e:  # noqa: BLE001
                return await self._fail(job, jobs.NEW, str(e))
        self._set(job, jobs.CLONING)
        d = self.dir(job)
        lf = self.log_file(job, 'clone')
        if (d / '.git').is_dir():
            r = await runner.capture(['git', 'remote', 'get-url', 'origin'], cwd=d)
            if f'{self.cfg.org}/{job.problem}' not in r.output:
                return await self._fail(job, jobs.CLONING, f'{d} exists but its origin is {r.output.strip()!r}')
            r = await runner.capture(['git', 'branch', '--show-current'], cwd=d)
            if r.output.strip() != self.cfg.branch:
                return await self._fail(job, jobs.CLONING, f'{d} is on branch {r.output.strip()!r}, not {self.cfg.branch}')
            if await self.dirty(job):
                log('existing clone has uncommitted changes — keeping them, not pulling')
                job.status = 'kept local changes'
            else:
                async with self.git_lock(job):
                    r = await runner.git(['pull', '--ff-only'], cwd=d, log_file=lf, on_line=log)
                if not r.ok:
                    return await self._fail(job, jobs.CLONING, f'git pull --ff-only failed:\n{r.output}')
        elif d.exists():
            return await self._fail(job, jobs.CLONING, f'{d} exists but is not a git repository')
        else:
            url = f'git@github.com:{self.cfg.org}/{job.problem}.git'
            r = await runner.git(['clone', url, str(d)], cwd=self.cfg.problems_dir, log_file=lf, on_line=log)
            if not r.ok:
                if 'Repository not found' in r.output:
                    return await self._fail(
                        job, jobs.CLONING,
                        f'{self.cfg.org}/{job.problem} does not exist (or you have no access): this problem has no '
                        f'repository yet, so it cannot be fixed through kfix (cf. the "missing repository" label).\n\n{r.output}')
                return await self._fail(job, jobs.CLONING, f'git clone {url} failed:\n{r.output}')
        await runner.git(['fetch', 'origin'], cwd=d, log_file=lf, on_line=log)
        head = await self._head(job)
        r = await runner.capture(['git', 'merge-base', 'HEAD', f'origin/{self.cfg.branch}'], cwd=d)
        job.base_sha = r.output.strip() if r.ok and r.output.strip() else head
        if head and head != job.base_sha:
            # Unpushed local commit(s) on top of origin: part of the fix, shown in the review and
            # pushed on approval (the last one is amended with the approved message).
            job.commit_sha, job.pushed = head, False
            log(f'clone has unpushed local commit(s) {job.base_sha[:10]}..{head[:10]} — included in the review')
        status = job.status
        self._set(job, jobs.FIXING, status)
        if await self.has_fix(job):
            await self.ready(job)
        else:
            self.hooks.attention(job, f'{job.problem}: cloned — press c (Claude), C (interactive) or e (edit)')

    async def run_claude(self, job: Job, feedback: str | None = None) -> None:
        log = self._log(job)
        self._set(job, jobs.CLAUDE, 'queued')
        async with self.claude_sem:
            self._status(job, 'running')
            render = self.cfg.render_dir / job.problem / 'claude'
            render.mkdir(parents=True, exist_ok=True)
            if feedback and job.claude_session:
                cmd = claude.headless_cmd(feedback, render, resume=job.claude_session, model=self.cfg.claude_model)
            else:
                prompt = claude.build_prompt(job.problem, self.dir(job), job.issue, render)
                if feedback:
                    prompt += f'\n\nAdditional instructions from the user:\n{feedback}\n'
                cmd = claude.headless_cmd(prompt, render, model=self.cfg.claude_model)
            facts: dict = {}
            raw_lf = self.log_file(job, 'claude-stream')

            def on_line(line: str) -> None:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    log(line)
                    return
                text, f = claude.describe_event(ev)
                facts.update(f)
                if text:
                    for t in text.splitlines():
                        log(t)

            res = await runner.run(cmd, cwd=self.dir(job), log_file=raw_lf, on_line=on_line)
        if facts.get('session_id'):
            job.claude_session = facts['session_id']
        job.claude_result = facts.get('result') or job.claude_result
        if res.returncode != 0 or facts.get('is_error'):
            return await self._fail(job, jobs.CLAUDE, f'claude exited {res.returncode}:\n{facts.get("result") or res.output[-2000:]}')
        if await self.has_fix(job):
            await self.ready(job)
        else:
            await self._fail(job, jobs.CLAUDE, 'Claude made no changes:\n\n' + (facts.get('result') or ''))

    async def ready(self, job: Job) -> bool:
        """fixing → review, if there is something to review."""
        if not await self.has_fix(job):
            return False
        self._set(job, jobs.REVIEW)
        self.hooks.attention(job, f'{job.problem}: ready for review')
        return True

    async def diff(self, job: Job) -> DiffInfo:
        async with self.git_lock(job):
            return await self._diff(job)

    async def _diff(self, job: Job) -> DiffInfo:
        d = self.dir(job)
        untracked = await runner.capture(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=d)
        files = [f for f in untracked.output.split('\0') if f]
        if files:  # make new files show up in the diff (and get committed by `git add -A`)
            await runner.capture(['git', 'add', '--intent-to-add', '--', *files], cwd=d)
        base = 'HEAD'
        if job.commit_sha and not job.pushed and job.base_sha and await self._head(job) == job.commit_sha:
            base = job.base_sha  # show the whole fix, including our unpushed commit
        r = await runner.capture(['git', '--no-optional-locks', 'diff', '--no-color', '--no-ext-diff', '-U2', base], cwd=d)
        return DiffInfo(r.output, chardiff.parse_unified(r.output), base)

    async def approve(self, job: Job, message: str) -> None:
        """review → commit → test → push → done"""
        if not message.strip():
            return await self._fail(job, jobs.COMMITTING, 'empty commit message')
        if not await self.commit(job, message.strip()):
            return
        await self.test(job)

    async def commit(self, job: Job, message: str) -> bool:
        log = self._log(job)
        d = self.dir(job)
        self._set(job, jobs.COMMITTING)
        lf = self.log_file(job, 'git')
        amend = False
        async with self.git_lock(job):
            r = await runner.git(['add', '-A'], cwd=d, log_file=lf, on_line=log)
            if not r.ok:
                error = f'git add -A failed:\n{r.output}'
            else:
                amend = bool(job.commit_sha and not job.pushed and await self._head(job) == job.commit_sha)
                args = ['commit', '--amend', '-m', message] if amend else ['commit', '-m', message]
                r = await runner.git(args, cwd=d, log_file=lf, on_line=log)
                error = None if r.ok else f'git {" ".join(args[:2])} failed:\n{r.output}'
        if error:
            await self._fail(job, jobs.COMMITTING, error)
            return False
        job.commit_sha = await self._head(job)
        job.commit_msg = message
        log(f'committed {job.commit_sha[:10]}{" (amended)" if amend else ""}: {message}')
        return True

    async def test(self, job: Job) -> None:
        log = self._log(job)
        self._set(job, jobs.TESTING, 'queued')
        job.tests = {}
        results: list[VerifyResult] = []
        async with self.test_sem:
            for name, argv in self.cfg.verifiers.items():
                self._status(job, f'{name}…')
                cmd = [a.replace('{dir}', str(self.dir(job))) for a in argv]
                res = await runner.run(cmd, cwd=self.cfg.problems_dir, log_file=self.log_file(job, name), on_line=log)
                vr = parse_output(name, res.output, res.returncode)
                results.append(vr)
                job.tests[name] = vr.to_dict()
                log(vr.summary())
                self.hooks.changed(job)
            failed = [vr for vr in results if not vr.passed]
            if failed:
                self._status(job, 'baseline check…')
                for vr in failed:
                    await self._baseline(job, vr)
                    job.tests[vr.name] = vr.to_dict()
        if failed:
            lines = []
            for vr in failed:
                lines.append(vr.summary())
                if vr.missing_toolchains:
                    lines.append(f'  missing toolchain(s): {", ".join(vr.missing_toolchains)} — run `kfix doctor`')
                for i, e in enumerate(vr.errors):
                    tag = ''
                    if vr.preexisting is not None and i < len(vr.preexisting):
                        tag = '[pre-existing] ' if vr.preexisting[i] else '[NEW] '
                    lines.append(f'  {tag}{e.splitlines()[0]}')
                if vr.note:
                    lines.append(f'  {vr.note}')
            return await self._fail(job, jobs.TESTING, '\n'.join(lines))
        await self.push(job)

    async def _baseline(self, job: Job, vr: VerifyResult) -> None:
        """Diagnostic only: run the failing verifier on origin/<branch> and tag errors pre-existing/new."""
        log = self._log(job)
        d = self.dir(job)
        parent = self.cfg.baseline_dir / f'{job.problem}-{int(time.time())}'
        wt = parent / job.problem
        parent.mkdir(parents=True, exist_ok=True)
        lf = self.log_file(job, f'baseline-{vr.name}')
        r = await runner.git(['worktree', 'add', '--detach', str(wt), f'origin/{self.cfg.branch}'], cwd=d, log_file=lf, on_line=log)
        if not r.ok:
            vr.note = 'baseline check failed (git worktree add)'
            return
        try:
            argv = self.cfg.verifiers[vr.name]
            cmd = [a.replace('{dir}', str(wt)) for a in argv]
            res = await runner.run(cmd, cwd=self.cfg.problems_dir, log_file=lf, on_line=log)
            base = parse_output(vr.name, res.output, res.returncode)
            known = {normalize_error(e, str(wt)) for e in base.errors}
            vr.preexisting = [normalize_error(e, str(d)) in known for e in vr.errors]
            vr.note = f'baseline (origin/{self.cfg.branch}): {base.summary()}'
        finally:
            await runner.git(['worktree', 'remove', '--force', str(wt)], cwd=d, log_file=lf)
            shutil.rmtree(parent, ignore_errors=True)

    async def push(self, job: Job) -> None:
        log = self._log(job)
        d = self.dir(job)
        self._set(job, jobs.PUSHING)
        if await self.dirty(job):
            return await self._fail(job, jobs.PUSHING, 'working tree changed after approval — review again (r)')
        if job.commit_sha and await self._head(job) != job.commit_sha:
            return await self._fail(job, jobs.PUSHING, f'HEAD is no longer the approved commit {job.commit_sha[:10]}')
        args = ['push'] + (['--dry-run'] if self.cfg.no_push else []) + ['origin', f'HEAD:{self.cfg.branch}']
        r = await runner.git(args, cwd=d, log_file=self.log_file(job, 'git'), on_line=log)
        if not r.ok:
            return await self._fail(job, jobs.PUSHING, f'git {" ".join(args)} failed:\n{r.output}')
        job.pushed = not self.cfg.no_push
        self._set(job, jobs.DONE, 'dry run — not pushed' if self.cfg.no_push else f'pushed {job.commit_sha[:10] if job.commit_sha else ""}')
        self.hooks.attention(job, f'{job.problem}: {"dry-run push OK" if self.cfg.no_push else "pushed"}'
                                  + (f' — {job.issue["url"]}' if job.issue else ''))

    async def discard(self, job: Job) -> None:
        """Throw away all local changes, including our unpushed commit."""
        log = self._log(job)
        d = self.dir(job)
        lf = self.log_file(job, 'git')
        target = job.base_sha or 'HEAD'
        async with self.git_lock(job):
            await runner.git(['reset', '--hard', target], cwd=d, log_file=lf, on_line=log)
            await runner.git(['clean', '-fd'], cwd=d, log_file=lf, on_line=log)
        job.commit_sha = None
        job.tests = {}
        self._set(job, jobs.FIXING, 'changes discarded')

    async def retry(self, job: Job) -> None:
        stage = job.failed_stage
        if stage in (jobs.NEW, jobs.CLONING, None):
            await self.start(job)
        elif stage in (jobs.CLAUDE, jobs.COMMITTING, jobs.REVIEW):
            self._set(job, jobs.FIXING)
            await self.ready(job)
        elif stage == jobs.TESTING:
            if await self.dirty(job):
                self._set(job, jobs.FIXING)
                await self.ready(job)
            else:
                await self.test(job)
        elif stage == jobs.PUSHING:
            await self.push(job)

    async def render(self, job: Job) -> list[Path]:
        log = self._log(job)
        out = self.cfg.render_dir / job.problem
        shutil.rmtree(out / 'html', ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        lf = self.log_file(job, 'render')
        produced = []
        r = await runner.run(['problem2html', '-d', str(out / 'html'), str(self.dir(job))], cwd=out, log_file=lf, on_line=log)
        if r.ok and (out / 'html' / 'index.html').is_file():
            produced.append(out / 'html' / 'index.html')
        pdf = out / f'{job.problem}.pdf'
        r = await runner.run(['problem2pdf', '-o', str(pdf), str(self.dir(job))], cwd=out, log_file=lf, on_line=log)
        if r.ok and pdf.is_file():
            produced.append(pdf)
        return produced
