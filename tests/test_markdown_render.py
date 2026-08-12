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


class FenceTest(unittest.TestCase):
    """코드블록이 본문과 구분되어야 '여기부터 실행할 명령'이 보인다."""

    def test_a_fence_gets_a_box_with_the_language_label(self):
        got = mr.render("```sql\nSELECT 1;\n```\n", width=40, color=False)
        self.assertIn("┌─ sql", got)
        self.assertIn("│ SELECT 1;", got)
        self.assertIn("└", got)

    def test_a_fence_without_a_language_still_gets_a_box(self):
        got = mr.render("```\nplain\n```\n", width=40, color=False)
        self.assertIn("┌", got)
        self.assertIn("│ plain", got)

    def test_markup_inside_a_fence_is_not_interpreted(self):
        """SQL 주석 `--`, bash 의 `## `, C 의 `**ptr` 이 펜스 안에 있다."""
        src = "```bash\n## 주석\n**ptr\n| a | b |\n```\n"
        got = mr.render(src, width=40, color=False)
        self.assertIn("│ ## 주석", got)
        self.assertIn("│ **ptr", got)
        self.assertIn("│ | a | b |", got)
        self.assertNotIn("◆", got)

    def test_a_long_code_line_is_split_not_lost(self):
        src = "```sql\n" + "SELECT " + "x" * 80 + ";\n```\n"
        got = mr.render(src, width=40, color=False)
        for line in got.split("\n"):
            self.assertLessEqual(cwidth(line), 40, repr(line))
        self.assertIn("x" * 20, got)

    def test_an_unclosed_fence_is_closed_instead_of_crashing(self):
        """'전체' 보기는 filter_lines 를 안 거치므로 여기까지 올 수 있다."""
        got = mr.render("```sql\nSELECT 1;\n", width=40, color=False)
        self.assertIn("│ SELECT 1;", got)
        self.assertIn("└", got)

    def test_the_box_never_exceeds_the_width(self):
        got = mr.render("```sql\nSELECT 1;\n```\n", width=30, color=False)
        for line in got.split("\n"):
            self.assertLessEqual(cwidth(line), 30, repr(line))

    def test_long_ascii_language_tag_does_not_overflow(self):
        """긴 ASCII 언어 태그(34자)가 width=20을 넘지 않는다."""
        src = "```verylonglanguagename1234567890\ncode\n```\n"
        got = mr.render(src, width=20, color=False)
        for line in got.split("\n"):
            self.assertLessEqual(cwidth(line), 20, repr(line))

    def test_long_korean_language_tag_does_not_overflow(self):
        """긴 한글 언어 태그(16자=폭32)가 width=20을 넘지 않는다."""
        src = "```가나다라마바사아자차카타파하\ncode\n```\n"
        got = mr.render(src, width=20, color=False)
        for line in got.split("\n"):
            self.assertLessEqual(cwidth(line), 20, repr(line))


class ListTest(unittest.TestCase):
    """불릿 523개, 번호 155개, 체크박스 239개 — 챕터 본문의 큰 축이다."""

    def test_a_bullet_becomes_a_dot(self):
        got = mr.render("- 첫 항목\n", width=40, color=False)
        self.assertIn("• 첫 항목", got)

    def test_an_asterisk_bullet_counts_too(self):
        self.assertIn("• 항목", mr.render("* 항목\n", width=40, color=False))

    def test_a_nested_bullet_gets_a_hollow_dot(self):
        """이 저장소는 중첩 들여쓰기를 2·3·4칸으로 섞어 쓴다(실측) — 셋 다
        같은 깊이로 읽혀야 목록이 들쭉날쭉해지지 않는다."""
        for pad in ("  ", "   ", "    "):
            got = mr.render(f"- 위\n{pad}- 아래\n", width=40, color=False)
            self.assertIn("◦ 아래", got, repr(pad))

    def test_a_deeply_nested_bullet_gets_a_dash(self):
        got = mr.render("- 위\n      - 아주 아래\n", width=40, color=False)
        self.assertIn("- 아주 아래", got)

    def test_an_ordered_item_keeps_its_number(self):
        got = mr.render("1. 첫째\n2. 둘째\n", width=40, color=False)
        self.assertIn("1. 첫째", got)
        self.assertIn("2. 둘째", got)

    def test_an_unchecked_box_becomes_an_empty_box(self):
        got = mr.render("- [ ] 할 수 있다\n", width=40, color=False)
        self.assertIn("☐ 할 수 있다", got)
        self.assertNotIn("[ ]", got)

    def test_a_checked_box_becomes_a_ticked_box(self):
        self.assertIn("☑ 했다", mr.render("- [x] 했다\n", width=40,
                                          color=False))

    def test_a_wrapped_item_hangs_under_its_text(self):
        got = mr.render("- " + "가나다 " * 15 + "\n", width=24, color=False)
        rows = [r for r in got.split("\n") if r.strip()]
        self.assertGreater(len(rows), 1)
        self.assertTrue(rows[1].startswith("  "), repr(rows[1]))
        for r in rows:
            self.assertLessEqual(cwidth(r), 24, repr(r))


class QuoteTest(unittest.TestCase):
    def test_a_quote_gets_a_left_bar(self):
        got = mr.render("> 주의할 점\n", width=40, color=False)
        self.assertIn("│ 주의할 점", got)
        self.assertNotIn(">", got)

    def test_a_long_quote_wraps_under_the_bar(self):
        got = mr.render("> " + "가나다 " * 15 + "\n", width=24, color=False)
        for r in got.split("\n"):
            self.assertLessEqual(cwidth(r), 24, repr(r))
            if r.strip():
                self.assertTrue(r.startswith("│ "), repr(r))


class BulletWidthPropertyTest(unittest.TestCase):
    """들여쓰기와 폭 조합에서 모든 줄이 width 를 넘지 않는지 확인."""

    def test_deeply_indented_bullets_fit_width(self):
        """들여쓰기 0~24 x 폭 20/24/40 을 순회하며 모든 줄이 폭을 넘지 않음을 확인."""
        for indent_count in range(25):
            indent = " " * indent_count
            for width in (20, 24, 40):
                text = f"{indent}- " + "가나다 " * 10 + "\n"
                got = mr.render(text, width=width, color=False)
                for line in got.split("\n"):
                    if line.strip():  # 빈 줄 제외
                        self.assertLessEqual(cwidth(line), width,
                                           f"indent={indent_count}, width={width}: {repr(line)}")

    def test_deeply_indented_quotes_fit_width(self):
        """들여쓰기 0~24 x 폭 20/24/40 을 순회하며 모든 줄이 폭을 넘지 않음을 확인."""
        for indent_count in range(25):
            indent = " " * indent_count
            for width in (20, 24, 40):
                text = f"{indent}> " + "가나다 " * 10 + "\n"
                got = mr.render(text, width=width, color=False)
                for line in got.split("\n"):
                    if line.strip():  # 빈 줄 제외
                        self.assertLessEqual(cwidth(line), width,
                                           f"indent={indent_count}, width={width}: {repr(line)}")


class TableTest(unittest.TestCase):
    """표 324줄. 한글이 섞여 소스로는 열이 맞지 않고, 치트시트는 폭을 넘는다."""

    SIMPLE = ("| 용어 | 설명 |\n"
              "|------|------|\n"
              "| 테이블 | 데이터를 담는 표 |\n"
              "| 행 | 표의 한 줄 |\n")

    def _rows(self, rendered):
        return [r for r in rendered.split("\n") if r.strip()]

    def test_columns_line_up_on_display_width(self):
        """`len` 기준이면 실패하도록 한글·영문 길이가 엇갈리는 표를 쓴다.

        **검증도 폭으로 해야 한다.** `"가나다"`는 3자·폭 6이고 `"ab"`는
        2자·폭 4+채움 2라, 화면에서 열이 맞아도 `str.index()` 는 4와 5로
        갈린다 — 문자 인덱스로 비교하면 정렬 성공을 실패로 신고한다.
        """
        src = ("| a | b |\n|---|---|\n"
               "| 가나다 | x |\n| ab | y |\n")
        rows = self._rows(mr.render(src, width=60, color=False))
        starts = set()
        for r in rows:
            for ch in ("x", "y"):
                if ch in r:
                    starts.add(cwidth(r[:r.index(ch)]))
        self.assertEqual(len(starts), 1, rows)

    def test_the_pipes_are_gone(self):
        got = mr.render(self.SIMPLE, width=60, color=False)
        self.assertNotIn("|", got)

    def test_a_separator_row_becomes_a_rule(self):
        rows = self._rows(mr.render(self.SIMPLE, width=60, color=False))
        self.assertIn("─", rows[1])
        self.assertNotIn("-", rows[1].replace("─", ""))

    def test_a_wide_table_wraps_inside_its_cells(self):
        src = ("| 구분 | 명령 |\n|---|---|\n"
               "| 권한 부여 | GRANT SELECT ON schema.table TO someuser; |\n")
        got = mr.render(src, width=34, color=False)
        for r in got.split("\n"):
            self.assertLessEqual(cwidth(r), 34, repr(r))
        self.assertIn("GRANT", got)
        self.assertIn("someuser", got)

    def test_a_wrapped_cell_keeps_its_column_start(self):
        src = ("| 구분 | 명령 |\n|---|---|\n"
               "| 권한 | AAAA BBBB CCCC DDDD EEEE |\n")
        rows = self._rows(mr.render(src, width=28, color=False))
        # 여기도 폭으로 잰다 — 첫 줄의 `권한` 은 2자·폭 4다.
        first = cwidth(rows[2][:rows[2].index("AAAA")])
        cont = [r for r in rows[3:] if "CCCC" in r or "DDDD" in r or "EEEE" in r]
        self.assertTrue(cont, rows)
        for r in cont:
            self.assertEqual(cwidth(r) - cwidth(r.lstrip()), first, repr(r))

    def test_inline_markup_inside_a_cell_is_rendered(self):
        src = "| a |\n|---|\n| `SELECT` |\n"
        got = mr.render(src, width=40, color=True)
        self.assertIn("SELECT", plain(got))
        self.assertNotIn("`", plain(got))

    def test_right_alignment_pads_before_the_value(self):
        """열이 셀보다 넓어야 채움이 보인다 — 셀과 같은 폭이면 채움이 0이라
        `_alignments()`를 지워도(모두 `"left"`) 이 표는 똑같이 나온다."""
        src = "| number | middle | e |\n|---:|:---:|---|\n| 1 | 2 | z |\n"
        rows = self._rows(mr.render(src, width=40, color=False))
        data = rows[2]
        gap = cwidth("number") - cwidth("1")
        lead = cwidth(data[:data.index("1")])
        self.assertEqual(lead, gap, repr(data))

    def test_centre_alignment_pads_both_sides(self):
        """가운데 정렬은 좌우로 나눠 채운다 — 오른쪽 정렬과 달리 앞쪽에만
        차이가 아니라 앞뒤 모두 절반씩 채워야 `right`와 구별된다."""
        src = "| number | middle | e |\n|---:|:---:|---|\n| 1 | 2 | z |\n"
        rows = self._rows(mr.render(src, width=40, color=False))
        data = rows[2]
        gap = cwidth("middle") - cwidth("2")
        before = cwidth(data[data.index("1") + 1:data.index("2")]) - 1  # 열 사이 구분 공백 1칸 제외
        after = cwidth(data[data.index("2") + 1:data.index("z")]) - 1
        self.assertEqual(before, gap // 2, repr(data))
        self.assertEqual(after, gap - gap // 2, repr(data))

    def test_a_wrapped_cell_pads_each_continuation_line_by_its_alignment(self):
        """가운데 정렬 셀이 접히면 접힌 줄마다 **따로** 채운다 — 첫 줄은 왼쪽
        열의 내용까지 포함해 채워지고, 이어지는 줄은 왼쪽 열이 비어 있어
        채움 폭이 달라진다. 현재 동작을 그대로 고정한다."""
        src = "| n | label |\n|---:|:---:|\n| 1 | AAAA BBBB CCCC DDDD |\n"
        rows = self._rows(mr.render(src, width=20, color=False))
        first, cont = rows[2], rows[3]
        self.assertIn("AAAA BBBB CCCC", first)
        self.assertIn("DDDD", cont)
        first_lead = cwidth(first[:first.index("AAAA")])
        cont_lead = cwidth(cont[:cont.index("DDDD")])
        self.assertEqual(first_lead, 4, repr(first))
        self.assertEqual(cont_lead, 9, repr(cont))
        self.assertNotEqual(first_lead, cont_lead)

    def test_a_lone_pipe_line_is_not_a_table(self):
        """구분선이 뒤따르지 않으면 표가 아니다 — 문단으로 낸다."""
        got = mr.render("| 그냥 파이프 문장\n", width=40, color=False)
        self.assertIn("|", got)

    def test_a_comment_between_rows_does_not_break_the_table(self):
        """dbms 마커가 행 사이에 끼면(부록 marking 작업이 그 경로다) 표가
        중간에 끊겨 남은 행이 문단으로 떨어지면 안 된다."""
        src = ("| 구분 | 명령 |\n|---|---|\n"
               "| 공통 | SELECT 1 |\n"
               "<!-- dbms:mysql -->\n"
               "| MySQL | SHOW DATABASES |\n"
               "<!-- /dbms:mysql -->\n"
               "| 끝 | END |\n")
        got = mr.render(src, width=60, color=False)
        self.assertNotIn("|", got)
        self.assertIn("SHOW DATABASES", got)
        self.assertIn("END", got)


class RealChapterTest(unittest.TestCase):
    """가장 넓은 표를 가진 실제 파일이 폭 안에 들어와야 한다.

    합성 픽스처만으로는 놓친다 — 치트시트는 4열에 SQL 이 들어가고 부록
    비교표는 클라우드 3사까지 붙는다.
    """

    WIDEST = ("01-beginner/07-commands-cheatsheet.md",
              "02-intermediate/09-commands-cheatsheet.md",
              "appendix/dbms-comparison-matrix.md")

    def test_no_rendered_line_exceeds_the_width(self):
        for rel in self.WIDEST:
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for width in (60, 80, 100):
                got = mr.render(src, width=width, color=False)
                for line in got.split("\n"):
                    self.assertLessEqual(cwidth(line), width,
                                         f"{rel} @ {width}: {line!r}")

    def test_a_real_chapter_renders_without_escapes_when_plain(self):
        src = (REPO_ROOT / "01-beginner/01-rdbms-fundamentals.md").read_text(
            encoding="utf-8")
        self.assertNotIn("\x1b", mr.render(src, width=80, color=False))


if __name__ == "__main__":
    unittest.main()
