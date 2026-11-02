"""Job model and JSON persistence (~/.local/state/kfix/jobs/<problem>.json)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# Stages
NEW = 'new'            # added, issue not resolved / not cloned yet
CLONING = 'cloning'
FIXING = 'fixing'      # waiting for you: c (claude) / C (interactive) / e (edit) / r (ready)
CLAUDE = 'claude'      # headless claude running
REVIEW = 'review'      # waiting for you: y (approve) / f (feedback) / e / x
COMMITTING = 'committing'
TESTING = 'testing'
PUSHING = 'pushing'
DONE = 'done'
FAILED = 'failed'
ABANDONED = 'abandoned'

RUNNING = {CLONING, CLAUDE, COMMITTING, TESTING, PUSHING}
WAITING_FOR_USER = {FIXING, REVIEW, FAILED}
FINISHED = {DONE, ABANDONED}


@dataclass
class Job:
    problem: str
    target: str = ''                  # what was passed to `kfix add`
    issue: dict | None = None          # number, title, body, url, labels, repo
    issue_ref: tuple | None = None     # (repo, number) before the issue is fetched
    stage: str = NEW
    failed_stage: str | None = None
    error: str | None = None
    status: str = ''                   # short live status, e.g. "queued", "verifyproblem_llm…"
    created: float = field(default_factory=time.time)
    stage_since: float = field(default_factory=time.time)
    claude_session: str | None = None
    claude_result: str | None = None
    commit_msg_user: str | None = None   # only what you typed in the review field
    commit_msg: str | None = None        # message of the last commit kfix made
    commit_sha: str | None = None      # our unpushed commit (amended on re-approval)
    base_sha: str | None = None        # HEAD when the job started fixing
    pushed: bool = False
    tests: dict = field(default_factory=dict)  # verifier name -> VerifyResult.to_dict()

    # ---- persistence -------------------------------------------------
    def path(self, jobs_dir: Path) -> Path:
        return jobs_dir / f'{self.problem}.json'

    def save(self, jobs_dir: Path) -> None:
        tmp = self.path(jobs_dir).with_suffix('.json.tmp')
        tmp.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False))
        os.replace(tmp, self.path(jobs_dir))

    @classmethod
    def load(cls, path: Path) -> Job:
        data = json.loads(path.read_text())
        known = {f.name for f in fields(cls)}
        job = cls(**{k: v for k, v in data.items() if k in known})
        if job.issue_ref is not None:
            job.issue_ref = tuple(job.issue_ref)
        return job

    def set_stage(self, stage: str, status: str = '') -> None:
        self.stage = stage
        self.stage_since = time.time()
        self.status = status
        if stage != FAILED:
            self.error = None
            self.failed_stage = None

    @property
    def issue_label(self) -> str:
        if self.issue:
            return f'#{self.issue["number"]}'
        if self.issue_ref:
            return f'#{self.issue_ref[1]}'
        return ''


def load_all(jobs_dir: Path) -> dict[str, Job]:
    jobs = {}
    for p in sorted(jobs_dir.glob('*.json')):
        try:
            job = Job.load(p)
        except (json.JSONDecodeError, TypeError, OSError):
            continue
        jobs[job.problem] = job
    return jobs
