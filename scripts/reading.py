#!/usr/bin/env python3
"""챕터 본문을 골라 읽는 모드. `./guide` 의 세 번째 항목.

읽기(챕터) → 확인(`./exam`) → 겪기(`./shoot`) 중 첫 축이다. 다 읽으면 그 챕터의
시험으로 이어 준다 — 경로를 손으로 찾게 하면 거기서 끊긴다.

본문은 `$PAGER` 에 넘긴다. 뷰어를 curses 로 만들지 않는다는 것이 이 저장소의
규약이고(`CLAUDE.md`), `less` 가 스크롤·검색을 이미 다 한다.

외부 의존성 없음(Python3 표준 라이브러리만).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exam  # noqa: E402
from filter_dbms import filter_lines  # noqa: E402
from tui import page_text, pause_after_output, pick, pick_line  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# 읽을거리가 있는 디렉터리. 개요·치트시트·부록도 읽을거리이므로 전부 내놓는다
# (시험 문제은행이 있는 티어만 세는 `exam.discover_tiers()` 와 다른 목록이다).
TIERS = ("01-beginner", "02-intermediate", "03-advanced", "appendix")

# `exam.DBMS_CHOICES` 와 같은 순서·같은 뜻. (표시 라벨, 필터값) — None 이면 전체.
DBMS_CHOICES = exam.DBMS_CHOICES


def discover_chapters(tier):
    """티어 안의 챕터를 저장소 상대 경로로. 파일명 순."""
    return sorted(f"{tier}/{p.name}" for p in (REPO_ROOT / tier).glob("*.md"))


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


def choose(title, labels):
    """세로 목록에서 하나 고른다 → 인덱스(뒤로/종료면 None).

    tty면 공용 curses 선택기, 아니면 공용 평문 선택기. 둘 다 `tui` 에 있다.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return pick_line(title, labels)
    import curses

    def _driver(stdscr):
        curses.curs_set(0)
        return pick(stdscr, curses, title, labels,
                    footer=" ↑↓ 또는 숫자 선택   Enter 선택   Esc/q 뒤로 ")

    return curses.wrapper(_driver)


def read_chapter(rel, dbms=None):
    """본문을 `$PAGER` 로 넘긴다. curses 밖에서 부른다."""
    page_text(chapter_text(rel, dbms))


def offer_exam(rel, bank, ask=input):
    """읽은 챕터의 시험을 권한다 → 하겠다고 했는가.

    `bank`(챕터→문제은행 매핑)는 호출부가 이미 `exam.exam_bank_for(rel)`로
    구해 둔 것을 받는다 — 여기서 다시 구하면 같은 챕터에 대해 같은 조회를
    두 번(핸드오프에서 또 한 번) 하게 된다.

    은행이 없는 챕터(개요·치트시트·부록)에서는 **묻지 않는다** — '예'를 받아도
    갈 곳이 없다. 비-tty 에서도 묻지 않는다: `input()` 이 다음 입력 줄을 삼켜
    파이프 실행이 깨진다(`./guide` 에서 같은 함정을 밟았다).
    """
    if not bank or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    print(f"\n읽은 챕터: {rel}")
    try:
        answer = ask("이제 확인해 볼까요? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("", "y", "yes")


def main(argv=None):
    """DBMS → 티어 → 챕터 → 읽기 → 시험 제안. 각 화면에서 뒤로 갈 수 있다."""
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
                c_idx = choose(f"{tier} — 어느 챕터를 읽을까요",
                               [Path(c).name for c in chapters])
                if c_idx is None:
                    break                  # 티어 선택으로
                rel = chapters[c_idx]
                bank = exam.exam_bank_for(rel)
                read_chapter(rel, dbms)
                if offer_exam(rel, bank):
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
                pause_after_output()


if __name__ == "__main__":
    sys.exit(main())
