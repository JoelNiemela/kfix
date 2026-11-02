from kfix import chardiff
from kfix.chardiff import DEL, ELIDE, EQ, INS, Seg

ROKNET_DIFF = '''diff --git a/problem_statement/problem.en.tex b/problem_statement/problem.en.tex
index 431bd12..5d443c9 100644
--- a/problem_statement/problem.en.tex
+++ b/problem_statement/problem.en.tex
@@ -12,7 +12,7 @@ In this problem we intend to consider five kinds of objects:
 \\begin{itemize}
 \\item \\texttt{OG} has two inputs, if both are true then the output is true, but is false otherwise.
 \\item \\texttt{EDA} has two inputs, if both are false then the output is false, but is otherwise true.
-\\item \\texttt{EKKI} has one input, if the input is true it outputs false and outputs false otherwise.
+\\item \\texttt{EKKI} has one input, if the input is true it outputs false, otherwise it outputs true.
 \\item \\texttt{INNTAK} has no input and one output.
 \\item \\texttt{UTTAK} has one input and no output.
 \\end{itemize}
'''


def text_of(segs, kinds):
    return ''.join(s.text for s in segs if s.kind in kinds)


def test_intraline_single_char_typo():
    segs = chardiff.intraline('before arrangment after', 'before arrangement after')
    assert segs == [Seg('before arrange', EQ), Seg('e', INS), Seg('nt after', EQ)] or \
        [s for s in segs if s.kind != EQ] == [Seg('e', INS)]
    assert text_of(segs, (EQ, DEL)) == 'before arrangment after'
    assert text_of(segs, (EQ, INS)) == 'before arrangement after'


def test_intraline_word_replacement_not_char_soup():
    segs = chardiff.intraline('the output is true', 'the output is false')
    changed = [s for s in segs if s.kind != EQ]
    assert changed == [Seg('true', DEL), Seg('false', INS)]


def test_intraline_roundtrip_long_line():
    old = 'x ' * 250 + 'trough Greece ' + 'y ' * 250
    new = 'x ' * 250 + 'through Greece ' + 'y ' * 250
    segs = chardiff.intraline(old, new)
    assert text_of(segs, (EQ, DEL)) == old
    assert text_of(segs, (EQ, INS)) == new
    assert [s for s in segs if s.kind != EQ] == [Seg('h', INS)]


def test_parse_roknet_pairs_changed_line():
    files = chardiff.parse_unified(ROKNET_DIFF)
    assert len(files) == 1 and files[0].path == 'problem_statement/problem.en.tex'
    lines = files[0].hunks[0].lines
    changes = [ln for ln in lines if ln.kind == 'change']
    assert len(changes) == 1
    ch = changes[0]
    assert ch.old_no == 15 and ch.new_no == 15
    assert text_of(ch.segs, (INS,)).replace(' ', '') in (',otherwiseittrue', ',otherwiseitoutputstrue', ',otherwiseit true'.replace(' ', ''))
    assert sum(1 for ln in lines if ln.kind == 'ctx') == 6
    assert chardiff.stats(files) == {'files': 1, 'changed': 1, 'added': 0, 'removed': 0}


def test_diffrun_latex_edit():
    old = 'written in the format $\\texttt{^k}$ where $k$ is'
    new = 'written in the format \\texttt{\\^{}}$k$ where $k$ is'
    segs = chardiff.intraline(old, new)
    assert text_of(segs, (EQ, DEL)) == old
    assert text_of(segs, (EQ, INS)) == new


def test_unrelated_lines_not_paired():
    diff = '''diff --git a/a.tex b/a.tex
--- a/a.tex
+++ b/a.tex
@@ -1,1 +1,1 @@
-completely different sentence about cats
+0123456789 !!!
'''
    lines = chardiff.parse_unified(diff)[0].hunks[0].lines
    assert [ln.kind for ln in lines] == ['del', 'add']


def test_new_file_meta():
    diff = '''diff --git a/x.txt b/x.txt
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/x.txt
@@ -0,0 +1,2 @@
+hello
+world
'''
    f = chardiff.parse_unified(diff)[0]
    assert f.meta == ['new file mode 100644']
    assert [ln.kind for ln in f.hunks[0].lines] == ['add', 'add']


def test_focus_trims_long_context():
    segs = [Seg('a' * 200, EQ), Seg('X', DEL), Seg('Y', INS), Seg('b' * 300, EQ), Seg('Z', INS), Seg('c' * 200, EQ)]
    out = chardiff.focus(segs, context=10)
    assert out[0] == Seg('…', ELIDE) and out[1] == Seg('a' * 10, EQ)
    assert Seg(' … ', ELIDE) in out
    assert out[-1] == Seg('…', ELIDE)
    assert sum(len(s.text) for s in out if s.kind == EQ) == 10 + 20 + 10


def test_visible_whitespace():
    assert chardiff.visible_ws('a b\tc') == 'a·b→c'
