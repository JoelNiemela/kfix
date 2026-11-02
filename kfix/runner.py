"""Async subprocess helper: stream merged stdout/stderr line by line to a log + callback."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from collections.abc import Callable
from pathlib import Path


class ProcResult:
    def __init__(self, returncode: int, output: str):
        self.returncode = returncode
        self.output = output

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def run(
    cmd: list[str],
    cwd: Path | None = None,
    log_file: Path | None = None,
    on_line: Callable[[str], None] | None = None,
    env: dict | None = None,
    stdin_devnull: bool = True,
) -> ProcResult:
    full_env = dict(os.environ)
    full_env.setdefault('COLUMNS', '200')
    if env:
        full_env.update(env)
    header = f'$ {shlex.join(cmd)}' + (f'   (in {cwd})' if cwd else '')
    if on_line:
        on_line(header)
    lines: list[str] = []
    logf = open(log_file, 'a', encoding='utf-8') if log_file else None
    try:
        if logf:
            logf.write(f'\n[{time.strftime("%F %T")}] {header}\n')
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=full_env,
            stdin=asyncio.subprocess.DEVNULL if stdin_devnull else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            limit=16 * 1024 * 1024,
        )
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode('utf-8', errors='replace').rstrip('\n')
            lines.append(line)
            if logf:
                logf.write(line + '\n')
                logf.flush()
            if on_line:
                on_line(line)
        rc = await proc.wait()
        if logf:
            logf.write(f'[exit {rc}]\n')
    except FileNotFoundError as e:
        rc = 127
        msg = f'command not found: {e.filename}'
        lines.append(msg)
        if on_line:
            on_line(msg)
    finally:
        if logf:
            logf.close()
    return ProcResult(rc, '\n'.join(lines))


async def git(args: list[str], cwd: Path, log_file: Path | None = None, on_line=None) -> ProcResult:
    return await run(['git', *args], cwd=cwd, log_file=log_file, on_line=on_line)


async def capture(cmd: list[str], cwd: Path | None = None) -> ProcResult:
    """Run quietly and return the output (for git diff/status etc.)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    text = out.decode('utf-8', errors='replace')
    if proc.returncode != 0 and err:
        text += err.decode('utf-8', errors='replace')
    return ProcResult(proc.returncode, text)
