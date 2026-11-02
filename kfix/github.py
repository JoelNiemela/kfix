"""Read-only GitHub access through `gh`. kfix never comments on or edits issues."""

from __future__ import annotations

import asyncio
import json
import re

_PROBLEM_ID = re.compile(r'^[a-z0-9]+$')
_ISSUE_URL = re.compile(r'^https?://github\.com/([^/]+/[^/]+)/issues/(\d+)')
_TITLE_ID = re.compile(r'^\s*`?([a-z0-9]+)`?\s*:')


def parse_target(arg: str, default_repo: str) -> tuple[str, str | int, str | None]:
    """Classify a `kfix add` argument.

    Returns ('issue', number, repo) or ('problem', problem_id, None)."""
    arg = arg.strip()
    if m := _ISSUE_URL.match(arg):
        return 'issue', int(m.group(2)), m.group(1)
    if arg.lstrip('#').isdigit():
        return 'issue', int(arg.lstrip('#')), default_repo
    if _PROBLEM_ID.match(arg):
        return 'problem', arg, None
    raise ValueError(f'not an issue number/URL or problem id: {arg!r}')


def problem_id_from_title(title: str) -> str | None:
    m = _TITLE_ID.match(title or '')
    return m.group(1) if m else None


async def fetch_issue(repo: str, number: int) -> dict:
    proc = await asyncio.create_subprocess_exec(
        'gh', 'issue', 'view', str(number), '-R', repo, '--json', 'number,title,body,url,labels,state',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f'gh issue view {number} -R {repo} failed: {err.decode().strip()}')
    data = json.loads(out)
    data['repo'] = repo
    data['labels'] = [lab['name'] for lab in data.get('labels', [])]
    return data


def parse_add_input(value: str) -> list[tuple[str, str | None]]:
    """"355 349 xorsist" -> three targets; "349 diffrun" (issue + problem id) -> one target with override."""
    parts = value.split()
    if len(parts) == 2 and _is_issue(parts[0]) and not _is_issue(parts[1]):
        return [(parts[0], parts[1])]
    return [(p, None) for p in parts]


def _is_issue(s: str) -> bool:
    return s.lstrip('#').isdigit() or s.startswith(('http://', 'https://'))
