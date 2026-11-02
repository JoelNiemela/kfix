import pytest

from kfix import chardiff, commitmsg


def one_line_diff(old: str, new: str, path: str = 'problem_statement/problem.en.tex') -> list:
    diff = (f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -3,1 +3,1 @@\n'
            f'-{old}\n+{new}\n')
    return chardiff.parse_unified(diff)


@pytest.mark.parametrize('old,new,expected', [
    # from the real kattis-problems history
    ('Count the squarefree numbers.', 'Count the square-free numbers.', ('squarefree', 'square-free')),
    ('Pebbles in an arrangment of holes.', 'Pebbles in an arrangement of holes.', ('arrangment', 'arrangement')),
    ('Before submiting your code, ...', 'Before submitting your code, ...', ('submiting', 'submitting')),
    ('He stood infront of the door.', 'He stood in front of the door.', ('infront', 'in front')),
    ('Just add a an apple.', 'Just add an apple.', ('a an', 'an')),
    ('The jaguar lead the way.', 'The jaguar led the way.', ('lead', 'led')),
    ('How they know it?', 'How do they know it?', ('they', 'do they')),
    ('his travel trough Greece.', 'his travel through Greece.', ('trough', 'through')),
])
def test_typo_pairs(old, new, expected):
    assert commitmsg.typo_pair(old, new) == expected


def test_typo_message_format():
    files = one_line_diff('a long line with arrangment in it', 'a long line with arrangement in it')
    assert commitmsg.typo_message(files) == 'Fix typo in problem statement: "arrangment" to "arrangement"'


def test_latex_change_is_not_a_typo():
    files = one_line_diff('written in the format $\\texttt{^k}$ where', 'written in the format \\texttt{\\^{}}$k$ where')
    assert commitmsg.typo_message(files) is None


def test_two_changes_is_not_a_typo():
    files = one_line_diff('teh cat adn dog', 'the cat and dog')
    assert commitmsg.typo_message(files) is None


def test_non_statement_file_is_not_a_typo():
    files = one_line_diff('arrangment', 'arrangement', path='problem.yaml')
    assert commitmsg.typo_message(files) is None


def test_icelandic_statement_counts():
    files = one_line_diff('Þetta er villla hér', 'Þetta er villa hér', path='problem_statement/problem.is.tex')
    assert commitmsg.typo_message(files) == 'Fix typo in problem statement: "villla" to "villa"'


def test_from_claude():
    text = 'I fixed the caret.\n\nCOMMIT: Fix LaTeX rendering error in problem statement\n'
    assert commitmsg.from_claude(text) == 'Fix LaTeX rendering error in problem statement'
    assert commitmsg.from_claude('COMMIT: "Fix x"') == 'Fix x'
    assert commitmsg.from_claude('no commit line') is None


@pytest.mark.parametrize('title,expected', [
    ('diffrun: broken LaTeX', 'Fix broken LaTeX'),
    ('flytibaka: Sample Input/Output headers with empty content', 'Fix sample Input/Output headers with empty content'),
    ('xorsist: Incorrect Quotes and Space Before Comma', 'Fix incorrect Quotes and Space Before Comma'),
    ('failingflagship: figure caption is inserted into statement text.', 'Fix figure caption is inserted into statement text'),
    ('hailstone: TLE on large input', 'Fix TLE on large input'),
    ('foo: Fix the bounds', 'Fix the bounds'),
])
def test_from_issue_title(title, expected):
    assert commitmsg.from_issue_title(title, title.split(':')[0]) == expected


def test_choose_priority():
    files = one_line_diff('x arrangment y', 'x arrangement y')
    assert commitmsg.choose('My message', files, None, None, 'p') == ('My message', 'yours')
    assert commitmsg.choose(None, files, 'COMMIT: Other', None, 'p')[1] == 'auto (typo)'
    latex = one_line_diff('$a^b$', '$a_b$')
    assert commitmsg.choose(None, latex, 'COMMIT: Fix LaTeX', 'p: broken', 'p') == ('Fix LaTeX', 'claude')
    assert commitmsg.choose(None, latex, None, 'p: broken LaTeX', 'p') == ('Fix broken LaTeX', 'issue title')
