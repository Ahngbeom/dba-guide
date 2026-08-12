# 챕터 읽기 마크다운 뷰어 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `./guide` → 챕터 읽기가 마크다운 서식(제목·코드블록·표·목록·강조)이 적용된 화면을 보여 준다.

**Architecture:** 순수 렌더러 모듈 `scripts/markdown_render.py`가 마크다운을 ANSI 입힌 평문으로 바꾸고, 스크롤·검색은 계속 `less`가 한다. 렌더러 내부는 `inline_spans`(인라인 파싱) → `layout`(표시 폭 줄바꿈) → `paint`(ANSI 부착) 3층이며, ANSI는 폭을 다 잰 뒤 마지막에만 붙는다. `tui.py`가 페이저의 색 지원 여부를 판정하고 `less`에 `-R`을 보강한다.

**Tech Stack:** Python 3 표준 라이브러리만. `unittest`. curses 불사용(렌더러는 I/O 없음).

**설계 문서:** `docs/superpowers/specs/2026-08-11-markdown-viewer-design.md`

## Global Constraints

- **표준 라이브러리만.** `pip`/`npm` 금지. 새 외부 패키지 금지.
- **모든 콘텐츠·주석·docstring은 한국어.** 저장소 전체 관례다.
- `markdown_render.py`는 **I/O를 하지 않는다** — 파일도, 프로세스도, 터미널 크기 조회도 하지 않는다. `width`·`color`는 인자로만 받는다.
- `markdown_render.py`는 **`curses`를 import하지 않는다.**
- **폭 계산은 `tui.cwidth()`/`tui.fit()`를 재사용한다.** 전각 처리를 두 번 구현하지 않는다.
- **ANSI는 8색만** 쓴다(SGR 30–37 + `1` 굵게 + `2` 흐리게 + `4` 밑줄). 256색·트루컬러 금지.
- `color=False`면 출력에 `\x1b`가 **하나도 없어야 한다.**
- 테스트 실행: `python3 -m unittest discover -s tests`
- 기존 테스트를 깨지 않는다. 특히 `tests/test_tui.py::PageTextCharacterizationTest`와 `tests/test_reading.py::ReadChapterTest`.
- 커밋 메시지는 영어 명령형 한 줄 + 본문(저장소 기존 관례). 마지막 줄에
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`

---

## 파일 구조

| 파일 | 상태 | 책임 |
|---|---|---|
| `scripts/markdown_render.py` | 신규 | 마크다운 → ANSI 평문. 순수 함수만 |
| `tests/test_markdown_render.py` | 신규 | 렌더러 단위 테스트 |
| `scripts/tui.py` | 수정 | `pager_supports_color()`·`text_width()` 추가, `page_text`에 `-R` 보강 |
| `tests/test_tui.py` | 수정 | 위 세 가지 테스트 추가 |
| `scripts/reading.py` | 수정 | `read_chapter`가 렌더를 거치도록 배선 |
| `tests/test_reading.py` | 수정 | 렌더 경유·필터 순서 테스트 추가 |
| `CLAUDE.md` | 수정 | `scripts/` 인벤토리에 새 모듈 한 줄 |

Task 1–5가 `markdown_render.py`를 층층이 쌓고, Task 6이 `tui.py`, Task 7이 배선, Task 8이 문서와 실물 확인이다.

---

## Task 1: 렌더러 3층 코어

**Files:**
- Create: `scripts/markdown_render.py`
- Test: `tests/test_markdown_render.py`

**Interfaces:**
- Consumes: `tui.cwidth(text) -> int`, `tui.fit(text, cols) -> str`
- Produces:
  - `SGR: dict[str, str]` — 스타일 이름 → SGR 파라미터
  - `inline_spans(text: str, color: bool = True) -> list[tuple[str, str|None]]`
  - `layout(spans: list[tuple[str, str|None]], width: int) -> list[list[tuple[str, str|None]]]`
  - `paint(spans: list[tuple[str, str|None]], color: bool = True) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_markdown_render.py`:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'markdown_render'`

- [ ] **Step 3: 최소 구현을 쓴다**

`scripts/markdown_render.py`:

```python
#!/usr/bin/env python3
"""챕터 마크다운을 ANSI 입힌 평문으로 바꾼다. 뷰어는 여전히 `less` 다.

`CLAUDE.md` 의 규약은 "curses 안에서 터미널 도구를 다시 만들지 않는다"이다
(SQL 콘솔 363줄이 그 이유로 지워졌다). 그래서 이 모듈은 **화면을 그리지
않는다** — 텍스트를 텍스트로 바꿀 뿐이고, 스크롤·검색은 `$PAGER` 가 계속 한다.

I/O 를 하지 않는다. 파일도, 프로세스도, 터미널 크기 조회도 없다. `width` 와
`color` 는 인자로만 받는다 — 그래야 tty 없이 전부 단위 테스트할 수 있다.

내부는 3층이다.

    inline_spans(text)   인라인 마크업 → [(글자, 스타일)]
    layout(spans, width) 표시 폭 기준 줄바꿈 → 줄별 span 목록
    paint(spans, color)  → ANSI 문자열 또는 평문

**순서가 핵심이다.** `**굵게**` 를 ANSI 로 먼저 바꾸고 나서 줄바꿈하면
`\\x1b[1m` 이 폭 계산에 잡혀 우측 여백이 들쭉날쭉해지고, 스타일이 줄 경계에서
끊겨 `less` 에 번진다. 폭은 반드시 색이 붙기 전에 잰다.

외부 의존성 없음(Python3 표준 라이브러리만).
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tui import cwidth, fit  # noqa: E402

# 8색만 쓴다. 256색·트루컬러는 터미널마다 다르게 나온다.
# h1 과 h2 가 같은 색인 것은 의도다 — 단계 구분은 접두 장식(`━━━ … ━━━` 대
# `◆`)이 지고, 색은 늘리지 않는다. 배경색이 제각각인 터미널에서 색을 하나씩
# 더하면 어딘가에서는 반드시 안 읽히는 색이 생긴다.
SGR = {
    "h1": "1;36",     # 굵은 청록
    "h2": "1;36",
    "h3": "1",        # 굵게
    "bold": "1",
    "code": "36",     # 청록 (h2 와 같은 색, 굵기로 갈린다)
    "fence": "33",    # 노랑
    "th": "1",        # 표 헤더는 굵게만 — 아래 `─` 구분선과 겹치지 않게
    "link": "4",      # 밑줄
    "dim": "2",       # 인용·수평선·테두리·URL
}

# 인라인 마크업. 코드가 **먼저** 와야 백틱 안의 `**` 가 강조로 새지 않는다.
_INLINE_RE = re.compile(
    r"(?P<fence>`+)(?P<code>.+?)(?P=fence)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)\s]+)\)"
)


def inline_spans(text, color=True):
    """인라인 마크업을 [(글자, 스타일)] 로 나눈다.

    `color` 에 따라 **결과가 달라진다.** 색이 있으면 백틱·별표를 떼고 스타일로
    대신하고, 색이 없으면 그대로 남긴다 — 구분 수단이 사라졌는데 기호까지
    지우면 정보가 사라진다.
    """
    spans = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            spans.append((text[pos:m.start()], None))
        if m.group("code") is not None:
            spans.append((m.group("code") if color else m.group(0), "code"))
        elif m.group("bold") is not None:
            spans.append((m.group("bold") if color else m.group(0), "bold"))
        elif color:
            spans.append((m.group("ltext"), "link"))
            spans.append((f" ({m.group('lurl')})", "dim"))
        else:
            spans.append((m.group(0), None))
        pos = m.end()
    if pos < len(text):
        spans.append((text[pos:], None))
    return spans


def swidth(spans):
    """span 목록의 표시 폭."""
    return sum(cwidth(t) for t, _ in spans)


def _words(spans):
    """span 목록 → 단어(=span 목록) 목록. 공백으로만 나눈다.

    **스타일 경계는 단어를 나누지 않는다.** 한국어 본문은 `` `employees` `` 와
    조사 `를` 사이에 공백이 없어서, span 단위로 끊으면 줄 끝에서 조사만 다음
    줄로 떨어진다.
    """
    out, word = [], []
    for text, style in spans:
        for ch in text:
            if ch.isspace():
                if word:
                    out.append(word)
                    word = []
            elif word and word[-1][1] == style:
                word[-1] = (word[-1][0] + ch, style)
            else:
                word.append((ch, style))
    if word:
        out.append(word)
    return out


def _split_word(word, width):
    """폭을 넘는 한 단어를 (앞부분, 나머지) 로 자른다."""
    head, rest, used = [], [], 0
    for text, style in word:
        if rest:
            rest.append((text, style))
            continue
        part = fit(text, width - used)
        if part:
            head.append((part, style))
            used += cwidth(part)
        if len(part) < len(text):
            rest.append((text[len(part):], style))
    return head, rest


def layout(spans, width):
    """span 목록을 표시 폭 기준으로 접는다 → 줄별 span 목록.

    항상 최소 한 줄을 돌려준다(`tui.wrap` 과 같은 계약).
    """
    width = max(4, width)
    lines, cur, used = [], [], 0

    def flush():
        nonlocal cur, used
        lines.append(cur)
        cur, used = [], 0

    for word in _words(spans):
        while swidth(word) > width:      # 한 단어가 폭 자체를 넘으면 강제 분할
            if used:
                flush()
            head, word = _split_word(word, width)
            if not head:
                break
            cur = head
            flush()
        ww = swidth(word)
        if not ww:
            continue
        if used and used + 1 + ww > width:
            flush()
        if used:
            cur.append((" ", None))
            used += 1
        cur.extend(word)
        used += ww
    flush()
    return lines


def paint(spans, color=True):
    """줄 하나의 span 목록 → 출력 문자열.

    스타일마다 리셋(`\\x1b[0m`)을 붙인다. 조금 장황하지만 줄바꿈·표 셀 경계에서
    스타일이 새지 않는 것이 훨씬 중요하다.
    """
    if not color:
        return "".join(t for t, _ in spans)
    out = []
    for text, style in spans:
        code = SGR.get(style)
        out.append(f"\x1b[{code}m{text}\x1b[0m" if code and text else text)
    return "".join(out)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: PASS (18개 테스트)

- [ ] **Step 5: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Add the inline/layout/paint core of the markdown renderer

Colour is applied last, after the width is measured, so escape sequences
never enter the wrap calculation. Words are split on whitespace across
style boundaries because Korean glues a particle onto inline code with no
space between them.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `render()` 진입점 — 제목·문단·수평선·HTML 주석

**Files:**
- Modify: `scripts/markdown_render.py` (Task 1 코드 아래에 추가)
- Test: `tests/test_markdown_render.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 1의 `inline_spans`, `layout`, `paint`, `swidth`
- Produces: `render(text: str, width: int = 80, color: bool = True) -> str`
  — 항상 개행으로 끝나는 문자열. Task 3·5가 이 함수 안에 분기를 더한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_markdown_render.py`의 `if __name__` 앞에 추가:

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: FAIL — `AttributeError: module 'markdown_render' has no attribute 'render'`

- [ ] **Step 3: 최소 구현을 쓴다**

`scripts/markdown_render.py` 끝에 추가:

```python
# --------------------------------------------------------------------------- #
# 블록 렌더
# --------------------------------------------------------------------------- #
# 줄 단위 스캐너로 쓴다 — `filter_dbms.filter_lines` 와 같은 방식이다. 챕터가
# 쓰는 마크다운 부분집합이 좁고 일정해서 범용 파서가 필요 없다.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")
_COMMENT_RE = re.compile(r"^\s*<!--.*-->\s*$")


def _blank(out):
    """직전 줄이 이미 비어 있지 않을 때만 빈 줄을 넣는다."""
    if out and out[-1] != "":
        out.append("")


def _emit_heading(level, body, width, color, out):
    """제목은 인라인 스타일을 버리고 통째로 한 스타일을 입는다.

    제목 안의 `` `코드` `` 를 따로 색칠하면 제목 색과 싸워서 오히려 단계가
    안 읽힌다. 기호만 떼고 제목 색으로 통일한다.
    """
    plain_body = "".join(t for t, _ in inline_spans(body, color))
    _blank(out)
    if level == 1:
        bar = "━" * 3
        text, style = f"{bar} {plain_body} {bar}", "h1"
    else:
        prefix, style = ("◆ ", "h2") if level == 2 else ("· ", "h3")
        text = prefix + plain_body
    # 제목은 접지 않고 자른다 — 접힌 제목은 본문과 구분이 안 된다. 자르지
    # 않으면 `less` 가 접어서 같은 결과가 되고, 폭 회귀 테스트도 깨진다.
    out.append(paint([(fit(text, width), style)], color))


def _emit_paragraph(line, width, color, out):
    for row in layout(inline_spans(line, color), width):
        out.append(paint(row, color))


def render(text, width=80, color=True):
    """마크다운 → 화면에 뿌릴 문자열. 항상 개행으로 끝난다."""
    width = max(20, width)
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1

        if _COMMENT_RE.match(line):
            continue                       # dbms 마커 등 — 화면에 낼 것이 아니다
        if not line.strip():
            _blank(out)
            continue
        if _HR_RE.match(line):
            out.append(paint([("─" * width, "dim")], color))
            continue
        m = _HEADING_RE.match(line)
        if m:
            _emit_heading(len(m.group(1)), m.group(2), width, color, out)
            continue
        _emit_paragraph(line, width, color, out)

    return "\n".join(out) + "\n"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: PASS (전체 32개)

- [ ] **Step 5: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Render headings, paragraphs, rules and hidden comments

Chapter reading with no vendor chosen never calls filter_lines, so the
`<!-- dbms:mysql -->` markers reach the screen today. Hiding HTML comments
removes that noise as a side effect of the same rule.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 코드펜스

**Files:**
- Modify: `scripts/markdown_render.py`
- Test: `tests/test_markdown_render.py`

**Interfaces:**
- Consumes: Task 2의 `render` 루프, `paint`, `SGR["fence"]`, `SGR["dim"]`
- Produces: `render`가 ` ``` ` 블록을 테두리 상자로 낸다. 새 공개 함수 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.FenceTest -v`
Expected: FAIL — `┌─ sql` 이 출력에 없다 (` ```sql ` 가 문단으로 나온다)

- [ ] **Step 3: 최소 구현을 쓴다**

`_COMMENT_RE` 아래에 추가:

```python
_FENCE_RE = re.compile(r"^\s*```+\s*(\w*)")
```

`_emit_paragraph` 아래에 추가:

```python
def _emit_fence(lines, i, width, color, out):
    """` ```lang ` 블록을 테두리 상자로 낸다 → 다음에 볼 줄 번호.

    상자 안에서는 마크업을 **전혀** 해석하지 않는다. SQL 주석 `--`, bash 의
    `## `, C 의 `**ptr` 이 그대로 들어 있다 — `filter_dbms.filter_lines` 가
    `in_fence` 를 추적하는 것과 같은 이유다.

    긴 코드 줄은 폭 단위로 자른다. 단어 단위로 접으면 보기에는 낫지만 `│`
    거터가 어긋나고, 자르지 않으면 `less` 가 접으면서 거터가 무너진다.
    """
    lang = _FENCE_RE.match(lines[i]).group(1)
    i += 1
    head = f"┌─ {lang} " if lang else "┌"
    out.append(paint([(head + "─" * max(0, width - cwidth(head)), "dim")],
                     color))
    while i < len(lines) and not _FENCE_RE.match(lines[i]):
        body = lines[i].rstrip()
        # 폭 - 2 = `│ ` 만큼 뺀 나머지. 빈 줄도 거터는 남긴다.
        while True:
            part = fit(body, width - 2)
            out.append(paint([("│ ", "dim"), (part, "fence")], color))
            body = body[len(part):]
            if not body:
                break
        i += 1
    out.append(paint([("└" + "─" * (width - 1), "dim")], color))
    # 닫는 펜스가 없으면(파일 끝) i 는 이미 끝이다 — 한 칸 더 넘겨도 안전하다.
    return i + 1
```

`render` 루프에서 `_COMMENT_RE` 검사 **뒤**, 빈 줄 검사 **앞**에 삽입:

```python
        if _FENCE_RE.match(line):
            i = _emit_fence(lines, i - 1, width, color, out)
            continue
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: PASS (전체 38개)

- [ ] **Step 5: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Box fenced code blocks and stop parsing markup inside them

A fence can hold a SQL comment, a bash '## ' or a C '**ptr'; filter_lines
tracks in_fence for the same reason. Long code lines are hard-split at the
width so the gutter survives -- less would otherwise wrap them and break it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 목록·체크박스·인용

**Files:**
- Modify: `scripts/markdown_render.py`
- Test: `tests/test_markdown_render.py`

**Interfaces:**
- Consumes: Task 1–2의 `inline_spans`, `layout`, `paint`, `swidth`
- Produces: `render`가 불릿·번호 목록·`- [ ]`·`>` 를 처리. 새 공개 함수 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.ListTest tests.test_markdown_render.QuoteTest -v`
Expected: FAIL — `• 첫 항목` 이 없다 (`- 첫 항목` 이 문단으로 나온다)

- [ ] **Step 3: 최소 구현을 쓴다**

`_FENCE_RE` 아래에 추가:

```python
# 체크박스가 불릿보다 **먼저** 걸려야 한다 — `- [ ] …` 는 불릿에도 맞는다.
_TASK_RE = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")

# 깊이별 불릿 기호. 이 저장소는 중첩 들여쓰기를 2·3·4칸으로 섞어 쓴다(실측)
# 이라 `len(indent) // 2` 로 재면 3칸과 4칸이 다른 깊이가 되어 같은 목록이
# 들쭉날쭉해진다. 1~4칸을 한 단계로 묶는다.
_MARKERS = ("•", "◦", "-")


def _depth(indent):
    return 0 if not indent else min(len(_MARKERS) - 1, 1 + (len(indent) - 1) // 4)
```

`_emit_fence` 아래에 추가:

```python
def _emit_hanging(head, body, width, color, out, head_style=None, repeat=False):
    """`head` 를 붙이고 접힌 줄을 그 폭만큼 들여 쓴다.

    목록은 이어지는 줄이 기호 아래가 아니라 **글자 아래**로 와야 항목 경계가
    보인다(`repeat=False`). 인용은 반대로 **막대가 이어져야** 인용 범위가
    보인다(`repeat=True`) — 둘째 줄에서 `│` 가 끊기면 인용이 끝난 것처럼
    읽힌다.
    """
    hw = cwidth(head)
    rows = layout(inline_spans(body, color), width - hw)
    for n, row in enumerate(rows):
        if n == 0 or repeat:
            prefix = [(head, head_style)]
        else:
            prefix = [(" " * hw, None)]
        out.append(paint(prefix + row, color))


def _emit_list_or_quote(line, width, color, out):
    """목록·체크박스·인용이면 내고 True. 아니면 False."""
    m = _TASK_RE.match(line)
    if m:
        indent, mark, body = m.groups()
        box = "☑" if mark.lower() == "x" else "☐"
        _emit_hanging(f"{indent}{box} ", body, width, color, out)
        return True
    m = _ORDERED_RE.match(line)
    if m:
        indent, num, body = m.groups()
        _emit_hanging(f"{indent}{num}. ", body, width, color, out)
        return True
    m = _BULLET_RE.match(line)
    if m:
        indent, body = m.groups()
        marker = _MARKERS[_depth(indent)]
        _emit_hanging(f"{indent}{marker} ", body, width, color, out)
        return True
    m = _QUOTE_RE.match(line)
    if m:
        _emit_hanging("│ ", m.group(1), width, color, out, head_style="dim",
                      repeat=True)
        return True
    return False
```

`render` 루프의 `_HEADING_RE` 분기 **뒤**, `_emit_paragraph` **앞**에 삽입:

```python
        if _emit_list_or_quote(line, width, color, out):
            continue
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: PASS (전체 48개)

`_HR_RE` 가 `- 항목`을 수평선으로 오인하지 않는지도 함께 통과해야 한다 (`RuleAndCommentTest.test_a_bullet_is_not_mistaken_for_a_rule`).

- [ ] **Step 5: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Render lists, checkboxes and blockquotes with hanging indents

Nesting depth is measured in 4-column bands, not halves: the chapters mix
2-, 3- and 4-space indents, so len(indent)//2 would hand the same nesting
level two different bullets.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 표

**Files:**
- Modify: `scripts/markdown_render.py`
- Test: `tests/test_markdown_render.py`

**Interfaces:**
- Consumes: Task 1–2의 `inline_spans`, `layout`, `paint`, `swidth`
- Produces: `render`가 파이프 표를 정렬해 낸다. 새 공개 함수 없음.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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

    def test_right_and_centre_alignment_are_honoured(self):
        src = "| a | b |\n|---:|:---:|\n| 1 | 2 |\n"
        rows = self._rows(mr.render(src, width=40, color=False))
        self.assertIn("1", rows[2])

    def test_a_lone_pipe_line_is_not_a_table(self):
        """구분선이 뒤따르지 않으면 표가 아니다 — 문단으로 낸다."""
        got = mr.render("| 그냥 파이프 문장\n", width=40, color=False)
        self.assertIn("|", got)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.TableTest -v`
Expected: FAIL — `test_the_pipes_are_gone` 등에서 `|` 가 그대로 남아 있다

- [ ] **Step 3: 최소 구현을 쓴다**

`_QUOTE_RE` 아래에 추가:

```python
_SEP_CELL_RE = re.compile(r"^:?-{1,}:?$")
_MIN_COL = 4          # 한글 두 글자. 0폭 열이 생기지 않게 하는 하한
```

`_emit_list_or_quote` 아래에 추가:

```python
def _cells(line):
    """`| a | b |` → ["a", "b"]. 앞뒤 파이프는 버린다.

    셀 안의 파이프는 다루지 않는다 — 이 저장소 챕터에는 한 건도 없다(실측).
    """
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def _is_separator(line):
    """`|---|---|` 인가. 셀이 하나도 없는 `|` 한 글자는 구분선이 아니다."""
    if not line.strip().startswith("|"):
        return False
    cells = [c for c in _cells(line) if c]
    return bool(cells) and all(_SEP_CELL_RE.match(c) for c in cells)


def _alignments(line):
    """구분선에서 열 정렬을 읽는다. 이 저장소에는 지시자가 0건이지만(실측)
    README·부록이 나중에 쓸 수 있어 세 줄로 지켜 둔다."""
    out = []
    for c in _cells(line):
        left, right = c.startswith(":"), c.endswith(":")
        out.append("center" if left and right else "right" if right else "left")
    return out


def _fit_columns(natural, width):
    """자연 폭이 화면을 넘으면 넓은 열부터 1칸씩 깎는다."""
    cols = list(natural)
    avail = width - (len(cols) - 1)          # 열 사이 여백 1칸
    while sum(cols) > avail:
        widest = max(range(len(cols)), key=lambda j: cols[j])
        if cols[widest] <= _MIN_COL:
            break                            # 더 깎을 데가 없다 — less 가 접는다
        cols[widest] -= 1
    return cols


def _pad(row_spans, col, align):
    """셀 한 줄을 열 폭에 맞춰 채운다."""
    gap = col - swidth(row_spans)
    if gap <= 0:
        return row_spans
    if align == "right":
        return [(" " * gap, None)] + row_spans
    if align == "center":
        left = gap // 2
        return [(" " * left, None)] + row_spans + [(" " * (gap - left), None)]
    return row_spans + [(" " * gap, None)]


def _emit_table(lines, i, width, color, out):
    """파이프 표를 정렬해 낸다 → 다음에 볼 줄 번호."""
    header = _cells(lines[i])
    aligns = _alignments(lines[i + 1])
    i += 2
    body = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        body.append(_cells(lines[i]))
        i += 1

    rows = [header] + body
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    aligns = (aligns + ["left"] * ncols)[:ncols]

    spans = [[inline_spans(c, color) for c in r] for r in rows]
    natural = [max(swidth(r[j]) for r in spans) for j in range(ncols)]
    cols = _fit_columns(natural, width)

    _blank(out)
    for n, row in enumerate(spans):
        style = "th" if n == 0 else None
        cells = [layout(row[j], cols[j]) for j in range(ncols)]
        height = max(len(c) for c in cells)
        for line_no in range(height):
            parts = []
            for j in range(ncols):
                piece = cells[j][line_no] if line_no < len(cells[j]) else []
                if style:
                    piece = [(t, s or style) for t, s in piece]
                parts.extend(_pad(piece, cols[j], aligns[j]))
                if j < ncols - 1:
                    parts.append((" ", None))
            out.append(paint(parts, color).rstrip())
        if n == 0:
            rule = []
            for j in range(ncols):
                rule.append(("─" * cols[j], "dim"))
                if j < ncols - 1:
                    rule.append((" ", None))
            out.append(paint(rule, color))
    return i
```

`render` 루프의 `_FENCE_RE` 분기 **뒤**에 삽입 (빈 줄 검사 앞):

```python
        if (line.strip().startswith("|") and i < len(lines)
                and _is_separator(lines[i])):
            i = _emit_table(lines, i - 1, width, color, out)
            continue
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: PASS (전체 56개)

- [ ] **Step 5: 실제 챕터로 폭 회귀 테스트를 추가한다**

`tests/test_markdown_render.py` 끝(`if __name__` 앞)에 추가:

```python
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
```

Run: `python3 -m unittest tests.test_markdown_render.RealChapterTest -v`
Expected: PASS.

실패하면 `_fit_columns` 의 `_MIN_COL` 하한에 걸린 것이다. 다만 **실측상 걸리지 않아야 한다**: 이 저장소의 최다 열은 5열(`appendix/dbms-comparison-matrix.md`)이고 치트시트 둘은 4열이라, 최악이 `_MIN_COL 4 × 5 + 여백 4 = 24` 로 폭 60에서도 여유가 있다. 그런데도 걸린다면 `_MIN_COL` 을 낮추지 말고 — 4는 한글 두 글자라 이미 최소다 — `_emit_table` 이 그 표를 세로(`열이름: 값`)로 낼지 판단해야 한다.

- [ ] **Step 6: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Align pipe tables and wrap oversized cells

Cell splitting is a plain '|' split: no chapter table contains an escaped
or doubled pipe (measured). Columns are measured with cwidth, so Korean
headers line up where ljust would drift, and the widest column is shaved a
column at a time until the row fits.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `tui.py` — 색 판정·폭·`-R` 보강

**Files:**
- Modify: `scripts/tui.py` (`page_text` 근처, 파일 끝 "외부 도구 위임" 절)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: 기존 `page_text`, `os`, `shlex`, `shutil`, `subprocess`, `sys`
- Produces:
  - `pager_supports_color(stream=None) -> bool`
  - `text_width() -> int`
  - `page_text(text) -> int` — 동작 동일, `less` 계열에 `-R` 만 보강

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_tui.py`의 `PickLineTest` 앞에 추가:

```python
class PagerColorTest(unittest.TestCase):
    """ANSI 를 넣어도 안전한 페이저인지 판정한다.

    지금보다 나빠지는 유일한 경우가 여기다 — `PAGER=more` 에 이스케이프를
    보내면 `ESC[1m` 이 글자로 찍힌다.
    """

    class _TTY(io.StringIO):
        def isatty(self):
            return True

    class _Pipe(io.StringIO):
        def isatty(self):
            return False

    @contextlib.contextmanager
    def _env(self, **kv):
        real = dict(os.environ)
        os.environ.pop("NO_COLOR", None)
        os.environ.pop("PAGER", None)
        os.environ["TERM"] = "xterm-256color"
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(real)

    def test_no_pager_set_means_we_will_use_less_dash_r(self):
        with self._env():
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_less_is_fine(self):
        with self._env(PAGER="less"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_less_with_flags_is_fine(self):
        with self._env(PAGER="less -FRX"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_an_absolute_path_to_less_is_fine(self):
        with self._env(PAGER="/usr/bin/less"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_more_is_not(self):
        with self._env(PAGER="more"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))

    def test_bat_is_fine(self):
        with self._env(PAGER="bat"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_no_color_env_wins_over_everything(self):
        with self._env(PAGER="less", NO_COLOR="1"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))

    def test_a_pipe_gets_no_colour(self):
        with self._env(PAGER="less"):
            self.assertFalse(tui.pager_supports_color(self._Pipe()))

    def test_a_dumb_terminal_gets_no_colour(self):
        with self._env(PAGER="less", TERM="dumb"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))

    def test_an_unparseable_pager_gets_no_colour(self):
        with self._env(PAGER="less 'unclosed"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))


class TextWidthTest(unittest.TestCase):
    """가로 200칸에서 한글 문단이 한 줄로 늘어지면 오히려 못 읽는다."""

    @contextlib.contextmanager
    def _cols(self, n):
        real = tui.shutil.get_terminal_size
        tui.shutil.get_terminal_size = lambda fallback=(80, 24): os.terminal_size(
            (n, 24))
        try:
            yield
        finally:
            tui.shutil.get_terminal_size = real

    def test_a_narrow_terminal_gets_the_floor(self):
        with self._cols(30):
            self.assertEqual(tui.text_width(), 40)

    def test_a_wide_terminal_gets_the_ceiling(self):
        with self._cols(200):
            self.assertEqual(tui.text_width(), 100)

    def test_a_normal_terminal_gets_two_columns_of_margin(self):
        with self._cols(90):
            self.assertEqual(tui.text_width(), 88)


class LessRawFlagTest(unittest.TestCase):
    """`less` 에 `-R` 이 없으면 ANSI 가 글자로 찍힌다. 없을 때만 붙인다."""

    def _cmd(self, pager):
        seen = {}

        class FakeProc:
            returncode = 0

            def communicate(self, text):
                pass

        real_popen = tui.subprocess.Popen
        real_env = dict(os.environ)
        tui.subprocess.Popen = lambda *a, **k: (seen.setdefault("cmd", a[0])
                                                and None or FakeProc())
        os.environ["PAGER"] = pager
        try:
            tui.page_text("본문")
        finally:
            tui.subprocess.Popen = real_popen
            os.environ.clear()
            os.environ.update(real_env)
        return seen["cmd"]

    def test_bare_less_gets_dash_r(self):
        self.assertEqual(self._cmd("less"), ["less", "-R"])

    def test_existing_dash_r_is_not_duplicated(self):
        self.assertEqual(self._cmd("less -R"), ["less", "-R"])

    def test_a_bundled_flag_counts(self):
        self.assertEqual(self._cmd("less -FRX"), ["less", "-FRX"])

    def test_the_long_form_counts(self):
        self.assertEqual(self._cmd("less --RAW-CONTROL-CHARS"),
                         ["less", "--RAW-CONTROL-CHARS"])

    def test_other_pagers_are_left_alone(self):
        self.assertEqual(self._cmd("more"), ["more"])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_tui -v`
Expected: FAIL — `AttributeError: module 'tui' has no attribute 'pager_supports_color'`

- [ ] **Step 3: 최소 구현을 쓴다**

`scripts/tui.py`의 `page_text` **위**에 추가:

```python
# 색을 살려 넘길 수 있는 페이저. 여기 없는 페이저에는 ANSI 를 보내지 않는다 —
# `more` 에 이스케이프를 보내면 `ESC[1m` 이 글자로 찍혀 **지금보다 나빠진다.**
COLOR_PAGERS = ("less", "most", "bat", "delta", "moar", "ov")


def pager_supports_color(stream=None):
    """지금 이 실행에서 ANSI 를 내보내도 안전한가.

    부르는 쪽이 색을 넣을지 결정하는 데 쓴다. 페이저 명령줄 조립은
    `page_text` 안에서만 일어나지만, 색 여부는 `page_text` 가 알 수 없다 —
    같은 함수가 `shoot` 의 등급표 같은 평문도 받기 때문이다.
    """
    stream = stream if stream is not None else sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if not (hasattr(stream, "isatty") and stream.isatty()):
        return False
    if os.environ.get("TERM", "") in ("", "dumb"):
        return False
    pager = os.environ.get("PAGER")
    if not pager:
        return True                      # page_text 가 `less -R` 을 쓴다
    try:
        argv = shlex.split(pager)
    except ValueError:
        return False                     # 따옴표가 안 닫힌 설정 — 안전한 쪽으로
    if not argv:
        return True
    return os.path.basename(argv[0]) in COLOR_PAGERS


def text_width():
    """본문 렌더 폭.

    가로 200칸 터미널에서 한글 문단이 한 줄로 늘어지면 오히려 못 읽는다.
    `get_terminal_size` 는 비-tty 에서 `COLUMNS` 또는 폴백으로 떨어지므로
    따로 처리할 것이 없다.
    """
    return max(40, min(shutil.get_terminal_size((80, 24)).columns - 2, 100))


def _with_raw_flag(argv):
    """`less` 에 `-R` 이 없으면 붙인다. 다른 페이저는 건드리지 않는다."""
    if not argv or os.path.basename(argv[0]) != "less":
        return argv
    for a in argv[1:]:
        if a.startswith("--") and a.lower().startswith("--raw"):
            return argv
        if a.startswith("-") and not a.startswith("--") and (
                "R" in a or "r" in a):
            return argv
    return argv + ["-R"]
```

`page_text` 안의 `Popen` 호출을 고친다:

```python
    try:
        proc = subprocess.Popen(_with_raw_flag(shlex.split(pager)),
                                stdin=subprocess.PIPE, text=True)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_tui -v`
Expected: PASS. 기존 `PageTextCharacterizationTest` 도 그대로 통과해야 한다(`PAGER="less -R"` → `["less", "-R"]`, 중복 없음).

- [ ] **Step 5: 커밋**

```bash
git add scripts/tui.py tests/test_tui.py
git commit -m "Decide pager colour support and top up less with -R

An ANSI-rendered chapter sent to a pager that cannot handle it prints
ESC[1m as literal text -- the one way this feature could make reading
worse. Unknown pagers, NO_COLOR, a pipe or TERM=dumb all fall back to
structure without escapes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `reading.py` 배선

**Files:**
- Modify: `scripts/reading.py:19` (import), `scripts/reading.py:84-86` (`read_chapter`)
- Test: `tests/test_reading.py` (`ReadChapterTest` 확장)

**Interfaces:**
- Consumes: `markdown_render.render(text, width, color)`, `tui.text_width()`, `tui.pager_supports_color()`
- Produces: 없음(최종 배선)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_reading.py`의 `ReadChapterTest`를 통째로 교체:

```python
class ReadChapterTest(unittest.TestCase):
    """본문은 렌더를 거쳐 `$PAGER` 로 간다 — curses 안에 뷰어를 만들지 않는다."""

    CHAPTER = "01-beginner/03-installation-and-access.md"

    @contextlib.contextmanager
    def _capture(self):
        seen = {}
        real = reading.page_text
        reading.page_text = lambda text: seen.setdefault("text", text)
        try:
            yield seen
        finally:
            reading.page_text = real

    def test_it_hands_the_filtered_text_to_the_pager(self):
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        self.assertIn("설치", seen["text"])
        self.assertNotIn("<!-- dbms:", seen["text"])

    def test_the_text_is_rendered_not_raw(self):
        """마크업 기호가 그대로 가면 렌더를 안 거친 것이다."""
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        self.assertNotIn("## ", seen["text"])
        self.assertIn("◆", seen["text"])

    def test_rendering_happens_after_the_vendor_filter(self):
        """순서가 뒤집히면 필터가 마커를 못 찾아 다른 벤더 본문이 남는다."""
        raw = reading.chapter_text(self.CHAPTER)
        if "<!-- dbms:" not in raw:
            self.skipTest("벤더 브랜치 — 이미 필터된 뷰라 걷어낼 마커가 없다")
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        rendered_full = reading.markdown_render.render(
            raw, width=reading.text_width(),
            color=reading.pager_supports_color())
        self.assertLess(len(seen["text"]), len(rendered_full))

    def test_a_pipe_gets_no_escape_sequences(self):
        """테스트는 tty 가 아니다 — 색이 꺼져야 한다."""
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        self.assertNotIn("\x1b", seen["text"])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_reading.ReadChapterTest -v`
Expected: FAIL — `test_the_text_is_rendered_not_raw` 에서 `## ` 가 그대로 있다

- [ ] **Step 3: 최소 구현을 쓴다**

`scripts/reading.py:19`의 import를 교체:

```python
import markdown_render  # noqa: E402
from tui import (page_text, pager_supports_color, pause_after_output,  # noqa: E402
                 pick, pick_line, text_width)
```

`scripts/reading.py:84-86`의 `read_chapter`를 교체:

```python
def read_chapter(rel, dbms=None):
    """본문을 렌더해 `$PAGER` 로 넘긴다. curses 밖에서 부른다.

    렌더는 벤더 필터 **뒤**다 — 순서가 뒤집히면 `filter_lines` 가 찾는
    `<!-- dbms:… -->` 마커가 이미 지워져 있어 다른 벤더 본문이 그대로 남는다.
    """
    page_text(markdown_render.render(chapter_text(rel, dbms),
                                     width=text_width(),
                                     color=pager_supports_color()))
```

모듈 docstring의 "본문은 `$PAGER` 에 넘긴다" 문단도 고친다:

```
본문은 `markdown_render` 로 서식을 입힌 뒤 `$PAGER` 에 넘긴다. 뷰어를 curses 로
만들지 않는다는 것이 이 저장소의 규약이고(`CLAUDE.md`), `less` 가 스크롤·검색을
이미 다 한다 — 렌더러는 화면을 그리지 않고 텍스트를 텍스트로 바꿀 뿐이다.
```

- [ ] **Step 4: 전체 스위트를 돌린다**

Run: `python3 -m unittest discover -s tests`
Expected: 전부 PASS. `tests/test_guide.py`도 `reading` 을 import 하므로 함께 확인된다.

- [ ] **Step 5: 커밋**

```bash
git add scripts/reading.py tests/test_reading.py
git commit -m "Render chapter markdown before handing it to the pager

Rendering runs after the vendor filter: flipping the order would erase the
<!-- dbms:... --> markers filter_lines looks for and leave the other
vendors' text in place.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 문서와 실물 확인

**Files:**
- Modify: `CLAUDE.md` (`scripts/` 인벤토리, `scripts/tui.py` 항목 앞)

**Interfaces:**
- Consumes: Task 1–7 전부
- Produces: 없음

- [ ] **Step 1: 전체 스위트가 깨끗한지 확인한다**

Run: `python3 -m unittest discover -s tests`
Expected: 전부 PASS, 에러·경고 없음

- [ ] **Step 2: 실제 화면을 눈으로 확인한다**

```bash
./guide      # 챕터 읽기 → 전체 → 01-beginner → 01-rdbms-fundamentals.md
```

확인 항목:
- `━━━`/`◆`/`·` 로 제목 3단계가 구분된다
- `┌─ sql`/`│` 로 코드블록이 본문과 갈린다
- 표의 열이 맞는다
- `<!-- dbms:` 마커가 안 보인다 ("전체" 보기)
- `/` 로 `less` 검색이 여전히 된다

이어서 가장 넓은 표 두 개:

```bash
./guide      # → 01-beginner → 07-commands-cheatsheet.md
./guide      # → appendix → dbms-comparison-matrix.md
```

폭 초과로 `less` 가 줄을 접는 곳이 없어야 한다.

- [ ] **Step 3: 무색 경로를 확인한다**

```bash
PAGER=more ./guide     # 이스케이프가 글자로 찍히지 않아야 한다
NO_COLOR=1 ./guide     # 색 없이 정렬·테두리·기호만 남아야 한다
./guide < /dev/null | head -40   # 파이프에도 \x1b 가 없어야 한다
```

- [ ] **Step 4: `CLAUDE.md` 인벤토리를 갱신한다**

`- **`scripts/tui.py`** — curses 프리미티브…` 항목 **앞**에 다음을 넣는다:

```markdown
- `scripts/markdown_render.py` — 챕터 마크다운을 ANSI 입힌 평문으로 바꾸는 렌더러. `reading.py`가 본문을 `$PAGER`로 넘기기 전에 통과시킨다. **뷰어가 아니다** — 화면을 그리지 않고 텍스트를 텍스트로 바꿀 뿐이라, "curses 안에서 터미널 도구를 다시 만들지 않는다"는 규약은 그대로다(스크롤·검색은 계속 `less`). 내부는 `inline_spans` → `layout` → `paint` 3층이고 **순서가 계약이다**: ANSI를 먼저 붙이면 `\x1b[1m`이 폭 계산에 잡혀 정렬이 어긋나고 스타일이 줄 경계에서 샌다. 폭은 `tui.cwidth`로 재고 I/O는 하지 않는다(`width`·`color`는 인자로만 받는다). 색을 넣을지는 `tui.pager_supports_color()`가 정하고 — `PAGER=more`처럼 모르는 페이저·`NO_COLOR`·파이프·`TERM=dumb`이면 끈다. 이때도 정렬·테두리·`☐` 같은 **구조 개선은 남는다**: 색과 구조를 분리해 두었기 때문이고, 색이 없을 때는 백틱·`**`를 **일부러 남긴다**(구분 수단이 사라졌는데 기호까지 지우면 정보가 준다). 중첩 불릿 깊이를 4칸 구간으로 재는 것도 실측 대응이다 — 챕터가 2·3·4칸 들여쓰기를 섞어 써서 `len(indent)//2`로는 같은 단계가 다른 기호를 받는다.
```

- [ ] **Step 5: 커밋**

```bash
git add CLAUDE.md
git commit -m "Document the markdown renderer in the scripts inventory

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## 자체 검토 기록

**스펙 커버리지** — 설계 문서의 각 절이 어느 태스크에 있는가:

| 스펙 절 | 태스크 |
|---|---|
| 1. 구조 (3층, 파이프라인) | Task 1, Task 7 |
| 2. 블록 요소 표 — 제목·수평선·주석·문단 | Task 2 |
| 2. 블록 요소 표 — 코드펜스 | Task 3 |
| 2. 블록 요소 표 — 목록·체크박스·인용 | Task 4 |
| 2. 블록 요소 표 — 표, 폭 배분, 정렬 지시자 | Task 5 |
| 2. 인라인 (색 유무 규칙) | Task 1 |
| 2. 색 팔레트 | Task 1 (`SGR`) |
| 3. `pager_supports_color()` | Task 6 |
| 3. `page_text` `-R` 보강 | Task 6 |
| 3. `text_width()` | Task 6 |
| 4. `reading.read_chapter` 배선 | Task 7 |
| 5. 검증 — 렌더러 테스트 | Task 1–5 |
| 5. 검증 — tui 테스트 | Task 6 |
| 5. 검증 — reading 테스트 | Task 7 |
| 5. 검증 — `CLAUDE.md`, 실행 확인 | Task 8 |
| 위험표 4항목 | Task 6(페이저), Task 3(펜스), Task 5 Step 5(실제 챕터 폭), Task 1·2(무-`\x1b`) |

빠진 스펙 요구사항 없음.

**스펙에서 구체화한 곳** — 스펙 2절은 중첩 불릿을 "2단계 `◦`"로만 적었고 깊이 계산
규칙이 없었다. 실측 결과 챕터가 2·3·4칸 들여쓰기를 섞어 써서 `len(indent)//2` 로는
같은 단계가 다른 기호를 받는다. Task 4에서 4칸 구간(`1 + (len(indent)-1)//4`,
최대 2)으로 확정했다.

**자체 검토에서 고친 결함 다섯**

1. `TableTest` 두 곳이 **문자 인덱스**로 열 정렬을 검증하고 있었다. `"가나다"` 는
   3자·폭 6, `"ab"` 는 2자·폭 4+채움 2 — 화면에서 열이 맞아도 `str.index()` 는
   4와 5로 갈려, 정렬 성공을 실패로 신고했을 것이다. `cwidth` 비교로 고쳤다.
   (이 저장소가 `cwidth` 를 만든 이유가 테스트에도 그대로 적용된다.)
2. `QuoteTest.test_a_long_quote_wraps_under_the_bar` 는 모든 줄이 `│ ` 로
   시작하기를 요구하는데, `_emit_hanging` 은 접힌 줄을 공백으로 들여썼다.
   인용은 막대가 이어져야 범위가 보이므로 `repeat=True` 를 추가했다 — 목록과
   요구가 반대인 것이 맞다.
3. `_is_separator` 가 `|` 한 글자를 구분선으로 봤다(빈 셀 목록에 `all()` 이
   True). 비어 있지 않은 셀이 하나는 있어야 한다는 조건을 넣었다.
4. `_emit_heading` 이 제목을 자르지 않아 긴 제목이 폭을 넘을 수 있었고,
   `RealChapterTest` 의 폭 회귀와 충돌했다. `fit()` 으로 자른다.
5. Task 5 Step 5의 실패 대응 지침이 근거 없이 "걸릴 수 있다"고만 했다.
   실측(최다 5열)을 넣어 걸리지 않아야 한다는 기대치를 명시했다.
