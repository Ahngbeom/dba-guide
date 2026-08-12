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
_FENCE_RE = re.compile(r"^\s*```+\s*(\w*)")

# 체크박스가 불릿보다 **먼저** 걸려야 한다 — `- [ ] …` 는 불릿에도 맞는다.
_TASK_RE = re.compile(r"^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")

_SEP_CELL_RE = re.compile(r"^:?-{1,}:?$")
_MIN_COL = 4          # 한글 두 글자. 0폭 열이 생기지 않게 하는 하한

# 깊이별 불릿 기호. 이 저장소는 중첩 들여쓰기를 2·3·4칸으로 섞어 쓴다(실측)
# 이라 `len(indent) // 2` 로 재면 3칸과 4칸이 다른 깊이가 되어 같은 목록이
# 들쭉날쭉해진다. 1~4칸을 한 단계로 묶는다.
_MARKERS = ("•", "◦", "-")

# 수평선 길이. **폭을 채우지 않는다** — 채우면 터미널이 좁아지는 순간 `less` 가
# 접어 구분선 하나가 두 줄이 된다. 가장 좁은 지원 폭(`tui.text_width()` 하한
# 40)에서도 안 접히면서 구분선 구실을 하는 길이다.
_RULE_WIDTH = 24


def _blank(out):
    """직전 줄이 이미 비어 있지 않을 때만 빈 줄을 넣는다."""
    if out and out[-1] != "":
        out.append("")


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


def _depth(indent):
    return 0 if not indent else min(len(_MARKERS) - 1, 1 + (len(indent) - 1) // 4)


def _emit_hanging(head, body, width, color, out, head_style=None, repeat=False):
    """`head` 를 붙이고 접힌 줄을 그 폭만큼 들여 쓴다.

    목록은 이어지는 줄이 기호 아래가 아니라 **글자 아래**로 와야 항목 경계가
    보인다(`repeat=False`). 인용은 반대로 **막대가 이어져야** 인용 범위가
    보인다(`repeat=True`) — 둘째 줄에서 `│` 가 끊기면 인용이 끝난 것처럼
    읽힌다.
    """
    hw = cwidth(head)

    # 접두가 너무 길면 선행 공백을 줄인다 — 마커는 지킨다. layout() 은
    # 인자를 max(4, width) 로 클램프하므로, hw > width - 4 인 경우
    # width - hw 가 음수나 매우 작으면 클램프가 무력화되어 최종 줄이 width 를 넘는다.
    if hw > width - 4:
        # head 는 "    • " 형태: 선행 공백 + 마커 + 공간
        space_end = 0
        while space_end < len(head) and head[space_end] == ' ':
            space_end += 1

        if space_end > 0:
            marker_part = head[space_end:]
            marker_width = cwidth(marker_part)

            # 본문을 위해 최소 4칸이 필요하므로, 선행 공백은 최대 width - 4 - marker_width
            available_for_spaces = width - 4 - marker_width

            if available_for_spaces > 0:
                head = ' ' * available_for_spaces + marker_part
            else:
                # 마커조차 4칸과 함께 안 맞으면 마커를 자른다
                head = fit(marker_part, width - 4)
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
    # dbms 마커 주석이 표 행 사이에 끼어들 수 있다(부록 dbms-branch-strategy 의
    # 행 단위 marking 작업이 그 경로다). 주석에서 표가 끊기면 남은 행이
    # `_is_separator` 검사를 통과하지 못해 문단으로 떨어지고 파이프가 그대로
    # 화면에 남는다 — 주석은 건너뛰되 행은 계속 모아 표를 안 끊는다.
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


def _emit_paragraph(line, width, color, out):
    for row in layout(inline_spans(line, color), width):
        out.append(paint(row, color))


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
    head = fit(f"┌─ {lang} ", width) if lang else "┌"
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
        if _FENCE_RE.match(line):
            i = _emit_fence(lines, i - 1, width, color, out)
            continue
        if (line.strip().startswith("|") and i < len(lines)
                and _is_separator(lines[i])):
            i = _emit_table(lines, i - 1, width, color, out)
            continue
        if not line.strip():
            _blank(out)
            continue
        if _HR_RE.match(line):
            out.append(paint([("─" * min(width, _RULE_WIDTH), "dim")], color))
            continue
        m = _HEADING_RE.match(line)
        if m:
            _emit_heading(len(m.group(1)), m.group(2), width, color, out)
            continue
        if _emit_list_or_quote(line, width, color, out):
            continue
        _emit_paragraph(line, width, color, out)

    return "\n".join(out) + "\n"
