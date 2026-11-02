import pytest

from kfix import github, verify

# verifyproblem_llm output for flytibaka from a real run (table glued to the summary line).
FLYTIBAKA_LLM = '''Loading problem flytibaka
Checking package structure: 21 files, 7 directories (1 ignored)
Checking config
Checking problem statements in 2 languages
Checking 1 input validator
WARNING No validator rejects an empty file with flags ""
Checking output validator
WARNING an empty file gets AC
Checking 1 test case in 1 test data group
WARNING No sample data provided
Checking 12 submissions
ERROR Compile error for accepted/arnar_100.cs (C#) (C#: could not find compiler "mcs" -- is it installed and on PATH?)
ERROR Compile error for accepted/arnar_100.js (JavaScript) (JavaScript: could not find compiler "nodejs" -- is it installed and on PATH?)
   Slowest AC runtime: 0.097, setting timelim to 1 secs, safety margin to 2 secs
     flytibaka @ 1s time limit
╭────────────────┬────────┬───────╮
│ Submission     │ secret │ Score │
├────────────────┼────────┼───────┤
│ arnar_100.c    │ 0.00s  │   100 │
├────────────────┼────────┼───────┤
│ chatgpt_0.py   │   WA   │     0 │
╰────────────────┴────────┴───────╯flytibaka tested: 2 errors, 3 warnings
'''

MULTILINE_ERROR = '''Checking 3 submissions
ERROR partially_accepted/vegan_0.py (Python 3 (w/PyPy3)) got WA, expected AC:
        === diffposition.txt: ===
        0 0
        === judgemessage.txt: ===
        Wrong answer on line 1 of output
flytibaka tested: 1 errors, 0 warnings
'''


def test_parse_llm_output():
    r = verify.parse_output('verifyproblem_llm', FLYTIBAKA_LLM, 1)
    assert (r.n_errors, r.n_warnings) == (2, 3)
    assert len(r.errors) == 2 and len(r.warnings) == 3
    assert r.missing_toolchains == ['mcs', 'nodejs']
    assert not r.passed
    assert 'chatgpt_0.py' in r.table and r.table.rstrip().endswith('╯')
    assert 'flytibaka @ 1s time limit' not in r.errors[-1]


def test_multiline_error_is_one_error():
    r = verify.parse_output('v', MULTILINE_ERROR, 1)
    assert len(r.errors) == 1
    assert 'Wrong answer on line 1' in r.errors[0]


def test_passed_requires_zero_errors_and_exit_zero():
    ok = verify.parse_output('v', 'x tested: 0 errors, 2 warnings\n', 0)
    assert ok.passed
    assert not verify.parse_output('v', 'x tested: 0 errors, 0 warnings\n', 1).passed
    assert not verify.parse_output('v', 'crashed\n', 0).passed  # no summary line


def test_ansi_is_stripped():
    r = verify.parse_output('v', '\x1b[31mERROR\x1b[0m boom\nx tested: 1 errors, 0 warnings\n', 1)
    assert r.errors == ['ERROR boom']


def test_normalize_error():
    a = verify.normalize_error('ERROR in /home/joel/problems/foo/data: took 1.23s', '/home/joel/problems/foo')
    b = verify.normalize_error('ERROR in /tmp/kfix/foo/data: took 0.98s', '/tmp/kfix/foo')
    assert a == b


def test_roundtrip_dict():
    r = verify.parse_output('v', FLYTIBAKA_LLM, 1)
    assert verify.VerifyResult.from_dict(r.to_dict()) == r


@pytest.mark.parametrize('arg,expected', [
    ('349', ('issue', 349, 'kattis-problems/issue-tracker')),
    ('#355', ('issue', 355, 'kattis-problems/issue-tracker')),
    ('https://github.com/Matistjati/kattis-problem-issues/issues/41', ('issue', 41, 'Matistjati/kattis-problem-issues')),
    ('roknet', ('problem', 'roknet', None)),
])
def test_parse_target(arg, expected):
    assert github.parse_target(arg, 'kattis-problems/issue-tracker') == expected


def test_parse_target_rejects_garbage():
    with pytest.raises(ValueError):
        github.parse_target('Not A Problem!', 'r')


@pytest.mark.parametrize('title,pid', [
    ('diffrun: broken LaTeX', 'diffrun'),
    ('`xorsist`: quotes', 'xorsist'),
    ('Scoring problems that are actually pass/fail problems', None),
    ('Latex problem on chopin', None),
])
def test_problem_id_from_title(title, pid):
    assert github.problem_id_from_title(title) == pid


def test_parse_add_input():
    assert github.parse_add_input('355 349 xorsist') == [('355', None), ('349', None), ('xorsist', None)]
    assert github.parse_add_input('349 diffrun') == [('349', 'diffrun')]
    assert github.parse_add_input('diffrun roknet') == [('diffrun', None), ('roknet', None)]
