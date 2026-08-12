#!/usr/bin/env python3
"""터미널 UI 공용 프리미티브 (표시 폭 계산·안전 출력·키 입력 정규화·외부 도구 위임).

`exam.py`(학습 점검 시험)와 `shooting.py`(장애 대응 게임)가 함께 쓴다.
외부 의존성 없음(Python3 표준 라이브러리만 사용).

이 모듈은 `curses`를 최상단에서 import하지 않는다 — curses가 필요한 함수는
호출자가 `curses` 모듈을 인자로 넘긴다. 덕분에 tty가 없는 환경(테스트 등)에서도
폭 계산·줄바꿈 같은 순수 함수를 그대로 쓸 수 있다.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import unicodedata


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


# --------------------------------------------------------------------------- #
# 표시 폭 계산 (순수 함수 — curses 불필요)
# --------------------------------------------------------------------------- #
def cwidth(text):
    """문자열의 화면 표시 폭(전각=2, 그 외=1)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in text)


def fit(text, cols):
    """표시 폭이 cols 컬럼을 넘지 않도록 문자열을 자른다."""
    out = []
    used = 0
    for c in text:
        cw = 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
        if used + cw > cols:
            break
        out.append(c)
        used += cw
    return "".join(out)


def wrap(text, cols):
    """표시 폭 기준 줄바꿈(전각 문자 고려). 항상 최소 1줄 반환."""
    cols = max(4, cols)
    lines = []
    for para in text.split("\n"):
        cur, curw = "", 0
        for word in para.split(" "):
            ww = cwidth(word)
            if cur and curw + 1 + ww > cols:
                lines.append(cur)
                cur, curw = word, ww
            elif cur:
                cur, curw = cur + " " + word, curw + 1 + ww
            else:
                cur, curw = word, ww
            while cwidth(cur) > cols:  # 한 단어가 폭을 넘으면 강제 분할
                part = fit(cur, cols)
                if not part:
                    break
                lines.append(part)
                cur = cur[len(part):]
                curw = cwidth(cur)
        lines.append(cur)
    return lines or [""]


# --------------------------------------------------------------------------- #
# 텍스트 편집 커서 계산 (순수 함수 — 입력 오버레이·SQL 콘솔이 사용)
# --------------------------------------------------------------------------- #
def word_left(chars, pos):
    """커서 왼쪽의 단어 시작으로 이동(Option+←). 공백을 먼저 건너뛴다."""
    i = min(max(pos, 0), len(chars))
    while i > 0 and chars[i - 1].isspace():
        i -= 1
    while i > 0 and not chars[i - 1].isspace():
        i -= 1
    return i


def word_right(chars, pos):
    """커서 오른쪽의 단어 끝으로 이동(Option+→). 공백을 먼저 건너뛴다."""
    n = len(chars)
    i = min(max(pos, 0), n)
    while i < n and chars[i].isspace():
        i += 1
    while i < n and not chars[i].isspace():
        i += 1
    return i


def line_start(chars, pos):
    """현재 줄의 시작 인덱스."""
    i = min(max(pos, 0), len(chars))
    while i > 0 and chars[i - 1] != "\n":
        i -= 1
    return i


def line_end(chars, pos):
    """현재 줄의 끝 인덱스(개행 앞)."""
    n = len(chars)
    i = min(max(pos, 0), n)
    while i < n and chars[i] != "\n":
        i += 1
    return i


def move_line(chars, pos, delta):
    """위/아래 줄로 이동하되 열(column)을 최대한 유지한다."""
    start = line_start(chars, pos)
    col = pos - start
    if delta < 0:
        if start == 0:
            return pos
        prev_end = start - 1                  # 이전 줄의 개행 위치
        prev_start = line_start(chars, prev_end)
        return min(prev_start + col, prev_end)
    end = line_end(chars, pos)
    if end >= len(chars):
        return pos
    next_start = end + 1
    next_end = line_end(chars, next_start)
    return min(next_start + col, next_end)


def put(stdscr, curses, y, x, text, cols, attr=0):
    """(y, x)부터 cols 컬럼 안에서 안전하게 텍스트를 그린다.

    표시 폭 기준으로 자르고 curses.error(화면 경계 초과)를 무시한다.
    """
    if y < 0 or x < 0 or cols <= 0:
        return
    try:
        stdscr.addstr(y, x, fit(text, cols), attr)
    except curses.error:
        pass


def bar(stdscr, curses, y, w, text):
    """반전색 상태 바를 한 줄로 그린다(폭에 맞춰 공백으로 채움)."""
    inner = fit(text, w - 1)
    inner += " " * max(0, (w - 1) - cwidth(inner))
    put(stdscr, curses, y, 0, inner, w - 1, curses.A_REVERSE)


# --------------------------------------------------------------------------- #
# 키 입력 정규화
# --------------------------------------------------------------------------- #
_EXT_KEY_RE = re.compile(r"k([A-Z]+)(\d)$")
_EXT_KEY_BASE = {
    "LFT": "KEY_LEFT", "RIT": "KEY_RIGHT", "UP": "KEY_UP", "DN": "KEY_DOWN",
    "HOM": "KEY_HOME", "END": "KEY_END", "DC": "KEY_DC", "IC": "KEY_IC",
    "PRV": "KEY_PPAGE", "NXT": "KEY_NPAGE",
}


def decode_named_key(curses, code):
    """ncurses 확장 키를 (kind, 기본키)로 해석. 해당 없으면 None.

    최신 ncurses는 terminfo 확장 능력을 알고 있어 수식키 조합을 ESC 시퀀스가
    아니라 단일 정수 키코드로 돌려준다(예: Alt+← = kLFT3, Ctrl+→ = kRIT5).
    이름 규칙은 `k<기본키><수식자숫자>`이며, 숫자는 xterm 관례로
    (값-1)이 비트마스크다: 1=Shift, 2=Alt, 4=Ctrl.
    """
    keyname = getattr(curses, "keyname", None)
    if keyname is None or not isinstance(code, int) or code < 0:
        return None
    try:
        name = keyname(code).decode("ascii", "replace")
    except Exception:
        return None
    m = _EXT_KEY_RE.fullmatch(name)
    if not m:
        return None
    base = _EXT_KEY_BASE.get(m.group(1))
    key = getattr(curses, base, None) if base else None
    if key is None:
        return None
    mod = max(0, int(m.group(2)) - 1)
    if mod & 2:
        return ("alt", key)
    if mod & 4:
        return ("ctrl", key)
    return ("key", key)


def read_key(stdscr, curses, wide=False, trace=None):
    """키를 읽어 (kind, value)로 정규화한다.

    trace 리스트를 주면 실제로 읽은 원시 값을 순서대로 담는다(--keydebug 용).

    kind: "key" 일반 키 / "esc" 단독 Esc / "alt" Alt(Option) 조합 / "ctrl" Ctrl 조합.

    터미널에서 ESC는 세 가지를 뜻한다.
      (1) 사용자가 누른 단독 Esc 키
      (2) 특수키 시퀀스의 시작 — `ESC[H`(Home), `ESC[D`(←) 등
      (3) Alt(Option) 수식 — `ESC b`, `ESC ESC[D` 등
    (1)은 뒤따르는 입력이 없다는 점으로, (2)/(3)은 ESC가 두 번 오는지와
    CSI 파라미터의 수식자 필드(`ESC[1;3D`의 3)로 구분한다. 시퀀스는 최종
    바이트까지 모두 소비해 잔여 바이트가 화면을 오작동시키지 않게 한다.
    """
    read = stdscr.get_wch if wide else stdscr.getch

    def _peek():
        stdscr.nodelay(True)
        try:
            v = read()
        except curses.error:
            v = None
        finally:
            stdscr.nodelay(False)
        v = -1 if v == -1 else v
        if trace is not None and v not in (None, -1):
            trace.append(v)
        return v

    try:
        ch = read()
    except curses.error:
        return ("key", None)
    if trace is not None:
        trace.append(ch)
    if ch not in ("\x1b", 27):
        # ncurses가 이미 확장 키(kLFT3=Alt+← 등)로 디코드한 경우를 먼저 해석
        return decode_named_key(curses, ch) or ("key", ch)

    nxt = _peek()
    if nxt in (None, -1):
        return ("esc", None)                 # (1) 단독 Esc
    alt = False
    if nxt in ("\x1b", 27):
        # (3) ESC ESC [ D — Alt 접두 + 실제 시퀀스. 안쪽을 이어서 파싱한다.
        alt = True
        nxt = _peek()
        if nxt in (None, -1):
            return ("esc", None)
    if nxt not in ("[", "O", 91, 79):
        return ("alt", nxt)                  # ESC b / ESC f / ESC CR / ESC KEY_*

    # CSI/SS3 시퀀스: 최종 바이트(@~)까지 소비
    params = ""
    final = None
    for _ in range(16):
        v = _peek()
        if v in (None, -1):
            break
        c = v if isinstance(v, str) else (chr(v) if 0 <= v < 0x110000 else "")
        if c and "@" <= c <= "~":
            final = c
            break
        params += c

    fields = params.split(";")
    # 수식자: `1;3D`의 두 번째 필드. (mod-1) 비트마스크 = 1:Shift 2:Alt 4:Ctrl
    mod = 0
    if len(fields) >= 2 and fields[1].isdigit():
        mod = max(0, int(fields[1]) - 1)
    if mod & 2:
        alt = True
    ctrl = bool(mod & 4)

    def _out(key):
        if alt:
            return ("alt", key)
        if ctrl:
            return ("ctrl", key)
        return ("key", key)

    finals = {"A": curses.KEY_UP, "B": curses.KEY_DOWN,
              "C": curses.KEY_RIGHT, "D": curses.KEY_LEFT,
              "H": curses.KEY_HOME, "F": curses.KEY_END}
    if final in finals:
        return _out(finals[final])
    if final == "~":
        tilde = {"1": curses.KEY_HOME, "7": curses.KEY_HOME,
                 "4": curses.KEY_END, "8": curses.KEY_END,
                 "2": curses.KEY_IC, "3": curses.KEY_DC,
                 "5": curses.KEY_PPAGE, "6": curses.KEY_NPAGE}
        head = fields[0] if fields else ""
        if head in tilde:
            return _out(tilde[head])
        # modifyOtherKeys: ESC[27;2;13~ → 코드 13(Enter)
        if head == "27" and len(fields) >= 3 and fields[2] == "13":
            return ("alt", "\r")
    if final == "u":
        # CSI-u: ESC[13;2u → Enter + 수식자
        if fields and fields[0] == "13":
            return ("alt", "\r")
    return ("alt" if alt else "key", None)


# --------------------------------------------------------------------------- #
# 키 값 정규화 (순수 함수 — curses 불필요)
# --------------------------------------------------------------------------- #
# read_key는 wide=False면 getch()의 **정수**를, wide=True면 get_wch()의 문자열을
# 돌려준다. 즉 같은 'q' 키가 상황에 따라 113이기도 하고 "q"이기도 하다.
# 호출부가 매번 이 차이를 신경 쓰면 어느 한쪽만 처리하다 키가 조용히 죽는다.
# 비교는 반드시 아래 헬퍼를 거친다.
def key_char(key):
    """키 값을 비교용 단일 문자로 정규화. 문자가 아니면 None.

        key_char(113) == key_char("q") == "q"

    KEY_UP(259) 같은 특수키도 chr()로는 문자가 되지만 일반 글자와 겹치지
    않으므로, 특수키는 호출부에서 정수 상수로 **먼저** 판별한다.
    """
    if isinstance(key, bool):
        return None
    if isinstance(key, str):
        return key if len(key) == 1 else None
    if isinstance(key, int) and 0 <= key < 0x110000:
        return chr(key)
    return None


def is_enter(key):
    """Enter/Return 키인가 (정수·문자열 표현 모두)."""
    return key in (10, 13, "\n", "\r") or key == 343  # 343 = KEY_ENTER


def is_backspace(key):
    """Backspace/Delete 키인가 (터미널마다 보내는 값이 제각각이다)."""
    return key in (127, 263, 8, "\x7f", "\b")         # 263 = KEY_BACKSPACE


def is_idle(kind, key):
    """입력 없이 타임아웃으로 돌아온 값인가(nodelay/timeout 사용 시)."""
    return kind == "key" and key in (-1, None)


def is_affirmative(key):
    """확인 화면에서 '예'로 읽을 키인가.

    **y만 승낙한다.** 되돌릴 수 없는 동작을 묻는 자리에 쓰이므로 Enter조차
    승낙이 아니다 — 화면을 안 읽고 누른 키가 곧 실행이 되면 확인의 의미가 없다.
    """
    return (key_char(key) or "").lower() == "y"


# --------------------------------------------------------------------------- #
# 세로 목록 선택기
# --------------------------------------------------------------------------- #
def pick(stdscr, curses, title, labels, footer=None,
         allow_cancel=True, allow_quit=True):
    """세로 목록에서 하나 고른다 → 고른 인덱스(취소면 None).

    '↑↓로 옮기고 Enter로 고른다'가 이 저장소 여러 화면에 흩어져 따로 구현돼
    있었다. 키 비교 함정(`read_key`가 모드에 따라 정수/문자열을 준다)이
    구현마다 따로 관리되는 것이 특히 위험해서 여기로 모았다.

    목록이 화면보다 길면 선택 위치를 따라 스크롤한다 — 자르면 마지막 항목을
    영영 고를 수 없다.

    `allow_quit`이면 **대문자** `Q`가 `QuitApp`을 올려 앱 전체를 끝낸다.
    소문자 `q`는 그대로 '취소/뒤로'다 — 둘을 가르는 것이 이 화면의 계약이다.
    게임이 진행 중인 화면(`shooting._pick_client_target`)은 이걸 꺼야 한다.
    거기서 앱을 끄면 랩 컨테이너가 뜬 채로 남는다.
    """
    if not labels:
        return None

    sel = 0
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bar(stdscr, curses, 0, w, f" {title} ")

        # 제목 줄(0)과 하단 바(h-1), 그리고 목록 위 여백 한 줄을 뺀 나머지.
        room = max(1, h - 3)
        # 선택 항목이 보이는 창으로 오도록 시작점을 민다.
        top = min(max(0, sel - room + 1), max(0, len(labels) - room))
        for row, i in enumerate(range(top, min(len(labels), top + room))):
            selected = (i == sel)
            put(stdscr, curses, 2 + row, 1, "▶" if selected else " ", 2,
                curses.color_pair(4))
            put(stdscr, curses, 2 + row, 3, f"{i + 1}) {labels[i]}", w - 4,
                curses.A_REVERSE if selected else 0)

        hint = footer or (" ↑↓ 또는 숫자 선택   Enter 확정" +
                          ("   Esc/q 취소" if allow_cancel else "") +
                          ("   Q 종료 " if allow_quit else " "))
        bar(stdscr, curses, h - 1, w, hint)
        stdscr.refresh()

        stdscr.timeout(-1)
        kind, key = read_key(stdscr, curses)
        if kind == "esc":
            if allow_cancel:
                return None
            continue
        if kind != "key" or key is None:
            continue
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
        # raw(대소문자 보존)로 비교한다 — allow_quit=False일 때 접힌 "Q"가
        # 소문자 q 취소로 오인되면 안 된다(그냥 무시돼야 한다).
        elif raw == "q" and allow_cancel:
            return None


# --------------------------------------------------------------------------- #
# 키 진단 표시 (--keydebug 용)
# --------------------------------------------------------------------------- #
def describe_raw(curses, v):
    """진단용: 원시 입력 값을 사람이 읽을 수 있게."""
    if isinstance(v, str):
        return " ".join(f"{ord(c):02x}" for c in v) + f" {v!r}"
    try:
        name = curses.keyname(v).decode("ascii", "replace")
    except Exception:
        name = "?"
    return f"{v} ({name})"


def describe_key(curses, kind, val):
    """진단용: 파싱 결과를 사람이 읽을 수 있게."""
    if val is None:
        return f"{kind} + (없음/미매핑)"
    if isinstance(val, str):
        return f"{kind} + {val!r}"
    try:
        name = curses.keyname(val).decode("ascii", "replace")
    except Exception:
        name = str(val)
    return f"{kind} + {name}"


def pick_line(title, labels, ask=None):
    """번호로 하나 고른다 → 고른 인덱스(취소면 None).

    `pick()`의 평문 짝이다. curses를 쓸 수 없을 때(파이프·비-tty) 쓴다.

    같은 모양이 러너 세 곳에 따로 있었고 네 번째가 생길 참이었다 — `pick()`이
    여기 모인 이유가 정확히 그것이었는데(`CLAUDE.md`) 평문 짝만 남아 있었다.

    입력 함수를 `ask`로 받는다. `prompt`라고 하면 `exam._pick_line(prompt,
    labels)`의 첫 인자(제목)와 이름이 겹쳐 호출부가 읽히지 않는다. 기본값을
    `input`으로 **박아 두지 않는 것**은 의도다 — 기본 인자는 def 시점에 한 번
    평가되어 빌트인을 붙들므로, 그렇게 두면 `tui.input` 을 바꿔 넣는 테스트가
    통하지 않는다. 호출 시점에 찾는다.

    취소를 **None**으로 알린다. 예외로 알리던 호출부(`exam`)는 얇은 어댑터로
    자기 계약을 지킨다 — 공용이 가장 단순한 계약을 갖는 편이 낫다.
    """
    ask = ask or input
    print(f"\n{title}\n")
    for i, label in enumerate(labels, 1):
        print(f"  {i}) {label}")
    print()
    while True:
        try:
            raw = ask(f"번호 (1-{len(labels)}, q=종료): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw in ("q", "Q"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        print("잘못된 입력입니다.")


# --------------------------------------------------------------------------- #
# 평문 출력 보존 (curses 리프레시가 지우기 전에 한 번 멈춤)
# --------------------------------------------------------------------------- #
def pause_after_output():
    """직전에 찍힌 평문 출력을 다음 curses 리프레시가 곧바로 지우지 못하게 멈춘다.

    `guide`(메뉴로 복귀)와 `reading`(챕터→시험 핸드오프 뒤 다음 챕터 선택
    화면으로 복귀)이 같은 함정을 밟는다: 다음 프레임에서 `curses.wrapper` →
    `stdscr.erase()`가 열리기 때문에, 방금 평문으로 찍힌 것 — `exam.main`이
    `SystemExit`에 실어 올린 사유, `KeyboardInterrupt`를 스스로 잡고 돌아오며
    남긴 "시험을 중단했습니다.", `shoot`의 등급표·후일담, `$PAGER`도 `less`도
    없을 때 그대로 print된 챕터 본문 — 이 한 프레임도 못 읽히고 사라진다.
    `guide.pause_after_mode`가 처음 이 문제를 풀었고, `reading`이 같은 모양을
    다시 필요로 하면서 여기로 옮겨 왔다(`pick_line`이 같은 이유로 여기 모인
    전례를 따른다).

    tty에서만 멈춘다. 비-tty(파이프)에서 `input()`을 부르면 다음 입력 줄을
    삼켜 파이프 실행(테스트 포함)이 깨지므로 거기서는 멈추지 않는다.

    `EOFError`·`KeyboardInterrupt`도 삼킨다 — 호출부가 애써 격리해 둔 것이
    여기서 새면 무의미해진다.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    try:
        input("\n계속하려면 Enter를 누르세요...")
    except (EOFError, KeyboardInterrupt):
        pass


# --------------------------------------------------------------------------- #
# 외부 도구 위임
# --------------------------------------------------------------------------- #
# 색을 살려 넘길 수 있는 순수 페이저들. 포맷터(bat, delta)는 입력을 재해석·
# 재장식하므로 이미 ANSI 가 들어간 마크다운을 먹이면 결과를 예측할 수 없다.
# 모르는 페이저로 분류되면 무색으로 안전하게 떨어진다. `more` 에 이스케이프를
# 보내면 `ESC[1m` 이 글자로 찍혀 **지금보다 나빠진다.**
COLOR_PAGERS = ("less", "most", "moar", "ov")


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

    # less의 단축 옵션 중 인자를 붙여 받는 글자들. 이들 뒤의 텍스트는 옵션이
    # 아니라 인자이므로 스캔을 중단해야 한다 (예: -Pcurrent 에서 current는
    # -P의 인자이지 옵션 글자들이 아니다). 부분문자열 검색은 인자값에 들어간
    # 'r'/'R'을 오탐할 수 있어 글자 단위 스캔이 필수.
    takes_arg = set("bhjkoOpPtTxyz#")

    for a in argv[1:]:
        if a.startswith("--") and a.lower().startswith("--raw"):
            return argv
        if a.startswith("-") and not a.startswith("--"):
            # 단축 옵션 글자를 하나하나 검사
            for c in a[1:]:
                if c == "R" or c == "r":
                    return argv
                if c in takes_arg:
                    # 이 글자는 인자를 받으므로 뒤의 텍스트는 모두 인자이다
                    break
    return argv + ["-R"]


def page_text(text):
    """텍스트를 페이저로 넘긴다(curses 밖에서 호출).

    뷰어를 curses로 만들지 않는다 — `less`가 스크롤·검색(`/`)을 이미 다 한다.
    목록 UI조차 필요 없다: 이어 붙여 넘기면 끝이다.
    """
    pager = os.environ.get("PAGER") or ("less -R" if shutil.which("less")
                                        else None)
    if not pager:
        print(text)
        return 0
    try:
        proc = subprocess.Popen(_with_raw_flag(shlex.split(pager)),
                                stdin=subprocess.PIPE, text=True)
        proc.communicate(text)
        return proc.returncode
    except (OSError, KeyboardInterrupt):
        print(text)
        return 0
