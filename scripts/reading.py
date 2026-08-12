#!/usr/bin/env python3
"""챕터 본문을 골라 읽는 모드. `./guide` 의 세 번째 항목.

읽기(챕터) → 확인(`./exam`) → 겪기(`./shoot`) 중 첫 축이다. 챕터 목록에서 `x` 를
누르면 그 챕터의 시험으로 이어 준다 — 경로를 손으로 찾게 하면 거기서 끊긴다.
막는 질문이 아니라 목록의 동작인 이유는 `docs/superpowers/specs/` 에 있다.

본문은 `markdown_render` 로 서식을 입힌 뒤 `$PAGER` 에 넘긴다. 뷰어를 curses 로
만들지 않는다는 것이 이 저장소의 규약이고(`CLAUDE.md`), `less` 가 스크롤·검색을
이미 다 한다 — 렌더러는 화면을 그리지 않고 텍스트를 텍스트로 바꿀 뿐이다.

외부 의존성 없음(Python3 표준 라이브러리만).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exam  # noqa: E402
import markdown_render  # noqa: E402
from filter_dbms import filter_lines  # noqa: E402
from tui import (Picked, QuitApp, page_text, pager_supports_color,  # noqa: E402
                 pause_after_output, pick, pick_line, text_width)

REPO_ROOT = Path(__file__).resolve().parent.parent

# 읽을거리가 있는 디렉터리. 개요·치트시트·부록도 읽을거리이므로 전부 내놓는다
# (시험 문제은행이 있는 티어만 세는 `exam.discover_tiers()` 와 다른 목록이다).
TIERS = ("01-beginner", "02-intermediate", "03-advanced", "appendix")

# `exam.DBMS_CHOICES` 와 같은 순서·같은 뜻. (표시 라벨, 필터값) — None 이면 전체.
DBMS_CHOICES = exam.DBMS_CHOICES


def discover_chapters(tier):
    """티어 안의 챕터를 저장소 상대 경로로. 파일명 순."""
    return sorted(f"{tier}/{p.name}" for p in (REPO_ROOT / tier).glob("*.md"))


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


def chapter_count():
    return sum(len(discover_chapters(t)) for t in TIERS)


def read_scale():
    """메뉴에 붙일 규모 한마디."""
    return f"{chapter_count()}챕터"


def chapter_text(rel, dbms=None):
    """챕터 본문. `dbms` 를 주면 그 벤더만 남긴다.

    `filter_dbms.filter_lines` 를 쓴다 — `generate-branch.sh` 가 단일 벤더
    브랜치를 만들 때 쓰는 그 함수다. 여기서 새로 만들면 브랜치로 보는 것과
    읽기 모드로 보는 것이 조용히 갈라진다.

    읽을 수 없는 파일은 예외로 죽지 않고 그 사실을 본문 자리에 적는다 —
    메뉴로 돌아갈 수 있어야 한다. `filter_lines` 는 마커 불균형·마커 미종료·
    닫히지 않은 코드 펜스에서 `ValueError` 를 올린다(잘못된 인코딩이면
    `UnicodeDecodeError` 도 `ValueError` 의 하위 클래스로 여기 걸린다) —
    `OSError` 만 잡으면 이 경로들이 그대로 새어 나간다.
    """
    try:
        lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines(True)
        if dbms:
            lines = filter_lines(lines, dbms)
    except (OSError, ValueError) as e:
        return f"{rel} 을(를) 읽을 수 없습니다: {e}\n"
    return "".join(lines)


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
    # "시험"이라는 낱말을 `actions` 글자에 그대로 붙여 문구를 만든다 — 여러
    # 동작 키를 넣지 않기로 한 결정(YAGNI) 위에서만 성립하는 결합이다.
    # `actions="xy"` 를 넘기면 이 줄이 "xy 시험"이라고 읽는다.
    verb = f"Enter 읽기   {actions} 시험" if actions else "Enter 선택"

    def _driver(stdscr):
        curses.curs_set(0)
        return pick(stdscr, curses, title, labels,
                    footer=f" ↑↓ 또는 숫자 선택   {verb}   Esc/q 뒤로   Q 종료 ",
                    actions=actions)

    return curses.wrapper(_driver)


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


def main(argv=None):
    """DBMS → 티어 → 챕터 → 읽기. 각 화면에서 뒤로 갈 수 있다.

    챕터 목록에서 `x` 를 누르면 그 챕터의 시험으로 넘어간다. 전에는 챕터를
    읽고 나올 때마다 `[Y/n]` 으로 물었는데, 기본값이 '예'라서 습관적으로 누른
    Enter 가 시험을 열었고 `Esc`/`q` 도 먹히지 않았다 — 이슈 #95 가 고친 것과
    같은 부류의 막다른 프롬프트였다.
    """
    argparse.ArgumentParser(
        prog="reading", description="챕터 본문 읽기").parse_args(argv)

    while True:
        d_idx = choose("어느 DBMS 기준으로 볼까요",
                       [label for label, _ in DBMS_CHOICES])
        if d_idx is None:
            return 0
        dbms = DBMS_CHOICES[d_idx][1]

        while True:
            t_idx = choose("어느 티어를 볼까요",
                           [f"{t}   {len(discover_chapters(t))}개"
                            for t in TIERS])
            if t_idx is None:
                break                      # DBMS 선택으로
            tier = TIERS[t_idx]

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


if __name__ == "__main__":
    # `python3 scripts/reading.py`로 직접 돌릴 때는 `guide.main`의 그물이 없다.
    # 그대로 두면 `Q` 한 번에 트레이스백이 뜬다. `shooting.py`의 `__main__`
    # 블록과 같은 모양으로 맞춘다 — 거기는 Ctrl-C도 함께 잡는데 여기는
    # 빠져 있었다.
    try:
        sys.exit(main())
    except QuitApp:
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        sys.exit(130)
