#!/usr/bin/env python3
"""`./exam`과 `./shoot`을 한 자리에서 고르는 최상위 메뉴.

두 러너를 감싸기만 한다 — 고르면 curses를 내리고 기존 `main()`에 그대로 넘기고,
끝나면 이 메뉴로 돌아온다. 기존 진입점은 그대로 살아 있다.

하나의 curses 세션이 전체를 감쌀 수는 없다. `shoot`은 장애 주입 로그를 평문으로
찍고 `c` 키로 진짜 클라이언트를 띄우므로 curses 밖이어야 한다. 그래서 메뉴만
curses를 열고 닫는다.

외부 의존성 없음(Python3 표준 라이브러리만).

사용법:
    ./guide        메뉴에서 골라 실행
"""
import argparse
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exam  # noqa: E402
import shooting  # noqa: E402
from tui import cwidth, pick  # noqa: E402

# key   프로그램 안에서 쓰는 짧은 이름
# title 메뉴에 보이는 이름
# scale "216문항"처럼 규모를 한마디로 (개인 기록은 읽지 않는다 — 얇게 유지)
# run   고르면 부를 것. 인자 없이 대화형으로 시작한다
Mode = namedtuple("Mode", "key title scale run")


def exam_scale():
    """문제은행 규모 한마디.

    깨진 은행은 건너뛴다 — 하나가 깨졌다고 메뉴가 비면 고치러 갈 방법도 사라진다.
    """
    total = 0
    for path in exam.discover_banks():
        try:
            total += len(exam.load_bank(path)["questions"])
        except (ValueError, OSError):
            continue
    return f"{total}문항"


def shoot_scale():
    """스테이지 규모 한마디. 정의를 읽지 않고 파일 수만 센다."""
    return f"{len(shooting.discover_stages())}스테이지"


MODES = (
    Mode("exam", "학습 점검 (퀴즈/시험)", exam_scale, lambda: exam.main([])),
    Mode("shoot", "장애 대응 (실전 훈련)", shoot_scale, lambda: shooting.main([])),
)


def menu_labels():
    """메뉴에 뿌릴 줄 목록. 제목을 왼쪽에 맞춰 규모가 세로로 정렬되게 한다.

    `str.ljust`는 글자 수로 맞추는데 한글은 화면 표시 폭이 2칸이라, 제목
    길이가 우연히 같지 않은 이상 규모 열이 어긋난다. 표시 폭 기준으로
    맞춰야 한다.
    """
    width = max(cwidth(m.title) for m in MODES)
    return [f"{m.title}{' ' * (width - cwidth(m.title))}   {m.scale()}"
            for m in MODES]


def run_mode(mode):
    """모드를 돌리고 **반드시** 메뉴로 돌아온다.

    두 `main()`은 끝나는 방식이 다르다. `exam.main`은 SystemExit을 여러 곳에서
    올리고(대상 없음·출제할 문항 없음·문제은행 없음 …), `shooting.main`은
    KeyboardInterrupt를 스스로 잡지 않는다 — 지금까지는 각 모듈의 `__main__`
    블록이 마지막 방어선이었고, 런처가 부르는 순간 그 방어선이 사라진다.
    잡지 않으면 모드 하나가 끝나는 것이 런처를 통째로 죽인다.

    **그 둘만 잡는다.** 예상 못 한 예외까지 삼키면 트레이스백이 사라져 버그를
    고칠 수 없게 된다.
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
    except KeyboardInterrupt:
        print("\n중단했습니다.")


def pause_after_mode():
    """모드가 남긴 평문 출력을 메뉴가 곧바로 지우지 못하게 한 번 멈춘다.

    tty에서만 멈춘다. `choose_menu`가 tty면 다음 프레임에 `curses.wrapper`→
    `tui.pick()`을 열고 `stdscr.erase()`부터 하므로, 방금 `run_mode`가 평문으로
    찍은 것(예: exam이 SystemExit으로 올린 "출제할 문항이 없습니다" 같은 사유,
    shoot이 curses를 내린 뒤 찍는 등급표·후일담·`./exam` 제안)이 한 프레임도
    못 읽히고 사라진다.

    비-tty(파이프)에서는 폴백 메뉴도 평문이라 지워질 것이 없고, 여기서
    `input()`을 부르면 다음 입력 줄을 삼켜 파이프로 돌리는 실행(테스트 포함)이
    깨지므로 멈추지 않는다.

    `EOFError`·`KeyboardInterrupt`도 삼킨다 — 여기서 새면 `run_mode`가 애써
    격리해 둔 것이 무의미해진다.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    try:
        input("\n계속하려면 Enter를 누르세요...")
    except (EOFError, KeyboardInterrupt):
        pass


def choose_line(labels, prompt=input):
    """평문 폴백 → 고른 인덱스(취소면 None).

    `pick()`은 curses를 요구하므로 파이프로 돌리면 쓸 수 없다. 같은 모양이
    `exam._pick_line`과 `shooting._choose_stage_line`에도 있지만, 셋을 합치려면
    `CLAUDE.md`가 손대지 말라고 못박은 `exam.py`를 건드려야 해서 여기 따로 둔다.
    """
    print("\n무엇을 할까요\n")
    for i, label in enumerate(labels, 1):
        print(f"  {i}) {label}")
    print()
    while True:
        try:
            raw = prompt("번호 (q=종료): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw in ("q", "Q"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        print("잘못된 입력입니다.")


def choose_menu(labels):
    """최상위 메뉴 → 고른 인덱스(종료면 None). tty가 아니면 평문으로 묻는다."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return choose_line(labels)
    import curses

    def _driver(stdscr):
        curses.curs_set(0)
        return pick(stdscr, curses, "무엇을 할까요", labels,
                    footer=" ↑↓ 또는 숫자 선택   Enter 시작   Esc/q 종료 ")

    return curses.wrapper(_driver)


def main(argv=None):
    """메뉴 → 모드 → 메뉴. 프로그램을 끝내는 곳은 메뉴 하나뿐이다.

    모드 안의 '종료'는 그 모드만 끝낸다 — 여러 모드를 오갈 수 있어야 하므로
    끝내는 자리를 한 곳으로 모은다. 같은 이유로 모드의 종료 코드는 전파하지
    않는다: 여러 번 돌 수 있어 대표할 코드가 없다.
    """
    argparse.ArgumentParser(
        prog="guide",
        description="DBA 학습 가이드 — 학습 점검과 장애 대응을 한 자리에서"
    ).parse_args(argv)

    while True:
        idx = choose_menu(menu_labels())
        if idx is None:
            return 0
        run_mode(MODES[idx])
        pause_after_mode()


if __name__ == "__main__":
    sys.exit(main())
