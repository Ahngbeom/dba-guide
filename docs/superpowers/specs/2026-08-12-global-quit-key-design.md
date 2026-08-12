# 전역 종료 키와 불필요한 멈춤 제거 — 설계

작성일: 2026-08-12
이슈: [#95 각 챕터 수행 후 앱을 바로 종료할 수 없음](https://github.com/Ahngbeom/dba-guide/issues/95)

## 배경

이슈 #95가 보고한 증상은 세 줄이다.

1. `./guide` 진입 후 각 챕터 수행.
2. Esc/q 단축키를 통해 앱 종료 시도 시, 이전 스텝/화면으로 뒤로가기만 진행됨.
3. 끝까지 뒤로가기를 진행해도 터미널 세션에 `계속하려면 Enter 키를 누르시오` 메시지와
   함께 앱을 벗어나지 못하는 경우가 있음.

코드를 추적한 결과 원인은 네 가지이고, 그중 둘은 명백한 버그다.

### ① `reading.main`이 조건 없이 멈춘다 (증상 3의 직접 원인)

`scripts/reading.py:160`:

```python
read_chapter(rel, dbms)
if offer_exam(rel, bank):
    exam.main(args)
pause_after_output()      # ← 조건 없음
```

`pause_after_output()`은 "직전에 찍힌 평문을 다음 curses 프레임의 `erase()`가 한 프레임도
못 읽히고 지우는 것"을 막으려고 있다. 그런데 `read_chapter`가 `page_text()`를 통해 본문을
진짜 `less`로 넘겼다면 **지울 평문이 애초에 없다** — `less`는 대체 화면을 쓰고 종료하면서
원래 화면을 복원한다. 그럼에도 챕터를 읽을 때마다 매번 `계속하려면 Enter를 누르세요...`가
뜬다.

그리고 이 프롬프트는 `scripts/tui.py:487`의 `input()`이다. Esc도 `q`도 그냥 글자로 먹힌다.
사용자 입장에서는 "Esc를 눌러도 아무 일이 없는 화면"이고, 그게 증상 3이다.

### ② `guide.main`도 조건 없이 멈춘다

`scripts/guide.py:146`이 `run_mode()` 뒤에서 `pause_after_mode()`를 무조건 부른다. 모드가
아무것도 출력하지 않고 돌아와도 마찬가지다. 그래서 챕터 하나 읽고 나가려면 Enter 프롬프트를
**두 번** 통과해야 한다.

### ③ 전역 종료 키가 없다 (증상 2)

화면 스택이 곧 함수 호출 스택이다. `guide.main` → `reading.main` → 3중 `while` 루프 →
(선택적으로) `exam.main`. 각 층은 `None`을 받고 **한 층씩만** 빠져나온다. 챕터 목록에서
앱을 끄려면:

```
Esc(챕터→티어) → Esc(티어→DBMS) → Esc(reading 종료) → Enter(guide 멈춤) → Esc(메뉴 종료)
```

5타이고 중간에 curses ↔ 평문 모달리티가 한 번 바뀐다.

### ④ 키 계약이 화면마다 다르다

| 화면 | Esc | q |
|---|---|---|
| `tui.pick` (guide·reading·shooting 선택 화면) | 뒤로/취소 | 뒤로/취소 |
| `exam._pick_curses` (`exam.py:1200`) | **무반응** | 시험 전체 종료 |
| `exam` 퀴즈 화면 (`exam.py:840`) | **무반응** | 시험 종료 |
| `exam` 결과 메뉴 (`exam.py:1068`) | **무반응** | 시험 종료 |
| `tui.pause_after_output` | **무반응** | **무반응** |

`exam`의 세 화면은 `if kind != "key": continue`로 `kind == "esc"`를 버린다. 즉 "Esc를
눌렀는데 아무 일도 안 일어나는 화면"이 실제로 존재한다.

## 목표

1. 어느 선택 화면에서든 **한 타로** 앱 전체를 끝낼 수 있게 한다.
2. 남길 평문이 없을 때는 `계속하려면 Enter` 프롬프트를 띄우지 않는다.
3. 정당하게 남는 프롬프트에도 종료 경로를 준다 — 막다른 골목을 없앤다.
4. 기존 Esc/q(= 뒤로/취소) 근육기억을 깨뜨리지 않는다.

## 비목표 (YAGNI)

- **`exam.py`의 자체 화면 3곳**(위 표의 ④). 원인이 이번 이슈와 다르고(전파 문제가 아니라
  키 처리 누락), `CLAUDE.md`가 그 `ord()` 비교를 "예전 코드지만 올바르니 두라"고 명시했다.
  별건으로 남긴다.
- **`tui.pick_line`(비-tty 경로)**. `exam._pick_line`이 이걸 감싸 쓰므로 여기에 `Q`를 넣으면
  "라인 모드 `exam`에서만 `Q`가 먹히는" 비대칭이 생긴다. `exam`을 범위 밖에 두기로 한 결정과
  어긋나지 않도록 함께 제외한다.
- **`shooting` 게임 진행 중 화면**. 랩 컨테이너가 뜬 상태에서 앱을 강제 종료하면 정리가
  꼬인다. 아래 2절에서 `allow_quit=False`로 명시적으로 막는다.
- 종료 확인 대화상자("정말 끝낼까요?"). `Q`는 대문자라 오타로 눌리기 어렵고, 이 앱은 잃을
  상태가 없다(시험 결과는 `save_result`가 이미 저장한 뒤다).
- 키 바인딩 설정 파일.

---

## 1. 전역 종료 신호 — `tui.QuitApp`

`scripts/tui.py`에 전용 예외를 둔다.

```python
class QuitApp(BaseException):
    """어느 화면에서든 앱 전체를 끝내라는 신호.

    화면 스택이 곧 호출 스택이라(`guide.main` → `reading.main` → 3중 루프),
    '한 층 위로'를 뜻하는 None 반환으로는 바닥에서 꼭대기까지 나갈 수 없다.
    중간 루프는 이 예외를 **잡지 않는다** — 그게 이 방식의 요점이다.

    `Exception`이 아니라 `BaseException`을 상속하는 것은 의도다. 아래 참조.
    """
```

**`BaseException`을 상속하는 이유.** `shooting.py`에는 화면 코드를 감싸는 `except Exception:`
폴백이 두 군데 있다 — `choose_stage`(3224행, 선택 화면 → 라인 모드)와 `cmd_play`(3330행,
게임 화면 → 라인 모드). 둘 다 traceback을 찍고 "화면에서 오류가 발생해 …로 전환합니다"를
출력한다. 조용히 넘기지 않으려고 **일부러** 넣은 안전망이고, 실제로 버그를 잡은 이력이 주석에
적혀 있다.

`QuitApp`이 평범한 `Exception`이면 월드 선택 화면에서 `Q`를 누른 사용자가 traceback과 함께
라인 모드 목록으로 떨어진다. 이 두 곳에 `except QuitApp: raise`를 먼저 다는 것으로도 막을 수
있지만, 그러면 앞으로 추가되는 모든 `except Exception`이 같은 함정을 다시 판다.

`SystemExit`·`KeyboardInterrupt`가 `BaseException`인 이유가 정확히 이것이다 — 오류가 아니라
제어 흐름 신호이고, "모든 오류를 잡는" 코드가 삼키면 안 된다. `QuitApp`도 같은 부류다.
따라서 `shooting.py`의 두 `except Exception`은 **수정하지 않는다**.

**터미널 복구는 이미 보장된다.** `pick`은 항상 `curses.wrapper(...)` 안에서 돌고,
`wrapper`는 `try/finally`로 `endwin()`을 부른 뒤 예외를 다시 올린다 — `finally`는
`BaseException`에도 걸리므로 `QuitApp`이 터미널을 raw 모드에 남겨 둘 일은 없다. 예외로
빠져나가는 설계에서 가장 먼저 확인해야 할 지점이라 여기 적어 둔다.

센티널 반환값(`tui.QUIT`)을 쓰지 않는 이유: 호출부가 전부 명시적으로 검사·전파해야 하고,
하나만 빠뜨려도 `Q`가 조용히 "뒤로"로 동작한다. 새 선택 화면을 추가할 때도 매번 같은 검사를
다시 써야 한다. 예외는 중간 계층을 손대지 않고 새 화면도 자동으로 얻는다.

## 2. `tui.pick()` — `Q` 처리

현재(`scripts/tui.py:390`):

```python
ch = (key_char(key) or "").lower()
```

`key_char`는 대소문자를 보존하는데(`key_char(81) == "Q"`), `pick`이 곧바로 `.lower()`로
접어버려 `q`와 `Q`가 구분되지 않는다. **대소문자 판정을 접기 전으로 끌어올리는 것**이 변경의
핵심이다.

```python
def pick(stdscr, curses, title, labels, footer=None,
         allow_cancel=True, allow_quit=True):
    ...
        raw = key_char(key) or ""
        if allow_quit and raw == "Q":       # 소문자로 접기 **전에** 검사
            raise QuitApp
        ch = raw.lower()
```

`allow_quit`가 필요한 이유는 호출부 하나 때문이다. `shooting._pick_client_target`
(`scripts/shooting.py:2510`)은 게임 **진행 중** `c` 키로 접속할 서버를 고르는 화면이다.
여기서 `Q`가 앱을 끄면 `dbshoot-primary`/`dbshoot-replica` 컨테이너가 뜬 채로 남는다.
이 호출부만 `allow_quit=False`로 막는다.

`pick`을 쓰는 나머지 호출부는 모두 게임/시험 시작 **전** 선택 화면이므로 기본값을 쓴다:

| 호출부 | 역할 | `allow_quit` |
|---|---|---|
| `guide.py:123` | 최상위 메뉴 | 기본(True) |
| `reading.py:81` | DBMS·티어·챕터 선택 | 기본(True) |
| `shooting.py:2510` | **게임 중** 접속 서버 선택 | **False** |
| `shooting.py:3145` | 월드 선택 | 기본(True) |
| `shooting.py:3152` | 월드 안 스테이지 선택 | 기본(True) |
| `shooting.py:3181` | DBMS 선택 | 기본(True) |
| `shooting.py:3122` | — **호출부 없음** (아래) | 손대지 않음 |

`shooting._choose_stage_curses`(3116행)는 **어디서도 불리지 않는다**. `choose_stage`는
curses 경로에서 `_choose_in_worlds_curses`를, 폴백에서 `_choose_stage_line`을 부를 뿐이다
(3223·3239행). 월드 단위 선택이 들어오면서 남은 잔재로 보인다. 이 작업에서는 **건드리지
않는다** — 죽은 코드 제거는 별건이고, 이 이슈의 diff에 섞이면 리뷰가 흐려진다. 여기 적어 두는
이유는 `pick` 호출부를 세는 사람이 이걸 살아 있는 화면으로 착각하지 않게 하기 위해서다.

## 3. `tui.page_text()` — 평문으로 찍었는지 알려주기

```python
def page_text(text):
    """텍스트를 페이저로 넘긴다 → (returncode, printed_inline).

    `printed_inline`은 페이저를 못 써서 이 함수가 직접 `print`했는가다. 호출부가
    `pause_after_output()`을 부를지 정하는 데 쓴다 — 페이저가 삼켰다면 화면이
    복원되므로 지킬 평문이 없다.
    """
```

반환 규칙:

| 경로 | 반환 |
|---|---|
| 페이저 실행 성공 | `(proc.returncode, False)` |
| `PAGER`도 `less`도 없음 → `print` | `(0, True)` |
| `OSError`/`KeyboardInterrupt` → `print` 폴백 | `(0, True)` |

판정을 `page_text` 안에 두는 이유: `PAGER` 환경변수 해석과 `less` 존재 확인 규칙이 이미
여기 있다. `reading` 쪽에서 같은 조건을 다시 쓰면 규칙이 두 곳으로 갈라진다 —
`filter_lines`를 `generate-branch.sh`와 `reading`이 공유하는 것과 같은 이유다.

현재 이 반환값을 읽는 프로덕션 호출부는 없다(`shooting.py:1450`, `shooting.py:2882`,
`reading.py:93` 모두 무시). 테스트만 `rc`를 검사하므로 갱신 대상은 테스트뿐이다.

**`less -X`/`--no-init`는 처리한다(리뷰 후 결정 변경).** `LESS=-FX`가 몇 년째 널리
권장돼 온 설정이라 이건 흔한 개발 환경이지 특이 케이스가 아니고, 방치하면 회귀다 — 이
브랜치 이전에는 조건 없는 pause가 이 경우를 우연히 지켜 주고 있었다. `page_text`가
`argv`(페이저 커맨드라인)와 `$LESS` 환경변수(대시가 있어도 없어도 된다 — `LESS=-FX`와
`LESS=FX`는 같은 뜻) 양쪽에서 `-X`/`--no-init`를 읽어 `printed_inline`을 `True`로 돌린다.
이건 "포터블하게 감지"하는 것과 다르다 — 사용자가 이미 명시적으로 선언한 것을 읽을 뿐이다.
`-F`만으로는 트리거하지 않는다: 긴 본문에서는 `-F`가 있어도 `less`가 대체 화면을 그대로
쓰기 때문이다.

**알려진 한계(고치지 않고 기록한다)**: `PAGER=cat`처럼 `less`도 아니고 스스로 선언하지도
않는 페이저를 지정하면 본문이 터미널에 그대로 남는데도 `printed_inline`은 `False`가 되어
pause를 건너뛰고, 다음 curses 프레임이 그 본문을 지운다. 이런 페이저가 대체 화면을 쓰는지
알아낼 이식성 있는 방법은 여전히 없다. `COLOR_PAGERS` 목록을 재사용하는 것도 답이 아니다 —
그 목록은 "ANSI를 통과시키는가"를 뜻하지 "화면을 복원하는가"를 뜻하지 않으며, 두 성질은
우연히 겹칠 뿐이다.

## 4. `tui.pause_after_output()` — 탈출구

```python
    try:
        raw = input("\n계속하려면 Enter (q=종료)...")
    except (EOFError, KeyboardInterrupt):
        return
    if raw.strip().lower() == "q":
        raise QuitApp
```

`input()` 기반을 유지한다. raw 단일키 읽기(`termios`/`tty`)로 바꾸면 두 가지가 함께
무너진다: 비-tty 가드(`isatty` 검사로 파이프 실행을 지키는 것)와, 기본 인자에 `input`을 박지
않아 얻은 테스트 교체 가능성.

`q`는 여기서만 소문자를 받는다. 이 프롬프트에는 "뒤로"라는 선택지가 없어(Enter = 계속이
유일한 대안) `q`/`Q`를 구분할 이유가 없고, 이미 화면에 `q=종료`라고 적어 두므로 대문자를
요구하면 오히려 안 먹히는 것처럼 보인다. `strip().lower()`로 둘 다 받는다.

## 5. 호출부 조건화

### 5.1 `reading.main`

```python
rel = chapters[c_idx]
bank = exam.exam_bank_for(rel)
printed = read_chapter(rel, dbms)          # ← page_text의 printed_inline을 그대로 반환
ran_exam = offer_exam(rel, bank)
if ran_exam:
    args = [str(exam.REPO_ROOT / bank)]
    if dbms:
        args += ["--dbms", dbms]
    exam.main(args)
if printed or ran_exam:
    pause_after_output()
```

`read_chapter`는 `page_text`의 두 번째 값을 그대로 돌려주도록 바꾼다(`returncode`는 여기서
쓸 데가 없다). `ran_exam`이 참이면 `exam.main`이 평문을 남겼을 수 있으므로 멈춘다 —
`exam`이 조용히 끝났는지까지 알아내려면 `exam` 내부를 들여다봐야 하고, 그건 이 이슈의 범위가
아니다. 안전한 쪽(멈춤)으로 떨어진다.

`offer_exam`을 `if` 조건에서 꺼내 변수로 받는 것이 유일한 구조 변경이다.

### 5.2 `guide`

`Mode`에 `pause` 필드를 더한다.

```python
Mode = namedtuple("Mode", "key title scale run pause")

MODES = (
    Mode("read",  "챕터 읽기",            reading.read_scale, lambda: reading.main([]),  False),
    Mode("exam",  "학습 점검 (퀴즈/시험)", exam_scale,         lambda: exam.main([]),     True),
    Mode("shoot", "장애 대응 (실전 훈련)", shoot_scale,        lambda: shooting.main([]), True),
)
```

`read`가 `False`인 이유: `reading`이 5.1에서 스스로 더 정확하게 판단한다. 여기서 또 멈추면
그 판단이 무의미해진다. `exam`·`shoot`은 `True` — 현재 동작 그대로다(`shoot`은 등급표·후일담을
평문으로 찍고, `exam`은 라인 모드에서 평문으로 진행된다).

`run_mode`는 자신이 메시지를 찍었는지 돌려준다:

```python
def run_mode(mode):
    """모드를 돌리고 **반드시** 메뉴로 돌아온다 → 평문 메시지를 찍었는가."""
    try:
        mode.run()
    except SystemExit as e:
        if e.code not in (None, 0):
            print(...)
            return True
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return True
    return False
```

`main`은 둘을 OR로 묶는다:

```python
mode = MODES[idx]
if run_mode(mode) or mode.pause:
    pause_after_mode()
```

`run_mode`가 메시지를 찍었다면 `mode.pause`와 무관하게 멈춰야 한다 — `read` 모드도 여기
해당한다(`reading.main`이 `exam.main`의 `SystemExit`을 그대로 흘려보내므로).

**`run_mode`에 `except QuitApp`을 추가하지 않는다.** 지금도 `SystemExit`·`KeyboardInterrupt`만
잡으므로 `QuitApp`은 그대로 통과한다. 다만 "그 둘만 잡는다"는 기존 주석에 "`QuitApp`은 위로
흘려보내는 것이 의도"라는 한 줄을 덧붙인다 — 나중에 `except Exception`으로 넓히려는 사람을
막는 것이 이 주석의 목적이므로.

## 6. `QuitApp`을 잡는 곳 — 딱 두 종류

**(a) `guide.main`** — 앱의 꼭대기.

```python
    try:
        while True:
            idx = choose_menu(menu_labels())
            if idx is None:
                return 0
            mode = MODES[idx]
            if run_mode(mode) or mode.pause:
                pause_after_mode()
    except QuitApp:
        return 0
```

`choose_menu`(최상위 메뉴)에서 `Q`를 누른 경우도 여기 걸린다 — 그 화면에서는 Esc/q도 이미
종료라 결과가 같지만, 예외 경로가 화면마다 갈라지지 않는 편이 낫다.

**(b) `reading.py`·`shooting.py`의 `__main__` 블록** — 단독 실행 경로.

`./shoot`를 직접 실행하면 `guide.main`을 거치지 않으므로 (a)의 그물에 걸리지 않는다.
`reading.py`도 `python3 scripts/reading.py`로 직접 돌릴 수 있다. 그대로 두면 `Q` 한 번에
트레이스백이 뜬다.

```python
if __name__ == "__main__":
    try:
        sys.exit(main())
    except QuitApp:
        sys.exit(0)
```

대상은 **둘뿐이다**: `scripts/reading.py`, `scripts/shooting.py`.

- `scripts/guide.py`는 필요 없다 — `__main__`이 부르는 `main()`이 (a)에서 이미 잡는다.
  거기에 또 다는 것은 절대 실행되지 않는 코드다.
- `scripts/exam.py`도 필요 없다 — `tui.pick`을 쓰지 않으므로 `QuitApp`이 발생할 곳이 없다
  (2절 표 참조).

## 7. footer 문구

`pick`의 `footer` 인자는 호출부가 직접 넘기므로 하나씩 고친다.

규칙은 하나다: **Esc/q가 이미 "종료"를 뜻하는 화면에는 `Q 종료`를 덧붙이지 않는다.** 같은
결과를 두 번 적으면 읽는 사람이 차이를 찾느라 멈춘다.

| 파일:행 | 현재 | 변경 후 |
|---|---|---|
| `reading.py:82` | `Esc/q 뒤로` | `Esc/q 뒤로   Q 종료` |
| `shooting.py:3148` | `Esc/q {back}` | **조건부** (아래) |
| `shooting.py:3156` | `Esc/q 월드 선택으로` | `Esc/q 월드 선택으로   Q 종료` |
| `pick` 기본 footer | `Esc/q 취소` | `Esc/q 취소   Q 종료` (`allow_quit`일 때만) |
| `guide.py:124` | `Esc/q 종료` | 그대로 — 이미 종료 |
| `shooting.py:3184` | `Esc/q 종료` | 그대로 — 이미 종료 |
| `shooting.py:2512` | `Esc/q 취소` | 그대로 — `allow_quit=False` |
| `shooting.py:3123` | `Esc/q 종료` | 그대로 — 죽은 코드(2절) |

`shooting.py:3145`(`_pick_world_then_stage`의 월드 선택)만 문구가 동적이다. `back`이
`can_go_up`에 따라 `"DBMS 선택으로"` 또는 `"종료"`로 갈리므로, 후자일 때는 규칙에 따라
덧붙이지 않는다:

```python
back = "DBMS 선택으로" if can_go_up else "종료"
quit_hint = "   Q 종료" if can_go_up else ""
... footer=f" ↑↓ 또는 숫자 선택   Enter 들어가기   Esc/q {back}{quit_hint} "
```

footer가 좁은 터미널에서 잘리는 것은 `tui.bar`가 이미 `fit`으로 처리한다.

### 리뷰 이후 정정 (같은 브랜치, 최종 리뷰)

위 규칙 — "Esc/q가 이미 종료를 뜻하는 화면에는 `Q 종료`를 덧붙이지 않는다" — 은 그 화면의
`Esc`/`q`가 **정말로 앱을 끝내는** 경우에만 맞다. `guide.py:124`(최상위 메뉴)와
`shooting.py:3123`(2절의 죽은 코드)이 그런 화면이라 여전히 그대로 둔다.

하지만 `shooting.py:3148`의 `can_go_up=False` 분기(단일 DBMS일 때의 월드 선택)와
`shooting.py:3184`(DBMS 선택)는 잘못 분류돼 있었다. 이 두 화면은 "앱의 꼭대기"가 아니라
**`shooting.choose_stage` 흐름의 꼭대기**일 뿐이다. `./shoot`를 독립 실행하면 그 흐름의
꼭대기가 곧 프로그램의 꼭대기라 `Esc`/`q`와 `Q`의 결과가 우연히 같아 보였고, 그래서 애초에
"이미 종료"로 분류했다. 그런데 이 저장소의 문서화된 주 진입점인 `./guide` 아래에서는
`Esc`/`q`가 이 모드를 나가 **가이드 메뉴로 돌아갈 뿐**이고, `Q`(대문자)만 `tui.QuitApp`을
올려 앱 전체를 끝낸다 — 두 키가 서로 다른 결과를 내므로 하나만 적으면 다른 하나가
안내 없이 숨는다.

수정한 결과:

| 파일:행 | 이전 | 이후 |
|---|---|---|
| `shooting.py:3148`(`can_go_up=False`) | `Esc/q 종료` (Q 없음) | `Esc/q 나가기   Q 종료` |
| `shooting.py:3184`(DBMS 선택) | `Esc/q 종료` (Q 없음) | `Esc/q 나가기   Q 종료` |

`back`을 `"종료"`가 아니라 `"나가기"`로 바꾼 것은 `Esc/q 종료   Q 종료`처럼 같은 낱말이 두 번
나오는 것을 피하기 위해서다 — 둘 다 붙이더라도 각자 다른 결과를 가리켜야 안내가 된다.
`quit_hint`는 더 이상 조건부가 아니다(`can_go_up` 값과 무관하게 항상 `Q 종료`를 붙인다).

## 8. 테스트

### `tests/test_tui.py`

- **`QuitApp`이 `Exception`의 하위가 아니다** — `except Exception:`이 삼키지 않는지 직접
  확인한다. 1절의 근거를 코드로 못 박는 자리이고, 나중에 누군가 `Exception`으로 되돌리면
  여기서 잡힌다.
- `pick`: `Q` → `QuitApp` 발생 / `q`·Esc → `None` / `allow_quit=False`면 `Q`가 아무 일도
  안 함(무시되고 루프 계속) / 기본 footer에 `allow_quit`에 따라 `Q 종료`가 붙는지.
- `pause_after_output`: 빈 입력 → 정상 반환 / `"q"`·`"Q"`·`" q "` → `QuitApp` /
  `EOFError`·`KeyboardInterrupt` → 조용히 반환 / 비-tty → 아예 `input`을 부르지 않음.
- `page_text`: 세 경로가 각각 `(rc, False)` / `(0, True)` / `(0, True)`를 돌려주는지.
  **기존 `rc` 단일값 assert 3곳(`test_tui.py:355/366/381`)을 튜플 언패킹으로 갱신.**

### `tests/test_reading.py`

- 페이저가 삼켰으면(`printed_inline=False`, 시험 안 봄) `pause_after_output`이 **안** 불린다.
- 평문 폴백이면(`printed_inline=True`) 불린다.
- 시험을 봤으면 페이저가 삼켰어도 불린다.
- `read_chapter`가 `page_text`의 `printed_inline`을 그대로 돌려준다.

### `tests/test_guide.py`

- `MODES`의 `pause` 값이 `(False, True, True)`.
- `main`이 `QuitApp`을 잡아 `0`을 반환한다(모드 실행 중 발생 / 메뉴에서 발생 두 경우).
- `run_mode`가 `QuitApp`을 **삼키지 않는다**(그대로 올라온다).
- `run_mode`의 반환값: 조용히 끝나면 `False`, `SystemExit("사유")`·`KeyboardInterrupt`면 `True`.
- **기존 `ModeTableTest`와 `test_main_pauses_after_each_mode_but_not_after_quitting` 갱신** —
  전자는 `Mode` 필드 수가 늘고, 후자는 `pause` 조건이 생겨 `paused == [1, 1]` 가정이 깨진다.

### `tests/test_shooting.py`

- `_pick_client_target`이 `pick`을 `allow_quit=False`로 부른다(배선 테스트).
- **`choose_stage`가 `QuitApp`을 라인 모드 폴백으로 삼키지 않는다** — `_choose_in_worlds_curses`가
  `QuitApp`을 올리게 해 두고, `choose_stage`가 그대로 올려보내는지 확인한다(traceback을 찍고
  `_choose_stage_line`으로 떨어지면 실패). 1절에서 찾은 함정의 회귀 테스트다.

**모든 테스트는 tty를 요구하지 않아야 한다.** `pick` 테스트는 기존 방식대로 가짜 `stdscr`·가짜
`curses`를 주입하고, `pause_after_output` 테스트는 `tui.sys.stdin/stdout`의 `isatty`와
`tui.input`을 바꿔 끼운다.

## 9. 변경 파일 요약

| 파일 | 변경 |
|---|---|
| `scripts/tui.py` | `QuitApp` 정의 · `pick`에 `allow_quit` · `page_text` 튜플 반환 · `pause_after_output` 탈출구 |
| `scripts/guide.py` | `Mode.pause` 필드 · `run_mode` 반환값 · `main`의 `except QuitApp` |
| `scripts/reading.py` | `read_chapter` 반환값 · `main`의 조건부 pause · footer · `__main__` 가드 |
| `scripts/shooting.py` | `_pick_client_target`의 `allow_quit=False` · footer 2곳 · `__main__` 가드 |
| `tests/test_tui.py` | 신규 케이스 + `page_text` 기존 assert 갱신 |
| `tests/test_reading.py` | 신규 케이스 |
| `tests/test_guide.py` | 신규 케이스 + `ModeTableTest`·pause 배선 테스트 갱신 |
| `tests/test_shooting.py` | 배선 테스트 1건 |
| `README.md` | "한 번에 시작하기"(148–158행)에 `Q` 한 줄 |

`README.md`의 "한 번에 시작하기" 절은 `./guide`의 흐름을 산문으로 설명하는 유일한 자리다.
거기에 한 문장을 더한다 — 선택 화면 어디서든 `Q`(대문자)로 앱을 바로 끝낼 수 있고, `Esc`/`q`는
지금처럼 한 단계 뒤로 간다는 것. `./shoot` 절(221행)의 조작키 설명은 게임 **진행 중** 키를
다루므로 손대지 않는다 — 그 화면에는 `Q`가 의도적으로 없다(2절).

## 10. 검증

```
python3 -m unittest discover -s tests
```

수동 확인(실제 tty 필요, CI에서 못 하는 것):

1. `./guide` → 챕터 읽기 → DBMS → 티어 → 챕터 선택 → `less` 종료 → **프롬프트 없이** 챕터
   목록으로 돌아오는가.
2. 그 목록에서 `Q` → 한 타로 셸 프롬프트로 나오는가.
3. `PAGER=/nonexistent-pager ./guide`로 같은 경로 → `page_text`가 `OSError`로 폴백해 본문을
   평문으로 찍고, `계속하려면 Enter (q=종료)...`가 뜨는가. 거기서 `q` → 앱 종료.
   (`PAGER=cat`이 **아니다** — 3절의 알려진 한계대로 `cat`은 실행에 성공하므로
   `printed_inline`이 `False`가 되어 pause를 건너뛴다. 폴백 경로를 보려면 실행 자체가
   실패해야 한다.)
4. `./shoot` → 스테이지 시작 → `c`(접속 서버 선택, 서버 2개 이상인 스테이지) → `Q`가
   **무시되는가**. Esc로 취소되고 게임이 계속되는가.
5. `./shoot` 월드 선택에서 `Q` → 종료. 컨테이너가 뜨기 전이므로 정리할 것이 없고,
   traceback이나 "선택 화면에서 오류가 발생해…" 메시지가 **뜨지 않아야** 한다(1절).
6. `python3 scripts/reading.py` 단독 실행 → 챕터 목록에서 `Q` → 트레이스백 없이 종료(6절 b).

## 11. 릴리스 영향

`docs/release-policy.md` 기준 **MINOR**. JSON 스키마·진행 파일 형식·챕터 경로가 그대로이고,
CLI 인자도 늘거나 줄지 않는다.

같은 키가 다른 결과를 내게 되는 경우는 정확히 둘이다.

1. **`pick` 화면에서 `Shift+q`** — 지금은 `.lower()`로 접혀 "뒤로"였고, 앞으로는 앱 종료다.
   `q`를 대문자로 눌러 뒤로 가려던 사람은 없다고 본다.
2. **챕터를 읽은 뒤의 Enter 프롬프트** — 지금은 매번 뜨고, 앞으로는 페이저가 본문을 삼킨
   경우 뜨지 않는다. 습관적으로 Enter를 한 번 더 누르던 사람에게는 그 Enter가 챕터 목록의
   "선택"으로 들어가 챕터가 열린다. Esc 한 번이면 되돌아오므로 파괴적이지 않다.

`shoot`의 게임 진행 중 화면과 `exam`의 모든 화면은 키 동작이 그대로다.
