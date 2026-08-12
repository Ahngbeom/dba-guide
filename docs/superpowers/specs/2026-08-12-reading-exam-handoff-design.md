# 챕터 읽기 — 시험 제안 프롬프트를 목록의 동작 키로 바꾼다 — 설계

작성일: 2026-08-12
선행: [전역 종료 키와 불필요한 멈춤 제거](2026-08-12-global-quit-key-design.md) (PR #97, 같은 브랜치에 이어서 쌓는다)

## 배경

이슈 #95를 고친 직후 사용자가 보고했다.

> 챕터 읽기 → 챕터 선택 → less 종료 시, 간헐적으로 프롬프트에 "이제 확인해 볼까요? [Y/n]" 과정이 진행됨. 꼭 필요한건가?

### "간헐적"의 정체

무작위가 아니라 결정적이다. `offer_exam`(`scripts/reading.py:109`)은 **그 챕터에 문제은행이 있을 때만** 묻는다. 없는 은행에 "예"를 받으면 갈 곳이 없기 때문이고, 그 판단 자체는 옳다.

| 안 묻는 챕터 (8) | 묻는 챕터 (23) |
|---|---|
| 각 티어의 `00-overview.md` (3) | 나머지 본문 챕터 전부 |
| 각 티어의 `*-commands-cheatsheet.md` (3) | |
| `appendix/` 두 파일 (2) | |

개요·치트시트·부록을 읽을 때만 조용히 넘어가니 무작위처럼 느껴진다.

### 진짜 문제 — #95의 증상이 여기서 반복된다

```python
answer = ask("이제 확인해 볼까요? [Y/n] ")
return answer in ("", "y", "yes")      # ← 빈 입력(Enter) = 예
```

1. **기본값이 `Y`다.** #95 이전 빌드에서 `계속하려면 Enter`를 습관적으로 눌러 넘기던 사람이 그대로 Enter를 치면 이제 **시험이 시작된다.** 없앤 프롬프트 자리에 더 위험한 프롬프트가 남았다.
2. **`Esc`/`q`가 안 먹힌다.** 같은 평문 `input()`이라 `q`는 "거절"로 처리될 뿐 앱을 나가지 못한다 — #95가 지적한 막다른 골목의 축소판이다.
3. **타이밍이 기대와 어긋난다.** `less`를 끄는 순간은 "다 읽었으니 목록으로 돌아가겠지"인데 거기서 질문이 뜬다.

기능 자체는 의도된 것이다. 세 곳이 같은 근거를 적는다 — `reading.py` docstring(*"경로를 손으로 찾게 하면 거기서 끊긴다"*), `README.md`, `CLAUDE.md`. **없앨 것은 핸드오프가 아니라 그 형태다.**

## 목표

1. 챕터를 다 읽으면 **아무 질문 없이** 챕터 목록으로 돌아간다.
2. 읽기 → 시험 핸드오프는 유지한다. 다만 막는 질문이 아니라 목록에서 고르는 동작으로.
3. 어느 챕터에 시험이 있고 지난 기록이 어떤지 목록에서 보인다.

## 비목표 (YAGNI)

- **`tui.pick_line`(비-tty 경로)에 동작 키를 넣지 않는다.** #95에서 `Q`를 제외한 것과 같은 이유 — `exam._pick_line`이 이걸 감싸 쓰므로 비대칭이 생긴다. 라인 모드에서는 `Enter`만 있고 시험은 `학습 점검` 모드로 간다.
- **`exam` 모드의 챕터 선택 화면은 손대지 않는다.** 이미 `[지난 최고 …]`를 붙이고 있고, 이 작업은 읽기 모드만 건드린다.
- 동작 키를 설정으로 바꾸는 기능.
- 여러 동작 키(`x` 하나면 족하다).

---

## 1. `tui.pick`에 동작 키

```python
Picked = namedtuple("Picked", "index action")

def pick(stdscr, curses, title, labels, footer=None,
         allow_cancel=True, allow_quit=True, actions=""):
```

`actions`는 추가로 받을 키 글자들의 문자열이다(이 작업에서는 `"x"` 하나).

**반환 계약은 `actions`가 결정한다:**

| `actions` | 일반 선택(Enter·숫자) | 동작 키 | 취소(Esc/q) | `Q` |
|---|---|---|---|---|
| 비어 있음(기본) | `int` | — | `None` | `QuitApp` |
| 비어 있지 않음 | `Picked(index, None)` | `Picked(index, "x")` | `None` | `QuitApp` |

반환 모양이 둘인 것은 냄새다. 그럼에도 이 쪽을 택한 이유: **호출부는 자기가 `actions`를 넘겼는지 항상 알고 있으므로** 어느 모양을 받을지 헷갈릴 여지가 없고, 살아 있는 호출부 6곳(`guide.py:139` · `reading.py:81` · `shooting.py:2513·3152·3159·3188`)은 한 글자도 고치지 않아도 된다. 대안인 `pick_with_actions()` 신설은 `CLAUDE.md`가 경고한 "`pick`이 세 벌로 갈라졌다가 합쳐진" 상황을 다시 부른다.

(`shooting.py:3126`의 `_choose_stage_curses`도 `pick`을 부르지만 **호출부가 없는 죽은 코드**다. #95에서와 같이 건드리지 않는다.)

동작 키 판정은 **대소문자를 보존한 `raw`로** 한다. `Q`(전역 종료)를 소문자로 접기 전에 보는 것과 같은 자리이며, `actions="x"`가 `X`까지 삼키지 않게 한다.

키 처리 순서(현재 코드 기준): `Esc` → 비-키 무시 → `Enter` → `raw` 계산 → **`Q`** → **동작 키** → `ch = raw.lower()` → 이동(`↑↓`/`k`/`j`) → 숫자 → 취소. 동작 키는 `Q` 바로 뒤, 소문자로 접기 **전**에 들어간다. `Enter`와 숫자는 이미 `int`를 반환하는 자리이므로 `actions`가 있을 때 `Picked`로 감싸는 처리가 그 두 곳에도 필요하다.

동작 키가 이동 키(`j`/`k`)나 숫자와 겹치면 이동·숫자 쪽이 죽는다 — 호출부 책임이며, `"x"`는 겹치지 않는다.

## 2. 챕터 목록 라벨

| 상태 | 접미 | 행 수 |
|---|---|---|
| 은행 있음, 기록 없음 | **없음** | 23 (기록 쌓이기 전) |
| 은행 있음, 기록 있음 | `   [지난 최고 A·92%]` | 시험 본 만큼 |
| 은행 없음 | `   [시험 없음]` | 8 |

`[시험 있음]`을 23행에 붙이면 다수가 잡음이 된다. 소수인 "없음"만 표시해 목록을 조용히 유지한다. 문구와 서식은 `exam._chapter_labels`의 것을 그대로 쓴다 — 같은 정보가 두 화면에서 다르게 보이면 안 된다.

**은행 JSON을 열지 않는다.** 실측: 23개 은행 전부 `chapter` 필드가 챕터 상대경로와 일치하므로(`23/23`), `exam.best_result_for(rel, records)`에 `rel`을 그대로 넘길 수 있다. `exam._chapter_labels`가 `_bank_meta`로 파일을 여는 것은 그쪽이 **은행 경로**에서 출발하기 때문이고, 읽기 모드는 **챕터 경로**에서 출발하므로 그 우회가 필요 없다.

`exam.read_results()`는 **챕터 목록을 그릴 때마다** 부른다. 시험을 보고 목록으로 돌아오면 방금 기록이 바로 보여야 한다.

## 3. `reading` 흐름

`offer_exam`은 **통째로 삭제한다**(테스트 `ExamOfferTest` 6개 포함).

```python
while True:
    chapters = discover_chapters(tier)
    records = exam.read_results()
    sel = choose(f"{tier} — 어느 챕터를 읽을까요",
                 chapter_labels(chapters, records), actions="x")
    if sel is None:
        break                              # 티어 선택으로
    rel = chapters[sel.index]
    if sel.action == "x":
        bank = exam.exam_bank_for(rel)
        if bank:
            run_exam(rel, bank, dbms)
            pause_after_output()
        continue                           # 은행 없으면 무시 — 그 행이 이미 [시험 없음]
    if read_chapter(rel, dbms):
        pause_after_output()
```

`run_exam(rel, bank, dbms)`는 기존 핸드오프 코드(절대경로 + `--dbms`)를 그대로 옮긴 작은 함수다. 그 인자 조립 규칙과 여덟 줄 주석은 이유가 있어 존재하므로 함께 옮긴다.

**`ran_exam` 분기가 사라져 pause 조건이 `if printed`로 단순해진다** — #95에서 만든 조건부 pause가 오히려 더 단순해진다. 시험을 본 직후에는 `exam.main`이 평문을 남겼을 수 있으므로 그 자리에서 따로 멈춘다.

### 비-tty 경로

`choose`는 tty가 아니면 `pick_line`으로 떨어진다. `pick_line`은 동작 키를 모르므로(비목표), `choose`가 어댑터 역할을 한다: `actions`가 주어졌으면 `pick_line`의 `int`/`None`을 `Picked(idx, None)`/`None`으로 감싸 돌려준다. 라인 모드에서는 읽기만 되고 시험은 `학습 점검` 모드로 간다.

## 4. 의도적 타협 — `[시험 없음]` 행의 `x`

아무 일도 일어나지 않는다. 죽은 키를 또 만드는 것 아니냐는 지적이 가능하고, 실제로 #95의 핵심 증상이 그것이었다.

다른 점은 **그 행이 이유를 화면에 적고 있다**는 것이다. `Esc`가 반응 없던 화면은 왜인지 알 방법이 없었지만, 여기서는 커서가 놓인 바로 그 줄에 `[시험 없음]`이 보인다.

`pick`에 알림 줄(`notice`) 파라미터를 더해 "이 챕터에는 시험이 없습니다"를 띄우는 방안도 검토했다. 공용 함수에 파라미터를 하나 더 얹을 만큼의 값은 아니라고 판단했으나, 이 판단은 뒤집을 수 있다 — 뒤집는다면 `pick`에 `notice=None`을 더하고 하단 바 위 한 줄에 그리면 된다(약 5줄).

## 5. 문서

세 곳이 핸드오프를 이 모드의 존재 이유로 적고 있다. **핸드오프는 없어지는 것이 아니라 형태가 바뀌는 것**이므로 그에 맞게 고친다.

| 파일 | 현재 | 변경 후 |
|---|---|---|
| `scripts/reading.py` docstring | "다 읽으면 그 챕터의 시험으로 이어 준다" | 목록에서 `x`로 이어 준다 |
| `README.md` "한 번에 시작하기" | "챕터를 다 읽으면 그 챕터의 시험으로 바로 이어진다" | 같은 취지, 새 조작법 |
| `CLAUDE.md` `reading.py` 항목 | "then an offer to run that chapter's bank" | 목록의 동작 키 + 라벨 |

`CLAUDE.md`에는 `pick`의 새 계약(`actions`가 반환 모양을 바꾼다)도 적는다 — `tui.py` 항목이 이미 `pick`의 키 비교 함정을 기록하고 있는 자리다.

## 6. 테스트

### `tests/test_tui.py`

- `actions`를 안 주면 반환 모양이 **그대로** `int`/`None`이다(기존 `PickTest` 전부가 이미 이걸 지킨다 — 회귀 방어).
- `actions="x"`를 주면 Enter·숫자는 `Picked(index, None)`, `x`는 `Picked(index, "x")`.
- `actions`에 없는 글자(`z`)는 무시된다.
- 대문자 `X`는 `actions="x"`에 걸리지 않는다.
- `Q`는 `actions`가 있어도 여전히 `QuitApp`을 올린다.
- 동작 키를 누른 시점의 **하이라이트 행 인덱스**가 실려 온다(↓ 두 번 뒤 `x` → `index == 2`).

### `tests/test_reading.py`

- `x` + 은행 있는 챕터 → `exam.main`이 절대경로 + `--dbms`로 불린다. 목록으로 돌아온다.
- `x` + 은행 없는 챕터 → `exam.main`이 **안** 불린다. 목록으로 돌아온다.
- `Enter` → `read_chapter`가 불리고 `exam.main`은 안 불린다.
- 라벨 세 종류: 은행 없음 → `[시험 없음]`, 기록 있음 → `[지난 최고 …]`, 기록 없음 → 접미 없음.
- 챕터를 읽고 나면 `pause_after_output`이 **안** 불린다(페이저가 삼킨 경우) — #95 회귀 방어.
- `offer_exam`이 더 이상 존재하지 않는다.
- 비-tty에서 `choose(..., actions="x")`가 `Picked`를 돌려준다.

기록이 있는 경우는 `exam.read_results`를 가짜로 바꿔 검증한다 — `.exam-results/`는 gitignored라 CI에 존재하지 않고, 실제 파일을 만들면 사용자 기록을 오염시킨다.

## 7. 릴리스 영향

`docs/release-policy.md` 기준 **MINOR**. JSON 스키마·진행 파일 형식·챕터 경로가 그대로다. `tui.pick`의 반환 계약이 넓어지지만 저장소 내부 API이고 기본 경로는 무변경이다.

사용자에게 보이는 변화는 둘이다. 챕터를 읽은 뒤 `[Y/n]` 질문이 사라지고 곧바로 목록으로 돌아온다. 그 질문에 습관적으로 Enter를 눌러 시험을 보던 사람은 이제 목록에서 `x`를 눌러야 한다 — 파괴적이지 않고, footer가 그 자리에서 안내한다.
