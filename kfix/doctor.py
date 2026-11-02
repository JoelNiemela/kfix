"""`kfix doctor`: check that every compiler/interpreter problemtools may call is installed.

Reads languages.yaml the way problemtools does (package default, then
/etc/kattis/problemtools, then ~/.config/problemtools, later overriding earlier),
for both the system problemtools and the LLM checkout.
"""

from __future__ import annotations

import glob
import os
import shlex
import shutil
from pathlib import Path

import yaml

ARCH_PACKAGES = {
    'gcc': 'gcc', 'g++': 'gcc', 'mcs': 'mono', 'mono': 'mono', 'gccgo': 'gcc-go', 'ghc': 'ghc',
    'javac': 'jdk-openjdk', 'java': 'jdk-openjdk', 'kotlinc': 'kotlin', 'kotlin': 'kotlin',
    'nodejs': 'nodejs — Arch only ships `node`: override in ~/.config/problemtools/languages.yaml',
    'node': 'nodejs', 'sbcl': 'sbcl', 'ocamlopt': 'ocaml', 'fpc': 'fpc', 'php': 'php',
    'swipl': 'swi-prolog', 'pypy': 'pypy', 'pypy3': 'pypy3', 'ruby': 'ruby', 'rustc': 'rust',
    'scalac': 'scala (AUR)', 'scala': 'scala (AUR)', 'cobc': 'gnucobol (AUR)', 'fsharpc': 'fsharp (AUR)',
}

USER_OVERRIDE = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'problemtools' / 'languages.yaml'


def _deep_update(orig: dict, upd: dict) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(orig.get(k), dict):
            _deep_update(orig[k], v)
        else:
            orig[k] = v


def problemtools_installs() -> list[tuple[str, Path]]:
    found = []
    for p in sorted(glob.glob('/usr/lib/python3*/site-packages/problemtools/config/languages.yaml')):
        found.append(('verifyproblem (system)', Path(p)))
    llm = Path.home() / '.local/bin/problemtools_llm/problemtools/config/languages.yaml'
    if llm.is_file():
        found.append(('verifyproblem_llm', llm))
    return found


def load_languages(default_file: Path) -> dict:
    langs: dict = {}
    for f in (default_file, Path('/etc/kattis/problemtools/languages.yaml'), USER_OVERRIDE):
        if f.is_file():
            _deep_update(langs, yaml.safe_load(f.read_text()) or {})
    return {k: v for k, v in langs.items() if isinstance(v, dict)}


def commands(lang: dict) -> list[str]:
    out = []
    for key in ('compile', 'run'):
        cmd = lang.get(key)
        if not cmd:
            continue
        try:
            first = shlex.split(cmd)[0]
        except ValueError:
            first = cmd.split()[0]
        if '{' in first:  # '{binary}', '"{mainfile}.out"': produced by compilation
            continue
        out.append(first)
    return out


def check() -> tuple[str, bool]:
    lines = []
    all_ok = True
    for label, path in problemtools_installs():
        langs = load_languages(path)
        missing = {}
        for name, lang in sorted(langs.items()):
            for exe in commands(lang):
                if shutil.which(exe) is None:
                    missing.setdefault(exe, []).append(name)
        lines.append(f'{label}: {path}')
        if not missing:
            lines.append('  all language tools found')
        for exe, names in sorted(missing.items()):
            all_ok = False
            lines.append(f'  MISSING {exe:<9} ({", ".join(sorted(set(names)))}) → {ARCH_PACKAGES.get(exe, "?")}')
    lines.append(f'user override: {USER_OVERRIDE} ({"present" if USER_OVERRIDE.is_file() else "absent"})')
    for tool in ('git', 'gh', 'claude', 'problem2html', 'problem2pdf', 'notify-send', 'xdg-open'):
        if shutil.which(tool) is None:
            all_ok = False
            lines.append(f'MISSING tool: {tool}')
    try:
        import textual  # noqa: F401
    except ImportError:
        all_ok = False
        lines.append('MISSING python module: textual → sudo pacman -S python-textual')
    return '\n'.join(lines), all_ok
