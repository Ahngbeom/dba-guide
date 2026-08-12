# 챕터 읽기 — 시험 제안을 목록 동작 키로 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 챕터를 다 읽으면 아무 질문 없이 목록으로 돌아가고, 그 챕터의 시험은 목록에서 `x` 키로 시작한다.

**Architecture:** 공용 선택기 `tui.pick`에 `actions` 파라미터를 더해 "추가 동작 키"를 만든다. `actions`를 준 호출부만 `Picked(index, action)`을 받고, 나머지 호출부 6곳은 한 글자도 고치지 않는다. `reading`은 챕터 목록에 시험 유무·지난 기록 라벨을 붙이고, `x`를 받으면 그 챕터의 시험으로 넘긴다. 막는 프롬프트 `offer_exam`은 통째로 삭제한다.

**Tech Stack:** Python 3 표준 라이브러리만 (`curses`, `collections.namedtuple`, `unittest`).

**설계 문서:** [`docs/superpowers/specs/2026-08-12-reading-exam-handoff-design.md`](../specs/2026-08-12-reading-exam-handoff-design.md) — 각 결정의 근거는 거기 있다.

**선행 작업:** 이 계획은 이슈 #95 브랜치(`Ahngbeom/issue-95`, PR #97) 위에 이어서 쌓는다. 그 작업이 만든 `tui.QuitApp`, `tui.pick`의 `allow_quit`, `page_text`의 튜플 반환, `reading.read_chapter`의 `printed` 반환이 모두 이미 있다.

## Global Constraints

- **Python 표준 라이브러리만.** `pip`/`npm`/빌드 시스템이 없고 CI도 PyPI에서 아무것도 설치하지 않는다.
- **테스트는 tty를 요구하지 않는다.** CI는 파이프로 돈다(ubuntu × Python 3.9/3.13, macOS). curses가 필요한 코드는 가짜 `stdscr`·가짜 `curses`를 주입해 검사한다.
- **Python 3.9에서 동작해야 한다** — 3.10+ 문법 금지.
- **모든 새 주석·docstring·화면 문구는 한국어.**
- **테스트 실행 명령은 하나뿐이다:** `python3 -m unittest discover -s tests` (저장소 루트에서). **시작 기준선(실측): 790개 통과, 약 33초.**
- **`README.md`를 고치면 `python3 scripts/check_content.py`가 exit 0이어야 한다.**
- **`scripts/exam.py`는 건드리지 않는다.** 이 작업은 `exam`의 공개 함수(`read_results`, `best_result_for`, `exam_bank_for`, `main`)를 **읽기만** 한다.
- **`tui.pick_line`(비-tty 평문 선택기)에 동작 키를 넣지 않는다.** `exam._pick_line`이 감싸 쓰므로 비대칭이 생긴다.
- **`shooting._choose_stage_curses`(3126행)는 죽은 코드다** — 제거하지도 수정하지도 않는다.
- 커밋 메시지는 영어 명령형 한 줄 + 본문. 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## 파일 구조

| 파일 | 이 계획에서의 책임 |
|---|---|
| `scripts/tui.py` | `Picked` 네임드튜플 + `pick`의 `actions` 파라미터. 다른 러너가 공유하는 유일한 변경점 |
| `scripts/reading.py` | 라벨 생성(`chapter_labels`), 시험 핸드오프(`run_exam`), 흐름(`main`), `choose`의 `actions` 전달. `offer_exam` 삭제 |
| `tests/test_tui.py` | `actions` 계약 |
| `tests/test_reading.py` | 라벨·흐름·footer |
| `README.md` · `CLAUDE.md` | 새 조작법 |

---

## Task 1: `tui.pick`의 동작 키

**Files:**
- Modify: `scripts/tui.py` (import 블록, `pick()` 369–441행)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `tui.QuitApp`, `tui.key_char`, `tui.is_enter` (모두 이미 있다)
- Produces:
  - `tui.Picked` — `namedtuple("Picked", "index action")`
  - `tui.pick(stdscr, curses, title, labels, footer=None, allow_cancel=True, allow_quit=True, actions="")` — `actions`가 빈 문자열(기본)이면 반환은 지금과 **완전히 동일**(`int` 또는 `None`). 비어 있지 않으면 일반 선택은 `Picked(index, None)`, 동작 키는 `Picked(index, key)`, 취소는 `None`, `Q`는 `QuitApp`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_tui.py`의 `class PageTextCharacterizationTest` **바로 앞**에 추가한다:

```python
class PickActionsTest(unittest.TestCase):
    """`actions`를 주면 추가 동작 키가 생기고 반환 모양이 바뀐다.

    반환 모양이 둘인 것은 냄새지만, 호출부는 자기가 `actions`를 넘겼는지 항상
    알고 있으므로 어느 모양을 받을지 헷갈릴 여지가 없다. 선택기를 한 벌 더
    만드는 쪽은 `pick`이 세 벌로 갈라졌다가 합쳐진 전례를 되풀이한다.
    """

    LABELS = ["primary", "replica", "arbiter"]

    def _pick(self, keys, **kw):
        screen = _PickScreen(keys)
        return tui.pick(screen, _PickCurses(), "고르세요", self.LABELS, **kw)

    def test_without_actions_the_return_shape_is_unchanged(self):
        """기존 호출부 6곳이 한 글자도 안 고쳐도 되는 근거."""
        got = self._pick([10])
        self.assertIsInstance(got, int)
        self.assertEqual(got, 0)

    def test_enter_returns_picked_with_no_action(self):
        self.assertEqual(self._pick([10], actions="x"), tui.Picked(0, None))

    def test_a_number_key_returns_picked_with_no_action(self):
        self.assertEqual(self._pick([ord("2")], actions="x"),
                         tui.Picked(1, None))

    def test_the_action_key_carries_the_highlighted_row(self):
        """동작은 '지금 커서가 놓인 줄'에 대한 것이다."""
        got = self._pick([_PickCurses.KEY_DOWN, _PickCurses.KEY_DOWN,
                          ord("x")], actions="x")
        self.assertEqual(got, tui.Picked(2, "x"))

    def test_an_unlisted_letter_is_ignored(self):
        """`z`는 동작 키가 아니다 — 무시되고 계속 고르게 된다."""
        self.assertEqual(self._pick([ord("z"), 10], actions="x"),
                         tui.Picked(0, None))

    def test_the_action_key_is_case_sensitive(self):
        """`actions="x"`가 `X`까지 삼키면 안 된다.

        대소문자를 보존한 `raw`로 비교해야 한다 — `Q`(전역 종료)를 소문자로
        접기 전에 보는 것과 같은 자리다.
        """
        self.assertEqual(self._pick([ord("X"), 10], actions="x"),
                         tui.Picked(0, None))

    def test_the_string_representation_of_the_action_key_works_too(self):
        """`read_key(wide=True)`는 정수 대신 문자열을 준다."""
        self.assertEqual(self._pick(["x"], actions="x"), tui.Picked(0, "x"))

    def test_cancel_still_returns_none(self):
        self.assertIsNone(self._pick([ord("q")], actions="x"))

    def test_quit_still_raises(self):
        with self.assertRaises(tui.QuitApp):
            self._pick([ord("Q")], actions="x")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_tui.PickActionsTest -v 2>&1 | tail -20`

Expected: FAIL — `AttributeError: module 'tui' has no attribute 'Picked'` 및 `TypeError: pick() got an unexpected keyword argument 'actions'`.
(`test_without_actions_the_return_shape_is_unchanged`만 이미 통과한다 — 그건 회귀 방어용이라 의도된 것이다.)

- [ ] **Step 3: `Picked`를 정의한다**

`scripts/tui.py`의 import 블록에 표준 라이브러리 import를 하나 더한다. 현재 `import os`부터 `import unicodedata`까지 알파벳 순이므로 맨 앞에 넣는다:

```python
from collections import namedtuple
import os
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata
```

그리고 기존 `class QuitApp(BaseException):` 블록 **바로 뒤**(전역 종료 신호 구분선 섹션의 끝, 표시 폭 계산 구분선 앞)에 추가한다:

```python
# `pick`이 동작 키(`actions`)를 받았을 때 돌려주는 값.
# `action`은 눌린 동작 키이거나, 일반 선택(Enter·숫자)이면 `None`이다.
Picked = namedtuple("Picked", "index action")
```

- [ ] **Step 4: `pick`에 `actions`를 단다**

시그니처를 바꾼다:

```python
def pick(stdscr, curses, title, labels, footer=None,
         allow_cancel=True, allow_quit=True, actions=""):
```

docstring 끝(`"""` 닫기 직전, `allow_quit` 문단 뒤)에 덧붙인다:

```
    `actions`에 글자를 주면 그 키들이 **추가 동작**이 된다. 그때 반환값은
    `int`가 아니라 `Picked(index, action)`이다 — 일반 선택이면 `action`이
    `None`, 동작 키면 그 글자다. 취소는 여전히 `None`, `Q`는 여전히 `QuitApp`.
    반환 모양이 둘인 것은 호출부가 `actions`를 넘겼는지 스스로 알기 때문에
    감당할 만하다고 본 것이다 — 선택기를 한 벌 더 만드는 쪽은 이 함수가 세
    벌로 갈라졌다가 합쳐진 전례를 되풀이한다.

    동작 키는 대소문자를 **보존해** 비교한다. `actions="x"`가 `X`까지 삼키면
    안 된다. 동작 키가 이동 키(`k`/`j`)나 숫자와 겹치면 그쪽이 죽는다 —
    호출부 책임이다.
```

`if not labels: return None` **바로 뒤**, `sel = 0` 앞에 헬퍼를 넣는다:

```python
    def _result(index, action=None):
        """`actions`를 준 호출부에만 `Picked`를 돌려준다."""
        return Picked(index, action) if actions else index
```

그리고 키 루프의 세 반환 지점을 그것으로 감싼다. **현재:**

```python
        if is_enter(key):
            return sel

        raw = key_char(key) or ""
        if allow_quit and raw == "Q":       # 소문자로 접기 **전에** 검사한다
            raise QuitApp
        ch = raw.lower()
        if key == curses.KEY_UP or ch == "k":
            sel = (sel - 1) % len(labels)
        elif key == curses.KEY_DOWN or ch == "j":
            sel = (sel + 1) % len(labels)
        elif ch.isdigit() and 1 <= int(ch) <= len(labels):
            return int(ch) - 1
```

**변경 후** — 동작 키 검사는 `Q` 바로 뒤, 소문자로 접기 **전**이다:

```python
        if is_enter(key):
            return _result(sel)

        raw = key_char(key) or ""
        if allow_quit and raw == "Q":       # 소문자로 접기 **전에** 검사한다
            raise QuitApp
        # 동작 키도 대소문자를 보존해 본다. `raw and` 가드는 **필수**다 —
        # 파이썬에서 빈 문자열은 어떤 문자열에도 들어 있다(`"" in "x"` 도
        # 참이다). `key_char` 는 특수키·미매핑 키에 대해 `""` 를 주므로,
        # 가드가 없으면 방향키 한 번에 동작이 발동한다.
        if raw and raw in actions:
            return _result(sel, raw)
        ch = raw.lower()
        if key == curses.KEY_UP or ch == "k":
            sel = (sel - 1) % len(labels)
        elif key == curses.KEY_DOWN or ch == "j":
            sel = (sel + 1) % len(labels)
        elif ch.isdigit() and 1 <= int(ch) <= len(labels):
            return _result(int(ch) - 1)
```

취소 분기(`elif (raw == "q" or ...) and allow_cancel: return None`)는 **그대로 둔다** — 취소는 `actions` 유무와 무관하게 `None`이다.

- [ ] **Step 5: 통과를 확인한다**

Run: `python3 -m unittest tests.test_tui -v 2>&1 | tail -15`

Expected: PASS. 기존 `PickTest`가 전부 그대로 통과해야 한다 — 그게 "기본 경로 무변경"의 증거다.

- [ ] **Step 6: 다른 러너가 안 깨졌는지 본다**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`

Expected: `OK`, 799개 (790 + 9).

- [ ] **Step 7: 커밋**

```bash
git add scripts/tui.py tests/test_tui.py
git commit -m "Let the shared picker carry action keys

pick() gains an optional actions string. Callers that pass one get
Picked(index, action) instead of a bare int; the six existing call sites
pass nothing and are untouched. The action key is compared case-preserved,
in the same spot the Q check already lives.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 챕터 목록 라벨

**Files:**
- Modify: `scripts/reading.py` (`discover_chapters` 뒤에 새 함수)
- Test: `tests/test_reading.py`

**Interfaces:**
- Consumes: `exam.exam_bank_for(rel) -> str | None`, `exam.best_result_for(chapter, records) -> dict | None` (기록은 `grade`·`score`·`auto_total` 키를 갖는다)
- Produces: `reading.chapter_labels(chapters, records) -> list[str]` — `chapters`는 저장소 상대경로 리스트, `records`는 `exam.read_results()`의 결과. 반환은 같은 순서의 표시 라벨.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_reading.py`의 `class ReadChapterTest` **바로 앞**에 추가한다:

```python
class ChapterLabelTest(unittest.TestCase):
    """목록만 보고 어느 챕터에 시험이 있고 지난 기록이 어떤지 알 수 있어야 한다.

    조용한 쪽이 기본이다. 은행이 있는 챕터가 23개로 다수라 거기 전부
    `[시험 있음]`을 붙이면 그게 잡음이 된다 — 소수인 '없음'과 실제 기록만
    표시한다.
    """

    WITH_BANK = "02-intermediate/01-transaction-and-locking.md"
    NO_BANK = "01-beginner/00-overview.md"

    def setUp(self):
        # 고정값이 틀리면 아래 단언들이 엉뚱한 것을 증명한다.
        self.assertIsNotNone(reading.exam.exam_bank_for(self.WITH_BANK),
                             f"{self.WITH_BANK} 에 은행이 없다")
        self.assertIsNone(reading.exam.exam_bank_for(self.NO_BANK),
                          f"{self.NO_BANK} 에 은행이 생겼다")

    def test_a_chapter_without_a_bank_says_so(self):
        self.assertEqual(reading.chapter_labels([self.NO_BANK], []),
                         ["00-overview.md   [시험 없음]"])

    def test_a_banked_chapter_with_no_record_stays_quiet(self):
        self.assertEqual(reading.chapter_labels([self.WITH_BANK], []),
                         ["01-transaction-and-locking.md"])

    def test_a_record_is_appended_in_the_exam_modules_wording(self):
        """같은 정보가 두 화면에서 다르게 보이면 안 된다.

        `exam._chapter_labels`가 쓰는 서식 그대로다.
        """
        records = [{"chapter": self.WITH_BANK, "auto_total": 10,
                    "score": 0.92, "grade": "A"}]
        self.assertEqual(
            reading.chapter_labels([self.WITH_BANK], records),
            ["01-transaction-and-locking.md   [지난 최고 A·92%]"])

    def test_a_record_for_another_chapter_does_not_leak(self):
        records = [{"chapter": "01-beginner/99-없는챕터.md", "auto_total": 10,
                    "score": 0.92, "grade": "A"}]
        self.assertEqual(reading.chapter_labels([self.WITH_BANK], records),
                         ["01-transaction-and-locking.md"])

    def test_it_keeps_the_given_order(self):
        got = reading.chapter_labels([self.NO_BANK, self.WITH_BANK], [])
        self.assertEqual(got[0], "00-overview.md   [시험 없음]")
        self.assertEqual(got[1], "01-transaction-and-locking.md")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_reading.ChapterLabelTest -v 2>&1 | tail -15`

Expected: FAIL — `AttributeError: module 'reading' has no attribute 'chapter_labels'`.

- [ ] **Step 3: 구현한다**

`scripts/reading.py`의 `discover_chapters` **바로 뒤**(`chapter_count` 앞)에 추가한다:

```python
def chapter_labels(chapters, records):
    """챕터 목록에 붙일 라벨 — 파일명 + 시험 상태 접미.

    조용한 쪽이 기본이다. 은행이 있는 챕터가 다수(23/31)라 거기 전부
    `[시험 있음]`을 붙이면 그게 잡음이 된다 — 소수인 '없음'과 실제 기록만
    표시한다.

    `exam.best_result_for` 에 챕터 상대경로를 **그대로** 넘긴다. 은행 JSON의
    `chapter` 필드가 그 경로와 같아서(실측 23/23) 파일을 열 이유가 없다.
    `exam._chapter_labels` 가 `_bank_meta` 로 파일을 여는 것은 그쪽이 **은행
    경로**에서 출발하기 때문이고, 읽기 모드는 **챕터 경로**에서 출발한다.

    문구·서식은 `exam._chapter_labels` 의 것을 그대로 쓴다 — 같은 정보가 두
    화면에서 다르게 보이면 안 된다.
    """
    labels = []
    for rel in chapters:
        name = Path(rel).name
        if not exam.exam_bank_for(rel):
            labels.append(f"{name}   [시험 없음]")
            continue
        best = exam.best_result_for(rel, records)
        if best:
            name += (f"   [지난 최고 {best['grade']}"
                     f"·{best['score'] * 100:.0f}%]")
        labels.append(name)
    return labels
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_reading -v 2>&1 | tail -10`

Expected: PASS

- [ ] **Step 5: 실제 챕터 전체에 대해 눈으로 확인한다**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
import reading
for tier in reading.TIERS:
    chapters = reading.discover_chapters(tier)
    for label in reading.chapter_labels(chapters, []):
        print(f'{tier}  {label}')
"
```

Expected: 31줄. `00-overview.md`·`*-commands-cheatsheet.md`·`appendix/` 두 파일(합계 8줄)에만 `[시험 없음]`이 붙고 나머지 23줄은 파일명만 있어야 한다. 다르면 멈추고 보고한다.

- [ ] **Step 6: 커밋**

```bash
git add scripts/reading.py tests/test_reading.py
git commit -m "Label the reading list with exam status and best score

Only the minority gets a suffix: the eight chapters with no bank say so,
and a chapter with a past attempt carries it in exam.py's own wording. The
other 23 stay bare, because marking the majority is noise.

Reuses exam.best_result_for directly — every bank's chapter field equals
the chapter path, so there is nothing to open.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `reading` 흐름 — 프롬프트를 동작 키로

**Files:**
- Modify: `scripts/reading.py` (import, `choose` 70–84행, `offer_exam` 98–116행 **삭제**, `main` 127–178행)
- Test: `tests/test_reading.py` (`ExamOfferTest` **삭제**, `_TTYStringIO` **삭제**, `ReadingMainTest`·`ChapterPauseTest`·`ReadingQuitKeyTest` 갱신)

**Interfaces:**
- Consumes: `tui.Picked`, `tui.pick(..., actions=...)` (Task 1), `reading.chapter_labels(chapters, records)` (Task 2), `reading.read_chapter(rel, dbms) -> bool`, `exam.read_results()`, `exam.exam_bank_for(rel)`
- Produces:
  - `reading.choose(title, labels, actions="") -> int | None | Picked` — `actions`가 있으면 `Picked`(취소는 `None`)
  - `reading.run_exam(rel, bank, dbms) -> None` — 그 챕터의 시험을 돌린다
  - `reading.offer_exam` — **없어진다**

- [ ] **Step 1: 실패하는 테스트를 쓴다 (a) — 지울 것부터 지운다**

`tests/test_reading.py`에서 **삭제**한다:

1. `class _TTYStringIO(io.StringIO)` 전체(92–97행 근처). `ExamOfferTest`에서만 쓰이므로 함께 사라져야 한다 — 남기면 죽은 코드다.
2. `class ExamOfferTest` 전체(100–172행 근처, 테스트 5개).

- [ ] **Step 2: 실패하는 테스트를 쓴다 (b) — 흐름 픽스처를 새 계약에 맞춘다**

`class ReadingMainTest`의 `_flow` 컨텍스트 매니저를 아래로 **통째로 교체**한다. `offer_exam`이 사라졌고, `choose`가 `actions` 여부에 따라 다른 모양을 돌려주기 때문이다:

```python
    @contextlib.contextmanager
    def _flow(self, picks, action=None, printed=False):
        """DBMS → 티어 → 챕터 한 바퀴를 돌린다.

        `action`은 챕터 화면에서 누를 동작 키(`"x"` 또는 `None`). 가짜 `choose`는
        진짜와 같은 계약을 지킨다 — `actions`를 받았을 때만 `Picked`를 돌려준다.
        """
        seq = iter(list(picks) + [None] * 6)
        read, exams, paused = [], [], []
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "run_exam",
                          "pause_after_output")}

        def fake_choose(title, labels, actions=""):
            idx = next(seq)
            if not actions or idx is None:
                return idx
            return reading.Picked(idx, action)

        reading.choose = fake_choose
        reading.read_chapter = (
            lambda rel, dbms=None: read.append((rel, dbms)) or printed)
        reading.run_exam = (
            lambda rel, bank, dbms: exams.append((rel, bank, dbms)))
        reading.pause_after_output = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield read, exams, paused
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)
```

기존 `ReadingMainTest`의 세 테스트를 새 픽스처에 맞게 고친다:

```python
    def test_dbms_then_tier_then_chapter_reaches_the_reader(self):
        # 0=전체, 0=01-beginner, 0=그 티어의 첫 챕터
        with self._flow([0, 0, 0]) as (read, exams, _):
            self.assertEqual(reading.main([]), 0)
        self.assertEqual(len(read), 1)
        self.assertTrue(read[0][0].startswith("01-beginner/"))
        self.assertIsNone(read[0][1])          # '전체'는 필터 없음
        self.assertEqual(exams, [], "Enter는 읽기다 — 시험이 아니다")

    def test_choosing_a_vendor_passes_it_through(self):
        with self._flow([1, 0, 0]) as (read, _, _):
            reading.main([])
        self.assertEqual(read[0][1], "postgresql")

    def test_quitting_at_the_first_screen_reads_nothing(self):
        with self._flow([]) as (read, exams, _):
            self.assertEqual(reading.main([]), 0)
        self.assertEqual(read, [])
        self.assertEqual(exams, [])
```

`test_the_exam_handoff_uses_an_absolute_bank_path_and_forwards_dbms`(281–315행)를 **삭제**하고, 그 자리에 동작 키 테스트 세 개를 넣는다. 핸드오프 인자 계약은 Step 3에서 `run_exam` 단위 테스트가 이어받는다:

```python
    def test_the_action_key_runs_that_chapters_exam_instead_of_reading(self):
        """1=PostgreSQL, 1=02-intermediate, 1=은행이 있는 챕터.

        `discover_chapters`는 파일명 순이라 인덱스 1은 `00-overview.md` 다음,
        즉 `01-transaction-and-locking.md`다. 고정값이 흔들리면 아래
        `assertIsNotNone`이 먼저 알려 준다.
        """
        rel = reading.discover_chapters("02-intermediate")[1]
        self.assertIsNotNone(reading.exam.exam_bank_for(rel),
                             f"고정값이 틀렸다 — {rel} 에 은행이 없다")
        with self._flow([1, 1, 1], action="x") as (read, exams, paused):
            reading.main([])
        self.assertEqual(read, [], "시험을 골랐는데 챕터를 읽었다")
        self.assertEqual(len(exams), 1)
        self.assertEqual(exams[0][0], rel)
        self.assertIsNotNone(exams[0][1])
        self.assertEqual(exams[0][2], "postgresql", "고른 벤더를 흘렸다")
        self.assertEqual(paused, [1], "시험 뒤에는 평문이 남을 수 있다")

    def test_the_action_key_does_nothing_on_a_chapter_without_a_bank(self):
        """0=전체, 0=01-beginner, 0=`00-overview.md`(은행 없음).

        그 행이 이미 `[시험 없음]`이라 화면이 이유를 적고 있다.
        """
        rel = reading.discover_chapters("01-beginner")[0]
        self.assertIsNone(reading.exam.exam_bank_for(rel),
                          f"고정값이 틀렸다 — {rel} 에 은행이 생겼다")
        with self._flow([0, 0, 0], action="x") as (read, exams, paused):
            reading.main([])
        self.assertEqual(exams, [], "은행이 없는데 시험을 열었다")
        self.assertEqual(read, [], "시험 키를 눌렀는데 챕터를 읽었다")
        self.assertEqual(paused, [], "아무 일도 안 했는데 멈췄다")

    def test_the_action_key_comes_back_to_the_chapter_list(self):
        """시험을 보고 나면 목록으로 돌아와 다음 챕터를 고를 수 있어야 한다."""
        with self._flow([1, 1, 1, 1], action="x") as (_, exams, _p):
            reading.main([])
        self.assertEqual(len(exams), 2, "한 번 보고 목록을 떠났다")
```

`class ChapterPauseTest`의 `_flow`도 같은 이유로 교체하고, 세 번째 테스트를 삭제한다(그 커버리지는 위 `test_the_action_key_runs_...`의 `paused == [1]`이 이어받는다):

```python
    @contextlib.contextmanager
    def _flow(self, picks, printed=False):
        """선택 → 읽기 한 바퀴를 돌리고 pause 호출을 센다."""
        seq = iter(list(picks) + [None] * 6)
        paused = []
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "pause_after_output")}

        def fake_choose(title, labels, actions=""):
            idx = next(seq)
            if not actions or idx is None:
                return idx
            return reading.Picked(idx, None)

        reading.choose = fake_choose
        reading.read_chapter = lambda rel, dbms=None: printed
        reading.pause_after_output = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield paused
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)
```

`test_taking_the_exam_pauses_even_when_the_pager_swallowed_it`(365–386행)를 **삭제**한다. 나머지 두 테스트(`test_the_pager_swallowed_it_so_we_do_not_pause`, `test_a_plain_text_fallback_still_pauses`)는 본문 그대로 두되, `with self._flow(...) as paused:` 형태가 새 픽스처와 맞으므로 수정 불필요하다.

- [ ] **Step 3: 실패하는 테스트를 쓴다 (c) — `run_exam`과 footer**

`class ReadingQuitKeyTest` **바로 앞**에 추가한다:

```python
class RunExamTest(unittest.TestCase):
    """핸드오프 인자 계약. 전에는 `main` 안에 인라인으로 있었다.

    `exam.main`은 대상을 cwd 기준 상대경로로 받는다(CLI 계약). `./guide`를
    저장소 밖 cwd에서 띄운 경우에도 은행을 찾으려면 절대경로여야 하고, 고른
    벤더를 흘리면 PostgreSQL 챕터를 읽고도 MySQL·Oracle 문항이 다 나온다.
    """

    CHAPTER = "02-intermediate/01-transaction-and-locking.md"

    @contextlib.contextmanager
    def _capture(self):
        captured = {}
        real = reading.exam.main
        reading.exam.main = lambda argv: captured.setdefault("argv", argv) or 0
        try:
            yield captured
        finally:
            reading.exam.main = real

    def test_it_passes_an_absolute_bank_path(self):
        bank = reading.exam.exam_bank_for(self.CHAPTER)
        with self._capture() as captured:
            reading.run_exam(self.CHAPTER, bank, None)
        argv = captured["argv"]
        self.assertTrue(Path(argv[0]).is_absolute(), argv)
        self.assertTrue(argv[0].startswith(str(REPO_ROOT)), argv)
        self.assertTrue(argv[0].endswith(".json"), argv)

    def test_it_forwards_the_chosen_vendor(self):
        bank = reading.exam.exam_bank_for(self.CHAPTER)
        with self._capture() as captured:
            reading.run_exam(self.CHAPTER, bank, "postgresql")
        self.assertEqual(captured["argv"][1:], ["--dbms", "postgresql"])

    def test_the_whole_choice_omits_the_flag(self):
        """`dbms`가 `None`('전체')이면 `exam`이 스스로 묻게 둔다."""
        bank = reading.exam.exam_bank_for(self.CHAPTER)
        with self._capture() as captured:
            reading.run_exam(self.CHAPTER, bank, None)
        self.assertEqual(captured["argv"][1:], [])
```

그리고 `class ReadingQuitKeyTest` **안**, `test_the_footer_offers_both_back_and_quit` 바로 뒤에 추가한다. 기존 테스트의 `fake_curses`/`fake_pick` 구조를 그대로 쓰되 `actions`를 넘긴다:

```python
    def _footer(self, **kw):
        """`choose`가 `pick`에 실제로 넘긴 footer를 가로챈다."""
        seen = {}
        fake_curses = types.SimpleNamespace(
            curs_set=lambda _n: None,
            wrapper=lambda driver: driver(object()))

        def fake_pick(_stdscr, _curses, _title, _labels, footer=None, **_kw):
            seen["footer"] = footer
            return 0

        real_pick = reading.pick
        real_curses = sys.modules.get("curses")
        real_in, real_out = sys.stdin.isatty, sys.stdout.isatty
        reading.pick = fake_pick
        sys.modules["curses"] = fake_curses
        sys.stdin.isatty = lambda: True
        sys.stdout.isatty = lambda: True
        try:
            reading.choose("제목", ["a", "b"], **kw)
        finally:
            reading.pick = real_pick
            if real_curses is None:
                del sys.modules["curses"]
            else:
                sys.modules["curses"] = real_curses
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out
        return seen["footer"]

    def test_the_chapter_footer_offers_the_exam_action(self):
        footer = self._footer(actions="x")
        self.assertIn("Enter 읽기", footer)
        self.assertIn("x 시험", footer)
        self.assertIn("Esc/q 뒤로", footer)
        self.assertIn("Q 종료", footer)

    def test_a_screen_without_actions_says_nothing_about_the_exam(self):
        """없는 키를 안내하면 안내가 거짓말이 된다."""
        footer = self._footer()
        self.assertIn("Enter 선택", footer)
        self.assertNotIn("시험", footer)

    def test_the_chapter_footer_fits_an_eighty_column_terminal(self):
        """`tui.bar`가 잘라내면 안내가 조용히 사라진다.

        `bar`는 폭 `w-1`로 자르므로 80칸 터미널에서 쓸 수 있는 것은 79칸이다.
        """
        self.assertLessEqual(tui.cwidth(self._footer(actions="x")), 79)

    def test_the_line_fallback_wraps_its_answer_in_picked(self):
        """평문 선택기는 동작 키를 모른다 — `choose`가 계약만 맞춰 준다."""
        real_pick_line = reading.pick_line
        real_in, real_out = sys.stdin.isatty, sys.stdout.isatty
        reading.pick_line = lambda title, labels: 1
        sys.stdin.isatty = lambda: False
        sys.stdout.isatty = lambda: False
        try:
            self.assertEqual(reading.choose("제목", ["a", "b"], actions="x"),
                             reading.Picked(1, None))
            reading.pick_line = lambda title, labels: None
            self.assertIsNone(reading.choose("제목", ["a", "b"], actions="x"))
        finally:
            reading.pick_line = real_pick_line
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out
```

`tests/test_reading.py` 상단 import 블록에 `import tui  # noqa: E402`를 더한다(`import reading` 바로 뒤). `cwidth`가 필요하다.

- [ ] **Step 4: 실패를 확인한다**

Run: `python3 -m unittest tests.test_reading -v 2>&1 | tail -30`

Expected: FAIL — `AttributeError: module 'reading' has no attribute 'run_exam'`, `Picked` 미노출, footer에 `x 시험` 없음, `choose()`가 `actions` 인자를 모름.

- [ ] **Step 5: `choose`에 `actions`를 단다**

`scripts/reading.py:21–22`의 import에 `Picked`를 더한다:

```python
from tui import (Picked, QuitApp, page_text, pager_supports_color,  # noqa: E402
                 pause_after_output, pick, pick_line, text_width)
```

`choose`(70–84행)를 아래로 교체한다:

```python
def choose(title, labels, actions=""):
    """세로 목록에서 하나 고른다 → 인덱스(뒤로/종료면 None).

    tty면 공용 curses 선택기, 아니면 공용 평문 선택기. 둘 다 `tui` 에 있다.

    `actions` 를 주면 `tui.pick` 의 계약 그대로 `Picked(index, action)` 을
    돌려준다. 평문 선택기는 동작 키를 모르므로(설계상 범위 밖 — `exam` 이
    `pick_line` 을 감싸 쓰는 탓에 거기 키를 더하면 비대칭이 생긴다) 여기서
    `Picked` 로 감싸 계약만 맞춘다. 라인 모드에서는 읽기만 되고 시험은
    `학습 점검` 모드로 간다.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        idx = pick_line(title, labels)
        if not actions or idx is None:
            return idx
        return Picked(idx, None)
    import curses

    # 동작 키가 있는 화면은 Enter의 뜻도 달라진다 — '선택'이 아니라 '읽기'다.
    verb = f"Enter 읽기   {actions} 시험" if actions else "Enter 선택"

    def _driver(stdscr):
        curses.curs_set(0)
        return pick(stdscr, curses, title, labels,
                    footer=f" ↑↓ 또는 숫자 선택   {verb}   Esc/q 뒤로   Q 종료 ",
                    actions=actions)

    return curses.wrapper(_driver)
```

- [ ] **Step 6: `offer_exam`을 `run_exam`으로 갈아 끼운다**

`scripts/reading.py`의 `offer_exam` 함수(98–116행, docstring 포함) 전체를 **삭제**하고 그 자리에 넣는다:

```python
def run_exam(rel, bank, dbms):
    """읽던 자리에서 그 챕터의 시험으로 넘긴다. curses 밖에서 부른다.

    `exam.main` 은 대상 인자를 cwd 기준 상대경로로 받는다(CLI 계약이라 `exam`
    쪽에서 바꾸지 않는다) — 여기서는 `./guide` 를 저장소 밖 cwd 에서 띄운
    경우에도 은행을 찾게 `REPO_ROOT` 기준 절대경로로 넘긴다. 고른 벤더도 함께
    넘기지 않으면 PostgreSQL 챕터를 읽고도 MySQL·Oracle 문항까지 다 나온다 —
    `dbms` 가 `None`("전체")이면 그대로 생략해 `exam` 이 스스로 묻게 한다.
    """
    args = [str(exam.REPO_ROOT / bank)]
    if dbms:
        args += ["--dbms", dbms]
    exam.main(args)
```

- [ ] **Step 7: `main`의 챕터 루프를 바꾼다**

`scripts/reading.py`의 `main` docstring 첫 줄과 챕터 루프를 바꾼다. **현재 docstring:**

```python
    """DBMS → 티어 → 챕터 → 읽기 → 시험 제안. 각 화면에서 뒤로 갈 수 있다."""
```

**변경 후:**

```python
    """DBMS → 티어 → 챕터 → 읽기. 각 화면에서 뒤로 갈 수 있다.

    챕터 목록에서 `x` 를 누르면 그 챕터의 시험으로 넘어간다. 전에는 챕터를
    읽고 나올 때마다 `[Y/n]` 으로 물었는데, 기본값이 '예'라서 습관적으로 누른
    Enter 가 시험을 열었고 `Esc`/`q` 도 먹히지 않았다 — 이슈 #95 가 고친 것과
    같은 부류의 막다른 프롬프트였다.
    """
```

챕터 루프(가장 안쪽 `while True:`, 현재 149–178행)를 아래로 교체한다:

```python
            while True:
                chapters = discover_chapters(tier)
                # 기록은 그릴 때마다 새로 읽는다 — 방금 본 시험의 결과가
                # 목록으로 돌아오자마자 보여야 한다.
                sel = choose(f"{tier} — 어느 챕터를 읽을까요",
                             chapter_labels(chapters, exam.read_results()),
                             actions="x")
                if sel is None:
                    break                  # 티어 선택으로
                rel = chapters[sel.index]
                if sel.action == "x":
                    bank = exam.exam_bank_for(rel)
                    if bank:
                        run_exam(rel, bank, dbms)
                        pause_after_output()
                    # 은행이 없으면 아무 일도 하지 않는다 — 그 행이 이미
                    # `[시험 없음]` 이라 화면이 이유를 적고 있다.
                    continue
                # 페이저가 본문을 삼켰다면 화면에 지킬 평문이 없다. 그때도
                # 멈추면 챕터를 읽을 때마다 뜻 없는 Enter 를 요구하게 된다
                # (이슈 #95).
                if read_chapter(rel, dbms):
                    pause_after_output()
```

- [ ] **Step 8: 통과를 확인한다**

Run: `python3 -m unittest tests.test_reading -v 2>&1 | tail -20`

Expected: PASS

- [ ] **Step 9: 전체 스위트를 돌린다**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`

Expected: `OK`. `offer_exam`이 사라지면서 `guide`·`exam` 쪽 테스트가 깨지면 안 된다 — `offer_exam`을 부르는 곳은 `reading.main` 하나뿐이었다. 깨지면 멈추고 보고한다.

이 태스크가 **명시적으로 지운 것**은 다음 7개뿐이다. 그 밖의 테스트가 사라졌다면 실수다.
- `ExamOfferTest` 5개
- `ReadingMainTest.test_the_exam_handoff_uses_an_absolute_bank_path_and_forwards_dbms` 1개
- `ChapterPauseTest.test_taking_the_exam_pauses_even_when_the_pager_swallowed_it` 1개

- [ ] **Step 10: `offer_exam`의 흔적이 남지 않았는지 본다**

Run: `rg -n "offer_exam|_TTYStringIO" scripts/ tests/ README.md CLAUDE.md`

Expected: **아무것도 출력되지 않는다**(`rg`는 일치가 없으면 종료 코드 1로 끝나는데, 여기서는 그게 정상이다). `docs/superpowers/` 아래 설계·계획 문서에는 서술이 남아 있어야 한다 — 그건 왜 없앴는지에 대한 기록이므로 검색 대상에서 뺐다.

- [ ] **Step 11: 커밋**

```bash
git add scripts/reading.py tests/test_reading.py
git commit -m "Replace the post-chapter prompt with a list action

Reading a chapter now returns straight to the list. The exam handoff moves
to an x key on that list, so it no longer blocks and no longer defaults to
yes — a reflexive Enter used to start an exam.

offer_exam is gone; run_exam keeps its argument contract under test.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 문서와 전체 검증

**Files:**
- Modify: `scripts/reading.py` (모듈 docstring 3–4행), `README.md` (154–159행), `CLAUDE.md` (`scripts/tui.py` 항목과 `reading.py` 문장)
- Test: `tests/test_reading.py` (문서 검사 1건)

**Interfaces:**
- Consumes: Task 1–3 전부
- Produces: 없음 (마지막 태스크)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_reading.py`의 `class ReadingQuitKeyTest` 끝에 추가한다:

```python
    def test_the_docs_explain_the_exam_action(self):
        """안내 없는 단축키는 없는 것과 같다.

        `README.md` 의 "한 번에 시작하기" 절이 `./guide` 흐름을 산문으로
        설명하는 유일한 자리다.
        """
        body = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`x`", body, "README에 시험 단축키 안내가 없다")
        self.assertNotIn("시험을 볼지 물은", body,
                         "없어진 [Y/n] 프롬프트를 README가 아직 설명한다")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_reading.ReadingQuitKeyTest.test_the_docs_explain_the_exam_action -v`

Expected: FAIL — `README에 시험 단축키 안내가 없다`.

- [ ] **Step 3: `reading.py` 모듈 docstring을 고친다**

`scripts/reading.py`의 3–4행. **현재:**

```
읽기(챕터) → 확인(`./exam`) → 겪기(`./shoot`) 중 첫 축이다. 다 읽으면 그 챕터의
시험으로 이어 준다 — 경로를 손으로 찾게 하면 거기서 끊긴다.
```

**변경 후:**

```
읽기(챕터) → 확인(`./exam`) → 겪기(`./shoot`) 중 첫 축이다. 챕터 목록에서 `x` 를
누르면 그 챕터의 시험으로 이어 준다 — 경로를 손으로 찾게 하면 거기서 끊긴다.
막는 질문이 아니라 목록의 동작인 이유는 `docs/superpowers/specs/` 에 있다.
```

- [ ] **Step 4: `README.md`를 고친다**

"한 번에 시작하기" 절의 문단(154–159행). **현재:**

```markdown
챕터 읽기 · 학습 점검 · 장애 대응을 한 메뉴에서 고른다. 챕터를 다 읽으면 그
챕터의 시험으로 바로 이어진다 — 읽기 → 확인 → 겪기가 한 자리에서 닫힌다.
학습 점검·장애 대응을 고르면 아래 두 절과 똑같은 화면으로 이어진다. 챕터
읽기는 별도 절이 없다 — 고른 챕터 본문이 그대로 `$PAGER`(따로 지정하지
않았고 `less`가 있으면 `less`)로 열리고, 나오면 그 챕터의 시험을 볼지 물은
뒤 메뉴로 돌아온다. 무엇을 할지 정해져 있다면 `./exam`·`./shoot`을 바로 써도
된다 — 인자(`--dbms`, `--seed` 등)는 그쪽이 받는다.
```

**변경 후:**

```markdown
챕터 읽기 · 학습 점검 · 장애 대응을 한 메뉴에서 고른다. 읽기 → 확인 → 겪기가
한 자리에서 닫힌다. 학습 점검·장애 대응을 고르면 아래 두 절과 똑같은 화면으로
이어진다. 챕터 읽기는 별도 절이 없다 — 고른 챕터 본문이 그대로 `$PAGER`(따로
지정하지 않았고 `less`가 있으면 `less`)로 열리고, 나오면 곧바로 챕터 목록으로
돌아온다. 그 목록에서 `x`를 누르면 커서가 놓인 챕터의 시험이 시작되고, 아직
시험이 없는 챕터(개요·치트시트·부록)는 `[시험 없음]`으로 표시된다 — 한 번
풀어 본 챕터에는 `[지난 최고 A·92%]`처럼 기록이 붙는다. 무엇을 할지 정해져
있다면 `./exam`·`./shoot`을 바로 써도 된다 — 인자(`--dbms`, `--seed` 등)는
그쪽이 받는다.
```

- [ ] **Step 5: `CLAUDE.md`를 고친다**

두 곳을 고친다.

**(a) `reading.py` 설명 문장.** `The third mode is ... then an offer to run that chapter's bank.` 에서 마지막 절을 바꾼다:

```
The third mode is `scripts/reading.py`: DBMS → tier → chapter, body rendered by `scripts/markdown_render.py` before it is handed to `$PAGER`, then straight back to the chapter list — `x` on that list starts the highlighted chapter's bank, and the list labels carry `[시험 없음]` / `[지난 최고 …]` so you can see which chapters have one. It used to ask `[Y/n]` after every chapter instead; that prompt defaulted to yes, so the reflexive Enter that dismissed the old pause started an exam, and `Esc`/`q` did nothing in it — the same dead-end shape issue #95 fixed elsewhere.
```

**(b) `scripts/tui.py` 항목**에 `pick`의 새 계약을 더한다. 그 항목이 이미 `pick`의 키 비교 함정을 기록하는 자리이므로, `**Before writing a "pick one from a vertical list" screen, use `pick()`**` 문장 뒤에 이어 붙인다:

```
`pick()` takes an optional `actions` string of extra keys; pass one and the return value becomes `Picked(index, action)` instead of a bare int (`action` is `None` for a normal Enter/number selection, `None` return still means cancel, `Q` still raises `QuitApp`). The two return shapes are deliberate — a caller always knows whether it passed `actions`, and the alternative was a second picker, which is exactly the drift `pick()` was consolidated to end. Action keys are compared **case-preserved**, in the same spot the `Q` check lives, so `actions="x"` does not swallow `X`.
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python3 -m unittest tests.test_reading -v 2>&1 | tail -10`

Expected: PASS

- [ ] **Step 7: 콘텐츠 검사기를 돌린다**

Run: `python3 scripts/check_content.py; echo "exit=$?"`

Expected: `exit=0`

- [ ] **Step 8: 전체 스위트를 돌린다**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`

Expected: `OK`

- [ ] **Step 9: 변경 파일을 대조한다**

Run: `git status --short`

Expected: 이 태스크에서 남은 변경은 `scripts/reading.py`, `README.md`, `CLAUDE.md`, `tests/test_reading.py` 넷뿐. `scripts/exam.py`나 `scripts/shooting.py`가 보이면 범위를 벗어난 것이므로 되돌린다.

- [ ] **Step 10: 커밋**

```bash
git add scripts/reading.py README.md CLAUDE.md tests/test_reading.py
git commit -m "Document the exam action key

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 11: 수동 확인 (실제 터미널 필요 — CI가 못 하는 것)**

사람이 실제 tty에서 해야 한다. 결과를 PR #97 본문에 적는다.

1. `./guide` → 챕터 읽기 → DBMS → 티어 → 챕터 선택 → `less` 종료 → **아무 질문 없이** 챕터 목록으로 돌아오는가. `[Y/n]`이 더는 뜨지 않는가.
2. 그 목록에서 개요·치트시트 행에 `[시험 없음]`이 보이는가. 나머지 행은 파일명만 있는가.
3. 은행이 있는 행에서 `x` → 그 챕터의 시험이 시작되는가. 끝나면 목록으로 돌아오고, 그 행에 `[지난 최고 …]`가 **바로** 붙는가.
4. `[시험 없음]` 행에서 `x` → 아무 일도 일어나지 않고 목록에 머무는가.
5. footer가 `↑↓ 또는 숫자 선택   Enter 읽기   x 시험   Esc/q 뒤로   Q 종료`로 보이는가. 80칸 터미널에서 잘리지 않는가.
6. 그 목록에서 `Q` → 앱 전체 종료(이슈 #95 회귀 확인). 소문자 `q` → 티어 선택으로.

---

## 자체 검토 메모

**스펙 대응표:**

| 설계 문서 절 | 태스크 |
|---|---|
| 1. `tui.pick`의 `actions`·`Picked` | Task 1 |
| 2. 챕터 목록 라벨 | Task 2 |
| 3. `reading` 흐름 (`choose`·`run_exam`·`main`·`offer_exam` 삭제) | Task 3 |
| 3. 비-tty 경로 어댑터 | Task 3 Step 5 + `test_the_line_fallback_wraps_its_answer_in_picked` |
| 4. `[시험 없음]` 행의 `x`는 무반응 | Task 3 Step 7 + `test_the_action_key_does_nothing_on_a_chapter_without_a_bank` |
| 5. 문서 세 곳 | Task 4 Step 3·4·5 |
| 6. 테스트 | 각 태스크에 분산 |
| 7. 릴리스 영향 | 코드 변경 없음 — MINOR로 매긴다 |

**태스크 간 의존:** Task 1 → Task 3(‑`actions` 필요), Task 2 → Task 3(`chapter_labels` 필요). Task 2는 Task 1과 무관하므로 순서를 바꿔도 된다. Task 4는 마지막.

**놓치기 쉬운 것 넷:**
1. **`raw and raw in actions`의 `raw and` 가드.** 파이썬에서 빈 문자열은 어떤 문자열에도 들어 있다 — `"" in ""`도 `"" in "x"`도 **참**이다(실측). `key_char`는 특수키·미매핑 키에 `""`를 주므로, 가드가 없으면 `actions`가 있든 없든 방향키 한 번에 동작이 발동한다. Task 1 Step 4에 주석으로 박아 두었다.
2. **`_TTYStringIO`가 죽은 코드가 된다.** `ExamOfferTest`에서만 쓰이므로 함께 지워야 한다. Task 3 Step 1에 명시.
3. **`ChapterPauseTest`와 `ReadingMainTest`가 각자 `_flow`를 갖고 있다.** 둘 다 `offer_exam`을 패치하므로 **둘 다** 고쳐야 한다. 하나만 고치면 나머지가 `AttributeError`로 죽는다.
4. **`_PickScreen.getch`는 키가 떨어지면 `ord("q")`를 무한히 돌려준다.** 그래서 Task 1의 "무시된다" 테스트들이 `[무시될 키, 10]`으로 키를 **두 개** 준다. 하나만 주면 취소로 끝나 엉뚱한 것을 증명한다.
