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

    def test_a_long_heading_wraps_instead_of_losing_its_tail(self):
        """제목을 자르면 꼬리가 조용히 사라진다 — 렌더러의 유일한 데이터 손실이었다."""
        body = '시나리오: "프로덕션 DB CPU 100%, 애플리케이션 전면 5xx" (PostgreSQL 기준)'
        got = mr.render(f"### {body}\n", width=40, color=False)
        for word in ("시나리오:", "5xx", "(PostgreSQL", "기준)"):
            self.assertIn(word, got, got)
        for line in got.split("\n"):
            self.assertLessEqual(cwidth(line), 40, repr(line))

    def test_a_wrapped_h1_keeps_its_closing_bars(self):
        """`━━━ … ━━━` 는 한 줄 안의 장식이다. 접히면 닫는 막대가 마지막 줄로 간다."""
        got = mr.render("# " + "가나다라마 " * 8 + "\n", width=40, color=False)
        lines = [ln for ln in got.split("\n") if ln.strip()]
        self.assertGreater(len(lines), 1, got)
        self.assertTrue(lines[0].startswith("━━━"), lines)
        self.assertTrue(lines[-1].endswith("━━━"), lines)

    def test_every_wrapped_heading_line_carries_the_heading_style(self):
        """접힌 제목이 본문과 구분되는 유일한 수단이다 — 줄마다 스타일이 실려야 한다."""
        got = mr.render("## " + "가나다라마 " * 8 + "\n", width=40, color=True)
        lines = [ln for ln in got.split("\n") if ln.strip()]
        self.assertGreater(len(lines), 1, got)
        for line in lines:
            self.assertIn(f"\x1b[{mr.SGR['h2']}m", line, repr(line))


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
    def test_a_short_width_rule_still_fits_that_width(self):
        """폭이 _RULE_WIDTH 이하면 폭만큼 그린다 — 여기서는 동작이 안 바뀐다."""
        got = mr.render("---\n", width=20, color=False)
        self.assertIn("─" * 20, got)
        self.assertNotIn("─" * 21, got)

    def test_asterisk_rules_count_too(self):
        self.assertIn("─" * 20, mr.render("***\n", width=20, color=False))

    def test_a_wide_rule_stops_at_the_fixed_length(self):
        """폭을 채우면 터미널이 좁아지는 순간 `less` 가 접어 두 줄이 된다.

        24칸은 가장 좁은 지원 폭(`tui.text_width()` 하한 40)에서도 안 접히고
        구분선 구실을 하기에 충분하다.
        """
        for width in (40, 78, 120):
            got = mr.render("---\n", width=width, color=False)
            rule = [ln for ln in got.split("\n") if "─" in ln]
            self.assertEqual(len(rule), 1, got)
            self.assertEqual(cwidth(rule[0]), mr._RULE_WIDTH,
                             f"width={width}: {rule[0]!r}")

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
    """코드블록이 본문과 구분되어야 '여기부터 실행할 명령'이 보인다.

    테두리 상자를 쓰지 않는다 — `│ ` 거터는 `less` 가 접는 순간 이어지는 줄에
    다시 찍히지 않아 무너지고, 그걸 피하려던 폭 단위 강제 개행은 원문 코드를
    토큰 중간에서 끊어 복사를 막았다.
    """

    def test_a_fence_gets_an_indented_body_under_a_language_label(self):
        got = mr.render("```sql\nSELECT 1;\n```\n", width=40, color=False)
        self.assertIn("sql", got)
        self.assertIn(mr._FENCE_INDENT + "SELECT 1;", got)

    def test_a_fence_draws_no_box(self):
        got = mr.render("```sql\nSELECT 1;\n```\n", width=40, color=False)
        for glyph in ("┌", "└", "│"):
            self.assertNotIn(glyph, got, got)

    def test_a_fence_without_a_language_gets_no_label_line(self):
        """뜻 없는 라벨 줄을 지어내지 않는다. 들여쓰기가 구분을 진다."""
        got = mr.render("```\nplain\n```\n", width=40, color=False)
        self.assertIn(mr._FENCE_INDENT + "plain", got)
        lines = [ln for ln in got.split("\n") if ln.strip()]
        self.assertEqual(lines, [mr._FENCE_INDENT + "plain"], got)

    def test_markup_inside_a_fence_is_not_interpreted(self):
        """SQL 주석 `--`, bash 의 `## `, C 의 `**ptr` 이 펜스 안에 있다."""
        src = "```bash\n## 주석\n**ptr\n| a | b |\n```\n"
        got = mr.render(src, width=40, color=False)
        self.assertIn(mr._FENCE_INDENT + "## 주석", got)
        self.assertIn(mr._FENCE_INDENT + "**ptr", got)
        self.assertIn(mr._FENCE_INDENT + "| a | b |", got)
        self.assertNotIn("◆", got)

    def test_a_long_code_line_is_kept_whole(self):
        """예전에는 폭 단위로 끊었다 — 실측 40칸에서 코드 줄의 49%가 훼손됐다."""
        code = "SELECT " + "x" * 80 + ";"
        got = mr.render(f"```sql\n{code}\n```\n", width=40, color=False)
        self.assertIn(mr._FENCE_INDENT + code, got)

    def test_a_blank_line_inside_a_fence_stays_blank(self):
        """들여쓰기만 남은 줄을 만들지 않는다 — 뒤에 공백이 붙으면 복사가 지저분해진다."""
        got = mr.render("```sql\nA\n\nB\n```\n", width=40, color=False)
        self.assertIn(f"{mr._FENCE_INDENT}A\n\n{mr._FENCE_INDENT}B", got)

    def test_an_unclosed_fence_is_closed_instead_of_crashing(self):
        """'전체' 보기는 filter_lines 를 안 거치므로 여기까지 올 수 있다."""
        got = mr.render("```sql\nSELECT 1;\n", width=40, color=False)
        self.assertIn(mr._FENCE_INDENT + "SELECT 1;", got)

    def test_only_the_code_body_may_exceed_the_width(self):
        """라벨 줄은 폭 안에 유지된다. 넘어도 되는 것은 코드 본문뿐이다."""
        src = "```verylonglanguagename1234567890\ncode\n```\n"
        got = mr.render(src, width=20, color=False)
        for line in got.split("\n"):
            if line.strip() == "code":
                continue
            self.assertLessEqual(cwidth(line), 20, repr(line))

    def test_a_long_korean_language_tag_does_not_overflow(self):
        """긴 한글 언어 태그(16자=폭32)가 width=20을 넘지 않는다."""
        src = "```가나다라마바사아자차카타파하\ncode\n```\n"
        got = mr.render(src, width=20, color=False)
        for line in got.split("\n"):
            if line.strip() == "code":
                continue
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


class TableCardTest(unittest.TestCase):
    """자연폭이 화면에 안 들어가면 카드로 편다.

    실측: 표 44개의 자연폭 중앙값이 130칸이라 기본 80칸에서도 36개가 압축됐다.
    압축은 `PostgreSQL` 을 `PostgreSQ`/`L` 로 쪼개고, 채움으로 만든 정렬은
    `less` 가 접는 순간 어차피 무너진다.
    """

    WIDE = ("| 항목 | PostgreSQL | MySQL |\n"
            "|---|---|---|\n"
            "| 기본 포트 | 5432 | 3306 |\n"
            "| 클라이언트 | psql | mysql |\n")

    def test_a_table_that_fits_is_still_aligned(self):
        got = mr.render(self.WIDE, width=60, color=False)
        self.assertNotIn(mr._CARD_MARK.strip(), got)
        self.assertIn("─", got)

    def test_a_table_that_does_not_fit_becomes_cards(self):
        got = mr.render(self.WIDE, width=24, color=False)
        self.assertIn(f"{mr._CARD_MARK}기본 포트", got)
        self.assertIn("  PostgreSQL: 5432", got)
        self.assertIn("  MySQL: 3306", got)

    def test_cards_use_a_marker_the_headings_do_not(self):
        """`◆`(h2)·`·`(h3) 를 재사용하면 표 카드가 챕터 제목으로 읽힌다."""
        self.assertNotIn(mr._CARD_MARK.strip(), ("◆", "·"))

    def test_cards_still_fit_the_width(self):
        """카드 값은 접는다 — 폭을 넘어도 되는 것은 코드 펜스뿐이다."""
        src = ("| 구분 | 명령 |\n|---|---|\n"
               "| 권한 부여 | GRANT SELECT ON schema.table TO someuser; |\n")
        got = mr.render(src, width=28, color=False)
        for line in got.split("\n"):
            self.assertLessEqual(cwidth(line), 28, repr(line))
        self.assertIn("GRANT", got)
        self.assertIn("someuser", got)

    def test_an_empty_cell_makes_no_line(self):
        """`MySQL: ` 처럼 값 없는 줄을 만들지 않는다."""
        src = ("| 항목 | PostgreSQL | MySQL |\n|---|---|---|\n"
               "| 확장 | CREATE EXTENSION 으로 붙이는 아주 긴 설명 문장 |  |\n")
        got = mr.render(src, width=24, color=False)
        self.assertIn("PostgreSQL:", got)
        self.assertNotIn("MySQL:", got)

    def test_the_pipes_are_gone_in_card_mode_too(self):
        self.assertNotIn("|", mr.render(self.WIDE, width=24, color=False))

    def test_compression_is_gone(self):
        """압축이 사라지면 압축 전용 코드도 사라져야 한다."""
        self.assertFalse(hasattr(mr, "_fit_columns"))
        self.assertFalse(hasattr(mr, "_MIN_COL"))


def fence_bodies(src):
    """소스의 코드 펜스 본문이 **렌더되면 나와야 하는 모습** 의 집합.

    폭 보장의 **유일한** 예외를 골라내는 데 쓴다. 코드는 자르지도 접지도 않고
    내보내므로 폭을 넘을 수 있고, 접는 일은 `less` 가 한다.

    `strip()` 이 아니라 렌더 형태 그대로 담는 것이 중요하다 — 양쪽을 벗겨서
    비교하면 원문의 **들여쓰기가 사라져도** 테스트가 통과한다. `CREATE TABLE`
    본문처럼 들여쓴 코드가 챕터에 실제로 있다.
    """
    out, in_fence = set(), False
    for line in src.split("\n"):
        if mr._FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence and line.strip():
            out.add(mr._FENCE_INDENT + line.rstrip())
    return out


class RealChapterTest(unittest.TestCase):
    """가장 넓은 표를 가진 실제 파일이 폭 안에 들어와야 한다.

    합성 픽스처만으로는 놓친다 — 치트시트는 4열에 SQL 이 들어가고 부록
    비교표는 클라우드 3사까지 붙는다.
    """

    WIDEST = ("01-beginner/07-commands-cheatsheet.md",
              "02-intermediate/09-commands-cheatsheet.md",
              "appendix/dbms-comparison-matrix.md")

    def test_no_rendered_line_exceeds_the_width(self):
        """코드 펜스 본문을 **제외한** 모든 줄이 폭 안에 들어온다.

        예외는 하나뿐이고 의도된 것이다 — `_emit_fence` 가 코드를 원문 그대로
        내보내므로 긴 명령은 폭을 넘고 `less` 가 접는다.

        `WIDEST` 세 파일은 전부 비교표라 코드 펜스가 0줄이므로(실측) 지금은
        이 예외가 실제로 발동하지 않는다. 그래도 걸러 두는 이유는, 치트시트에
        코드 예시가 한 줄이라도 들어오는 날 이 테스트가 **틀린 이유로** 빨개
        지지 않게 하기 위해서다. 예외가 실제로 작동하는지는 챕터 전량을 도는
        `ReflowSafetyTest.test_only_fence_bodies_may_exceed_the_width` 가 본다.
        """
        for rel in self.WIDEST:
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            code = fence_bodies(src)
            for width in (60, 80, 100):
                got = mr.render(src, width=width, color=False)
                for line in got.split("\n"):
                    if line in code:
                        continue
                    self.assertLessEqual(cwidth(line), width,
                                         f"{rel} @ {width}: {line!r}")

    def test_a_real_chapter_renders_without_escapes_when_plain(self):
        src = (REPO_ROOT / "01-beginner/01-rdbms-fundamentals.md").read_text(
            encoding="utf-8")
        self.assertNotIn("\x1b", mr.render(src, width=80, color=False))


ALL_CHAPTERS = sorted(
    p for d in ("01-beginner", "02-intermediate", "03-advanced", "appendix")
    for p in (REPO_ROOT / d).glob("*.md"))


class ReflowSafetyTest(unittest.TestCase):
    """불변식: 렌더 출력의 어떤 줄도 줄머리 위치에 의미를 싣지 않는다.

    `less` 는 소프트 랩할 때 SGR 속성은 유지하지만 줄머리 글리프와 공백 채움은
    이어지는 줄에 다시 찍지 않는다. 그래서 읽는 중 창을 줄이면 거터·표 정렬·
    폭을 채운 구분선이 무너졌다. 이 클래스가 그 회귀를 막는다.
    """

    def test_every_code_line_survives_verbatim(self):
        """강제 개행 부재의 직접 검증 — 화면에서 복사한 명령이 실행돼야 한다.

        비교는 벗기지 않고 한다. 양쪽을 `strip()` 하면 원문 들여쓰기가 사라져도
        통과하는데, `CREATE TABLE` 본문처럼 들여쓴 코드가 챕터에 실제로 있다.
        """
        for path in ALL_CHAPTERS:
            src = path.read_text(encoding="utf-8")
            code = fence_bodies(src)
            if not code:
                continue
            rendered = set(mr.render(src, width=40, color=False).split("\n"))
            self.assertEqual(code - rendered, set(), path.name)

    def test_horizontal_rules_do_not_depend_on_the_width(self):
        """폭을 채운 구분선은 창이 좁아지는 순간 두 줄이 된다."""
        src = "문단\n\n---\n\n문단\n"
        seen = set()
        for width in (40, 78, 120):
            got = mr.render(src, width=width, color=False)
            rules = [ln for ln in got.split("\n") if set(ln.strip()) == {"─"}]
            self.assertEqual(len(rules), 1, f"width={width}: {got!r}")
            seen.add(cwidth(rules[0]))
        self.assertEqual(seen, {mr._RULE_WIDTH})

    def test_no_heading_text_is_ever_dropped(self):
        """폭을 좁혀도 제목의 글자가 사라지지 않는다.

        **공백을 지우고 비교한다.** 폭보다 긴 한 단어는 `layout` 이 강제 분할
        하는데(`tui.wrap` 과 같은 계약), 그건 손실이 아니라 줄바꿈이다. 단어
        단위로 단정하면 `옵티마이저·파티셔닝·캐싱·커넥션`(폭 31, 공백 없음)
        같은 한국어 제목에서 정상 동작을 실패로 신고한다 — 실측으로 폭 24에서
        `…캐싱` / `·커넥션…` 으로 갈렸다. 한국어는 조사와 중점으로 이어져
        공백 없는 긴 단어가 흔하므로 영어 기준의 단어 단정이 맞지 않는다.
        """
        def squash(text):
            return "".join(text.split())

        heading_re = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
        for path in ALL_CHAPTERS:
            src = path.read_text(encoding="utf-8")
            headings, in_fence = [], False
            for line in src.split("\n"):
                if mr._FENCE_RE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                m = heading_re.match(line)
                if m:
                    headings.append(m.group(2))
            for width in (24, 40, 78, 120):
                flat = squash(plain(mr.render(src, width=width, color=False)))
                for text in headings:
                    self.assertIn(squash(text), flat,
                                  f"{path.name} @ {width}: {text!r}")

    def test_wrapped_headings_keep_the_heading_style(self):
        """접힌 제목이 본문과 구분되는 유일한 수단이다."""
        got = mr.render("## " + "가나다라마 " * 8 + "\n", width=40, color=True)
        lines = [ln for ln in got.split("\n") if ln.strip()]
        self.assertGreater(len(lines), 1, got)
        for line in lines:
            self.assertIn(f"\x1b[{mr.SGR['h2']}m", line, repr(line))

    def test_no_line_head_glyph_survives_from_the_old_box(self):
        """`┌ └ │` 는 접히면 무너지는 줄머리 장식이었다 — 인용의 `│` 만 남는다."""
        for path in ALL_CHAPTERS:
            got = mr.render(path.read_text(encoding="utf-8"),
                            width=40, color=False)
            for glyph in ("┌", "└"):
                self.assertNotIn(glyph, got, f"{path.name}: {glyph}")

    def test_only_fence_bodies_may_exceed_the_width(self):
        """폭 보장의 예외는 코드 펜스 본문 하나뿐이다."""
        for path in ALL_CHAPTERS:
            src = path.read_text(encoding="utf-8")
            code = fence_bodies(src)
            for width in (40, 78, 120):
                got = mr.render(src, width=width, color=False)
                for line in got.split("\n"):
                    if line in code:
                        continue
                    self.assertLessEqual(cwidth(line), width,
                                         f"{path.name} @ {width}: {line!r}")

    def test_wide_tables_never_squeeze_a_column(self):
        """압축은 `PostgreSQL` 을 `PostgreSQ`/`L` 로 쪼갰다. 카드형이 그걸 없앤다."""
        src = (REPO_ROOT / "01-beginner/07-commands-cheatsheet.md").read_text(
            encoding="utf-8")
        got = mr.render(src, width=40, color=False)
        self.assertIn(mr._CARD_MARK.strip(), got)
        self.assertNotIn("PostgreSQ\n", got)


if __name__ == "__main__":
    unittest.main()
