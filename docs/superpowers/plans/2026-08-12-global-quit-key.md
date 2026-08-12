# 전역 종료 키(Q)와 불필요한 멈춤 제거 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `./guide`의 어느 선택 화면에서든 대문자 `Q` 한 타로 앱 전체를 끝낼 수 있게 하고, 페이저가 본문을 삼킨 경우에는 `계속하려면 Enter` 프롬프트를 띄우지 않는다 (이슈 #95).

**Architecture:** `tui.py`에 `QuitApp`이라는 **`BaseException` 하위** 전용 예외를 두고, 공용 선택기 `tui.pick()`이 `Q`에서 그것을 올린다. 중간 계층(`reading.main`의 3중 루프, `shooting`의 월드/스테이지 루프)은 한 줄도 고치지 않고 통과시키며, `guide.main`과 두 러너의 `__main__` 블록만 잡는다. 멈춤 문제는 `page_text()`가 "평문으로 직접 찍었는가"를 반환하게 만들어 호출부가 조건부로 `pause_after_output()`을 부르게 하고, 정당하게 남는 프롬프트에는 `q` 탈출구를 준다.

**Tech Stack:** Python 3 표준 라이브러리만 (`curses`, `unittest`). 외부 패키지 없음.

**설계 문서:** [`docs/superpowers/specs/2026-08-12-global-quit-key-design.md`](../specs/2026-08-12-global-quit-key-design.md) — 각 결정의 근거는 거기 있다. 이 계획은 그것을 실행 단위로 쪼갠 것이다.

## Global Constraints

- **Python 표준 라이브러리만.** `pip`/`npm`/빌드 시스템이 없고 CI도 PyPI에서 아무것도 설치하지 않는다. 새 import는 표준 라이브러리에 있는 것이어야 한다.
- **테스트는 tty를 요구하지 않는다.** CI는 파이프로 돈다. curses가 필요한 코드는 가짜 `stdscr`·가짜 `curses`를 주입해 검사하고, `isatty`는 monkeypatch한다.
- **모든 새 주석·docstring·화면 문구는 한국어.** 저장소 전체가 한국어다.
- **테스트 실행 명령은 하나뿐이다:** `python3 -m unittest discover -s tests` (저장소 루트에서). **시작 기준선(실측): 743개 통과, 약 35초.** 이 계획은 여기에 새 테스트를 더할 뿐 기존 것을 줄이지 않는다 — 마지막에 743보다 적으면 무언가를 지운 것이다.
- **`scripts/exam.py`는 이 작업에서 건드리지 않는다.** `tui.pick`을 쓰지 않으므로 `QuitApp`이 발생할 곳이 없다.
- **`shooting.py`의 두 `except Exception:`(3224·3330행)은 건드리지 않는다.** `QuitApp`이 `BaseException`이라 애초에 걸리지 않는다. 그것이 `BaseException`을 고른 이유다.
- **`shooting._choose_stage_curses`(3116행)는 죽은 코드다** — 호출부가 없다. 이 작업에서 제거하지도, 수정하지도 않는다.
- 커밋 메시지는 영어 명령형 한 줄 + 본문. 기존 이력(`git log`) 형식을 따른다.

---

## Task 1: `tui.QuitApp`과 `pick()`의 `allow_quit`

**Files:**
- Modify: `scripts/tui.py` (18행 근처에 클래스 추가, `pick()` 344–398행)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `tui.QuitApp` — `BaseException` 하위 클래스. 인자 없이 `raise tui.QuitApp`으로 올린다.
  - `tui.pick(stdscr, curses, title, labels, footer=None, allow_cancel=True, allow_quit=True) -> int | None` — `allow_quit=True`(기본)일 때 `Q` 입력에서 `QuitApp`을 올린다. 나머지 반환 계약은 그대로(고른 인덱스, 취소면 `None`).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_tui.py`의 `class PickTest` **바로 앞**에 새 클래스를 추가한다 (즉 `class PickTest(unittest.TestCase):` 줄 위):

```python
class QuitAppTest(unittest.TestCase):
    """전역 종료 신호는 `Exception`이 아니라 `BaseException`이어야 한다.

    `shooting.py`는 화면 코드를 `except Exception:`으로 감싸 traceback을 찍고
    라인 모드로 폴백한다 — 선택 화면(`choose_stage`)과 게임 화면(`cmd_play`)
    두 곳이고, 조용히 넘기지 않으려고 **일부러** 넣은 안전망이라 없앨 수 없다.
    `QuitApp`이 평범한 `Exception`이면 `Q`를 누른 사용자가 traceback과 함께
    라인 모드 목록으로 떨어진다.

    `SystemExit`·`KeyboardInterrupt`가 `BaseException`인 이유가 정확히 이것이다
    — 오류가 아니라 제어 흐름 신호이고, "모든 오류를 잡는" 코드가 삼키면 안 된다.
    """

    def test_it_is_a_base_exception_but_not_an_exception(self):
        self.assertTrue(issubclass(tui.QuitApp, BaseException))
        self.assertFalse(issubclass(tui.QuitApp, Exception))

    def test_except_exception_does_not_swallow_it(self):
        """계층 관계를 말로만 확인하지 않고 실제 `except`로 확인한다."""
        swallowed = []
        with self.assertRaises(tui.QuitApp):
            try:
                raise tui.QuitApp
            except Exception as e:          # noqa: BLE001 - 일부러 넓게 잡는다
                swallowed.append(e)
        self.assertEqual(swallowed, [], "except Exception이 QuitApp을 삼켰다")
```

그리고 기존 `class PickTest` **안**, `test_cancel_can_be_disabled` 메서드 바로 뒤에 다음 메서드들을 추가한다:

```python
    def test_uppercase_q_quits_the_whole_app(self):
        """이슈 #95 — 화면 스택 바닥에서도 한 타로 나올 수 있어야 한다."""
        with self.assertRaises(tui.QuitApp):
            self._pick([ord("Q")])

    def test_lowercase_q_still_only_cancels(self):
        """기존 근육기억을 깨뜨리지 않는다 — 소문자는 여전히 '뒤로'다."""
        self.assertIsNone(self._pick([ord("q")])[0])

    def test_the_string_representation_quits_too(self):
        """`read_key(wide=True)`는 정수 대신 문자열을 준다.

        `pick`은 `key_char()`로 정규화한 뒤 비교해야 하므로 두 표현이 같은
        결과를 내야 한다. 한쪽만 처리하면 그 모드에서 키가 조용히 죽는다.
        """
        with self.assertRaises(tui.QuitApp):
            self._pick(["Q"])

    def test_quit_can_be_disabled(self):
        """게임 진행 중 화면은 `Q`를 막는다 — 랩 컨테이너가 뜬 채로 남는다.

        막으면 `Q`는 그냥 무시되고 계속 고르게 된다(취소가 아니다).
        """
        idx, _ = self._pick([ord("Q"), 10], allow_quit=False)
        self.assertEqual(idx, 0)

    def test_the_default_footer_advertises_quit(self):
        _, screen = self._pick([10])
        self.assertIn("Q 종료", "".join(screen.drawn))

    def test_the_default_footer_hides_quit_when_it_is_disabled(self):
        """없는 키를 안내하면 안내가 거짓말이 된다."""
        _, screen = self._pick([10], allow_quit=False)
        self.assertNotIn("Q 종료", "".join(screen.drawn))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_tui -v 2>&1 | tail -25`

Expected: FAIL. `QuitAppTest`는 `AttributeError: module 'tui' has no attribute 'QuitApp'`, `PickTest`의 새 메서드들은 같은 `AttributeError` 또는 `TypeError: pick() got an unexpected keyword argument 'allow_quit'`.

- [ ] **Step 3: `QuitApp`을 정의한다**

`scripts/tui.py`의 import 블록 바로 아래(`import unicodedata` 다음 빈 줄 뒤, `# ---- 표시 폭 계산 ----` 구분선 **앞**)에 추가한다:

```python
# --------------------------------------------------------------------------- #
# 전역 종료 신호
# --------------------------------------------------------------------------- #
class QuitApp(BaseException):
    """어느 화면에서든 앱 전체를 끝내라는 신호.

    이 저장소의 화면 스택은 곧 함수 호출 스택이다(`guide.main` →
    `reading.main` → 3중 while 루프). '한 층 위로'를 뜻하는 `None` 반환으로는
    바닥에서 꼭대기까지 나갈 수 없어서, 챕터 목록에서 앱을 끄려면 Esc를 네 번
    누르고 중간에 Enter 프롬프트까지 통과해야 했다(이슈 #95).

    중간 루프는 이 예외를 **잡지 않는다** — 그게 이 방식의 요점이다. 잡는 곳은
    `guide.main`과 `reading`·`shooting`의 `__main__` 블록뿐이다.

    `Exception`이 아니라 `BaseException`을 상속하는 것은 **의도**다.
    `shooting.py`는 화면 코드를 `except Exception:`으로 감싸 traceback을 찍고
    라인 모드로 폴백한다(`choose_stage`·`cmd_play`). 조용히 넘기지 않으려고
    일부러 넣은 안전망이라, 평범한 `Exception`이었다면 `Q`를 누른 사용자가
    traceback과 함께 라인 모드로 떨어졌을 것이다. 그 두 곳에 `except QuitApp:
    raise`를 다는 것으로도 막을 수 있지만, 그러면 앞으로 추가되는 모든
    `except Exception`이 같은 함정을 다시 판다. `SystemExit`·
    `KeyboardInterrupt`가 `BaseException`인 이유와 같다.
    """
```

- [ ] **Step 4: `pick()`에 `allow_quit`을 단다**

`scripts/tui.py`의 `pick()` 시그니처를 바꾼다:

```python
def pick(stdscr, curses, title, labels, footer=None,
         allow_cancel=True, allow_quit=True):
```

docstring 끝(`"""` 닫기 직전)에 한 문단을 덧붙인다:

```
    `allow_quit`이면 **대문자** `Q`가 `QuitApp`을 올려 앱 전체를 끝낸다.
    소문자 `q`는 그대로 '취소/뒤로'다 — 둘을 가르는 것이 이 화면의 계약이다.
    게임이 진행 중인 화면(`shooting._pick_client_target`)은 이걸 꺼야 한다.
    거기서 앱을 끄면 랩 컨테이너가 뜬 채로 남는다.
```

기본 footer 조립(374–375행)을 바꾼다. **현재:**

```python
        hint = footer or (" ↑↓ 또는 숫자 선택   Enter 확정" +
                          ("   Esc/q 취소 " if allow_cancel else " "))
```

**변경 후** (끝 공백이 정확히 한 번만 붙게 한다):

```python
        hint = footer or (" ↑↓ 또는 숫자 선택   Enter 확정" +
                          ("   Esc/q 취소" if allow_cancel else "") +
                          ("   Q 종료 " if allow_quit else " "))
```

키 판정(390행)을 바꾼다. **현재:**

```python
        ch = (key_char(key) or "").lower()
```

**변경 후** — `.lower()`로 접기 **전에** 대문자를 본다. 접고 나면 `q`와 `Q`가 구분되지 않는다:

```python
        raw = key_char(key) or ""
        if allow_quit and raw == "Q":       # 소문자로 접기 **전에** 검사한다
            raise QuitApp
        ch = raw.lower()
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python3 -m unittest tests.test_tui -v 2>&1 | tail -25`

Expected: PASS (`QuitAppTest` 2개 + `PickTest` 신규 6개 포함, 기존 `PickTest` 케이스도 전부 그대로 통과).

- [ ] **Step 6: 커밋**

```bash
git add scripts/tui.py tests/test_tui.py
git commit -m "Add a global quit key to the shared picker

tui.pick() folded the key to lowercase before comparing, so q and Q were
the same key. Split them: q still cancels, Q raises the new QuitApp.

QuitApp derives from BaseException, not Exception, because shooting.py
wraps its screens in two deliberate 'except Exception' nets that print a
traceback and fall back to line mode.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `pause_after_output()`의 탈출구

**Files:**
- Modify: `scripts/tui.py:465–489` (`pause_after_output`)
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `tui.QuitApp` (Task 1)
- Produces: `tui.pause_after_output() -> None` — 프롬프트가 `"\n계속하려면 Enter (q=종료)..."`로 바뀌고, 입력이 `q`/`Q`(주변 공백 무시)면 `QuitApp`을 올린다. 비-tty에서 `input`을 아예 부르지 않는 기존 계약은 그대로.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_tui.py`의 `class PageTextCharacterizationTest` **바로 앞**에 추가한다:

```python
class PauseAfterOutputTest(unittest.TestCase):
    """멈춤 프롬프트가 막다른 골목이면 안 된다 (이슈 #95).

    이건 curses가 아니라 평문 `input()`이다. 그래서 Esc도 `q`도 그냥 글자로
    먹혔고, 사용자 눈에는 "Esc를 눌러도 아무 일이 없는 화면"이었다. 나가는 길을
    하나 만들되 `input()` 기반은 유지한다 — raw 단일키 읽기(`termios`)로 바꾸면
    비-tty 가드와 `tui.input` 교체 가능성이 함께 무너진다.
    """

    @contextlib.contextmanager
    def _tty(self, is_tty=True, answer=""):
        """`input`을 가로채고 tty 여부를 고정한다.

        `answer`가 예외 **클래스**면 `input`이 그걸 올린다.
        """
        real_in, real_out = sys.stdin.isatty, sys.stdout.isatty
        sys.stdin.isatty = lambda: is_tty
        sys.stdout.isatty = lambda: is_tty
        asked = []

        def fake_input(prompt=""):
            asked.append(prompt)
            if isinstance(answer, type) and issubclass(answer, BaseException):
                raise answer
            return answer

        tui.input = fake_input
        try:
            yield asked
        finally:
            del tui.input
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out

    def test_enter_just_continues(self):
        with self._tty(answer="") as asked:
            tui.pause_after_output()
        self.assertEqual(len(asked), 1)

    def test_the_prompt_advertises_the_quit_key(self):
        """안내가 없으면 있어도 없는 기능이다."""
        with self._tty(answer="") as asked:
            tui.pause_after_output()
        self.assertIn("q=종료", asked[0])

    def test_q_quits_the_app(self):
        """대소문자와 주변 공백을 모두 받는다.

        이 프롬프트에는 '뒤로'라는 선택지가 없어 `q`/`Q`를 가를 이유가 없고,
        화면에 `q=종료`라고 소문자로 적어 두므로 대문자를 요구하면 오히려 안
        먹히는 것처럼 보인다.
        """
        for answer in ("q", "Q", " q ", "Q\n"):
            with self.subTest(answer=answer):
                with self._tty(answer=answer):
                    with self.assertRaises(tui.QuitApp):
                        tui.pause_after_output()

    def test_anything_else_just_continues(self):
        with self._tty(answer="아무거나"):
            tui.pause_after_output()     # 예외 없이 돌아와야 한다

    def test_it_still_swallows_eof_and_interrupt(self):
        """호출부가 애써 격리해 둔 것이 여기서 새면 무의미해진다."""
        for exc in (EOFError, KeyboardInterrupt):
            with self.subTest(exc=exc):
                with self._tty(answer=exc):
                    tui.pause_after_output()

    def test_it_does_not_prompt_when_not_a_tty(self):
        """파이프에서 `input()`을 부르면 다음 입력 줄을 삼켜 실행이 깨진다."""
        with self._tty(is_tty=False) as asked:
            tui.pause_after_output()
        self.assertEqual(asked, [])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_tui.PauseAfterOutputTest -v`

Expected: FAIL. `test_the_prompt_advertises_the_quit_key`는 `"q=종료" not found in "\n계속하려면 Enter를 누르세요..."`, `test_q_quits_the_app`은 `QuitApp` 미발생.

- [ ] **Step 3: 구현한다**

`scripts/tui.py`의 `pause_after_output()` 본문(486–489행)을 바꾼다. **현재:**

```python
    try:
        input("\n계속하려면 Enter를 누르세요...")
    except (EOFError, KeyboardInterrupt):
        pass
```

**변경 후:**

```python
    try:
        raw = input("\n계속하려면 Enter (q=종료)...")
    except (EOFError, KeyboardInterrupt):
        return
    if raw.strip().lower() == "q":
        raise QuitApp
```

같은 함수 docstring 끝에 한 문단을 덧붙인다:

```
    **`q`로 나갈 수 있다.** 이 프롬프트는 curses가 아니라 평문 `input()`이라
    Esc도 `q`도 그냥 글자로 먹혔고, 사용자 눈에는 앱이 멈춘 것으로 보였다
    (이슈 #95). 대소문자를 가르지 않는 것은 의도다 — 여기엔 '뒤로'라는 선택지가
    없어 `q`/`Q`를 구분할 이유가 없고, 화면에 소문자로 안내하기 때문이다.
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_tui -v 2>&1 | tail -20`

Expected: PASS. **주의:** `tests/test_guide.py`의 `PauseAfterModeTest`도 이 함수를 거치지만 `tui.input`을 가짜로 바꾸고 반환값이 `MagicMock`이 아니라 리스트 `append`의 결과(`None`)라 `.strip()`에서 깨질 수 있다. 다음 단계에서 확인한다.

- [ ] **Step 5: 기존 guide 테스트가 깨지지 않았는지 본다**

Run: `python3 -m unittest tests.test_guide -v 2>&1 | tail -20`

Expected: `PauseAfterModeTest`의 `test_it_pauses_when_a_tty`가 **FAIL** 할 수 있다 — 그 테스트의 `tui.input = lambda *a, **k: called.append(1)`은 `None`을 돌려주고, 새 코드가 `None.strip()`을 부르기 때문이다. 실패하면 그 두 람다를 고친다:

`tests/test_guide.py:195`와 `tests/test_guide.py:206`의

```python
        tui.input = lambda *a, **k: called.append(1)
```

를 각각 아래로 바꾼다 (`append`는 `None`을 돌려주므로 문자열을 명시적으로 돌려줘야 한다):

```python
        # `pause_after_output`이 이제 반환값을 `.strip()` 한다 —
        # `list.append`가 돌려주는 `None`으로는 그 경로를 지날 수 없다.
        tui.input = lambda *a, **k: called.append(1) or ""
```

- [ ] **Step 6: 두 스위트가 함께 통과하는지 확인한다**

Run: `python3 -m unittest tests.test_tui tests.test_guide 2>&1 | tail -5`

Expected: `OK`

- [ ] **Step 7: 커밋**

```bash
git add scripts/tui.py tests/test_tui.py tests/test_guide.py
git commit -m "Give the pause prompt a way out

The 'press Enter to continue' prompt is a plain input(), so Esc and q were
just characters and the app looked frozen. Accept q as quit; keep input()
so the non-tty guard and the swappable tui.input both survive.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `page_text()`가 평문으로 찍었는지 알려준다

**Files:**
- Modify: `scripts/tui.py:563–581` (`page_text`)
- Test: `tests/test_tui.py:341–386` (기존 3개 갱신)

**Interfaces:**
- Consumes: 없음
- Produces: `tui.page_text(text) -> (returncode: int, printed_inline: bool)` — 이전에는 `int` 하나였다. `printed_inline`은 "페이저를 못 써서 이 함수가 직접 `print`했는가".

- [ ] **Step 1: 기존 테스트를 새 계약으로 고쳐 실패하게 만든다**

`tests/test_tui.py`의 `PageTextCharacterizationTest` 세 메서드를 고친다.

`test_hands_the_text_to_the_pager` (355–360행 근처):

```python
            with self._env(pager="less -R"):
                rc, printed = self.mod.page_text("본문")
        finally:
            self.mod.subprocess.Popen = real
        self.assertEqual(seen["cmd"], ["less", "-R"])
        self.assertEqual(seen["text"], "본문")
        self.assertEqual(rc, 7)
        self.assertFalse(printed, "페이저가 삼켰는데 평문으로 찍었다고 보고했다")
```

`test_prints_plainly_when_there_is_no_pager`:

```python
    def test_prints_plainly_when_there_is_no_pager(self):
        buf = io.StringIO()
        with self._env(pager=None, which=None):
            with contextlib.redirect_stdout(buf):
                rc, printed = self.mod.page_text("본문")
        self.assertEqual(buf.getvalue().strip(), "본문")
        self.assertEqual(rc, 0)
        self.assertTrue(printed, "직접 찍어 놓고 아니라고 보고했다")
```

`test_a_failing_pager_falls_back_to_printing`:

```python
            with self._env(pager="없는페이저"):
                with contextlib.redirect_stdout(buf):
                    rc, printed = self.mod.page_text("본문")
        finally:
            self.mod.subprocess.Popen = real
        self.assertEqual(buf.getvalue().strip(), "본문")
        self.assertEqual(rc, 0)
        self.assertTrue(printed, "폴백으로 직접 찍어 놓고 아니라고 보고했다")
```

그리고 클래스 docstring 끝에 한 줄을 덧붙인다:

```
    v1.3.x 부터 `(returncode, printed_inline)` 튜플을 돌려준다 — 호출부가
    `pause_after_output()` 을 부를지 정하는 데 두 번째 값을 쓴다(이슈 #95).
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_tui.PageTextCharacterizationTest -v`

Expected: FAIL — `TypeError: cannot unpack non-sequence int` (또는 `int object is not iterable`).

- [ ] **Step 3: 구현한다**

`scripts/tui.py`의 `page_text()` 전체를 아래로 바꾼다:

```python
def page_text(text):
    """텍스트를 페이저로 넘긴다 → `(returncode, printed_inline)`.

    뷰어를 curses로 만들지 않는다 — `less`가 스크롤·검색(`/`)을 이미 다 한다.
    목록 UI조차 필요 없다: 이어 붙여 넘기면 끝이다.

    `printed_inline`은 **페이저를 못 써서 이 함수가 직접 `print`했는가**다.
    호출부가 `pause_after_output()`을 부를지 정하는 데 쓴다 — 페이저가
    삼켰다면 대체 화면이 복원되므로 지킬 평문이 없고, 그때도 멈추면 챕터를
    읽을 때마다 뜻 없는 Enter를 한 번씩 요구하게 된다(이슈 #95).

    판정이 여기 있는 이유: `PAGER` 해석과 `less` 존재 확인 규칙이 이미 이
    함수 안에 있다. 호출부에서 같은 조건을 다시 쓰면 규칙이 두 곳으로 갈라진다.

    **알려진 한계**: `PAGER=cat`처럼 대체 화면을 쓰지 않는 페이저를 지정하면
    본문이 화면에 남는데도 `printed_inline`은 `False`가 되어 다음 curses
    프레임이 그것을 지운다. 페이저가 대체 화면을 쓰는지 알아낼 이식성 있는
    방법이 없다. `COLOR_PAGERS`를 재사용하는 것도 답이 아니다 — 그 목록은
    "ANSI를 통과시키는가"를 뜻하지 "화면을 복원하는가"를 뜻하지 않는다.
    """
    pager = os.environ.get("PAGER") or ("less -R" if shutil.which("less")
                                        else None)
    if not pager:
        print(text)
        return 0, True
    try:
        proc = subprocess.Popen(_with_raw_flag(shlex.split(pager)),
                                stdin=subprocess.PIPE, text=True)
        proc.communicate(text)
        return proc.returncode, False
    except (OSError, KeyboardInterrupt):
        print(text)
        return 0, True
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_tui -v 2>&1 | tail -10`

Expected: PASS

- [ ] **Step 5: 다른 호출부가 깨지지 않았는지 본다**

`shooting.py:1450`과 `shooting.py:2882`는 반환값을 무시하므로 영향이 없다. `reading.py:93`은 다음 태스크에서 고친다. 지금 전체 스위트를 돌려 그 사실을 확인한다.

Run: `python3 -m unittest discover -s tests 2>&1 | tail -20`

Expected: `tests.test_reading`의 `ReadChapterTest`는 아직 통과한다 (`reading.read_chapter`가 반환값을 쓰지 않으므로). `OK`가 나와야 한다.

- [ ] **Step 6: 커밋**

```bash
git add scripts/tui.py tests/test_tui.py
git commit -m "Report from page_text whether it printed inline

Callers need to know if there is any plain text left on screen worth
pausing for. The PAGER and less lookup already lives here; deciding it
again at the call site would split the rule in two.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `reading` — 조건부 멈춤 · footer · 단독 실행 가드

**Files:**
- Modify: `scripts/reading.py` (21–22행 import, 82행 footer, 87–95행 `read_chapter`, 139–160행 `main` 안쪽 루프, 163–164행 `__main__`)
- Test: `tests/test_reading.py` (`ReadChapterTest._capture` 갱신 + 새 클래스)

**Interfaces:**
- Consumes: `tui.QuitApp` (Task 1), `tui.pause_after_output` (Task 2), `tui.page_text -> (rc, printed_inline)` (Task 3)
- Produces: `reading.read_chapter(rel, dbms=None) -> bool` — `page_text`의 `printed_inline`을 그대로 돌려준다. 이전에는 `None`이었다.

- [ ] **Step 1: 기존 픽스처를 새 계약으로 고치고 실패하는 테스트를 쓴다**

먼저 `tests/test_reading.py`의 `ReadChapterTest._capture`(179–187행)를 고친다. 지금은 텍스트를 그대로 돌려주는 람다라 튜플 언패킹에서 깨진다:

```python
    @contextlib.contextmanager
    def _capture(self, printed=False):
        """`page_text` 를 가로챈다.

        `page_text` 는 이제 `(returncode, printed_inline)` 튜플을 돌려주므로
        대역도 같은 모양이어야 한다 — 문자열을 돌려주면 호출부의 언패킹이
        깨진다.
        """
        seen = {}
        real = reading.page_text

        def fake(text):
            seen["text"] = text
            return 0, printed

        reading.page_text = fake
        try:
            yield seen
        finally:
            reading.page_text = real
```

같은 클래스 끝에 새 테스트를 추가한다:

```python
    def test_it_reports_whether_it_printed_inline(self):
        """`page_text` 의 `printed_inline` 을 그대로 넘겨야 한다.

        호출부(`main`)가 이 값 하나로 pause 여부를 정한다. 여기서 삼키면
        페이저가 없는 환경에서 챕터 본문이 다음 curses 프레임에 지워진다.
        """
        with self._capture(printed=True):
            self.assertTrue(reading.read_chapter(self.CHAPTER))
        with self._capture(printed=False):
            self.assertFalse(reading.read_chapter(self.CHAPTER))
```

그리고 파일 맨 끝(`ReadingMainTest` 뒤)에 새 클래스를 추가한다:

```python
class ChapterPauseTest(unittest.TestCase):
    """이슈 #95 — 챕터를 읽을 때마다 뜻 없는 'Enter'를 요구하지 않는다.

    `pause_after_output()`은 "다음 curses 프레임의 `erase()`가 방금 찍힌 평문을
    한 프레임도 못 읽히고 지우는 것"을 막으려고 있다. `less`가 본문을 삼켰다면
    지킬 평문이 애초에 없다 — 그런데도 무조건 불러서, 챕터를 하나 읽을 때마다
    Enter를 한 번씩 눌러야 했다.
    """

    @contextlib.contextmanager
    def _flow(self, picks, printed=False, took_exam=False):
        """선택 → 읽기 → (시험) 한 바퀴를 돌리고 pause 호출을 센다."""
        seq = iter(list(picks) + [None] * 6)
        paused = []
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "offer_exam",
                          "pause_after_output")}
        real_exam_main = reading.exam.main
        reading.choose = lambda title, labels: next(seq)
        reading.read_chapter = lambda rel, dbms=None: printed
        reading.offer_exam = lambda rel, bank, ask=input: took_exam
        reading.pause_after_output = lambda: paused.append(1)
        reading.exam.main = lambda argv: 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield paused
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)
            reading.exam.main = real_exam_main

    def test_the_pager_swallowed_it_so_we_do_not_pause(self):
        """평상시 경로다 — `less`가 있으면 여기로 온다."""
        with self._flow([0, 0, 0], printed=False) as paused:
            reading.main([])
        self.assertEqual(paused, [], "페이저가 삼켰는데도 멈췄다")

    def test_a_plain_text_fallback_still_pauses(self):
        """`$PAGER`도 `less`도 없으면 본문이 그대로 찍힌다 — 지켜야 한다."""
        with self._flow([0, 0, 0], printed=True) as paused:
            reading.main([])
        self.assertEqual(paused, [1])

    def test_taking_the_exam_pauses_even_when_the_pager_swallowed_it(self):
        """`exam.main`이 평문을 남겼을 수 있다 — 안전한 쪽으로 떨어진다.

        1=PostgreSQL, 1=02-intermediate, 1=그 티어의 두 번째 챕터(은행 있음).
        `offer_exam`이 '예'라고 답하도록 고정한다.
        """
        with self._flow([1, 1, 1], printed=False, took_exam=True) as paused:
            reading.main([])
        self.assertEqual(paused, [1])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_reading -v 2>&1 | tail -25`

Expected: FAIL.
- `test_it_reports_whether_it_printed_inline` → `read_chapter`가 `None`을 돌려줘 `assertTrue` 실패.
- `test_the_pager_swallowed_it_so_we_do_not_pause` → `paused == [1]`이라 실패 (지금은 무조건 멈춘다).

- [ ] **Step 3: `read_chapter`가 값을 돌려주게 한다**

`scripts/reading.py:87–95`를 바꾼다:

```python
def read_chapter(rel, dbms=None):
    """본문을 렌더해 `$PAGER` 로 넘긴다 → 평문으로 직접 찍었는가.

    curses 밖에서 부른다.

    렌더는 벤더 필터 **뒤**다 — 순서가 뒤집히면 `filter_lines` 가 찾는
    `<!-- dbms:… -->` 마커가 이미 지워져 있어 다른 벤더 본문이 그대로 남는다.

    반환값은 `page_text` 의 `printed_inline` 을 그대로 넘긴 것이다. 호출부가
    `pause_after_output()` 을 부를지 정하는 데 쓴다 — 페이저가 삼켰다면 화면이
    복원되므로 지킬 평문이 없다(이슈 #95).
    """
    _, printed_inline = page_text(
        markdown_render.render(chapter_text(rel, dbms),
                               width=text_width(),
                               color=pager_supports_color()))
    return printed_inline
```

- [ ] **Step 4: `main`의 멈춤을 조건부로 만든다**

`scripts/reading.py`의 챕터 루프(145–160행)를 바꾼다. **현재:**

```python
                rel = chapters[c_idx]
                bank = exam.exam_bank_for(rel)
                read_chapter(rel, dbms)
                if offer_exam(rel, bank):
                    # (기존 주석)
                    args = [str(exam.REPO_ROOT / bank)]
                    if dbms:
                        args += ["--dbms", dbms]
                    exam.main(args)
                pause_after_output()
```

**변경 후** — 기존 주석 블록(149–155행의 `# exam.main은 대상 인자를 …` 여덟 줄)은 **그대로 두고** 위치만 유지한다:

```python
                rel = chapters[c_idx]
                bank = exam.exam_bank_for(rel)
                printed = read_chapter(rel, dbms)
                ran_exam = offer_exam(rel, bank)
                if ran_exam:
                    # `exam.main`은 대상 인자를 cwd 기준 상대경로로 받는다
                    # (CLI 계약이라 `exam` 쪽에서 바꾸지 않는다) — 여기서는
                    # `./guide`를 저장소 밖 cwd에서 띄운 경우에도 은행을 찾게
                    # `REPO_ROOT` 기준 절대경로로 넘긴다. 고른 벤더도 함께
                    # 넘기지 않으면 PostgreSQL 챕터를 읽고도 MySQL·Oracle
                    # 문항까지 다 나온다 — `dbms`가 `None`("전체")이면 그대로
                    # 생략해 `exam`이 스스로 묻게 한다.
                    args = [str(exam.REPO_ROOT / bank)]
                    if dbms:
                        args += ["--dbms", dbms]
                    exam.main(args)
                # 페이저가 본문을 삼켰고 시험도 보지 않았다면 화면에 지킬 평문이
                # 없다. 그때도 멈추면 챕터를 읽을 때마다 뜻 없는 Enter를 한 번씩
                # 요구하게 된다(이슈 #95). 시험을 봤다면 `exam.main`이 평문을
                # 남겼을 수 있으므로 안전한 쪽(멈춤)으로 떨어진다 — 조용히
                # 끝났는지까지 알아내려면 `exam` 내부를 들여다봐야 한다.
                if printed or ran_exam:
                    pause_after_output()
```

- [ ] **Step 5: 통과를 확인한다**

Run: `python3 -m unittest tests.test_reading -v 2>&1 | tail -25`

Expected: PASS (기존 `ReadingMainTest` 포함).

- [ ] **Step 6: footer와 단독 실행 가드를 단다**

`scripts/reading.py:21–22`의 import에 `QuitApp`을 더한다:

```python
from tui import (QuitApp, page_text, pager_supports_color,  # noqa: E402
                 pause_after_output, pick, pick_line, text_width)
```

`scripts/reading.py:82`의 footer를 바꾼다:

```python
        return pick(stdscr, curses, title, labels,
                    footer=" ↑↓ 또는 숫자 선택   Enter 선택   Esc/q 뒤로   Q 종료 ")
```

파일 끝(163–164행)의 `__main__` 블록을 바꾼다:

```python
if __name__ == "__main__":
    # `python3 scripts/reading.py`로 직접 돌릴 때는 `guide.main`의 그물이 없다.
    # 그대로 두면 `Q` 한 번에 트레이스백이 뜬다.
    try:
        sys.exit(main())
    except QuitApp:
        sys.exit(0)
```

- [ ] **Step 7: footer 배선 테스트를 더한다**

`tests/test_reading.py`의 `ChapterPauseTest` 뒤에 추가한다.

**소스를 grep하지 않는다** — `reading.py`에는 이미 `"Esc/q 뒤로"`가 있어서 그런 단언은 변경 전에도 통과한다(실측). `pick`에 **실제로 넘어간** `footer` 값을 본다.

`choose`는 `curses.wrapper`를 함수 안에서 부르므로 진짜 터미널을 잡으려 든다. `import curses`는 `sys.modules`를 먼저 보므로, 거기에 대역을 끼워 넣으면 가로챌 수 있다. `finally`에서 **반드시** 원래대로 되돌린다 — 스위트가 한 프로세스에서 순차로 돌기 때문에 남겨 두면 뒤따르는 테스트가 조용히 망가진다.

파일 상단 import 블록에 `import types`를 더한다(없으면).

```python
class ReadingQuitKeyTest(unittest.TestCase):
    """선택 화면이 종료 키를 안내해야 한다 — 없는 것처럼 보이면 없는 것이다."""

    def test_the_footer_offers_both_back_and_quit(self):
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
            reading.choose("제목", ["a", "b"])
        finally:
            reading.pick = real_pick
            if real_curses is None:
                del sys.modules["curses"]
            else:
                sys.modules["curses"] = real_curses
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out

        self.assertIn("Esc/q 뒤로", seen["footer"], seen)
        self.assertIn("Q 종료", seen["footer"], seen)

    def test_running_it_standalone_survives_a_quit(self):
        """`__main__` 가드가 없으면 `Q` 한 번에 트레이스백이 뜬다.

        여기만 소스를 읽는다 — 파이프로 실행하면 비-tty라 `pick_line` 경로로
        떨어지고, 그 경로에는 `Q`가 없어서(설계상 범위 밖) 동작으로는 이
        가드에 도달할 방법이 없다. `test_the_launcher_points_at_the_script`가
        런처 내용을 읽는 것과 같은 종류의 검사다.
        """
        body = (REPO_ROOT / "scripts" / "reading.py").read_text(
            encoding="utf-8")
        self.assertIn("except QuitApp", body)
```

- [ ] **Step 8: 통과를 확인한다**

Run: `python3 -m unittest tests.test_reading 2>&1 | tail -5`

Expected: `OK`

- [ ] **Step 9: 커밋**

```bash
git add scripts/reading.py tests/test_reading.py
git commit -m "Stop asking for Enter after every chapter

read_chapter now forwards page_text's printed_inline, and main pauses only
when there is plain text left to protect or the exam just ran. Also
advertises the new Q key and guards standalone runs against it.

Fixes the third symptom in #95: the prompt only accepts Enter, so Esc and q
did nothing and the app looked frozen.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `guide` — `Mode.pause` · `run_mode` 반환값 · `QuitApp` 포착

**Files:**
- Modify: `scripts/guide.py` (25행 import, 31행 `Mode`, 55–59행 `MODES`, 74–98행 `run_mode`, 129–150행 `main`)
- Test: `tests/test_guide.py` (`ModeTableTest`·`ModeIsolationTest`·`PauseAfterModeTest` 갱신 + 새 클래스)

**Interfaces:**
- Consumes: `tui.QuitApp` (Task 1)
- Produces:
  - `guide.Mode` — 필드가 `key title scale run pause` 다섯 개로 늘어난다 (`pause`는 bool).
  - `guide.run_mode(mode) -> bool` — "평문 메시지를 찍었는가". 이전에는 `None`이었다.
  - `guide.main(argv=None) -> int` — `QuitApp`을 잡아 `0`을 돌려준다.

- [ ] **Step 1: 기존 테스트를 새 시그니처로 고친다**

`tests/test_guide.py`에서 `guide.Mode(...)`를 만드는 두 곳에 다섯 번째 인자를 더한다.

`ModeTableTest.test_labels_align_by_display_width_not_char_count`(68–72행):

```python
        fake_modes = (
            guide.Mode("a", "학습 점검 (퀴즈/시험)", lambda: "0문항",
                       lambda: 0, True),
            guide.Mode("b", "장애 대응 (실전 훈련)", lambda: "0스테이지",
                       lambda: 0, True),
            guide.Mode("c", "챕터 읽기", lambda: "0챕터", lambda: 0, False),
        )
```

`ModeIsolationTest._mode`(118–119행):

```python
    def _mode(self, boom):
        return guide.Mode("t", "테스트", lambda: "0개", boom, False)
```

`ModeIsolationTest.test_a_normal_return_comes_back`(127–128행)은 이제 반환값도 검사한다:

```python
    def test_a_normal_return_comes_back(self):
        self.assertEqual(self._run(lambda: 0), "")

    def test_a_normal_return_reports_nothing_was_printed(self):
        """`main`이 이 값으로 멈출지 정한다 — 안 찍었으면 멈출 이유가 없다."""
        self.assertFalse(guide.run_mode(self._mode(lambda: 0)))

    def test_a_reported_failure_says_it_printed(self):
        def boom():
            raise SystemExit("출제할 문항이 없습니다(필터 조건 확인).")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(guide.run_mode(self._mode(boom)))

    def test_an_interrupt_says_it_printed(self):
        def boom():
            raise KeyboardInterrupt
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(guide.run_mode(self._mode(boom)))

    def test_a_silent_system_exit_says_nothing_was_printed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(guide.run_mode(
                self._mode(lambda: (_ for _ in ()).throw(SystemExit(0)))))
```

`PauseAfterModeTest.test_main_pauses_after_each_mode_but_not_after_quitting`(226–245행)을 통째로 갈아 끼운다. `pause` 조건이 생겨 "모드마다 무조건 멈춘다"는 전제가 더는 성립하지 않는다:

```python
    def test_main_pauses_after_a_pausing_mode_but_not_after_quitting(self):
        """배선 테스트: `main()`이 `run_mode` 뒤에 `pause_after_mode`를 부르는가.

        마지막에 메뉴에서 바로 종료(`None`)할 때는 `run_mode`가 안 불리므로
        `pause_after_mode`도 불리면 안 된다.

        모드를 **키로** 찾는다 — 인덱스를 박아 두면 메뉴 순서를 바꿀 때마다
        이 배선 테스트가 함께 깨진다(`MainLoopTest._index_of`와 같은 이유).
        """
        keys = [m.key for m in guide.MODES]
        picks = [keys.index("exam"), keys.index("shoot")]
        real_choose, real_run, real_pause = (
            guide.choose_menu, guide.run_mode, guide.pause_after_mode)
        seq = iter(picks + [None])
        paused = []
        guide.choose_menu = lambda labels: next(seq)
        guide.run_mode = lambda mode: False      # 아무것도 찍지 않았다
        guide.pause_after_mode = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                guide.main([])
        finally:
            guide.choose_menu, guide.run_mode, guide.pause_after_mode = (
                real_choose, real_run, real_pause)
        self.assertEqual(paused, [1, 1])

    def test_the_read_mode_does_not_pause_because_reading_decides_itself(self):
        """`reading`이 페이저 사용 여부를 보고 더 정확하게 판단한다 (이슈 #95).

        여기서 또 멈추면 그 판단이 무의미해지고, 챕터 하나 읽고 나갈 때 Enter
        프롬프트를 두 번 통과해야 한다.
        """
        keys = [m.key for m in guide.MODES]
        real_choose, real_run, real_pause = (
            guide.choose_menu, guide.run_mode, guide.pause_after_mode)
        seq = iter([keys.index("read"), None])
        paused = []
        guide.choose_menu = lambda labels: next(seq)
        guide.run_mode = lambda mode: False
        guide.pause_after_mode = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                guide.main([])
        finally:
            guide.choose_menu, guide.run_mode, guide.pause_after_mode = (
                real_choose, real_run, real_pause)
        self.assertEqual(paused, [])

    def test_a_mode_that_printed_pauses_even_if_it_does_not_normally(self):
        """`run_mode`가 사유를 찍었다면 `pause` 값과 무관하게 멈춰야 한다.

        `read` 모드도 여기 해당한다 — `reading.main`이 `exam.main`의
        `SystemExit`을 그대로 흘려보낸다.
        """
        keys = [m.key for m in guide.MODES]
        real_choose, real_run, real_pause = (
            guide.choose_menu, guide.run_mode, guide.pause_after_mode)
        seq = iter([keys.index("read"), None])
        paused = []
        guide.choose_menu = lambda labels: next(seq)
        guide.run_mode = lambda mode: True       # 뭔가 찍었다
        guide.pause_after_mode = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                guide.main([])
        finally:
            guide.choose_menu, guide.run_mode, guide.pause_after_mode = (
                real_choose, real_run, real_pause)
        self.assertEqual(paused, [1])
```

`ModeTableTest`에 `pause` 값을 고정하는 테스트를 더한다 (`test_modes_are_offered_in_learning_order` 바로 뒤):

```python
    def test_only_the_read_mode_skips_the_launcher_pause(self):
        """`reading`은 페이저 사용 여부를 보고 스스로 판단한다 (이슈 #95).

        `exam`·`shoot`은 평문을 남기므로 런처가 멈춰 줘야 한다 — `shoot`은
        등급표·후일담을, `exam`은 라인 모드 진행을 평문으로 찍는다.
        """
        self.assertEqual({m.key: m.pause for m in guide.MODES},
                         {"read": False, "exam": True, "shoot": True})
```

마지막으로 파일 끝에 새 클래스를 추가한다:

```python
class GlobalQuitTest(unittest.TestCase):
    """이슈 #95 — 어느 화면에서든 `Q` 한 타로 앱이 끝나야 한다.

    `run_mode`는 `QuitApp`을 **잡지 않는다**. 지금도 `SystemExit`·
    `KeyboardInterrupt`만 잡으므로 그대로 통과하는데, 나중에
    `except Exception`으로 넓히는 사람을 막으려면 그 계약을 테스트로 못 박아야
    한다.
    """

    def test_run_mode_does_not_swallow_a_quit(self):
        def boom():
            raise tui.QuitApp
        mode = guide.Mode("t", "테스트", lambda: "0개", boom, False)
        with self.assertRaises(tui.QuitApp):
            with contextlib.redirect_stdout(io.StringIO()):
                guide.run_mode(mode)

    def test_main_ends_cleanly_when_a_mode_quits(self):
        real_choose, real_run = guide.choose_menu, guide.run_mode

        def boom(mode):
            raise tui.QuitApp

        guide.choose_menu = lambda labels: 0
        guide.run_mode = boom
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(guide.main([]), 0)
        finally:
            guide.choose_menu, guide.run_mode = real_choose, real_run

    def test_main_ends_cleanly_when_the_menu_quits(self):
        """최상위 메뉴에서 `Q`를 눌러도 같은 경로로 끝나야 한다."""
        real_choose = guide.choose_menu

        def boom(labels):
            raise tui.QuitApp

        guide.choose_menu = boom
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(guide.main([]), 0)
        finally:
            guide.choose_menu = real_choose
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_guide -v 2>&1 | tail -30`

Expected: FAIL. `Mode.__new__() takes 5 positional arguments but 6 were given` (필드 추가 전), `AttributeError: 'Mode' object has no attribute 'pause'`, `GlobalQuitTest`의 `main`이 `QuitApp`을 흘려보냄.

- [ ] **Step 3: `Mode`와 `MODES`를 바꾼다**

`scripts/guide.py:25`의 import에 `QuitApp`을 더한다:

```python
from tui import (QuitApp, cwidth, pause_after_output, pick,  # noqa: E402
                 pick_line)
```

`Mode` 정의(27–31행)를 바꾼다:

```python
# key   프로그램 안에서 쓰는 짧은 이름
# title 메뉴에 보이는 이름
# scale "216문항"처럼 규모를 한마디로 (개인 기록은 읽지 않는다 — 얇게 유지)
# run   고르면 부를 것. 인자 없이 대화형으로 시작한다
# pause 모드가 끝난 뒤 런처가 멈춰야 하는가. `read`만 False다 — `reading`이
#       페이저 사용 여부를 보고 스스로 더 정확하게 판단하므로, 여기서 또 멈추면
#       챕터 하나 읽고 나갈 때 Enter 프롬프트를 두 번 통과하게 된다(이슈 #95).
Mode = namedtuple("Mode", "key title scale run pause")
```

`MODES`(55–59행)를 바꾼다:

```python
MODES = (
    Mode("read", "챕터 읽기", reading.read_scale,
         lambda: reading.main([]), False),
    Mode("exam", "학습 점검 (퀴즈/시험)", exam_scale,
         lambda: exam.main([]), True),
    Mode("shoot", "장애 대응 (실전 훈련)", shoot_scale,
         lambda: shooting.main([]), True),
)
```

- [ ] **Step 4: `run_mode`가 값을 돌려주게 한다**

`scripts/guide.py:74–98`을 바꾼다. docstring 첫 줄과 마지막 문단만 손대고 나머지 설명은 그대로 둔다:

```python
def run_mode(mode):
    """모드를 돌리고 **반드시** 메뉴로 돌아온다 → 평문 메시지를 찍었는가.

    `main()`들은 끝나는 방식이 다르다. `exam.main`은 SystemExit을 여러 곳에서
    올리고(대상 없음·출제할 문항 없음·문제은행 없음 …), `shooting.main`은
    KeyboardInterrupt를 스스로 잡지 않는다 — 지금까지는 각 모듈의 `__main__`
    블록이 마지막 방어선이었고, 런처가 부르는 순간 그 방어선이 사라진다.
    `reading.main`은 챕터→시험 핸드오프에서 `exam.main`을 그대로 부르고, 거기서
    오르는 SystemExit을 자기도 잡지 않은 채 흘려보낸다 — 세 번째, 독립된
    이유다. 잡지 않으면 모드 하나가 끝나는 것이 런처를 통째로 죽인다.

    **그 둘만 잡는다.** 예상 못 한 예외까지 삼키면 트레이스백이 사라져 버그를
    고칠 수 없게 된다. `tui.QuitApp`(전역 종료)도 여기서 잡지 않고 위로
    흘려보내는 것이 **의도**다 — `main`이 잡아 프로그램을 끝낸다. `QuitApp`은
    `BaseException` 하위라 `except Exception`으로 넓혀도 걸리지 않지만, 애초에
    넓히지 않는다.

    반환값은 화면에 평문을 남겼는지다. `main`이 이 값으로 멈출지 정한다 —
    아무것도 안 찍혔는데 멈추면 뜻 없는 Enter를 요구하게 된다(이슈 #95).
    """
    try:
        mode.run()
    except SystemExit as e:
        # `str(e)` 로 판단하면 샌다 — `str(SystemExit(0))` 은 `"0"` 이고
        # `str(SystemExit(None))` 은 `"None"` 이라 정상 종료가 화면에 찍힌다.
        # `e.code` 는 메시지면 문자열, 정상 종료면 0 또는 None 이다.
        if e.code not in (None, 0):
            print(e.code if isinstance(e.code, str)
                  else f"모드가 코드 {e.code} 로 끝났습니다.")
            return True
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return True
    return False
```

- [ ] **Step 5: `main`이 `QuitApp`을 잡게 한다**

`scripts/guide.py:129–150`의 `main` 본문을 바꾼다:

```python
def main(argv=None):
    """메뉴 → 모드 → 메뉴. 프로그램을 끝내는 곳은 메뉴 하나뿐이다.

    모드 안의 '종료'는 그 모드만 끝낸다 — 여러 모드를 오갈 수 있어야 하므로
    끝내는 자리를 한 곳으로 모은다. 같은 이유로 모드의 종료 코드는 전파하지
    않는다: 여러 번 돌 수 있어 대표할 코드가 없다.

    **예외는 `Q`(전역 종료)뿐이다.** 화면 스택이 곧 호출 스택이라 바닥에서
    한 층씩 되짚어 나오는 것이 유일한 길이었고, 챕터 목록에서 나가려면 Esc를
    네 번 누르고 중간에 Enter 프롬프트까지 통과해야 했다(이슈 #95). `tui.pick`이
    올리는 `QuitApp`은 중간 루프를 그대로 통과해 여기서 끝난다.
    """
    argparse.ArgumentParser(
        prog="guide",
        description="DBA 학습 가이드 — 챕터 읽기·학습 점검·장애 대응을 한 자리에서"
    ).parse_args(argv)

    try:
        while True:
            idx = choose_menu(menu_labels())
            if idx is None:
                return 0
            mode = MODES[idx]
            # 모드가 사유를 찍었다면 `pause` 값과 무관하게 멈춰야 한다.
            if run_mode(mode) or mode.pause:
                pause_after_mode()
    except QuitApp:
        return 0
```

- [ ] **Step 6: 통과를 확인한다**

Run: `python3 -m unittest tests.test_guide -v 2>&1 | tail -25`

Expected: PASS

- [ ] **Step 7: 런처 통합 테스트가 살아 있는지 본다**

`LauncherTest.test_it_runs_and_offers_all_three_modes`가 실제로 `./guide`를 파이프로 실행한다. 비-tty라 `pick_line`으로 떨어지고 `q`로 끝나는데, `pick_line`은 이 작업에서 손대지 않았으므로 그대로 통과해야 한다.

Run: `python3 -m unittest tests.test_guide.LauncherTest -v`

Expected: PASS (4개 전부)

- [ ] **Step 8: 커밋**

```bash
git add scripts/guide.py tests/test_guide.py
git commit -m "Catch the global quit at the launcher and stop double-pausing

main() now ends on QuitApp, and the launcher pause is conditional: run_mode
reports whether it printed anything, and Mode carries a pause flag that is
False for the read mode because reading.py decides for itself.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `shooting` — 게임 중 종료 차단 · footer · 단독 실행 가드

**Files:**
- Modify: `scripts/shooting.py` (46–49행 import, 2510–2512행 `_pick_client_target`, 3140–3157행 `_pick_world_then_stage`, 파일 끝 `__main__`)
- Test: `tests/test_shooting.py`

**Interfaces:**
- Consumes: `tui.QuitApp`, `tui.pick(..., allow_quit=...)` (Task 1)
- Produces: 없음 (다른 태스크가 의존하지 않는다)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_shooting.py`의 `class ChooseStageFilterTest` **바로 앞**에 추가한다:

```python
class GlobalQuitTest(unittest.TestCase):
    """이슈 #95 — `Q`는 앱을 끝내되, 게임이 도는 중에는 막혀 있어야 한다."""

    def setUp(self):
        self.multi = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "2-2-replication-lag.json")

    def test_the_in_game_server_picker_disables_the_quit_key(self):
        """게임 중 `Q`가 앱을 끄면 랩 컨테이너가 뜬 채로 남는다.

        동작으로 확인하지 않고 **배선**으로 확인하는 이유: `_FakeScreen`은 같은
        키를 무한히 돌려주므로, `Q`가 무시되는 화면에 `Q`만 먹이면 테스트가
        끝나지 않는다.
        """
        seen = {}
        real = shooting.pick

        def fake_pick(*a, **k):
            seen["kw"] = k
            return 0

        shooting.pick = fake_pick
        try:
            shooting._pick_client_target(_FakeScreen(ord("1")), _FakeCurses(),
                                         self.multi)
        finally:
            shooting.pick = real
        self.assertIs(seen["kw"].get("allow_quit"), False)

    def test_choose_stage_does_not_swallow_a_quit_into_the_line_fallback(self):
        """`choose_stage`는 curses 화면을 `except Exception:`으로 감싼다.

        traceback을 찍고 라인 모드로 떨어지는 그 안전망은 **일부러** 넣은
        것이라 없앨 수 없다. `QuitApp`이 `BaseException` 하위이므로 걸리지
        않아야 한다 — 걸리면 `Q`를 누른 사용자가 traceback과 함께 목록 입력으로
        떨어진다.
        """
        stages = [REPO_ROOT / "shooting" / "stages" / n
                  for n in ("1-1-runaway-query.json",
                            "1-3-lock-contention.json")]
        fell_back = []
        real_curses = shooting._choose_in_worlds_curses
        real_line = shooting._choose_stage_line
        real_in, real_out = sys.stdin.isatty, sys.stdout.isatty

        def boom(*a, **k):
            raise shooting.QuitApp

        shooting._choose_in_worlds_curses = boom
        shooting._choose_stage_line = lambda *a, **k: fell_back.append(1)
        sys.stdin.isatty = lambda: True
        sys.stdout.isatty = lambda: True
        try:
            with self.assertRaises(shooting.QuitApp):
                with contextlib.redirect_stdout(io.StringIO()):
                    shooting.choose_stage(stages)
        finally:
            shooting._choose_in_worlds_curses = real_curses
            shooting._choose_stage_line = real_line
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out
        self.assertEqual(fell_back, [], "QuitApp이 라인 모드 폴백에 삼켜졌다")

    def _footers(self, can_go_up):
        """`_pick_world_then_stage`가 두 `pick` 호출에 넘긴 footer들.

        이 함수는 `stdscr`·`curses`를 **인자로** 받으므로 그냥 대역을 넣어
        직접 부를 수 있다 — 소스를 grep할 이유가 없다. 월드를 고른 뒤 스테이지
        선택에서 경로를 돌려주게 해서 한 바퀴만 돌고 끝나게 한다.
        """
        footers = []
        real = shooting.pick

        def fake_pick(_stdscr, _curses, _title, _labels, footer=None, **_kw):
            footers.append(footer)
            return 0

        shooting.pick = fake_pick
        try:
            groups = [(1, [(Path("s.json"), {"title": "t", "id": "1-1"})])]
            shooting._pick_world_then_stage(
                _FakeScreen(ord("1")), _FakeCurses(), groups, {},
                "어느 월드부터 할까요", can_go_up)
        finally:
            shooting.pick = real
        return footers

    def test_the_world_picker_advertises_quit_when_back_means_something_else(self):
        """`Esc/q`가 'DBMS 선택으로'일 때는 `Q 종료`가 따로 필요하다."""
        world_footer, stage_footer = self._footers(can_go_up=True)
        self.assertIn("DBMS 선택으로", world_footer)
        self.assertIn("Q 종료", world_footer)
        self.assertIn("월드 선택으로", stage_footer)
        self.assertIn("Q 종료", stage_footer)

    def test_the_world_picker_does_not_repeat_itself_when_back_is_quit(self):
        """`Esc/q`가 이미 '종료'면 `Q 종료`를 덧붙이지 않는다.

        같은 결과를 두 번 적으면 읽는 사람이 차이를 찾느라 멈춘다.
        """
        world_footer, _ = self._footers(can_go_up=False)
        self.assertIn("Esc/q 종료", world_footer)
        self.assertNotIn("Q 종료", world_footer.replace("Esc/q 종료", ""))

    def test_running_it_standalone_survives_a_quit(self):
        """`__main__` 가드가 없으면 `Q` 한 번에 트레이스백이 뜬다.

        여기만 소스를 읽는다 — `__main__` 블록은 동작으로 도달할 방법이 없다.
        """
        body = (REPO_ROOT / "scripts" / "shooting.py").read_text(
            encoding="utf-8")
        self.assertIn("except QuitApp", body)
```

**주의:** `_footers`는 `Path`를 쓴다 — `tests/test_shooting.py`는 이미 `from pathlib import Path`를 import하고 있다. `stage_menu_label`·`world_menu_label`이 가짜 스테이지 dict를 읽다 깨지면, 진짜 스테이지 파일 하나를 `load_stage`로 읽어 `groups`에 넣는다:

```python
            path = REPO_ROOT / "shooting" / "stages" / "1-1-runaway-query.json"
            groups = [(1, [(path, shooting.load_stage(path))])]
```

**주의:** 이 테스트 파일이 `contextlib`·`io`·`sys`를 이미 import하고 있는지 확인한다. 없으면 파일 상단 import 블록에 더한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_shooting.GlobalQuitTest -v`

Expected: FAIL — `assertIs(None, False)` (아직 `allow_quit`을 안 넘김), `AttributeError: module 'shooting' has no attribute 'QuitApp'`.

- [ ] **Step 3: import에 `QuitApp`을 더한다**

`scripts/shooting.py:46–49`:

```python
from tui import (  # noqa: E402
    QuitApp, bar, cwidth, is_affirmative, is_backspace, is_enter, is_idle,
    key_char, page_text, pick, pick_line, put, read_key, wrap,
)
```

- [ ] **Step 4: 게임 중 화면에서 종료를 막는다**

`scripts/shooting.py:2510–2512`를 바꾼다:

```python
    idx = pick(stdscr, curses, "어느 서버에 접속할까요",
               [f"{t}  ({PLAYER_HOST}:{PLAYER_PORTS[t]})" for t in targets],
               footer=" ↑↓ 또는 숫자 선택   Enter 접속   Esc/q 취소 ",
               allow_quit=False)
    return None if idx is None else targets[idx]
```

같은 함수 docstring 끝에 한 줄을 덧붙인다:

```
    **`Q`(전역 종료)를 막는다.** 여기는 게임이 도는 중이라, 앱이 끊기면
    `dbshoot-primary`/`dbshoot-replica` 컨테이너가 뜬 채로 남는다.
```

- [ ] **Step 5: 월드/스테이지 선택 footer에 안내를 넣는다**

`scripts/shooting.py:3140–3157`의 `_pick_world_then_stage`를 바꾼다:

```python
    back = "DBMS 선택으로" if can_go_up else "종료"
    # Esc/q가 이미 '종료'를 뜻하는 경우에는 `Q 종료`를 덧붙이지 않는다 — 같은
    # 결과를 두 번 적으면 읽는 사람이 차이를 찾느라 멈춘다.
    quit_hint = "   Q 종료" if can_go_up else ""
    while True:
        w_idx = pick(
            stdscr, curses, heading,
            [world_menu_label(w, items, best) for w, items in groups],
            footer=f" ↑↓ 또는 숫자 선택   Enter 들어가기   Esc/q {back}{quit_hint} ")
        if w_idx is None:
            return None
        world, items = groups[w_idx]
        s_idx = pick(
            stdscr, curses,
            f"월드 {world} · {WORLD_TITLES.get(world, '미분류')}",
            [stage_menu_label(p, st, best) for p, st in items],
            footer=" ↑↓ 또는 숫자 선택   Enter 시작   Esc/q 월드 선택으로   Q 종료 ")
        if s_idx is not None:
            return items[s_idx][0]
        # 스테이지 선택에서 나가면 월드 선택으로 되돌아간다.
```

- [ ] **Step 6: 단독 실행 가드를 더한다**

`scripts/shooting.py` 파일 끝의 `__main__` 블록을 바꾼다:

```python
if __name__ == "__main__":
    try:
        sys.exit(main())
    except QuitApp:
        # 선택 화면의 `Q`. `./shoot`를 직접 띄우면 `guide.main`의 그물이 없다.
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        sys.exit(130)
```

**순서가 중요하다** — `QuitApp`을 `KeyboardInterrupt`보다 먼저 둔다. 둘 다 `BaseException` 하위지만 서로 무관해서 순서가 정확성에 영향을 주지는 않는다. 읽는 순서를 "정상 종료 → 중단"으로 두는 편이 낫다.

- [ ] **Step 7: 통과를 확인한다**

Run: `python3 -m unittest tests.test_shooting -v 2>&1 | tail -25`

Expected: PASS. 특히 기존 `ClientTargetTest.test_quit_cancels`(소문자 `q`로 취소)가 그대로 통과해야 한다 — `allow_quit=False`는 `allow_cancel`에 영향을 주지 않는다.

- [ ] **Step 8: 커밋**

```bash
git add scripts/shooting.py tests/test_shooting.py
git commit -m "Wire the quit key into shoot, minus the in-game screen

Pre-game pickers get Q; the in-game server picker passes allow_quit=False
because quitting there would leave the lab containers running. Guards
standalone runs, and pins that choose_stage's except-Exception fallback
does not swallow QuitApp.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: README 안내와 전체 검증

**Files:**
- Modify: `README.md` (148–158행, "한 번에 시작하기" 절)
- Test: `tests/test_guide.py` (문서 검사 1건 추가)

**Interfaces:**
- Consumes: Task 1–6 전부
- Produces: 없음 (마지막 태스크)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_guide.py`의 `LauncherTest` 안, `test_the_docs_mention_it` 바로 뒤에 추가한다:

```python
    def test_the_docs_explain_the_quit_key(self):
        """안내 없는 단축키는 없는 것과 같다 (이슈 #95).

        `README.md`의 "한 번에 시작하기" 절이 `./guide` 흐름을 산문으로 설명하는
        유일한 자리다.
        """
        body = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`Q`", body, "README에 전역 종료 키 안내가 없다")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_guide.LauncherTest.test_the_docs_explain_the_quit_key -v`

Expected: FAIL — `README에 전역 종료 키 안내가 없다`

- [ ] **Step 3: README에 안내를 더한다**

`README.md`의 "한 번에 시작하기" 절에서 `된다 — 인자(\`--dbms\`, \`--seed\` 등)는 그쪽이 받는다.`로 끝나는 문단 **바로 뒤**에 새 문단을 넣는다 (그 아래 `## 학습 점검 (퀴즈/시험)` 제목 앞):

```markdown
선택 화면에서 `Esc`/`q`는 한 단계 **뒤로**, 대문자 `Q`는 **앱 전체 종료**다 —
챕터 목록처럼 깊이 들어간 자리에서도 한 타로 빠져나온다. 예외는 장애 대응 게임이
진행 중일 때 `c`로 접속할 서버를 고르는 화면 하나로, 거기서는 `Q`가 막혀 있다 —
게임을 끊으면 실습용 컨테이너가 뜬 채로 남기 때문이다(그 화면은 `Esc`로 취소한다).
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_guide.LauncherTest -v`

Expected: PASS

- [ ] **Step 5: 콘텐츠 검사기를 돌린다**

README를 고쳤으므로 링크·목차 검사를 다시 돌린다.

Run: `python3 scripts/check_content.py; echo "exit=$?"`

Expected: `exit=0` (위반 줄이 출력되지 않는다)

- [ ] **Step 6: 전체 스위트를 돌린다**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -15`

Expected: `OK`. 실패가 있으면 그 테스트가 이 작업의 어느 계약을 검사하는지 확인하고, 계약이 맞으면 테스트를, 테스트가 맞으면 구현을 고친다.

- [ ] **Step 7: `git status`로 의도치 않은 변경이 없는지 본다**

Run: `git status --short`

Expected: 변경 파일은 `README.md`, `scripts/guide.py`, `scripts/reading.py`, `scripts/shooting.py`, `scripts/tui.py`, `tests/test_guide.py`, `tests/test_reading.py`, `tests/test_shooting.py`, `tests/test_tui.py` 아홉 개뿐 (앞선 태스크에서 이미 커밋됐다면 이 단계에서는 README와 test_guide.py만 남는다). `scripts/exam.py`가 목록에 있으면 범위를 벗어난 것이므로 되돌린다.

- [ ] **Step 8: 커밋**

```bash
git add README.md tests/test_guide.py
git commit -m "Document the global quit key

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 9: 수동 확인 (실제 터미널 필요 — CI가 못 하는 것)**

아래는 사람이 실제 tty에서 해야 한다. 자동화 스위트는 curses 화면을 가짜로 돌리므로 진짜 터미널 동작을 증명하지 못한다. 각 항목의 결과를 PR 본문에 적는다.

1. `./guide` → 챕터 읽기 → DBMS → 티어 → 챕터 선택 → `less` 종료 → **프롬프트 없이** 챕터 목록으로 돌아오는가.
2. 그 챕터 목록에서 `Q`(Shift+q) → 한 타로 셸 프롬프트까지 나오는가. 소문자 `q`는 여전히 티어 선택으로 뒤로 가는가.
3. `PAGER=/nonexistent-pager ./guide`로 같은 경로 → 본문이 평문으로 찍히고 `계속하려면 Enter (q=종료)...`가 뜨는가. 거기서 `q` + Enter → 앱 종료.
   (`PAGER=cat`이 **아니다** — `cat`은 실행에 성공하므로 `printed_inline`이 `False`가 되어 pause를 건너뛴다. 설계 문서 3절의 알려진 한계.)
4. `LESS=-X ./guide`(또는 `PAGER="less -X"`)로 같은 경로 → `less` 종료 뒤에도 챕터 본문이 화면에 그대로 남아 있고, 이번에는 `계속하려면 Enter (q=종료)...`가 **떠야** 한다 — `-X`는 사용자가 대체 화면을 안 쓰겠다고 명시한 것이라 이제 `printed_inline`이 `True`가 된다(리뷰 이후 결정 변경, 설계 문서 3절). `PAGER=cat`(3번 항목)과 달리 이건 고쳐진 경로다.
5. `./shoot` 월드 선택에서 `Q` → 종료. traceback이나 `선택 화면에서 오류가 발생해…` 메시지가 **뜨지 않아야** 한다.
6. `./shoot` → 서버가 둘인 스테이지(`2-2-replication-lag`) 시작 → `c` → 서버 선택 화면에서 `Q`가 **취소로 접히는가**(막다른 죽은 키가 아니라 `Esc`와 같은 결과). 게임이 계속되는가. 끝나면 `./shoot down`으로 랩을 정리한다.
7. `python3 scripts/reading.py` 단독 실행 → 챕터 목록에서 `Q` → 트레이스백 없이 종료. Ctrl-C도 마찬가지로 트레이스백 없이 종료하는가.

---

## 자체 검토 메모

**스펙 대응표** — 설계 문서의 각 절이 어느 태스크에 들어갔는가:

| 설계 문서 절 | 태스크 |
|---|---|
| 1. `tui.QuitApp` (BaseException 포함) | Task 1 |
| 2. `tui.pick()`의 `allow_quit` | Task 1 (정의) · Task 6 (`allow_quit=False` 호출부) |
| 3. `page_text()` 튜플 반환 | Task 3 |
| 4. `pause_after_output()` 탈출구 | Task 2 |
| 5.1 `reading.main` 조건화 | Task 4 |
| 5.2 `guide`의 `Mode.pause`·`run_mode` 반환값 | Task 5 |
| 6(a) `guide.main`의 `except QuitApp` | Task 5 |
| 6(b) `reading`·`shooting`의 `__main__` 가드 | Task 4 · Task 6 |
| 7. footer 문구 | Task 1(기본값) · Task 4(reading) · Task 6(shooting) |
| 8. 테스트 | 각 태스크에 분산 |
| 9. 변경 파일 요약 | Task 7 Step 7이 대조한다 |
| 10. 검증 | Task 7 Step 5·6·9 |
| 11. 릴리스 영향 | 코드 변경 없음 — 릴리스 시 MINOR로 매긴다 |

**태스크 간 의존**: Task 1 → (2, 3) → 4 → … 실제로는 Task 1이 먼저여야 하고, Task 3이 Task 4보다 먼저여야 하며, Task 2가 Task 4·5보다 먼저여야 한다(Task 2 Step 5가 `test_guide.py`의 람다를 고치므로). Task 5·6은 Task 1 뒤라면 순서 무관. Task 7은 마지막.

**놓치기 쉬운 것 세 가지**:
1. `tests/test_reading.py`의 `ReadChapterTest._capture`가 `page_text` 대역으로 **문자열**을 돌려준다. Task 3에서 튜플로 바꾸는 순간 `reading.read_chapter`가 언패킹에 실패한다 — Task 4 Step 1에서 함께 고친다.
2. `tests/test_guide.py`의 `tui.input = lambda *a, **k: called.append(1)`이 `None`을 돌려준다. Task 2에서 `.strip()`을 부르는 순간 깨진다 — Task 2 Step 5에서 함께 고친다.
3. `_FakeScreen`(shooting)과 `_PickScreen`(tui)은 키가 떨어지면 같은 키를 계속 돌려준다. `Q`가 무시되는 화면에 `Q`만 먹이면 테스트가 끝나지 않는다 — 그래서 Task 6은 동작이 아니라 배선을 검사하고, Task 1의 `test_quit_can_be_disabled`는 `[Q, Enter]`로 두 키를 준다.
