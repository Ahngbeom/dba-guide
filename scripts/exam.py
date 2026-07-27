#!/usr/bin/env python3
"""챕터별 학습 점검용 TUI 퀴즈/시험 러너.

`exams/**/*.json` 문제은행을 읽어 터미널에서 시험을 진행한다.
- 이론 개념 문항은 객관식(mcq), 명령어/실전 문항은 주관식(short)/서술형(essay).
- mcq/short는 자동 채점(정규화 매칭), essay는 모범답안 표시 후 자기채점.
- 표준 라이브러리 curses 기반 풀스크린 TUI. tty가 아니면 라인 모드로 폴백.

외부 의존성 없음(Python3 표준 라이브러리만 사용).

사용법:
    python3 scripts/exam.py [<대상>] [--dbms <name>] [--shuffle]

    <대상>   생략하면 메뉴에서 선택. `exams/**/*.json` 경로 또는
             티어 이름(예: 01-beginner, 챕터 전체)을 줄 수 있다.
    --dbms   지정한 DBMS(neutral 항상 포함) 문항만 출제.
    --shuffle 문항 순서를 무작위로 섞는다.
"""
import argparse
import datetime
import glob
import json
import locale
import os
import random
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMS_DIR = REPO_ROOT / "exams"
RESULTS_DIR = REPO_ROOT / ".exam-results"   # 시험 결과 로컬 저장(비커밋 — .gitignore)
RESULTS_FILE = RESULTS_DIR / "results.jsonl"

VALID_DBMS = ("postgresql", "mysql", "oracle")
QUESTION_TYPES = ("mcq", "short", "essay")
PASS_THRESHOLD = 0.7  # 자동채점 정답률이 이 미만이면 재학습 권고

# TUI DBMS 선택지: (표시 라벨, 필터값). None이면 전체(공통 + 모든 벤더).
DBMS_CHOICES = (
    ("전체 (공통 + 모든 벤더)", None),
    ("PostgreSQL", "postgresql"),
    ("MySQL", "mysql"),
    ("Oracle", "oracle"),
)

# 티어 디렉터리명 → 사람이 읽는 라벨
TIER_LABELS = {
    "01-beginner": "초급",
    "02-intermediate": "중급",
    "03-advanced": "고급",
}

# 시험 종료 후 결과 메뉴: (라벨, 액션)
POST_EXAM_MENU = (
    ("다른 챕터 (같은 티어)", "next_chapter"),
    ("처음부터 다시 선택", "restart"),
    ("종료", "quit"),
)

_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# 순수 로직 (UI와 분리 — 테스트 대상)
# --------------------------------------------------------------------------- #
def normalize_answer(text):
    """단답/명령어 채점을 위한 정규화.

    대소문자 무시(casefold), 앞뒤 공백 제거, 내부 연속 공백 1칸 축소,
    끝의 세미콜론/공백 제거.
    """
    if text is None:
        return ""
    text = _WS_RE.sub(" ", text.strip()).casefold()
    return text.rstrip("; ").strip()


def grade_short(user, accept):
    """정규화한 사용자 입력이 accept 목록 중 하나와 일치하면 정답."""
    norm_user = normalize_answer(user)
    if not norm_user:
        return False
    return any(norm_user == normalize_answer(a) for a in (accept or []))


def grade_mcq(selected_index, answer_index):
    """선택한 보기 인덱스가 정답 인덱스와 같으면 정답."""
    return selected_index == answer_index


# 등급 임계값(자동채점 정답률). 통과선 PASS_THRESHOLD(0.7)와 정합.
GRADE_BANDS = ((0.9, "A"), (0.8, "B"), (0.7, "C"), (0.6, "D"))


def grade_letter(score):
    """정답률(0~1)을 문자 등급 A~F로 변환."""
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


# 연속 정답 마일스톤(축하 이벤트를 띄우는 지점)
STREAK_MILESTONES = (3, 5, 10, 15, 20, 30, 50)


def next_streak(streak, correct):
    """제출 결과로 연속 정답 수를 갱신. 정답이면 +1, 오답이면 0."""
    return streak + 1 if correct else 0


def is_milestone(n):
    """연속 정답 수가 축하 마일스톤인지."""
    return n in STREAK_MILESTONES


def is_locked(st):
    """채점이 확정된 문항인가.

    제출은 첫 1회로 확정된다(정답 공개 후 점수·연속 조작 방지).
    서술형 '건너뜀'(correct=None)은 채점된 답이 아니므로 잠그지 않아 다시 풀 수 있다.
    """
    return bool(st.get("answered")) and st.get("correct") is not None


def record_streak(session, correct, already_counted):
    """연속 정답을 집계한다(멱등).

    이미 집계된 제출이거나 건너뜀(None)이면 스트릭을 건드리지 않고 False.
    실제로 집계했으면 True(→ 피드백 이벤트를 재생할 시점).
    """
    if already_counted or correct is None:
        return False
    session["streak"] = next_streak(session["streak"], correct)
    session["best"] = max(session["best"], session["streak"])
    return True


# --------------------------------------------------------------------------- #
# 텍스트 편집 커서 계산 (순수 함수 — 입력 오버레이가 사용)
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


def shuffle_choices(choices, answer_index, rng):
    """객관식 보기를 무작위로 섞는다.

    (섞인 보기 리스트, 섞인 정답 인덱스, 표시순서(원본 인덱스 리스트))를 반환.
    표시순서를 세션 동안 보관하면 이동해도 순서가 유지되고,
    선택한 표시 인덱스 → 원본 인덱스 매핑으로 정확히 채점할 수 있다.
    """
    order = list(range(len(choices)))
    rng.shuffle(order)
    new_choices = [choices[i] for i in order]
    new_answer = order.index(answer_index)
    return new_choices, new_answer, order


def filter_by_dbms(questions, dbms):
    """dbms가 지정되면 neutral과 해당 벤더 문항만 남긴다.

    dbms가 None이면 모든 문항을 그대로 통과시킨다.
    문항에 dbms 필드가 없으면 neutral로 간주한다.
    """
    if not dbms:
        return list(questions)
    keep = {"neutral", dbms}
    return [q for q in questions if q.get("dbms", "neutral") in keep]


def validate_bank(bank):
    """문제은행 구조를 검증하고 오류 메시지 리스트를 반환(빈 리스트면 정상)."""
    errors = []
    if not isinstance(bank, dict):
        return ["최상위 구조가 객체(dict)가 아닙니다."]
    questions = bank.get("questions")
    if not isinstance(questions, list) or not questions:
        return ["'questions' 배열이 없거나 비어 있습니다."]
    for i, q in enumerate(questions):
        where = f"questions[{i}]"
        qtype = q.get("type")
        if qtype not in QUESTION_TYPES:
            errors.append(f"{where}: 알 수 없는 type={qtype!r}")
            continue
        if not q.get("q"):
            errors.append(f"{where}: 질문 텍스트 'q'가 비어 있습니다.")
        dbms = q.get("dbms", "neutral")
        if dbms not in ("neutral",) + VALID_DBMS:
            errors.append(f"{where}: 잘못된 dbms={dbms!r}")
        if qtype == "mcq":
            choices = q.get("choices")
            answer = q.get("answer")
            if not isinstance(choices, list) or len(choices) < 2:
                errors.append(f"{where}: mcq는 choices(2개 이상)가 필요합니다.")
            elif not isinstance(answer, int) or not (0 <= answer < len(choices)):
                errors.append(f"{where}: mcq answer 인덱스가 범위를 벗어났습니다.")
        elif qtype == "short":
            if not q.get("accept"):
                errors.append(f"{where}: short는 accept(허용 정답) 목록이 필요합니다.")
        elif qtype == "essay":
            if not q.get("reference"):
                errors.append(f"{where}: essay는 reference(모범답안)가 필요합니다.")
    return errors


def load_bank(path):
    """JSON 문제은행을 로드하고 구조를 검증한다. 실패 시 ValueError."""
    with open(path, encoding="utf-8") as f:
        bank = json.load(f)
    errors = validate_bank(bank)
    if errors:
        raise ValueError(f"{path}: 문제은행 검증 실패:\n  - " + "\n  - ".join(errors))
    return bank


def summarize(results):
    """결과 리스트를 집계.

    results 항목: {"type": str, "correct": bool|None}
      - mcq/short: correct = True/False (자동채점)
      - essay: correct = True/False (자기채점) 또는 None(건너뜀)
    반환: total, auto_total, auto_correct, auto_wrong, essay_total, essay_correct,
          score(자동채점 정답률 0~1), grade(A~F), passed(bool)
    """
    total = len(results)
    auto = [r for r in results if r["type"] in ("mcq", "short")]
    essays = [r for r in results if r["type"] == "essay"]
    auto_correct = sum(1 for r in auto if r["correct"])
    essay_correct = sum(1 for r in essays if r["correct"])
    score = (auto_correct / len(auto)) if auto else 1.0
    return {
        "total": total,
        "auto_total": len(auto),
        "auto_correct": auto_correct,
        "auto_wrong": len(auto) - auto_correct,
        "essay_total": len(essays),
        "essay_correct": essay_correct,
        "score": score,
        "grade": grade_letter(score),
        "passed": score >= PASS_THRESHOLD,
    }


# --------------------------------------------------------------------------- #
# 시험 결과 로컬 저장 (git 비커밋 — .exam-results/)
# --------------------------------------------------------------------------- #
def build_result_record(bank, results, session, dbms, ts):
    """저장용 결과 레코드(dict)를 만든다. 순수 함수(IO 없음)."""
    s = summarize(results)
    wrong_ids = [r["q"].get("id") for r in results if r["correct"] is False]
    return {
        "ts": ts,
        "chapter": bank.get("chapter"),
        "title": bank.get("title"),
        "dbms": dbms or "all",
        "auto_total": s["auto_total"],
        "auto_correct": s["auto_correct"],
        "score": round(s["score"], 4),
        "grade": s["grade"],
        "essay_total": s["essay_total"],
        "essay_correct": s["essay_correct"],
        "best_streak": (session or {}).get("best", 0),
        "wrong_ids": wrong_ids,
    }


def best_result_for(chapter, records):
    """해당 챕터의 최고 기록(정답률 → 등급 우선)을 반환. 없으면 None."""
    same = [r for r in records
            if r.get("chapter") == chapter and r.get("auto_total")]
    if not same:
        return None
    return max(same, key=lambda r: (r.get("score", 0), -_GRADE_ORDER.get(
        r.get("grade", "F"), 5)))


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}


def read_results():
    """results.jsonl을 읽어 레코드 리스트 반환(없거나 손상 줄은 건너뜀)."""
    if not RESULTS_FILE.exists():
        return []
    out = []
    try:
        for line in RESULTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out


def save_result(bank, results, session, dbms):
    """결과를 results.jsonl에 한 줄 append. IO 오류는 경고만(시험을 막지 않음)."""
    if not results:
        return
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    record = build_result_record(bank, results, session, dbms, ts)
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(RESULTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"(결과 저장 실패: {e})", file=sys.stderr)


# --------------------------------------------------------------------------- #
# 대상(문제은행) 해석
# --------------------------------------------------------------------------- #
def resolve_targets(target):
    """CLI 대상 인자를 JSON 파일 경로 리스트로 해석한다."""
    if target:
        p = Path(target)
        if p.suffix == ".json" and p.is_file():
            return [p]
        # 티어 이름 또는 exams 하위 경로
        for base in (Path(target), EXAMS_DIR / target):
            if base.is_dir():
                found = sorted(base.glob("*.json"))
                if found:
                    return found
        raise SystemExit(f"대상을 찾을 수 없습니다: {target}")
    # 대상 미지정 → 전체 목록에서 선택
    return None


def discover_banks():
    """exams/ 아래 모든 문제은행 경로를 정렬해 반환."""
    return sorted(Path(p) for p in glob.glob(str(EXAMS_DIR / "**" / "*.json"), recursive=True))


def discover_tiers():
    """문제은행이 하나라도 있는 티어 디렉터리명을 정렬해 반환(예: ['01-beginner'])."""
    tiers = {b.parent.name for b in discover_banks() if b.parent != EXAMS_DIR}
    return sorted(tiers)


def discover_banks_in(tier):
    """특정 티어 디렉터리 안의 문제은행 경로를 정렬해 반환."""
    return sorted((EXAMS_DIR / tier).glob("*.json"))


def tier_label(tier):
    """티어 디렉터리명을 '01-beginner (초급)' 형태로 표시."""
    name = TIER_LABELS.get(tier)
    return f"{tier} ({name})" if name else tier


# --------------------------------------------------------------------------- #
# 라인 모드 (curses 폴백)
# --------------------------------------------------------------------------- #
def _prompt(text=""):
    """input() 래퍼. EOF(Ctrl-D/파이프 끝)를 중단으로 처리한다."""
    try:
        return input(text)
    except EOFError:
        raise KeyboardInterrupt


def _line_combo(correct, streak):
    """라인 모드 연속 정답 표시. 갱신된 streak 반환(essay 건너뜀은 불변)."""
    if correct is None:
        return streak
    streak = next_streak(streak, correct)
    if correct and streak >= 2:
        msg = f"  🔥 연속 {streak}!"
        if is_milestone(streak):
            msg += f"  {streak}연속 정답 달성!"
        print(msg)
    return streak


def run_line(bank, questions, session):
    """input()/print() 폴백 러너. (results, 결과메뉴 액션) 반환."""
    rng = random.Random()
    results = []
    streak = 0
    title = bank.get("title", "")
    print(f"\n== {title} ({len(questions)}문항) ==\n")
    for i, q in enumerate(questions, 1):
        if results:  # 실시간 진행상황
            s = summarize(results)
            rate = f"{s['score'] * 100:.0f}%" if s["auto_total"] else "-"
            grade = s["grade"] if s["auto_total"] else "-"
            print(f"[진행] 정답 {s['auto_correct']} · 오답 {s['auto_wrong']}"
                  f" · 정답률 {rate} · 등급 {grade}")
        print(f"[{i}/{len(questions)}] {q['q']}")
        qtype = q["type"]
        if qtype == "mcq":
            if q.get("shuffle", True):
                choices, answer, _ = shuffle_choices(q["choices"], q["answer"], rng)
            else:
                choices, answer = q["choices"], q["answer"]
            for idx, choice in enumerate(choices):
                print(f"   {idx + 1}) {choice}")
            sel = _line_read_choice(q, len(choices)) - 1
            correct = grade_mcq(sel, answer)
            print("  → 정답입니다!" if correct else
                  f"  → 오답. 정답: {answer + 1}) {choices[answer]}")
            _print_explain(q)
            results.append({"type": "mcq", "correct": correct, "q": q})
            streak = _line_combo(correct, streak)
            session["best"] = max(session["best"], streak)
        elif qtype == "short":
            ans = _line_read_text("  답 입력 (h=힌트): ", q)
            correct = grade_short(ans, q["accept"])
            print("  → 정답입니다!" if correct else
                  f"  → 오답. 예시 정답: {q['accept'][0]}")
            _print_explain(q)
            results.append({"type": "short", "correct": correct, "q": q})
            streak = _line_combo(correct, streak)
            session["best"] = max(session["best"], streak)
        else:  # essay
            _line_read_text("  답을 생각한 뒤 Enter (h=힌트, 직접 적어도 됨): ", q)
            print(f"  [모범답안] {q['reference']}")
            if q.get("keywords"):
                print(f"  [핵심 키워드] {', '.join(q['keywords'])}")
            yn = _prompt("  스스로 맞았다고 보십니까? (y/n/s=건너뜀): ").strip().lower()
            correct = True if yn == "y" else (None if yn == "s" else False)
            _print_explain(q)
            results.append({"type": "essay", "correct": correct, "q": q})
            streak = _line_combo(correct, streak)
            session["best"] = max(session["best"], streak)
        print()
    _print_summary_line(bank, results, session)
    return results, _post_exam_menu_line()


def _post_exam_menu_line():
    """라인 모드 결과 메뉴. 액션 문자열 반환(EOF/q는 quit)."""
    try:
        idx = _pick_line("다음", [lbl for lbl, _ in POST_EXAM_MENU])
    except KeyboardInterrupt:
        return "quit"
    return POST_EXAM_MENU[idx][1]


def _show_hint_line(q):
    hint = q.get("hint")
    print(f"  💡 힌트: {hint}" if hint else "  💡 이 문항은 힌트가 없습니다.")


def _line_read_choice(q, n):
    """mcq 번호 입력. 'h'면 힌트 표시 후 재입력."""
    while True:
        raw = _prompt(f"  번호 입력 (1-{n}, h=힌트): ").strip().lower()
        if raw == "h":
            _show_hint_line(q)
            continue
        if raw.isdigit() and 1 <= int(raw) <= n:
            return int(raw)
        print("  잘못된 입력입니다.")


def _line_read_text(prompt, q):
    """단답/서술형 입력. 'h'만 입력하면 힌트 표시 후 재입력."""
    while True:
        ans = _prompt(prompt)
        if ans.strip().lower() == "h":
            _show_hint_line(q)
            continue
        return ans


def _print_explain(q):
    if q.get("explain"):
        print(f"  해설: {q['explain']}")


def _print_summary_line(bank, results, session=None):
    s = summarize(results)
    print("=" * 48)
    grade = f"등급 {s['grade']} · 정답률 {s['score'] * 100:.0f}%" \
        if s["auto_total"] else "등급 - · 정답률 -"
    print(f"결과: {grade}")
    print(f"  자동채점 {s['auto_correct']}/{s['auto_total']} "
          f"(정답 {s['auto_correct']} · 오답 {s['auto_wrong']}), "
          f"서술형 자기채점 {s['essay_correct']}/{s['essay_total']}")
    if session and session.get("best", 0) >= 2:
        print(f"  최고 연속 정답: 🔥 {session['best']}")
    wrong = [r["q"] for r in results if r["correct"] is False]
    if wrong:
        print("복습이 필요한 문항:")
        for q in wrong:
            print(f"  - {q['q']}")
    if not s["passed"]:
        chapter = bank.get("chapter")
        hint = f" ({chapter} 다시 학습 권장)" if chapter else ""
        print(f"통과 기준({PASS_THRESHOLD * 100:.0f}%) 미달입니다.{hint}")
    else:
        print("통과했습니다. 다음 챕터로 넘어가도 좋습니다!")


# --------------------------------------------------------------------------- #
# curses 모드
# --------------------------------------------------------------------------- #
def _cwidth(text):
    """문자열의 화면 표시 폭(전각=2, 그 외=1)."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in text)


def _fit(text, cols):
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


def _wrap(text, cols):
    """표시 폭 기준 줄바꿈(전각 문자 고려). 항상 최소 1줄 반환."""
    cols = max(4, cols)
    lines = []
    for para in text.split("\n"):
        cur, curw = "", 0
        for word in para.split(" "):
            ww = _cwidth(word)
            if cur and curw + 1 + ww > cols:
                lines.append(cur)
                cur, curw = word, ww
            elif cur:
                cur, curw = cur + " " + word, curw + 1 + ww
            else:
                cur, curw = word, ww
            while _cwidth(cur) > cols:  # 한 단어가 폭을 넘으면 강제 분할
                part = _fit(cur, cols)
                if not part:
                    break
                lines.append(part)
                cur = cur[len(part):]
                curw = _cwidth(cur)
        lines.append(cur)
    return lines or [""]


def _init_states(questions, rng):
    """문항별 런타임 상태 배열을 만든다(mcq는 선택지 셔플 순서 고정)."""
    states = []
    for q in questions:
        st = {"answered": False, "correct": None, "text": "", "hint": False,
              "counted": False}
        if q["type"] == "mcq":
            if q.get("shuffle", True):
                choices, answer, order = shuffle_choices(
                    q["choices"], q["answer"], rng)
            else:
                choices, answer, order = (list(q["choices"]), q["answer"],
                                          list(range(len(q["choices"]))))
            st.update(choices=choices, answer=answer, order=order, sel=0)
        states.append(st)
    return states


def _live_results(questions, states):
    """답한 문항만으로 summarize용 결과 리스트를 만든다(실시간 집계)."""
    return [{"type": q["type"], "correct": s["correct"], "q": q}
            for q, s in zip(questions, states) if s["answered"]]


def run_curses(bank, questions, session):
    """한 챕터 시험을 진행하고 (results, 결과메뉴 액션)을 반환."""
    import curses
    rng = random.Random()

    def _driver(stdscr):
        curses.curs_set(0)
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_RED, -1)
            curses.init_pair(3, curses.COLOR_CYAN, -1)
            curses.init_pair(4, curses.COLOR_YELLOW, -1)
        except curses.error:
            pass
        states = _init_states(questions, rng)
        cur = 0
        while True:
            action = _question_screen(stdscr, curses, bank, questions,
                                      states, cur, session)
            if action == "prev":
                cur = (cur - 1) % len(questions)
            elif action == "next":
                cur = (cur + 1) % len(questions)
            elif action == "advance":
                # Enter 진행: 마지막 문항이면 결과 화면으로
                if cur >= len(questions) - 1:
                    break
                cur += 1
            elif action == "quit":
                break
        results = _live_results(questions, states)
        menu = _summary_curses(stdscr, curses, bank, results, session)
        return results, menu

    return curses.wrapper(_driver)


def _describe_raw(curses, v):
    """진단용: 원시 입력 값을 사람이 읽을 수 있게."""
    if isinstance(v, str):
        return " ".join(f"{ord(c):02x}" for c in v) + f" {v!r}"
    try:
        name = curses.keyname(v).decode("ascii", "replace")
    except Exception:
        name = "?"
    return f"{v} ({name})"


def _describe_key(curses, kind, val):
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


def run_keydebug():
    """터미널이 실제로 보내는 키를 확인하는 진단 화면(`--keydebug`)."""
    import curses

    def _driver(stdscr):
        curses.curs_set(0)
        log = []
        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            _bar(stdscr, curses, 0, w, " 키 진단 (--keydebug) ")
            _put(stdscr, curses, 2, 1,
                 "키를 눌러 보세요. 예: Option+←/→, Home, End, Shift+Enter",
                 w - 2)
            _put(stdscr, curses, 3, 1,
                 "'단어 이동'이 되려면 alt 또는 ctrl + KEY_LEFT/KEY_RIGHT "
                 "(또는 'b'/'f')로 잡혀야 합니다.", w - 2, curses.A_DIM)
            _put(stdscr, curses, 4, 1, "종료: q", w - 2, curses.A_DIM)
            row = 6
            for line in log[-(max(1, h - 8)):]:
                _put(stdscr, curses, row, 1, line, w - 2)
                row += 1
            _bar(stdscr, curses, h - 1, w, " q 종료 ")
            stdscr.refresh()

            trace = []
            kind, val = _read_key(stdscr, curses, wide=True, trace=trace)
            raw = "  ".join(_describe_raw(curses, v) for v in trace)
            log.append(f"raw: {raw}")
            log.append(f"  → {_describe_key(curses, kind, val)}")
            if kind == "key" and val in ("q", "Q"):
                return

    curses.wrapper(_driver)


def _answer_event(stdscr, curses, correct, streak, session):
    """제출 직후 짧은 피드백 이벤트(강조 배너 + 플래시/비프 + 콤보)."""
    milestone = correct and is_milestone(streak)
    if session.get("fx"):
        try:
            curses.flash()
            if milestone:
                curses.napms(60)
                curses.flash()  # 마일스톤 더블 플래시
            if not correct:
                curses.beep()
        except curses.error:
            pass

    h, w = stdscr.getmaxyx()
    if correct:
        title = "✓  정답입니다!"
        pair = curses.color_pair(1)
    else:
        title = "✗  오답입니다"
        pair = curses.color_pair(2)
    lines = [title]
    if milestone:
        lines.append(f"🔥  {streak}연속 정답!")

    box_h = len(lines) + 2
    top = max(0, (h - box_h) // 2)
    band = pair | curses.A_BOLD | curses.A_REVERSE
    # 배너 밴드(가로 전체)
    for r in range(top, min(h, top + box_h)):
        _put(stdscr, curses, r, 0, " " * (w - 1), w - 1, band)
    for i, line in enumerate(lines):
        x = max(1, (w - _cwidth(line)) // 2)
        _put(stdscr, curses, top + 1 + i, x, line, w - 2, band)
    stdscr.refresh()
    curses.napms(800 if milestone else 500)
    curses.flushinp()  # 홀드 중 눌린 키는 버린다(의도치 않은 이동 방지)


_EXT_KEY_RE = re.compile(r"k([A-Z]+)(\d)$")
_EXT_KEY_BASE = {
    "LFT": "KEY_LEFT", "RIT": "KEY_RIGHT", "UP": "KEY_UP", "DN": "KEY_DOWN",
    "HOM": "KEY_HOME", "END": "KEY_END", "DC": "KEY_DC", "IC": "KEY_IC",
    "PRV": "KEY_PPAGE", "NXT": "KEY_NPAGE",
}


def _decode_named_key(curses, code):
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


def _read_key(stdscr, curses, wide=False, trace=None):
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
        return _decode_named_key(curses, ch) or ("key", ch)

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


def _put(stdscr, curses, y, x, text, cols, attr=0):
    """(y, x)부터 cols 컬럼 안에서 안전하게 텍스트를 그린다.

    표시 폭 기준으로 자르고 curses.error(화면 경계 초과)를 무시한다.
    """
    if y < 0 or x < 0 or cols <= 0:
        return
    try:
        stdscr.addstr(y, x, _fit(text, cols), attr)
    except curses.error:
        pass


def _bar(stdscr, curses, y, w, text):
    """반전색 상태 바를 한 줄로 그린다(폭에 맞춰 공백으로 채움)."""
    inner = _fit(text, w - 1)
    inner += " " * max(0, (w - 1) - _cwidth(inner))
    _put(stdscr, curses, y, 0, inner, w - 1, curses.A_REVERSE)


def _dot(curses, i, cur, st):
    """진행 점 스트립의 (문자, 속성)을 결정."""
    if i == cur:
        return "◆", curses.color_pair(4) | curses.A_BOLD
    if not st["answered"]:
        return "·", curses.A_DIM
    if st["correct"] is True:
        return "✓", curses.color_pair(1)
    if st["correct"] is False:
        return "✗", curses.color_pair(2)
    return "~", curses.color_pair(3)  # essay 건너뜀


def _draw_question_screen(stdscr, curses, bank, questions, states, cur, session):
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    q, st = questions[cur], states[cur]
    total = len(questions)
    _bar(stdscr, curses, 0, w,
         f" {bank.get('title', '학습 점검')} — 문항 {cur + 1}/{total} ")

    # 실시간 진행상황
    s = summarize(_live_results(questions, states))
    auto_all = sum(1 for qq in questions if qq["type"] in ("mcq", "short"))
    if s["auto_total"]:
        rate, grade = f"{s['score'] * 100:.0f}%", s["grade"]
    else:
        rate, grade = "-", "-"
    status = (f"채점 {s['auto_total']}/{auto_all} · 정답 {s['auto_correct']}"
              f" · 오답 {s['auto_wrong']} · 정답률 {rate} · 등급 {grade}")
    if session.get("streak", 0) >= 2:
        status += f" · 🔥 연속 {session['streak']}"
    if not session.get("fx", True):
        status += " · 🔇"
    _put(stdscr, curses, 1, 1, status, w - 2, curses.A_BOLD)

    # 진행 점 스트립
    x = 1
    _put(stdscr, curses, 2, x, "[", w - 2)
    x += 1
    for i, dst in enumerate(states):
        ch, attr = _dot(curses, i, cur, dst)
        _put(stdscr, curses, 2, x, ch, max(1, w - 1 - x), attr)
        x += _cwidth(ch)
    _put(stdscr, curses, 2, x, "]", max(1, w - 1 - x))

    # 구분선
    _put(stdscr, curses, 3, 1, "─" * (w - 2), w - 2, curses.A_DIM)

    # 문제 본문
    row = 4
    for line in _wrap(q["q"], w - 2):
        _put(stdscr, curses, row, 1, line, w - 2)
        row += 1
    row += 1

    # 힌트
    if st["hint"]:
        hint = q.get("hint")
        if hint:
            for line in _wrap("💡 힌트: " + hint, w - 2):
                _put(stdscr, curses, row, 1, line, w - 2, curses.color_pair(3))
                row += 1
        else:
            _put(stdscr, curses, row, 1, "💡 이 문항은 힌트가 없습니다.",
                 w - 2, curses.color_pair(4))
            row += 1
        row += 1

    if q["type"] == "mcq":
        row = _draw_mcq_body(stdscr, curses, q, st, row, w)
    else:
        row = _draw_text_body(stdscr, curses, q, st, row, w)

    # 채점 후 해설
    if st["answered"] and q.get("explain"):
        row += 1
        for line in _wrap("해설: " + q["explain"], w - 2):
            if row >= h - 1:
                break
            _put(stdscr, curses, row, 1, line, w - 2, curses.color_pair(3))
            row += 1

    # 하단 키 범례(문항 유형별)
    if is_locked(st):
        nxt = "결과 보기" if cur >= total - 1 else "다음 문항"
        keys = f" Enter {nxt}  ←→ 이전·다음  h 힌트  m 효과  q 결과/종료 "
    elif q["type"] == "mcq":
        keys = " ↑↓ 보기  Enter 제출  ←→ 이전·다음  h 힌트  m 효과  q 결과/종료 "
    else:
        keys = " Enter 답 입력  ←→ 이전·다음  h 힌트  m 효과  q 결과/종료 "
    _bar(stdscr, curses, h - 1, w, keys)
    stdscr.refresh()


def _draw_mcq_body(stdscr, curses, q, st, row, w):
    for ci, choice in enumerate(st["choices"]):
        marker = "▶" if ci == st["sel"] else " "
        attr = curses.A_NORMAL
        if st["answered"]:
            if ci == st["answer"]:
                marker = "✓"
                attr = curses.color_pair(1) | curses.A_BOLD
            elif ci == st["sel"]:
                marker = "✗"
                attr = curses.color_pair(2) | curses.A_BOLD
        elif ci == st["sel"]:
            attr = curses.A_REVERSE
        _put(stdscr, curses, row, 2, f"{marker} {ci + 1}. {choice}", w - 3, attr)
        row += 1
    return row


def _draw_text_body(stdscr, curses, q, st, row, w):
    shown = st["text"] if st["text"] else "(미입력 — Enter로 입력)"
    for line in _wrap("내 답: " + shown, w - 2):
        _put(stdscr, curses, row, 1, line, w - 2)
        row += 1
    if is_locked(st):
        _put(stdscr, curses, row, 1, "제출 완료 — 수정할 수 없습니다.", w - 2,
             curses.A_DIM)
        row += 1
    if st["answered"]:
        row += 1
        if q["type"] == "short":
            if st["correct"]:
                _put(stdscr, curses, row, 1, "○ 정답입니다!", w - 2,
                     curses.color_pair(1) | curses.A_BOLD)
            else:
                _put(stdscr, curses, row, 1,
                     f"× 오답 (예시 정답: {q['accept'][0]})", w - 2,
                     curses.color_pair(2) | curses.A_BOLD)
        else:  # essay 자기채점 결과 + 모범답안
            label = {True: "○ 맞음(자기채점)", False: "× 틀림(자기채점)",
                     None: "~ 건너뜀"}[st["correct"]]
            pair = {True: 1, False: 2, None: 3}[st["correct"]]
            _put(stdscr, curses, row, 1, label, w - 2,
                 curses.color_pair(pair) | curses.A_BOLD)
            row += 2
            _put(stdscr, curses, row, 1, "[모범답안]", w - 2, curses.A_BOLD)
            row += 1
            for line in _wrap(q["reference"], w - 3):
                _put(stdscr, curses, row, 2, line, w - 3)
                row += 1
        row += 1
    return row


def _register_answer(stdscr, curses, st, session):
    """채점 결과를 집계하고(첫 1회만) 피드백 이벤트를 재생."""
    st["answered"] = True
    if record_streak(session, st["correct"], st.get("counted", False)):
        st["counted"] = True
        _answer_event(stdscr, curses, st["correct"], session["streak"], session)


def _question_screen(stdscr, curses, bank, questions, states, cur, session):
    """한 문항 화면의 키 루프. 이동/종료 시 'prev'/'next'/'quit' 반환."""
    q, st = questions[cur], states[cur]
    while True:
        _draw_question_screen(stdscr, curses, bank, questions, states, cur,
                              session)
        kind, key = _read_key(stdscr, curses)
        if kind != "key":
            continue  # Esc·Option/Shift 조합은 무시(오작동 방지)
        if key == curses.KEY_LEFT:
            return "prev"
        if key == curses.KEY_RIGHT:
            return "next"
        if key in (ord("q"), ord("Q")):
            return "quit"
        if key in (ord("h"), ord("H")):
            st["hint"] = not st["hint"]
            continue
        if key in (ord("m"), ord("M")):
            session["fx"] = not session["fx"]
            continue
        if is_locked(st):
            # 제출 확정된 문항: 읽기 전용. Enter는 다음 문항(마지막이면 결과)으로.
            if key in (curses.KEY_ENTER, 10, 13):
                return "advance"
            continue
        if q["type"] == "mcq":
            n = len(st["choices"])
            if key in (curses.KEY_UP, ord("k")):
                st["sel"] = (st["sel"] - 1) % n
            elif key in (curses.KEY_DOWN, ord("j")):
                st["sel"] = (st["sel"] + 1) % n
            elif key in (curses.KEY_ENTER, 10, 13):
                st["correct"] = grade_mcq(st["sel"], st["answer"])
                _register_answer(stdscr, curses, st, session)
        else:
            if key in (curses.KEY_ENTER, 10, 13):
                text, submitted = _input_overlay(stdscr, curses, q, st["text"])
                st["text"] = text          # 제출하지 않아도 초안은 보존
                if submitted:
                    if q["type"] == "short":
                        st["correct"] = grade_short(text, q["accept"])
                    else:
                        st["correct"] = _essay_selfcheck_curses(
                            stdscr, curses, q, text)
                    _register_answer(stdscr, curses, st, session)


def _input_overlay(stdscr, curses, q, initial):
    """단답/서술형 입력 오버레이.

    반환: (텍스트, 제출여부). Esc는 '닫기'이며 작성 내용은 초안으로 보존된다.
    서술형(essay)은 여러 줄 입력을 지원한다(Shift/Option+Enter로 개행).
    """
    curses.curs_set(1)
    multiline = q["type"] == "essay"
    buf = list(initial)
    pos = len(buf)
    top = 0  # 여러 줄 세로 스크롤 시작 줄
    try:
        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            _bar(stdscr, curses, 0, w, " 답 입력 ")
            row = 2
            for line in _wrap(q["q"], w - 2):
                _put(stdscr, curses, row, 1, line, w - 2)
                row += 1
            row += 1

            text = "".join(buf)
            lines = text.split("\n")
            cur_line = text.count("\n", 0, pos)
            cur_col = pos - line_start(buf, pos)
            avail = max(1, h - row - 2)          # 입력 영역 높이
            if cur_line < top:
                top = cur_line
            elif cur_line >= top + avail:
                top = cur_line - avail + 1

            cy, cx = row, 3
            for i in range(top, min(len(lines), top + avail)):
                y = row + (i - top)
                prefix = "> " if i == 0 else "  "
                _put(stdscr, curses, y, 1, prefix, w - 2)
                _put(stdscr, curses, y, 3, lines[i], w - 4)
                if i == cur_line:
                    cy = y
                    cx = min(3 + _cwidth(lines[i][:cur_col]), w - 2)

            if multiline:
                keys = (" Enter 제출  Shift/Option+Enter 개행  ↑↓ 줄  "
                        "←→ 커서(Option: 단어)  Esc 닫기(임시저장) ")
            else:
                keys = (" ←→ 커서(Option: 단어)  Home/End  ⌫/Del 편집  "
                        "Enter 제출  Esc 닫기(임시저장) ")
            _bar(stdscr, curses, h - 1, w, keys)
            try:
                stdscr.move(cy, cx)
            except curses.error:
                pass
            stdscr.refresh()

            kind, ch = _read_key(stdscr, curses, wide=True)
            if kind == "esc":
                return "".join(buf), False          # 닫기(초안 보존)
            if kind in ("alt", "ctrl"):
                # Option/Alt+←→, Meta-b/f, Ctrl+←→ 모두 단어 단위 이동
                if ch in (curses.KEY_LEFT, "b", "B"):
                    pos = word_left(buf, pos)
                elif ch in (curses.KEY_RIGHT, "f", "F"):
                    pos = word_right(buf, pos)
                elif (kind == "alt" and multiline
                        and ch in ("\r", "\n", curses.KEY_ENTER)):
                    buf.insert(pos, "\n")           # Shift/Option+Enter = 개행
                    pos += 1
                continue
            if ch in ("\n", "\r", curses.KEY_ENTER):
                return "".join(buf), True           # 제출
            if ch == curses.KEY_LEFT:
                pos = max(0, pos - 1)
            elif ch == curses.KEY_RIGHT:
                pos = min(len(buf), pos + 1)
            elif ch == curses.KEY_UP:
                if multiline:
                    pos = move_line(buf, pos, -1)
            elif ch == curses.KEY_DOWN:
                if multiline:
                    pos = move_line(buf, pos, 1)
            elif ch in (curses.KEY_HOME, 1):  # Home / Ctrl-A
                pos = line_start(buf, pos) if multiline else 0
            elif ch in (curses.KEY_END, 5):   # End / Ctrl-E
                pos = line_end(buf, pos) if multiline else len(buf)
            elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b", 127, 8):
                if pos > 0:
                    del buf[pos - 1]
                    pos -= 1
            elif ch == curses.KEY_DC:  # Delete
                if pos < len(buf):
                    del buf[pos]
            elif isinstance(ch, str) and ch.isprintable():
                buf.insert(pos, ch)
                pos += 1
    finally:
        curses.curs_set(0)


def _essay_selfcheck_curses(stdscr, curses, q, buf):
    """서술형: 내 답 + 모범답안을 보여주고 자기채점. True/False/None 반환."""
    h, w = stdscr.getmaxyx()
    stdscr.clear()
    _bar(stdscr, curses, 0, w, " 서술형 자기채점 ")
    row = 2
    _put(stdscr, curses, row, 1, "[내 답]", w - 2, curses.A_BOLD)
    row += 1
    for line in _wrap(buf or "(입력 없음)", w - 3):
        _put(stdscr, curses, row, 2, line, w - 3)
        row += 1
    row += 1
    _put(stdscr, curses, row, 1, "[모범답안]", w - 2, curses.A_BOLD)
    row += 1
    for line in _wrap(q["reference"], w - 3):
        _put(stdscr, curses, row, 2, line, w - 3)
        row += 1
    if q.get("keywords"):
        row += 1
        _put(stdscr, curses, row, 1, "핵심 키워드: " + ", ".join(q["keywords"]), w - 2)
    _bar(stdscr, curses, h - 1, w, " 스스로 맞았습니까?  y=예  n=아니오  s=건너뜀 ")
    stdscr.refresh()
    while True:
        kind, key = _read_key(stdscr, curses)
        if kind != "key":
            continue
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N")):
            return False
        if key in (ord("s"), ord("S")):
            return None


def _summary_curses(stdscr, curses, bank, results, session=None):
    s = summarize(results)
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    _bar(stdscr, curses, 0, w, " 시험 결과 ")
    row = 2
    grade_line = (f"등급 {s['grade']} · 정답률 {s['score'] * 100:.0f}%"
                  if s["auto_total"] else "등급 - · 정답률 -")
    _put(stdscr, curses, row, 1, grade_line, w - 2,
         (curses.color_pair(1) if s["passed"] else curses.color_pair(2))
         | curses.A_BOLD)
    row += 2
    lines = [
        f"자동채점: {s['auto_correct']}/{s['auto_total']} "
        f"(정답 {s['auto_correct']} · 오답 {s['auto_wrong']})",
        f"서술형 자기채점: {s['essay_correct']}/{s['essay_total']}",
    ]
    if session and session.get("best", 0) >= 2:
        lines.append(f"최고 연속 정답: 🔥 {session['best']}")
    lines.append("")
    if s["passed"]:
        lines.append("통과했습니다. 다음 챕터로 넘어가도 좋습니다!")
    else:
        chapter = bank.get("chapter", "")
        lines.append(f"통과 기준({PASS_THRESHOLD * 100:.0f}%) 미달 — 재학습 권장")
        if chapter:
            lines.append(f"  → {chapter}")
    wrong = [r["q"] for r in results if r["correct"] is False]
    if wrong:
        lines.append("")
        lines.append("복습이 필요한 문항:")
        lines.extend(f"  - {q['q']}" for q in wrong)
    cutoff = h - len(POST_EXAM_MENU) - 3  # 메뉴+구분+푸터 자리 확보
    for line in lines:
        if row >= cutoff:
            break
        for wrapped in _wrap(line, w - 2):
            if row >= cutoff:
                break
            _put(stdscr, curses, row, 1, wrapped, w - 2)
            row += 1

    # 결과 메뉴(다른 챕터 / 처음부터 / 종료)
    menu_row = row + 1
    sel = 0
    while True:
        for i, (lbl, _) in enumerate(POST_EXAM_MENU):
            marker = "▶" if i == sel else " "
            attr = curses.A_BOLD | curses.A_REVERSE if i == sel else curses.A_NORMAL
            _put(stdscr, curses, min(menu_row + i, h - 2), 1,
                 f"{marker} {lbl}", w - 2, attr)
        _bar(stdscr, curses, h - 1, w, " ↑↓ 이동  Enter 선택  q 종료 ")
        stdscr.refresh()
        kind, key = _read_key(stdscr, curses)
        if kind != "key":
            continue
        if key in (curses.KEY_UP, ord("k")):
            sel = (sel - 1) % len(POST_EXAM_MENU)
        elif key in (curses.KEY_DOWN, ord("j")):
            sel = (sel + 1) % len(POST_EXAM_MENU)
        elif key in (ord("q"), ord("Q")):
            return "quit"
        elif key in (curses.KEY_ENTER, 10, 13):
            return POST_EXAM_MENU[sel][1]


# --------------------------------------------------------------------------- #
# 엔트리포인트
# --------------------------------------------------------------------------- #
def build_question_set(paths, dbms, shuffle):
    """여러 문제은행을 합쳐 (표시용 bank, 문항 리스트)를 만든다."""
    banks = [load_bank(p) for p in paths]
    if len(banks) == 1:
        bank = banks[0]
        questions = filter_by_dbms(bank["questions"], dbms)
    else:
        questions = []
        for b in banks:
            questions.extend(filter_by_dbms(b["questions"], dbms))
        titles = ", ".join(b.get("title", "?") for b in banks)
        bank = {"title": f"통합 시험 ({titles})", "questions": questions,
                "chapter": None}
    if shuffle:
        random.shuffle(questions)
    return bank, questions


def use_curses():
    """curses 풀스크린을 쓸 수 있는 환경인지 판단."""
    if os.environ.get("EXAM_NO_CURSES"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


def main(argv=None):
    parser = argparse.ArgumentParser(description="챕터별 학습 점검 퀴즈/시험")
    parser.add_argument("target", nargs="?",
                        help="문제은행 JSON 경로 또는 티어 이름(예: 01-beginner)")
    parser.add_argument("--dbms", choices=VALID_DBMS,
                        help="지정 DBMS(+neutral) 문항만 출제")
    parser.add_argument("--shuffle", action="store_true", help="문항 순서 섞기")
    parser.add_argument("--no-effects", action="store_true",
                        help="정답/오답 시 화면 플래시·비프음 없이 시작(TUI 중 m으로 토글)")
    parser.add_argument("--keydebug", action="store_true",
                        help="터미널이 보내는 키 코드를 확인하는 진단 화면")
    args = parser.parse_args(argv)

    locale.setlocale(locale.LC_ALL, "")

    if args.keydebug:
        if not use_curses():
            raise SystemExit("--keydebug는 실제 터미널에서 실행해야 합니다.")
        try:
            run_keydebug()
        except KeyboardInterrupt:
            pass
        return 0

    fx = not args.no_effects

    # 첫 진입: 대상 인자를 주면 그걸로, 없으면 대화형 선택.
    initial = resolve_targets(args.target)
    if initial is not None:
        pending = {"kind": "run", "dbms": args.dbms,
                   "tier": _common_tier(initial), "paths": initial}
    else:
        pending = {"kind": "select_full", "dbms": args.dbms}

    try:
        while True:
            dbms, tier, paths = _resolve_pending(pending, args)
            bank, questions = build_question_set(paths, dbms, args.shuffle)
            if not questions:
                raise SystemExit("출제할 문항이 없습니다(필터 조건 확인).")

            session = {"streak": 0, "best": 0, "fx": fx}
            if use_curses():
                results, action = run_curses(bank, questions, session)
            else:
                results, action = run_line(bank, questions, session)
            save_result(bank, results, session, dbms)
            fx = session["fx"]  # 음소거 선호는 챕터를 넘겨도 유지

            if action == "quit":
                break
            if action == "next_chapter" and tier is not None:
                pending = {"kind": "select_chapter", "dbms": dbms, "tier": tier}
            else:  # restart 또는 티어 불명 → 처음부터
                pending = {"kind": "select_full", "dbms": args.dbms}
    except KeyboardInterrupt:
        print("\n시험을 중단했습니다.")
        return 130
    except ValueError as e:
        raise SystemExit(str(e))
    return 0


def _resolve_pending(pending, args):
    """다음 시험의 (dbms, tier, paths)를 확정한다."""
    kind = pending["kind"]
    if kind == "run":
        return pending["dbms"], pending["tier"], pending["paths"]
    if kind == "select_chapter":
        select = _select_curses if use_curses() else _select_line
        return select(None, start="chapter", dbms=pending["dbms"],
                      tier=pending["tier"])
    # select_full
    select = _select_curses if use_curses() else _select_line
    return select(pending["dbms"], start="dbms")


def _pick_curses(stdscr, curses, prompt, labels):
    """세로 목록에서 하나를 고르는 범용 선택기. 선택 인덱스 반환, q는 종료."""
    curses.curs_set(0)
    sel = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        _bar(stdscr, curses, 0, w, f" {prompt} ")
        for i, label in enumerate(labels):
            marker = "▶" if i == sel else " "
            attr = curses.A_BOLD if i == sel else curses.A_NORMAL
            _put(stdscr, curses, 2 + i, 1, f"{marker} {label}", w - 2, attr)
        _bar(stdscr, curses, h - 1, w, " ↑↓ 이동  Enter 선택  q 종료 ")
        stdscr.refresh()
        kind, key = _read_key(stdscr, curses)
        if kind != "key":
            continue
        if key in (curses.KEY_UP, ord("k")):
            sel = (sel - 1) % len(labels)
        elif key in (curses.KEY_DOWN, ord("j")):
            sel = (sel + 1) % len(labels)
        elif key in (ord("q"), ord("Q")):
            raise KeyboardInterrupt
        elif key in (curses.KEY_ENTER, 10, 13):
            return sel


def _bank_meta(path):
    """문제은행 파일의 (title, chapter)."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d.get("title", Path(path).stem), d.get("chapter")
    except (OSError, ValueError):
        return Path(path).stem, None


def _chapter_labels(banks, records):
    """챕터 선택 라벨에 지난 최고 기록을 접미한다."""
    labels = []
    for b in banks:
        title, chapter = _bank_meta(b)
        best = best_result_for(chapter, records) if chapter else None
        if best:
            title += f"   [지난 최고 {best['grade']}·{best['score'] * 100:.0f}%]"
        labels.append(title)
    return labels


def _common_tier(paths):
    """모든 경로가 같은 티어 디렉터리에 있으면 그 티어명, 아니면 None."""
    parents = {Path(p).parent.name for p in paths}
    return next(iter(parents)) if len(parents) == 1 else None


def _select_curses(cli_dbms, start="dbms", dbms=None, tier=None):
    """curses 대화형 선택. (dbms, tier, [bank_path]) 반환.

    start="dbms": DBMS→티어→챕터 전체. start="chapter": dbms·tier 재사용, 챕터만.
    """
    import curses
    tiers = discover_tiers()
    if not tiers:
        raise SystemExit(f"문제은행이 없습니다: {EXAMS_DIR}")
    records = read_results()

    def _driver(stdscr):
        d, t = dbms, tier
        if start == "dbms":
            if cli_dbms:
                d = cli_dbms
            else:
                di = _pick_curses(stdscr, curses, "DBMS 선택",
                                  [lbl for lbl, _ in DBMS_CHOICES])
                d = DBMS_CHOICES[di][1]
            t = tiers[0] if len(tiers) == 1 else \
                tiers[_pick_curses(stdscr, curses, "티어 선택",
                                   [tier_label(x) for x in tiers])]
        banks = discover_banks_in(t)
        bi = _pick_curses(stdscr, curses, f"챕터 선택 — {tier_label(t)}",
                          _chapter_labels(banks, records))
        return d, t, [banks[bi]]

    return curses.wrapper(_driver)


def _pick_line(prompt, labels):
    """라인 모드 범용 선택기. 선택 인덱스 반환, q는 종료."""
    print(f"\n== {prompt} ==")
    for i, label in enumerate(labels, 1):
        print(f"  {i}. {label}")
    while True:
        raw = _prompt(f"번호 선택 (1-{len(labels)}, q=종료): ").strip()
        if raw.lower() == "q":
            raise KeyboardInterrupt
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        print("잘못된 입력입니다.")


def _select_line(cli_dbms, start="dbms", dbms=None, tier=None):
    """라인 모드 대화형 선택. (dbms, tier, [bank_path]) 반환."""
    tiers = discover_tiers()
    if not tiers:
        raise SystemExit(f"문제은행이 없습니다: {EXAMS_DIR}")
    records = read_results()
    d, t = dbms, tier
    if start == "dbms":
        if cli_dbms:
            d = cli_dbms
        else:
            d = DBMS_CHOICES[_pick_line("DBMS 선택",
                                        [lbl for lbl, _ in DBMS_CHOICES])][1]
        t = tiers[0] if len(tiers) == 1 else \
            tiers[_pick_line("티어 선택", [tier_label(x) for x in tiers])]
    banks = discover_banks_in(t)
    bi = _pick_line(f"챕터 선택 — {tier_label(t)}", _chapter_labels(banks, records))
    return d, t, [banks[bi]]


if __name__ == "__main__":
    sys.exit(main())
