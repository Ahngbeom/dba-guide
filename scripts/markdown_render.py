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
