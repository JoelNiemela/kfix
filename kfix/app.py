"""The kfix TUI (Textual)."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from collections import deque
from functools import partial

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from . import chardiff, claude, commitmsg, jobs
from .config import Config
from .github import parse_add_input
from .jobs import Job
from .pipeline import DiffInfo, Pipeline
from .verify import VerifyResult

SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'

STAGE_STYLE = {
    jobs.NEW: 'yellow', jobs.CLONING: 'yellow', jobs.CLAUDE: 'yellow', jobs.COMMITTING: 'yellow',
    jobs.TESTING: 'yellow', jobs.PUSHING: 'yellow', jobs.FIXING: 'bold cyan', jobs.REVIEW: 'bold cyan',
    jobs.FAILED: 'bold red', jobs.DONE: 'green', jobs.ABANDONED: 'dim',
}


def _elapsed(since: float) -> str:
    s = int(time.time() - since)
    if s < 60:
        return f'{s}s'
    if s < 3600:
        return f'{s // 60}m{s % 60:02d}'
    return f'{s // 3600}h{(s % 3600) // 60:02d}'


class InputModal(ModalScreen[str | None]):
    DEFAULT_CSS = """
    InputModal { align: center middle; }
    InputModal > Vertical { width: 90; height: auto; border: thick $accent; background: $surface; padding: 1 2; }
    """
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(self, title: str, placeholder: str = '', value: str = ''):
        super().__init__()
        self._title, self._placeholder, self._value = title, placeholder, value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Input(value=self._value, placeholder=self._placeholder)
            yield Label('[dim]Enter to confirm, Esc to cancel[/dim]')

    @on(Input.Submitted)
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > Vertical { width: 80; height: auto; border: thick $error; background: $surface; padding: 1 2; }
    """
    BINDINGS = [Binding('y', 'yes', 'Yes'), Binding('n,escape', 'no', 'No')]

    def __init__(self, text: str):
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._text)
            yield Label('[b]y[/b] = yes, [b]n[/b]/Esc = no')

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


class _AppHooks:
    """Adapter so the pipeline's hook names don't clash with Textual's App attributes (e.g. App.log)."""

    def __init__(self, app: KfixApp):
        self.app = app

    def changed(self, job: Job) -> None:
        self.app.job_changed(job)

    def log(self, job: Job, line: str) -> None:
        self.app.job_log(job, line)

    def failed(self, job: Job) -> None:
        self.app.job_failed(job)

    def attention(self, job: Job, message: str) -> None:
        self.app.job_attention(job, message)


class KfixApp(App):
    TITLE = 'kfix'
    CSS = """
    #jobs { width: 46; border-right: solid $panel-lighten-2; }
    #detail { width: 1fr; }
    #diff-scroll { height: 1fr; }
    #commit-row { height: auto; padding: 0 1; }
    #commit-source { color: $text-muted; }
    #commit-msg { width: 1fr; }
    .pane-text { padding: 0 1; }
    """
    BINDINGS = [
        Binding('a', 'add', 'Add'),
        Binding('c', 'claude', 'Claude'),
        Binding('C,shift+c', 'claude_interactive', 'Claude (interactive)'),
        Binding('e', 'edit', 'Edit'),
        Binding('r', 'ready', 'Ready→review'),
        Binding('y', 'approve', 'Approve'),
        Binding('f', 'feedback', 'Feedback→Claude'),
        Binding('m', 'message', 'Commit msg'),
        Binding('o', 'open_render', 'Render+open'),
        Binding('z', 'toggle_focus', 'Diff focus'),
        Binding('x', 'discard', 'Discard'),
        Binding('R,shift+r', 'retry', 'Retry'),
        Binding('d', 'drop', 'Drop'),
        Binding('q', 'quit', 'Quit'),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.jobs: dict[str, Job] = {}
        self.busy: set[str] = set()
        self.logs: dict[str, deque[str]] = {}
        self.selected: str | None = None
        self.diffs: dict[str, DiffInfo] = {}
        self.focus_mode = True
        self.tick = 0
        self.pipeline = Pipeline(cfg, _AppHooks(self))

    # ---- layout ------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield DataTable(id='jobs', cursor_type='row', zebra_stripes=True)
            with TabbedContent(id='detail', initial='tab-diff'):
                with TabPane('Issue', id='tab-issue'):
                    with VerticalScroll():
                        yield Markdown(id='issue-md')
                with TabPane('Diff', id='tab-diff'):
                    with VerticalScroll(id='diff-scroll'):
                        yield Static(id='diff-view', classes='pane-text')
                    with Horizontal(id='commit-row'):
                        yield Label('', id='commit-source')
                        yield Input(id='commit-msg', placeholder='commit message')
                with TabPane('Tests', id='tab-tests'):
                    with VerticalScroll():
                        yield Static(id='tests-view', classes='pane-text')
                with TabPane('Error', id='tab-error'):
                    with VerticalScroll():
                        yield Static(id='error-view', classes='pane-text')
                with TabPane('Log', id='tab-log'):
                    yield RichLog(id='log-view', max_lines=5000, wrap=False, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one('#jobs', DataTable)
        table.add_column('problem', key='problem')
        table.add_column('#', key='issue')
        table.add_column('stage', key='stage')
        table.add_column('time', key='time')
        self.jobs = jobs.load_all(self.cfg.jobs_dir)
        for job in self.jobs.values():
            if job.stage in jobs.RUNNING:  # kfix was restarted mid-stage
                job.failed_stage = job.stage
                job.stage = jobs.FAILED
                job.error = f'interrupted while {job.failed_stage} (kfix was restarted) — press R to retry'
                job.save(self.cfg.jobs_dir)
        for job in self.jobs.values():
            if job.stage == jobs.NEW:
                self._spawn(job, self.pipeline.start(job))
        self.refresh_table()
        if self.jobs:
            self.select(next(iter(self.jobs)))
        self.set_interval(1.0, self._on_tick)
        self.set_interval(2.0, self._scan_new_jobs)
        self.sub_title = 'DRY RUN (KFIX_NO_PUSH)' if self.cfg.no_push else str(self.cfg.problems_dir)

    # ---- hooks for the pipeline ----------------------------------------
    def job_changed(self, job: Job) -> None:
        job.save(self.cfg.jobs_dir)
        self.refresh_table()
        if job.problem == self.selected:
            self.refresh_detail(job, stage_changed=True)
        self.refresh_bindings()

    def job_log(self, job: Job, line: str) -> None:
        buf = self.logs.setdefault(job.problem, deque(maxlen=5000))
        stamped = f'{time.strftime("%H:%M:%S")} {line}'
        buf.append(stamped)
        if job.problem == self.selected:
            self.query_one('#log-view', RichLog).write(stamped)

    def job_failed(self, job: Job) -> None:
        self.bell()
        self.notify(f'{job.problem} failed at {job.failed_stage}', title='kfix', severity='error', timeout=15)

    def job_attention(self, job: Job, message: str) -> None:
        self.notify(message, title='kfix', timeout=8)

    # ---- job list ------------------------------------------------------
    def _spawn(self, job: Job, coro) -> None:
        self.busy.add(job.problem)

        async def wrapper():
            try:
                await coro
            except Exception as e:  # noqa: BLE001 — surface any bug as a job failure
                import traceback
                await self.pipeline._fail(job, job.stage, f'internal error: {e}\n{traceback.format_exc()}')
            finally:
                self.busy.discard(job.problem)
                self.refresh_bindings()

        self.run_worker(wrapper, group=job.problem, exclusive=False)
        self.refresh_bindings()

    def _row(self, job: Job) -> tuple:
        style = STAGE_STYLE.get(job.stage, '')
        if job.stage in jobs.RUNNING:
            icon = SPINNER[self.tick % len(SPINNER)]
        elif job.stage == jobs.FAILED:
            icon = '✗'
        elif job.stage == jobs.DONE:
            icon = '✓'
        elif job.stage in (jobs.FIXING, jobs.REVIEW):
            icon = '▶'
        else:
            icon = ' '
        stage = job.stage if job.stage != jobs.FAILED else f'failed@{job.failed_stage}'
        if job.status and job.stage != jobs.DONE:
            stage += f' ({job.status})'
        return (
            Text(job.problem, style='bold' if job.stage in jobs.WAITING_FOR_USER else ''),
            job.issue_label,
            Text(f'{icon} {stage}', style=style),
            _elapsed(job.stage_since),
        )

    def refresh_table(self) -> None:
        table = self.query_one('#jobs', DataTable)
        ordered = sorted(self.jobs.values(), key=lambda j: j.created)
        current_keys = [rk.value for rk in table.rows]
        if current_keys == [j.problem for j in ordered]:
            for job in ordered:  # same rows: update in place (no flicker, cursor kept)
                for col, value in zip(('problem', 'issue', 'stage', 'time'), self._row(job)):
                    table.update_cell(job.problem, col, value)
            return
        cursor_key = self.selected
        table.clear()
        for job in ordered:
            table.add_row(*self._row(job), key=job.problem)
        if cursor_key in self.jobs:
            try:
                table.move_cursor(row=table.get_row_index(cursor_key))
            except Exception:  # noqa: BLE001
                pass

    def _on_tick(self) -> None:
        self.tick += 1
        if any(j.stage in jobs.RUNNING or j.stage in jobs.WAITING_FOR_USER for j in self.jobs.values()):
            self.refresh_table()

    def _scan_new_jobs(self) -> None:
        for p in self.cfg.jobs_dir.glob('*.json'):
            name = p.stem
            if name in self.jobs:
                continue
            try:
                job = Job.load(p)
            except Exception:  # noqa: BLE001
                continue
            self.jobs[job.problem] = job
            self.refresh_table()
            if job.stage == jobs.NEW:
                self._spawn(job, self.pipeline.start(job))

    @on(DataTable.RowHighlighted, '#jobs')
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value if event.row_key else None
        if key and key != self.selected:
            self.select(key)

    def select(self, problem: str) -> None:
        self.selected = problem
        job = self.jobs[problem]
        log = self.query_one('#log-view', RichLog)
        log.clear()
        log.write(f'problem dir: {self.pipeline.dir(job)}')
        log.write(f'logs:        {self.cfg.logs_dir / problem}')
        log.write(f'state:       {job.path(self.cfg.jobs_dir)}')
        for line in self.logs.get(problem, []):
            log.write(line)
        self.refresh_detail(job, stage_changed=True)
        self.refresh_bindings()

    @property
    def job(self) -> Job | None:
        return self.jobs.get(self.selected) if self.selected else None

    # ---- detail panes ----------------------------------------------------
    def refresh_detail(self, job: Job, stage_changed: bool = False) -> None:
        issue = job.issue
        md = f'# {job.problem}\n\n'
        if issue:
            md += f'**[#{issue["number"]}]({issue["url"]})** {issue["title"]}  \n'
            md += f'labels: {", ".join(issue.get("labels", [])) or "-"} · state: {issue.get("state", "?")}\n\n---\n\n'
            md += (issue.get('body') or '*(no body)*')
        else:
            md += '*(no GitHub issue — added by problem id)*'
        self.query_one('#issue-md', Markdown).update(md)
        self.query_one('#tests-view', Static).update(self._tests_text(job))
        err = Text()
        if job.error:
            err.append(f'failed at {job.failed_stage}\n\n', style='bold red')
            err.append(job.error + '\n\n')
            err.append(f'logs: {self.cfg.logs_dir / job.problem}\n', style='dim')
            err.append(f'error log: {self.cfg.error_log}\n', style='dim')
        else:
            err.append('no error', style='dim')
        self.query_one('#error-view', Static).update(err)
        if (self.pipeline.dir(job) / '.git').is_dir():
            self.run_worker(partial(self._refresh_diff, job), group=f'diff-{job.problem}', exclusive=True)
        else:
            self.query_one('#diff-view', Static).update(Text('(not cloned yet)', style='dim'))
        if stage_changed:
            tabs = self.query_one('#detail', TabbedContent)
            if job.stage == jobs.FAILED:
                tabs.active = 'tab-error'
            elif job.stage in (jobs.REVIEW, jobs.FIXING):
                tabs.active = 'tab-diff'
            elif job.stage in (jobs.TESTING,):
                tabs.active = 'tab-log'

    async def _refresh_diff(self, job: Job) -> None:
        # Shielded: if a newer refresh cancels this worker, the git commands still finish (and release
        # the index lock) instead of being orphaned mid-run.
        info = await asyncio.shield(self.pipeline.diff(job))
        self.diffs[job.problem] = info
        if job.problem != self.selected:
            return
        self._render_diff(job, info)

    def _render_diff(self, job: Job, info: DiffInfo) -> None:
        view = self.query_one('#diff-view', Static)
        if not info.text.strip():
            view.update(Text('(no changes)', style='dim'))
        else:
            st = chardiff.stats(info.files)
            head = Text(f'vs {info.base[:10]} · {st["files"]} file(s), {st["changed"]} changed, '
                        f'{st["added"]} added, {st["removed"]} removed line(s) · focus {"on" if self.focus_mode else "off"} (z)\n\n',
                        style='dim')
            view.update(head + chardiff.render_rich(info.files, self.focus_mode, self.cfg.focus_context))
        msg, source = commitmsg.choose(job.commit_msg_user, info.files, job.claude_result,
                                       job.issue['title'] if job.issue else None, job.problem)
        inp = self.query_one('#commit-msg', Input)
        if not inp.has_focus:
            inp.value = msg
        self.query_one('#commit-source', Label).update(f'msg ({source}): ')

    def _tests_text(self, job: Job) -> Text:
        t = Text()
        if not job.tests:
            t.append('no test results yet', style='dim')
            return t
        for name, d in job.tests.items():
            vr = VerifyResult.from_dict(d)
            t.append(('✓ ' if vr.passed else '✗ ') + vr.summary() + '\n', style='bold green' if vr.passed else 'bold red')
            if vr.missing_toolchains:
                t.append(f'  missing toolchain(s): {", ".join(vr.missing_toolchains)} — run `kfix doctor`\n', style='yellow')
            for i, e in enumerate(vr.errors):
                tag = ''
                if vr.preexisting is not None and i < len(vr.preexisting):
                    tag = '[pre-existing] ' if vr.preexisting[i] else '[NEW] '
                t.append(f'  {tag}{e}\n', style='red')
            if vr.note:
                t.append(f'  {vr.note}\n', style='dim')
            if vr.table:
                t.append(vr.table + '\n')
            t.append('\n')
        return t

    # ---- actions -------------------------------------------------------
    def check_action(self, action: str, parameters) -> bool | None:
        always = {'add', 'quit', 'toggle_focus', 'command_palette'}
        if action in always or action.startswith(('focus', 'scroll', 'select', 'cursor')):
            return True
        job = self.job
        if job is None:
            return None if action in {b.action for b in self.BINDINGS} else True
        busy = job.problem in self.busy
        fixable = job.stage in (jobs.FIXING, jobs.REVIEW) or (
            job.stage == jobs.FAILED and job.failed_stage in (jobs.CLAUDE, jobs.TESTING, jobs.COMMITTING, jobs.REVIEW, jobs.PUSHING))
        cloned = (self.pipeline.dir(job) / '.git').is_dir()
        rules = {
            'claude': fixable and not busy,
            'claude_interactive': fixable and not busy,
            'edit': fixable and not busy,
            'ready': job.stage in (jobs.FIXING, jobs.FAILED) and fixable and not busy,
            'approve': job.stage == jobs.REVIEW and not busy,
            'message': job.stage == jobs.REVIEW,
            'feedback': fixable and not busy,
            'open_render': cloned and not busy,
            'discard': fixable and not busy and cloned,
            'retry': job.stage == jobs.FAILED and not busy,
            'drop': not busy,
        }
        if action in rules:
            return True if rules[action] else None
        return True

    def _need_job(self) -> Job | None:
        job = self.job
        if job is None:
            self.notify('no job selected', severity='warning')
        return job

    def action_add(self) -> None:
        def done(value: str | None) -> None:
            if not value or not value.strip():
                return
            for target, override in parse_add_input(value):
                self.run_worker(partial(self._add, target, override), exclusive=False)

        self.push_screen(InputModal('Add: issue numbers / URLs / problem ids (space separated), or "<issue> <problemid>"',
                                    '355 349 xorsist'), done)

    async def _add(self, target: str, override: str | None) -> None:
        try:
            job = await self.pipeline.create_job(target, override, self.jobs)
        except Exception as e:  # noqa: BLE001
            self.notify(str(e), title=f'add {target}', severity='error', timeout=12)
            return
        self.jobs[job.problem] = job
        self.refresh_table()
        self.select(job.problem)
        self._spawn(job, self.pipeline.start(job))

    def action_claude(self) -> None:
        if job := self._need_job():
            self._spawn(job, self.pipeline.run_claude(job))

    def action_feedback(self) -> None:
        job = self._need_job()
        if not job:
            return

        def done(text: str | None) -> None:
            if text and text.strip():
                self._spawn(job, self.pipeline.run_claude(job, feedback=text.strip()))

        hint = 'continues the previous Claude session' if job.claude_session else 'starts a new Claude session'
        self.push_screen(InputModal(f'Feedback for Claude on {job.problem} ({hint})'), done)

    async def _foreground(self, job: Job, argv: list[str]) -> None:
        """Suspend the TUI, run an interactive program in the problem dir, then check for changes."""
        self.busy.add(job.problem)
        try:
            with self.suspend():
                proc = await asyncio.create_subprocess_exec(*argv, cwd=self.pipeline.dir(job))
                await proc.wait()
            if job.stage == jobs.FAILED:
                job.set_stage(jobs.FIXING)
            if not await self.pipeline.ready(job):
                self.job_changed(job)
                self.notify(f'{job.problem}: no changes', severity='warning')
        finally:
            self.busy.discard(job.problem)
            self.refresh_bindings()

    async def action_claude_interactive(self) -> None:
        if job := self._need_job():
            render = self.cfg.render_dir / job.problem / 'claude'
            render.mkdir(parents=True, exist_ok=True)
            prompt = claude.build_prompt(job.problem, self.pipeline.dir(job), job.issue, render)
            await self._foreground(job, claude.interactive_cmd(prompt, render, self.cfg.claude_model))

    async def action_edit(self) -> None:
        if job := self._need_job():
            d = self.pipeline.dir(job)
            files = sorted((d / 'problem_statement').glob('problem*.tex')) or [d]
            editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vi'
            await self._foreground(job, [*shlex.split(editor), *map(str, files)])

    async def action_ready(self) -> None:
        if job := self._need_job():
            if job.stage == jobs.FAILED:
                job.set_stage(jobs.FIXING)
            if not await self.pipeline.ready(job):
                self.job_changed(job)
                self.notify(f'{job.problem}: nothing to review (no changes)', severity='warning')

    def action_message(self) -> None:
        self.query_one('#detail', TabbedContent).active = 'tab-diff'
        self.query_one('#commit-msg', Input).focus()

    @on(Input.Submitted, '#commit-msg')
    def _msg_submitted(self, event: Input.Submitted) -> None:
        if job := self.job:
            job.commit_msg_user = event.value.strip() or None
            job.save(self.cfg.jobs_dir)
            self._refresh_label_after_edit(job)
        self.query_one('#jobs', DataTable).focus()

    def _refresh_label_after_edit(self, job: Job) -> None:
        if info := self.diffs.get(job.problem):
            self._render_diff(job, info)

    def on_key(self, event) -> None:
        if event.key == 'escape' and self.query_one('#commit-msg', Input).has_focus:
            self.query_one('#jobs', DataTable).focus()
            event.stop()

    async def action_approve(self) -> None:
        job = self._need_job()
        if not job:
            return
        shown = self.diffs.get(job.problem)
        current = await self.pipeline.diff(job)
        if shown is None or shown.digest != current.digest:
            self.diffs[job.problem] = current
            self._render_diff(job, current)
            self.notify('The diff changed since it was shown — review it again, then press y', severity='warning')
            return
        if not current.text.strip() and not job.commit_sha:
            self.notify('nothing to commit', severity='warning')
            return
        message = self.query_one('#commit-msg', Input).value.strip()
        self._spawn(job, self.pipeline.approve(job, message))

    def action_open_render(self) -> None:
        job = self._need_job()
        if not job:
            return

        async def go():
            self.notify(f'rendering {job.problem}…', timeout=4)
            paths = await self.pipeline.render(job)
            if not paths:
                self.notify('rendering failed — see Log', severity='error')
                return
            for p in paths:
                await asyncio.create_subprocess_exec('xdg-open', str(p), stdout=asyncio.subprocess.DEVNULL,
                                                     stderr=asyncio.subprocess.DEVNULL)

        self.run_worker(go, exclusive=False)

    def action_toggle_focus(self) -> None:
        self.focus_mode = not self.focus_mode
        if (job := self.job) and (info := self.diffs.get(job.problem)):
            self._render_diff(job, info)

    def action_discard(self) -> None:
        job = self._need_job()
        if not job:
            return

        def done(yes: bool | None) -> None:
            if yes:
                self._spawn(job, self.pipeline.discard(job))

        self.push_screen(ConfirmModal(f'Discard ALL local changes in {self.pipeline.dir(job)}'
                                      f' (git reset --hard {(job.base_sha or "HEAD")[:10]} + git clean -fd)?'), done)

    def action_retry(self) -> None:
        if job := self._need_job():
            self._spawn(job, self.pipeline.retry(job))

    def action_drop(self) -> None:
        job = self._need_job()
        if not job:
            return

        def done(yes: bool | None) -> None:
            if not yes:
                return
            job.path(self.cfg.jobs_dir).unlink(missing_ok=True)
            del self.jobs[job.problem]
            self.selected = None
            self.refresh_table()
            if self.jobs:
                self.select(next(iter(self.jobs)))
            self.refresh_bindings()

        if job.stage in jobs.FINISHED:
            done(True)
        else:
            self.push_screen(ConfirmModal(f'Drop {job.problem} from kfix? (the clone in {self.pipeline.dir(job)} is kept)'), done)


def run(cfg: Config) -> None:
    KfixApp(cfg).run()
