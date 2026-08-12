# 접기 안전 렌더 구현 계획 (이슈 #96)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 챕터 읽기 화면의 렌더 출력이 `less`에서 임의의 폭으로 접혀도 무너지지 않게 만든다.

**Architecture:** 불변식은 하나다 — **렌더 출력의 어떤 줄도 줄머리 위치에 의미를 싣지 않는다.** 여기서 네 변경이 나온다: 수평선의 폭 채우기 폐지, 제목을 자르지 않고 접기, 코드 펜스의 `│ ` 거터·테두리 폐지, 표가 폭에 안 들어가면 카드형으로 전환. 변경 범위는 `scripts/markdown_render.py` 단독이고 `tui.py`·`reading.py`는 손대지 않는다.

**Tech Stack:** Python 3 표준 라이브러리만. 테스트는 `unittest`.

**설계 문서:** `docs/superpowers/specs/2026-08-12-terminal-width-design.md`

## Global Constraints

- **외부 패키지 금지.** Python 표준 라이브러리만 쓴다. `pip`/`npm`/빌드 시스템이 없고 CI도 PyPI에서 아무것도 설치하지 않는다.
- **`markdown_render.py`는 I/O를 하지 않는다.** 파일도, 프로세스도, 터미널 크기 조회도 없다. `width`와 `color`는 인자로만 받는다.
- **3층 순서 계약 유지:** `inline_spans` → `layout` → `paint`. ANSI는 반드시 폭 계산이 끝난 뒤에 붙는다.
- **`color=False`면 출력에 `\x1b`가 하나도 없어야 한다.**
- **8색 ANSI만.** 256색·트루컬러 금지. 새 스타일 이름을 만들지 말고 기존 `SGR` 키(`h1` `h2` `h3` `bold` `code` `fence` `th` `link` `dim`)를 쓴다.
- **주석과 독스트링은 한국어.** 저장소 전체 규약이다.
- **테스트 실행:** `python3 -m unittest discover -s tests` (저장소 루트에서).
- 손대지 않는 것: `scripts/tui.py`, `scripts/reading.py`, `text_width()`, `page_text()`, `pager_supports_color()`.

---

## File Structure

| 파일 | 책임 | 이 계획에서 |
|---|---|---|
| `scripts/markdown_render.py` | 마크다운 → ANSI 평문 변환 (473줄) | Task 1~4에서 수정 |
| `tests/test_markdown_render.py` | 렌더러 단위·속성 테스트 (480줄) | Task 1~5에서 수정·추가 |
| `CLAUDE.md` | 저장소 규약 문서 | Task 6에서 수정 |

새 파일은 만들지 않는다. 렌더러는 이미 층이 나뉘어 있고 473줄이라 분할이 필요하지 않다. Task 4에서 `_emit_table`이 두 함수로 갈리는 것이 유일한 구조 변경이며, 이는 "정렬 표"와 "카드형"이 서로 다른 책임이기 때문이다.

**Task 순서가 중요하다.** Task 3(펜스)이 `RealChapterTest.test_no_rendered_line_exceeds_the_width`를 깨뜨리므로 같은 Task 안에서 고친다. Task 5의 속성 테스트는 Task 1~4가 모두 끝나야 통과한다.

---

## Task 1: 수평선 — 폭 채우기 폐지

가장 작고 독립적인 변경이다. 여기서 워크플로(테스트 먼저 → 실패 확인 → 구현 → 통과 확인 → 커밋)를 익히고 넘어간다.

**Files:**
- Modify: `scripts/markdown_render.py` — 상수 추가(`_MARKERS` 근처), `render()`의 `_HR_RE` 분기 (현재 462-464행)
- Test: `tests/test_markdown_render.py` — `RuleAndCommentTest` (현재 156-173행)

**Interfaces:**
- Consumes: 없음 (첫 Task)
- Produces: 모듈 상수 `_RULE_WIDTH = 24` — Task 5의 속성 테스트가 이 값을 참조한다

**배경:** 지금은 `"─" * width`라 폭을 정확히 채운다. 터미널이 그보다 좁아지면 `less`가 접어 구분선 하나가 두 줄이 된다. 24칸으로 고정하면 가장 좁은 지원 폭(`tui.text_width()`의 하한 40)에서도 안 접힌다.

**주의:** `render()`는 첫 줄에서 `width = max(20, width)`로 클램프한다. 그래서 기존 테스트 두 개(`width=20`)는 `min(20, 24) == 20`이라 **고치지 않아도 통과한다.** 이름만 부정확해지므로 이름을 바로잡고, 24를 넘는 폭에서 실제로 달라지는지 보는 케이스를 새로 넣는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_markdown_render.py`의 `RuleAndCommentTest` 안, `test_a_horizontal_rule_spans_the_width`와 `test_asterisk_rules_count_too`를 아래 세 메서드로 **바꿔 쓴다**(둘을 지우고 셋을 넣는다).

```python
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
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.RuleAndCommentTest -v`
Expected: `test_a_wide_rule_stops_at_the_fixed_length`가 `AttributeError: module 'markdown_render' has no attribute '_RULE_WIDTH'`로 FAIL. 나머지 둘은 PASS.

- [ ] **Step 3: 구현한다**

`scripts/markdown_render.py`의 `_MARKERS` 정의 바로 아래(현재 198행 다음)에 상수를 넣는다.

```python
# 수평선 길이. **폭을 채우지 않는다** — 채우면 터미널이 좁아지는 순간 `less` 가
# 접어 구분선 하나가 두 줄이 된다. 가장 좁은 지원 폭(`tui.text_width()` 하한
# 40)에서도 안 접히면서 구분선 구실을 하는 길이다.
_RULE_WIDTH = 24
```

`render()`의 `_HR_RE` 분기(현재 462-464행)를 바꾼다.

```python
        if _HR_RE.match(line):
            out.append(paint([("─" * min(width, _RULE_WIDTH), "dim")], color))
            continue
```

- [ ] **Step 4: 테스트를 돌려 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: 전부 PASS. `test_a_bullet_is_not_mistaken_for_a_rule`은 `"─" * 10`이 없음을 보는 테스트라 24칸 구분선과 무관하게 그대로 통과한다.

- [ ] **Step 5: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Stop filling the width with horizontal rules

A rule drawn at exactly the render width becomes two lines the moment the
terminal is narrower than that width. Twenty-four columns fits the
narrowest supported width and still reads as a divider."
```

---

## Task 2: 제목 — 자르지 말고 접는다

**Files:**
- Modify: `scripts/markdown_render.py` — `_emit_heading` (현재 207-223행)
- Test: `tests/test_markdown_render.py` — `HeadingTest` (현재 115-138행)에 메서드 추가

**Interfaces:**
- Consumes: `layout(spans, width)`, `paint(spans, color)` — 기존 함수, 시그니처 변경 없음
- Produces: `_emit_heading(level, body, width, color, out)` — 시그니처 그대로. 이제 `out`에 **한 줄이 아니라 여러 줄**을 넣을 수 있다

**배경:** 현재 코드는 제목을 `fit(text, width)`로 **잘라낸다.** 실측: 제목 322개의 최대 폭이 73칸이라 40·60칸 터미널에서 꼬리가 조용히 사라진다. 예를 들어 폭 40에서

```
### 시나리오: "프로덕션 DB CPU 100%, 애플리케이션 전면 5xx" (PostgreSQL 기준)
```

이 `· 시나리오: "프로덕션 DB CPU 100%, 애플`로 끝난다. 렌더러의 유일한 실제 데이터 손실이다.

**이건 실수가 아니라 근거가 적힌 결정이었다.** 현재 주석이 남긴 근거는 셋이다.

> 제목은 접지 않고 자른다 — 접힌 제목은 본문과 구분이 안 된다. 자르지 않으면 `less` 가 접어서 같은 결과가 되고, 폭 회귀 테스트도 깨진다.

둘은 이 변경으로 해소된다. "`less`가 접어서 같은 결과"는 `layout()`으로 접으면 **제목 스타일이 모든 줄에 실리므로** 더 이상 같은 결과가 아니고, "폭 회귀 테스트가 깨진다"는 `layout()`이 폭 안에서 접으므로 발생하지 않는다. 남는 하나 — 무색 페이저에서 접힌 제목이 본문과 구분되는가 — 는 **의도적으로 받아들이는 대가**다: 무색 + 좁은 폭에서 "구분이 약한 두 줄"이 "조용히 사라진 꼬리"보다 낫다.

**핵심 구현 요령:** h1의 `━━━ 제목 ━━━`을 통째로 `layout()`에 넘기면 된다. `_words()`가 공백으로 나누므로 `━━━`·제목 단어들·`━━━`이 각각 단어가 되고, 여는 막대는 첫 줄에 닫는 막대는 마지막 줄에 자연스럽게 놓인다. 별도 분기가 필요 없다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_markdown_render.py`의 `HeadingTest` 안, `test_a_heading_is_preceded_by_a_blank_line` 다음에 넣는다.

```python
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
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.HeadingTest -v`
Expected: 세 테스트 모두 FAIL. `test_a_long_heading_wraps_instead_of_losing_its_tail`은 `"5xx"`가 잘려 나가 `AssertionError`, 나머지 둘은 출력이 한 줄뿐이라 `assertGreater`에서 실패.

- [ ] **Step 3: 구현한다**

`scripts/markdown_render.py`의 `_emit_heading`(현재 207-223행) 전체를 바꾼다.

```python
def _emit_heading(level, body, width, color, out):
    """제목은 인라인 스타일을 버리고 통째로 한 스타일을 입는다.

    제목 안의 `` `코드` `` 를 따로 색칠하면 제목 색과 싸워서 오히려 단계가
    안 읽힌다. 기호만 떼고 제목 색으로 통일한다.

    **자르지 않고 접는다.** 예전에는 `fit` 으로 잘랐고 그 근거는 "접힌 제목은
    본문과 구분이 안 된다" 였다. 접힌 줄마다 제목 스타일을 실으면 색이 있는
    한 구분되고, 무색에서는 구분이 약해지지만 **꼬리가 조용히 사라지는 것보다
    낫다** — 실측으로 제목 322개의 최대 폭이 73칸이라 40·60칸에서 잘려 나갔다.

    h1 의 `━━━ … ━━━` 은 통째로 `layout` 에 넘긴다. 막대가 공백으로 분리된
    단어라 여는 막대는 첫 줄, 닫는 막대는 마지막 줄에 자연히 놓인다.
    """
    plain_body = "".join(t for t, _ in inline_spans(body, color))
    _blank(out)
    if level == 1:
        bar = "━" * 3
        text, style = f"{bar} {plain_body} {bar}", "h1"
    else:
        prefix, style = ("◆ ", "h2") if level == 2 else ("· ", "h3")
        text = prefix + plain_body
    for row in layout([(text, style)], width):
        out.append(paint(row, color))
```

- [ ] **Step 4: 테스트를 돌려 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: 전부 PASS. 기존 `HeadingTest` 네 개는 폭 60에 짧은 제목이라 접히지 않아 동작이 같다. 깨진다면 변경이 의도보다 넓게 퍼진 것이다.

`fit`이 이제 `_emit_heading`에서 안 쓰이지만 `_emit_hanging`·`_split_word`·`_emit_fence`가 여전히 쓰므로 import는 그대로 둔다.

- [ ] **Step 5: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Wrap long headings instead of truncating them

Headings were the one place the renderer dropped content: at forty
columns a scenario heading ended mid-word and the tail was gone. The
original reason for truncating was that a wrapped heading reads as body
text, so carry the heading style onto every wrapped line."
```

---

## Task 3: 코드 펜스 — 거터·테두리 폐지

**Files:**
- Modify: `scripts/markdown_render.py` — `_emit_fence` (현재 410-437행), 상수 추가
- Test: `tests/test_markdown_render.py` — `FenceTest` (현재 191-244행), `RealChapterTest` (현재 453-471행)

**Interfaces:**
- Consumes: `_RULE_WIDTH`는 쓰지 않는다. `fit`, `paint`, `_blank` — 기존 함수
- Produces: 모듈 상수 `_FENCE_INDENT = "    "` — Task 5의 속성 테스트가 참조한다

**배경:** 현재 `_emit_fence`는 `┌─ sql ────` / 줄마다 `│ ` 거터 / `└────`로 상자를 그리고, 폭을 넘는 코드 줄은 `fit(body, width - 2)`로 **폭 단위 강제 개행**한다. 두 가지가 걸린다.

1. **거터는 접히면 어차피 무너진다.** `less`가 소프트 랩할 때 SGR 속성은 유지되지만 줄머리 글리프는 이어지는 줄에 다시 찍히지 않는다. 접힌 코드 줄이 블록 밖처럼 보인다.
2. **강제 개행이 원문을 훼손한다.** 실측으로 코드 줄 1,867개 중 80칸에서 6%, 60칸에서 25%, 40칸에서 49%가 토큰 중간에서 끊긴다. 화면에서 복사한 명령이 실행되지 않는다.

거터를 버리고 들여쓰기 4칸 + `fence` 색으로 구분하면 둘 다 사라진다. 4칸인 이유는 챕터의 리스트 들여쓰기가 2·3칸 대역이라 겹치지 않고, 마크다운의 indented code block 관습과 맞기 때문이다.

**대가:** 코드 줄은 이 모듈에서 **유일하게 `width`를 넘을 수 있다.** 접는 일을 `less`에 맡긴다. 무색 페이저에서는 블록 경계가 라벨 줄 + 들여쓰기로만 남는다 — 지금은 `│`가 확실했다. 실측상 저장소의 펜스 259개가 **전부** 언어 태그를 가지고 있어(sql 126, bash 92, ini 13, text 10, yaml 7, markdown 5, hcl 5, conf 1) 라벨 줄이 항상 나온다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_markdown_render.py`의 `FenceTest` 전체(현재 191-244행)를 아래로 **바꿔 쓴다**.

```python
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
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.FenceTest -v`
Expected: 전부 FAIL — `AttributeError: module 'markdown_render' has no attribute '_FENCE_INDENT'`.

- [ ] **Step 3: 구현한다**

`scripts/markdown_render.py`의 `_RULE_WIDTH` 아래에 상수를 넣는다.

```python
# 코드 펜스 본문 들여쓰기. 챕터의 리스트 들여쓰기는 2·3칸 대역이라 겹치지 않고,
# 마크다운의 indented code block 관습과 맞는다.
_FENCE_INDENT = "    "
```

`_emit_fence`(현재 410-437행) 전체를 바꾼다.

```python
def _emit_fence(lines, i, width, color, out):
    """` ```lang ` 블록을 들여쓴 덩어리로 낸다 → 다음에 볼 줄 번호.

    블록 안에서는 마크업을 **전혀** 해석하지 않는다. SQL 주석 `--`, bash 의
    `## `, C 의 `**ptr` 이 그대로 들어 있다 — `filter_dbms.filter_lines` 가
    `in_fence` 를 추적하는 것과 같은 이유다.

    **테두리 상자와 `│ ` 거터를 쓰지 않는다.** 예전에는 상자를 그리고 폭을
    넘는 줄을 `fit` 으로 강제 개행했는데, 둘 다 같은 곳에서 무너졌다. 거터는
    `less` 가 접는 순간 이어지는 줄에 다시 찍히지 않아 코드가 블록 밖처럼
    보이고, 강제 개행은 원문을 토큰 중간에서 끊어(실측 코드 줄 1,867개 중
    80칸 6%, 60칸 25%, 40칸 49%) 화면에서 복사한 명령이 실행되지 않게 만든다.
    들여쓰기와 색으로 구분하면 둘 다 사라진다.

    그래서 **코드 줄은 이 모듈에서 유일하게 `width` 를 넘을 수 있다.** 접는
    일은 `less` 에 맡긴다. 언어 태그가 없으면 라벨 줄도 내지 않는다 — 뜻 없는
    라벨을 지어내는 쪽이 더 나쁘다(실측상 저장소의 펜스 259개는 전부 태그가
    있어 이 경로는 새 챕터를 위한 방어다).
    """
    lang = _FENCE_RE.match(lines[i]).group(1)
    i += 1
    _blank(out)
    if lang:
        out.append(paint([(fit(lang, width), "dim")], color))
    while i < len(lines) and not _FENCE_RE.match(lines[i]):
        body = lines[i].rstrip()
        # 빈 줄에 들여쓰기만 남기지 않는다 — 복사하면 공백이 딸려 온다.
        out.append(paint([(_FENCE_INDENT + body, "fence")], color)
                   if body else "")
        i += 1
    _blank(out)
    # 닫는 펜스가 없으면(파일 끝) i 는 이미 끝이다 — 한 칸 더 넘겨도 안전하다.
    return i + 1
```

- [ ] **Step 4: 테스트를 돌려 `RealChapterTest`가 깨지는 것을 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: `FenceTest`는 전부 PASS. `RealChapterTest.test_no_rendered_line_exceeds_the_width`가 FAIL — 코드 줄이 이제 의도적으로 폭을 넘기 때문이다. **이건 예상된 실패이고, 다음 스텝에서 계약을 다시 쓴다.**

- [ ] **Step 5: 폭 보장 테스트에 펜스 예외를 새긴다**

`tests/test_markdown_render.py`의 `RealChapterTest`(현재 453-471행)에서, 클래스 위에 헬퍼를 넣고 `test_no_rendered_line_exceeds_the_width`를 바꾼다.

```python
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
```

```python
    def test_no_rendered_line_exceeds_the_width(self):
        """코드 펜스 본문을 **제외한** 모든 줄이 폭 안에 들어온다.

        예외는 하나뿐이고 의도된 것이다 — `_emit_fence` 가 코드를 원문 그대로
        내보내므로 긴 명령은 폭을 넘고 `less` 가 접는다.
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
```

- [ ] **Step 6: 테스트를 돌려 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: 전부 PASS. 표는 아직 압축 경로라(Task 4 전) 폭 안에 들어온다.

- [ ] **Step 7: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Drop the code fence box so code survives a reflow

The gutter never reached a soft-wrapped continuation line, so a wrapped
code line already read as being outside the block. Avoiding that with a
width-sized hard wrap cut real commands mid-token — measured at 49% of
code lines on a forty-column terminal. Indentation and colour carry the
distinction instead, and code lines become the one place a rendered line
may exceed the width."
```

---

## Task 4: 표 — 안 들어가면 카드형

이 계획에서 가장 큰 Task다. `_emit_table`이 두 함수로 갈리고, 압축 전용 코드가 죽는다.

**Files:**
- Modify: `scripts/markdown_render.py` — `_fit_columns` 삭제 (현재 330-339행), `_MIN_COL` 삭제 (현재 193행), `_emit_table` 재작성 (현재 355-402행), 상수 추가
- Test: `tests/test_markdown_render.py` — `TableTest` (현재 333-450행)

**Interfaces:**
- Consumes: `layout`, `paint`, `swidth`, `inline_spans`, `_pad`, `_alignments`, `_cells`, `_blank` — 기존 함수
- Produces:
  - `_CARD_MARK = "▸ "` — 카드 제목 표지. Task 5의 속성 테스트가 참조한다
  - `_emit_table_aligned(spans, cols, aligns, color, out) -> None`
  - `_emit_table_cards(spans, width, color, out) -> None`
  - `_emit_table(lines, i, width, color, out) -> int` — 시그니처 그대로

**배경:** 실측으로 표 **44개**의 자연폭 중앙값이 **130칸**(최대 192칸)이다. 기본 80칸 터미널에서도 **36/44(82%)** 가 압축된다. 압축은 40칸에서 `PostgreSQL`을 `PostgreSQ`/`L`로 쪼개고 `localhost:` / `1521/XEPDB` / `` 1` `` 처럼 셀을 토큰 중간에서 끊는다. 게다가 공백 채움으로 만든 정렬은 `less`가 접는 순간 전부 어긋난다.

그래서 규칙을 바꾼다: **정렬 표를 그릴 수 있으면 그리고, 못 그리면 카드로 편다.** 압축은 하지 않는다.

**이 판정이 코드를 죽인다.** 정렬 표가 자연폭이 들어갈 때만 그려지므로 압축은 영영 일어나지 않는다. `_fit_columns`와 `_MIN_COL`은 압축 전용이었으므로 **삭제한다.**

| 심볼 | 처리 |
|---|---|
| `_fit_columns` | 삭제 — 호출부가 없어진다 |
| `_MIN_COL` | 삭제 — `_fit_columns` 전용 상수였다 |
| `_pad` | 유지 — 정렬 표에서 셀을 자연폭에 맞춰 채운다 |
| `_alignments` | 유지 — 정렬 표에만 적용 |

**카드 표지에 `▸`를 쓰는 이유:** `◆`는 h2가, `·`는 h3가 이미 쓰고 있다. 재사용하면 표 카드가 챕터 제목으로 오독된다.

**구현에서 반드시 걸리는 함정:** `layout()`은 `_words()`를 거치는데 `_words()`는 공백을 **구분자로만** 취급한다. 그래서 `layout([("  ", None), ...], width)`로는 **선행 들여쓰기가 사라진다.** 들여쓰기는 `layout` **뒤에** 붙이고, 폭은 미리 `width - 2`로 줄여야 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_markdown_render.py`의 `TableTest`에서 아래 셋을 **지운다**:
`test_a_wide_table_wraps_inside_its_cells`, `test_a_wrapped_cell_keeps_its_column_start`, `test_a_wrapped_cell_pads_each_continuation_line_by_its_alignment`. 셋 다 셀 접힘을 고정하던 테스트인데 셀 접힘 자체가 사라진다.

그 자리에 새 클래스를 `TableTest` 바로 뒤에 넣는다.

```python
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
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.TableCardTest -v`
Expected: `test_cards_use_a_marker_the_headings_do_not`를 뺀 전부 FAIL — `AttributeError: module 'markdown_render' has no attribute '_CARD_MARK'`, 그리고 `test_compression_is_gone`은 `_fit_columns`가 아직 있어 FAIL.

- [ ] **Step 3: 상수를 넣고 압축 코드를 지운다**

`scripts/markdown_render.py`에서:

(a) `_MIN_COL = 4` 줄(현재 193행)을 **지운다.**

(b) `_FENCE_INDENT` 아래에 카드 표지 상수를 넣는다.

```python
# 표 카드 제목 표지. `◆`(h2)·`·`(h3) 를 재사용하면 표 카드가 챕터 제목으로
# 읽히므로 겹치지 않는 글리프를 쓴다.
_CARD_MARK = "▸ "
```

(c) `_fit_columns` 함수 전체(현재 330-339행)를 **지운다.**

- [ ] **Step 4: 두 렌더 경로를 나눠 쓴다**

`_emit_table`(현재 355-402행) 전체를 아래 세 함수로 바꾼다. `_pad` 다음, `_emit_paragraph` 앞에 놓는다.

```python
def _emit_table_aligned(spans, cols, aligns, color, out):
    """자연폭이 화면에 들어갈 때만 부른다 — 열 폭은 자연폭 그대로다.

    압축하지 않으므로 셀이 접힐 일이 없지만, `layout` 호출은 남긴다 — 셀 안
    줄바꿈을 다루는 유일한 통로이고, 여기서만 쓰는 특수 경로를 새로 만들
    이유가 없다.
    """
    ncols = len(cols)
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


def _emit_table_cards(spans, width, color, out):
    """행 하나를 카드 하나로 편다. 첫 열이 카드 제목, 나머지가 `헤더: 값`.

    공백 채움으로 만든 열 정렬은 `less` 가 접는 순간 전부 어긋난다. 카드형은
    줄머리에 의미를 싣지 않으므로(`헤더:` 가 항목 시작을 알린다) 어느 폭에서
    접혀도 읽힌다.

    **들여쓰기는 `layout` 뒤에 붙인다.** `layout` 은 `_words` 를 거치는데
    `_words` 는 공백을 구분자로만 취급해서, 선행 공백을 span 으로 넘기면 그냥
    사라진다. 그래서 폭을 미리 2칸 줄여 접고 나서 들여쓴다.
    """
    header, body = spans[0], spans[1:]
    ncols = len(header)
    indent = "  "
    _blank(out)
    for row in body:
        title = [(t, s or "th") for t, s in row[0]]
        for line in layout([(_CARD_MARK, "th")] + title, width):
            out.append(paint(line, color).rstrip())
        for j in range(1, ncols):
            if not swidth(row[j]):
                continue                     # 빈 셀은 줄을 만들지 않는다
            # 콜론 뒤 공백이 **반드시** 있어야 한다. `_words` 는 공백으로만
            # 단어를 나누므로, `(":", …)` 로 두면 라벨과 값이 한 단어로 붙어
            # `PostgreSQL:5432` 가 된다(실측).
            label = [(t, s or "th") for t, s in header[j]] + [(": ", "th")]
            for line in layout(label + row[j], width - len(indent)):
                out.append((indent + paint(line, color)).rstrip())
        _blank(out)


def _emit_table(lines, i, width, color, out):
    """파이프 표를 낸다 → 다음에 볼 줄 번호.

    자연폭이 화면에 들어가면 정렬해서, 안 들어가면 카드로 편다. **압축하지
    않는다** — 열을 깎으면 `PostgreSQL` 이 `PostgreSQ`/`L` 로 쪼개지고(실측
    40칸), 채움으로 맞춘 정렬은 `less` 가 접으면 어차피 무너진다.

    dbms 마커 주석이 표 행 사이에 끼어들 수 있다(부록 dbms-branch-strategy 의
    행 단위 marking 작업이 그 경로다). 주석에서 표가 끊기면 남은 행이
    `_is_separator` 검사를 통과하지 못해 문단으로 떨어지고 파이프가 그대로
    화면에 남는다 — 주석은 건너뛰되 행은 계속 모아 표를 안 끊는다.
    """
    header = _cells(lines[i])
    aligns = _alignments(lines[i + 1])
    i += 2
    body = []
    while i < len(lines) and (lines[i].strip().startswith("|")
                              or _COMMENT_RE.match(lines[i])):
        if not _COMMENT_RE.match(lines[i]):
            body.append(_cells(lines[i]))
        i += 1

    rows = [header] + body
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    aligns = (aligns + ["left"] * ncols)[:ncols]

    spans = [[inline_spans(c, color) for c in r] for r in rows]
    natural = [max(swidth(r[j]) for r in spans) for j in range(ncols)]
    if sum(natural) + ncols - 1 <= width:
        _emit_table_aligned(spans, natural, aligns, color, out)
    else:
        _emit_table_cards(spans, width, color, out)
    return i
```

- [ ] **Step 5: 테스트를 돌려 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render -v`
Expected: 전부 PASS.

`TableTest`의 정렬 테스트(`test_right_alignment_pads_before_the_value`, `test_centre_alignment_pads_both_sides`, `test_columns_line_up_on_display_width`)는 픽스처가 좁은 표라 정렬 표 경로로 남아 그대로 통과해야 한다. **깨진다면 카드형 전환 판정이 자연폭보다 공격적으로 걸린 것이다** — `sum(natural) + ncols - 1 <= width`의 부등호와 열 사이 여백 계산을 다시 본다.

`RealChapterTest.test_no_rendered_line_exceeds_the_width`도 통과해야 한다. 카드 값은 `layout`으로 접히므로 폭을 넘지 않는다.

- [ ] **Step 6: 커밋**

```bash
git add scripts/markdown_render.py tests/test_markdown_render.py
git commit -m "Lay tables out as cards when they do not fit

Table natural widths run to a median of 130 columns, so 36 of the 44
tables were already being squeezed on a standard eighty-column terminal —
and padding-based alignment collapses the moment less soft-wraps it
anyway. Draw the aligned table only when it fits and fall back to one
card per row otherwise, which removes the column-squeezing code entirely."
```

---

## Task 5: 접기 안전 속성 테스트

Task 1~4가 각각 자기 계약을 지켰다. 이제 저장소 전체에 대해 **불변식 자체**를 건다.

**Files:**
- Test: `tests/test_markdown_render.py` — 파일 끝, `if __name__ == "__main__":` 앞에 클래스 추가

**Interfaces:**
- Consumes: `mr._RULE_WIDTH` (Task 1), `mr._FENCE_INDENT` (Task 3), `mr._CARD_MARK` (Task 4), `fence_bodies` (Task 3에서 만든 모듈 수준 헬퍼)
- Produces: 없음 (마지막 테스트 Task)

**배경:** `BulletWidthPropertyTest`가 같은 방식(입력 조합을 순회하며 속성을 검사)의 전례를 만들어 두었다. 여기서는 챕터 31개 전량을 픽스처로 쓴다 — 합성 픽스처만으로는 놓치는 것이 있다는 게 `RealChapterTest`의 존재 이유다.

**벤더 브랜치 주의:** 이 테스트는 챕터 파일을 읽는다. 벤더 브랜치에서는 필터로 내용이 줄지만 마크다운 구조는 유지되므로 `skipTest`가 필요 없다(`ShippedContentTest`가 섹션 구조를 건너뛰는 것과는 다른 상황이다).

- [ ] **Step 1: 속성 테스트를 쓴다**

`tests/test_markdown_render.py`의 `RealChapterTest` 다음, `if __name__ == "__main__":` 앞에 넣는다.

```python
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
        """폭을 좁혀도 제목의 어느 단어도 사라지지 않는다."""
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
                got = plain(mr.render(src, width=width, color=False))
                flat = " ".join(got.split())
                for text in headings:
                    for word in text.split():
                        self.assertIn(word, flat,
                                      f"{path.name} @ {width}: {text!r}")

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
                    if line.strip() in code:
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
```

- [ ] **Step 2: 테스트를 돌려 통과를 확인한다**

Run: `python3 -m unittest tests.test_markdown_render.ReflowSafetyTest -v`
Expected: 전부 PASS. Task 1~4가 모두 끝났으므로 새 실패가 나오면 안 된다.

실패하면 어느 Task로 돌아갈지가 테스트 이름에 있다: `..._rules_...` → Task 1, `..._heading_...` → Task 2, `..._code_line_...`/`..._line_head_glyph_...` → Task 3, `..._tables_...` → Task 4.

- [ ] **Step 3: 전체 스위트를 돌린다**

Run: `python3 -m unittest discover -s tests`
Expected: 전부 PASS. 렌더러 밖(`test_reading.py`, `test_check_content.py` 등)은 렌더 출력의 모양에 기대지 않으므로 영향이 없어야 한다. `test_reading.py`가 깨지면 `read_chapter`가 렌더 결과의 특정 글리프를 보고 있다는 뜻이니 그 테스트를 읽고 판단한다.

- [ ] **Step 4: 커밋**

```bash
git add tests/test_markdown_render.py
git commit -m "Pin the reflow-safety invariant across every chapter

Each of the four changes defended its own contract. This asserts the
property they share — that no rendered line carries meaning in its line
head — against all 31 chapters, so a future edit cannot reintroduce a
gutter or a width-filling rule without a test naming the reason."
```

---

## Task 6: 문서 갱신과 실행 검증

**Files:**
- Modify: `CLAUDE.md` — `scripts/markdown_render.py` 단락

**Interfaces:**
- Consumes: Task 1~5의 결과 전부
- Produces: 없음 (마지막 Task)

**배경:** `CLAUDE.md`의 렌더러 단락은 지금 **예외 없는 폭 보장**으로 읽힌다. 코드 펜스 본문이라는 예외가 생겼고, `│ ` 거터와 표 압축이 사라졌다. 문서가 이걸 반영하지 않으면 다음 사람이 지워진 거터를 "버그"로 되돌린다.

모호폭(East Asian Ambiguous) 한계 서술은 **그대로 둔다** — 이번 범위 밖이고 여전히 사실이다.

- [ ] **Step 1: `CLAUDE.md`의 렌더러 단락을 고친다**

`CLAUDE.md`에서 `- \`scripts/markdown_render.py\` — 렌더링` 으로 시작하는 항목을 찾는다. 아래 세 가지를 반영해 고쳐 쓴다. **기존 문장 중 여전히 참인 것은 그대로 둔다** — 3층 순서 계약, "뷰어가 아니다", `tui.cwidth`로 폭을 잰다, I/O 없음, 색 판정은 `tui.pager_supports_color()`, 4칸 밴드 중첩 깊이, 모호폭 한계.

고칠 내용:

1. **폭 보장에 예외가 생겼다.** 코드 펜스 본문은 원문 그대로 나가므로 `width`를 넘을 수 있고, 접는 일은 `less`가 한다. 예전에는 `│ ` 거터를 두고 폭 단위로 강제 개행했는데, 거터가 소프트 랩된 줄에 다시 찍히지 않아 어차피 무너지는 데다 강제 개행이 원문을 토큰 중간에서 끊어(실측 40칸에서 코드 줄의 49%) 복사한 명령이 실행되지 않았다.
2. **표는 압축하지 않는다.** 자연폭이 들어가면 정렬 표, 안 들어가면 카드형(`▸ 제목` + `  헤더: 값`)이다. 표 44개의 자연폭 중앙값이 130칸이라 기본 80칸에서도 36개가 카드형이 된다 — 치트시트의 "한 화면 비교" 성격은 넓은 터미널에서만 유지된다는 뜻이고, 이는 의도된 대가다.
3. **제목은 자르지 않고 접는다.** 접힌 줄마다 제목 스타일이 실려 본문과 구분된다.

글리프 목록(`─ │ ┌ └ ━ • · ◆`)이 나오는 모호폭 문장이 있다면 실제로 남은 글리프로 맞춘다 — `┌`·`└`는 사라졌고 `▸`가 늘었다. **853줄이라는 수치는 이번 변경으로 달라졌을 수 있으므로, 고쳐 쓰기 전에 아래 명령으로 다시 잰다.**

```bash
python3 - <<'PY'
import sys, re, pathlib, unicodedata
sys.path.insert(0, "scripts"); import markdown_render
def cwidth2(t):
    return sum(2 if unicodedata.east_asian_width(c) in ("W","F","A") else 1 for c in t)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
chapters = sorted(p for d in ("01-beginner","02-intermediate","03-advanced","appendix")
                  for p in pathlib.Path(d).glob("*.md"))
n = sum(1 for p in chapters
        for ln in markdown_render.render(p.read_text(encoding="utf-8"), width=80, color=False).split("\n")
        if cwidth2(ANSI.sub("", ln)) > 80)
print("ambiguous=2 가정, width=80 초과 줄:", n)
PY
```

측정한 수치로 문장을 갱신한다. **재지 않고 853을 그대로 두면 안 된다.**

- [ ] **Step 2: 전체 테스트를 돌린다**

Run: `python3 -m unittest discover -s tests`
Expected: 전부 PASS.

- [ ] **Step 3: 실제 화면으로 확인한다**

`./guide` → "챕터 읽기"로 아래를 눈으로 본다. 자동 테스트가 잡지 못하는 것은 "읽히는가"뿐이다.

| 확인할 것 | 어디서 |
|---|---|
| 4열 표의 카드형 전환 | `01-beginner/07-commands-cheatsheet.md` |
| 가장 넓은 표(192칸) | `appendix/dbms-comparison-matrix.md` |
| 들여쓴 코드 블록 — 마우스로 긁어 복사한 명령이 온전한가 | `03-advanced/` 중 코드가 긴 챕터 |
| **읽는 중 터미널 폭을 절반으로 줄여 접힘 확인** | 아무 챕터. 이번 작업의 핵심 목표다 |

그리고 무색 경로를 확인한다.

```bash
PAGER=more ./guide
NO_COLOR=1 ./guide
```

여기서 볼 것은 둘이다. (a) 코드 블록 경계가 라벨 줄 + 들여쓰기로 식별되는가, (b) 좁은 폭에서 접힌 긴 제목이 본문으로 오독되지 않는가. **(b)가 Task 2에서 의식적으로 받아들인 대가이므로, 실제로 견딜 만한지 여기서 판단한다.** 못 견딜 수준이면 그건 새 이슈이지 이 계획의 롤백 사유가 아니다 — 자르는 쪽이 더 나빴다는 판단은 그대로다.

- [ ] **Step 4: 커밋**

```bash
git add CLAUDE.md
git commit -m "Record the renderer's new width contract

The width guarantee now has exactly one exception — code fence bodies go
out verbatim and less does the folding — and tables fall back to cards
rather than squeezing columns. Left undocumented, the removed gutter
reads as a regression to whoever touches this next."
```

---

## Self-Review

**1. 스펙 커버리지**

| 스펙 절 | 구현 Task |
|---|---|
| 2. 코드 펜스 — 거터·테두리 폐지 | Task 3 |
| 3. 표 — 안 들어가면 카드형 (+ `_fit_columns`/`_MIN_COL` 삭제) | Task 4 |
| 4. 제목 — 자르지 말고 접는다 | Task 2 |
| 5. 구분선 — 폭 채우기 폐지 | Task 1 |
| 6. 검증 — `ReflowSafetyTest` 6개 | Task 5 (스펙의 6개 항목이 6개 메서드로 대응) |
| 6. 검증 — 갱신되는 기존 테스트 11개 | Task 1(2개, 이름 교정), Task 3(6개), Task 4(3개 삭제 + 대체) |
| 6. 검증 — 실행 검증 | Task 6 Step 3 |
| 6. 검증 — 문서 | Task 3(`_emit_fence` 독스트링), Task 6(`CLAUDE.md`) |

**스펙과 계획이 갈린 곳 하나:** 스펙은 `test_a_horizontal_rule_spans_the_width`와 `test_asterisk_rules_count_too`가 "갱신된다"고 썼지만, `render()`가 `width = max(20, width)`로 클램프하므로 두 테스트(폭 20)는 `min(20, 24) == 20`이라 **고치지 않아도 통과한다.** 계획에서는 이름만 바로잡고 24를 넘는 폭 케이스를 새로 넣는 것으로 낮췄다.

**2. 플레이스홀더 스캔** — 없음. 모든 코드 스텝이 실제 코드를 담고 있고, Task 6 Step 1만 산문 지시인데 무엇을 고칠지 세 항목으로 못박고 재측정 명령까지 붙였다.

**3. 프로토타입으로 확인한 사실** — 계획을 쓰면서 아래 넷을 실제로 돌려 봤다. 문자열 단정을 감으로 쓰지 않기 위해서다.

| 확인한 것 | 결과 |
|---|---|
| `layout([(_CARD_MARK, "th")] + title, width)` | `▸ 기본 포트` — `_words`가 공백을 정규화해 한 칸으로 만든다 |
| 카드 라벨의 콜론 | `(":", …)`면 `PostgreSQL:5432`로 **붙는다.** `(": ", …)`여야 `PostgreSQL: 5432` |
| h1 접힘 | 폭 40에서 3줄, 첫 줄이 `━━━`로 시작하고 마지막 줄이 `━━━`로 끝난다. 별도 분기 불필요 |
| 펜스 원문 보존 | 챕터 31개 전량에서 누락 0. 원문 들여쓰기(`        email VARCHAR(100) …`)까지 유지된다 |

**4. 타입 일관성**

- `_emit_table_aligned(spans, cols, aligns, color, out)` — `_emit_table`의 호출 `(spans, natural, aligns, color, out)`과 일치. `width`를 받지 않는다(자연폭이 이미 들어간다는 것이 전제이므로 필요 없다).
- `_emit_table_cards(spans, width, color, out)` — 호출과 일치.
- `fence_bodies(src) -> set[str]` — Task 3에서 모듈 수준 함수로 정의하고 Task 5가 그대로 쓴다. **렌더된 형태**(`_FENCE_INDENT + line.rstrip()`)를 담으므로 Task 3·Task 5 양쪽에서 벗기지 않고 `line in code`로 비교한다. `mr._FENCE_INDENT`를 참조하니 Task 3에서 상수가 먼저 정의돼야 한다(같은 Task 안이라 순서가 보장된다).
- 상수 세 개(`_RULE_WIDTH`, `_FENCE_INDENT`, `_CARD_MARK`)는 정의한 Task와 참조하는 Task가 모두 명시되어 있다.
- `_CARD_MARK`는 `"▸ "`(뒤 공백 포함)이고 테스트는 `.strip()`으로 글리프만 비교하거나 `f"{mr._CARD_MARK}기본 포트"`로 통째 비교한다 — `layout`이 공백을 정규화해 한 칸으로 되돌리므로 후자가 맞는다.
