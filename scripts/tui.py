#!/usr/bin/env python3
"""터미널 UI 공용 프리미티브 (표시 폭 계산·안전 출력·키 입력 정규화).

`exam.py`(학습 점검 시험)와 `shooting.py`(장애 대응 게임)가 함께 쓴다.
외부 의존성 없음(Python3 표준 라이브러리만 사용).

이 모듈은 `curses`를 최상단에서 import하지 않는다 — curses가 필요한 함수는
호출자가 `curses` 모듈을 인자로 넘긴다. 덕분에 tty가 없는 환경(테스트 등)에서도
폭 계산·줄바꿈 같은 순수 함수를 그대로 쓸 수 있다.
"""
import re
import unicodedata


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
