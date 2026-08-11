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


if __name__ == "__main__":
    unittest.main()
