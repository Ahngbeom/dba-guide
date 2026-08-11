#!/usr/bin/env python3
"""markdown_render.py(챕터 마크다운 렌더러) 단위 테스트.

렌더러는 I/O가 없는 순수 모듈이라 tty·파일 없이 전부 검증할 수 있다.

실행:
    python3 -m unittest discover -s tests
"""
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import markdown_render as mr  # noqa: E402
from tui import cwidth  # noqa: E402

ESC = re.compile(r"\x1b\[[0-9;]*m")


def plain(text):
    """ANSI를 걷어낸 글자만."""
    return ESC.sub("", text)


class InlineSpansTest(unittest.TestCase):
    """인라인 마크업 파싱. 색이 있으면 기호를 떼고, 없으면 남긴다."""

    def test_plain_text_is_one_unstyled_span(self):
        self.assertEqual(mr.inline_spans("보통 문장"), [("보통 문장", None)])

    def test_inline_code_drops_backticks_when_coloured(self):
        self.assertEqual(mr.inline_spans("`SELECT` 문"),
                         [("SELECT", "code"), (" 문", None)])

    def test_inline_code_keeps_backticks_without_colour(self):
        """색이 없으면 구분 수단이 사라진다 — 기호를 지우면 정보가 준다."""
        self.assertEqual(mr.inline_spans("`SELECT` 문", color=False),
                         [("`SELECT`", "code"), (" 문", None)])

    def test_bold_drops_asterisks_when_coloured(self):
        self.assertEqual(mr.inline_spans("**중요** 함"),
                         [("중요", "bold"), (" 함", None)])

    def test_bold_keeps_asterisks_without_colour(self):
        self.assertEqual(mr.inline_spans("**중요** 함", color=False),
                         [("**중요**", "bold"), (" 함", None)])

    def test_code_wins_over_bold_inside_it(self):
        """백틱 안은 코드다 — `**`가 강조로 해석되면 안 된다."""
        self.assertEqual(mr.inline_spans("`a ** b`"), [("a ** b", "code")])

    def test_a_link_becomes_text_plus_dim_url(self):
        self.assertEqual(mr.inline_spans("[개요](00-overview.md) 참고"),
                         [("개요", "link"), (" (00-overview.md)", "dim"),
                          (" 참고", None)])

    def test_a_link_is_left_alone_without_colour(self):
        self.assertEqual(mr.inline_spans("[개요](00-overview.md)", color=False),
                         [("[개요](00-overview.md)", None)])


class LayoutTest(unittest.TestCase):
    """표시 폭 기준 줄바꿈. ANSI가 붙기 **전에** 잰다."""

    def _widths(self, lines):
        return [sum(cwidth(t) for t, _ in line) for line in lines]

    def test_short_text_stays_on_one_line(self):
        lines = mr.layout([("가 나 다", None)], 20)
        self.assertEqual(len(lines), 1)

    def test_it_wraps_on_display_width_not_character_count(self):
        # 한글 6자 = 폭 12. 폭 8이면 두 줄이어야 한다.
        lines = mr.layout([("가나 다라 마바", None)], 8)
        self.assertGreater(len(lines), 1)
        for w in self._widths(lines):
            self.assertLessEqual(w, 8)

    def test_a_style_boundary_does_not_split_a_word(self):
        """`employees`를 처럼 코드와 조사가 붙어 있으면 한 단어다."""
        spans = [("employees", "code"), ("를", None), (" 조회", None)]
        lines = mr.layout(spans, 40)
        self.assertEqual(len(lines), 1)
        joined = "".join(t for t, _ in lines[0])
        self.assertIn("employees를", joined)

    def test_an_overlong_word_is_force_split(self):
        lines = mr.layout([("x" * 30, None)], 10)
        self.assertGreater(len(lines), 1)
        for w in self._widths(lines):
            self.assertLessEqual(w, 10)

    def test_empty_input_yields_one_empty_line(self):
        self.assertEqual(mr.layout([], 20), [[]])


class PaintTest(unittest.TestCase):
    def test_colour_off_emits_no_escapes(self):
        got = mr.paint([("가", "bold"), ("나", "code")], color=False)
        self.assertEqual(got, "가나")
        self.assertNotIn("\x1b", got)

    def test_colour_on_wraps_each_styled_span(self):
        got = mr.paint([("가", "bold")], color=True)
        self.assertEqual(got, "\x1b[1m가\x1b[0m")
        self.assertEqual(plain(got), "가")

    def test_unstyled_spans_are_left_bare(self):
        self.assertEqual(mr.paint([("가", None)], color=True), "가")


class HeadingTest(unittest.TestCase):
    """제목 3단계가 서로 다르게 보여야 챕터의 4단 구조가 스캔된다."""

    def test_h1_is_wrapped_in_bars(self):
        got = mr.render("# 관계형 데이터베이스 기초\n", width=60, color=False)
        self.assertIn("━━━ 관계형 데이터베이스 기초 ━━━", got)

    def test_h2_gets_a_diamond(self):
        got = mr.render("## 1. 핵심 개념 설명\n", width=60, color=False)
        self.assertIn("◆ 1. 핵심 개념 설명", got)

    def test_h3_and_deeper_get_a_dot(self):
        got = mr.render("### ACID\n#### 원자성\n", width=60, color=False)
        self.assertIn("· ACID", got)
        self.assertIn("· 원자성", got)

    def test_heading_markup_is_stripped_when_coloured(self):
        got = mr.render("## `SELECT` 문\n", width=60, color=True)
        self.assertIn("SELECT", plain(got))
        self.assertNotIn("`", plain(got))

    def test_a_heading_is_preceded_by_a_blank_line(self):
        lines = mr.render("문단\n## 제목\n", width=60, color=False).split("\n")
        self.assertEqual(lines[1], "", lines)


class ParagraphTest(unittest.TestCase):
    def test_a_long_paragraph_wraps_to_width(self):
        text = "가나다 " * 20 + "\n"
        got = mr.render(text, width=30, color=False)
        for line in got.split("\n"):
            self.assertLessEqual(cwidth(line), 30, repr(line))

    def test_blank_lines_survive(self):
        got = mr.render("첫 문단\n\n둘째 문단\n", width=60, color=False)
        self.assertIn("첫 문단\n\n둘째 문단", got)

    def test_output_always_ends_with_a_newline(self):
        self.assertTrue(mr.render("문단", width=60, color=False).endswith("\n"))


class RuleAndCommentTest(unittest.TestCase):
    def test_a_horizontal_rule_spans_the_width(self):
        got = mr.render("---\n", width=20, color=False)
        self.assertIn("─" * 20, got)

    def test_asterisk_rules_count_too(self):
        self.assertIn("─" * 20, mr.render("***\n", width=20, color=False))

    def test_a_dbms_marker_comment_is_hidden(self):
        """'전체' 보기는 filter_lines 를 거치지 않아 마커가 본문에 남는다."""
        got = mr.render("<!-- dbms:mysql -->\n본문\n<!-- /dbms:mysql -->\n",
                        width=60, color=False)
        self.assertNotIn("dbms:", got)
        self.assertIn("본문", got)

    def test_a_bullet_is_not_mistaken_for_a_rule(self):
        got = mr.render("- 항목\n", width=60, color=False)
        self.assertNotIn("─" * 10, got)


class NoEscapesWhenPlainTest(unittest.TestCase):
    """`color=False` 계약: 출력에 `\\x1b` 가 하나도 없다."""

    SAMPLE = ("# 제목\n\n## 절\n\n본문 **강조** 와 `코드`.\n\n---\n"
              "<!-- dbms:mysql -->\n")

    def test_no_escape_anywhere(self):
        got = mr.render(self.SAMPLE, width=50, color=False)
        self.assertNotIn("\x1b", got)

    def test_colour_on_does_emit_escapes(self):
        got = mr.render(self.SAMPLE, width=50, color=True)
        self.assertIn("\x1b", got)


if __name__ == "__main__":
    unittest.main()
