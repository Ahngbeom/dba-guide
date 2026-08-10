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
import reading  # noqa: E402
from tui import cwidth, pause_after_output, pick, pick_line  # noqa: E402

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
    Mode("read", "챕터 읽기", reading.read_scale, lambda: reading.main([])),
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

    실제 tty 가드·멈춤 로직은 `tui.pause_after_output()`에 있다. `reading`도
    챕터→시험 핸드오프 뒤 다음 챕터 선택 화면으로 돌아가며 같은 함정(다음
    curses 프레임의 `erase()`가 방금 찍힌 평문을 한 프레임도 못 읽고 지움)을
    밟아서, `guide`만의 것으로 남겨 둘 수 없었다 — `pick_line`이 같은 이유로
    `tui`에 모인 전례를 따른다. 이 얇은 래퍼를 남기는 이유는 `main()`이
    `run_mode` 바로 뒤에서 부르는 배선을 이 모듈 안에서 그대로 읽을 수 있게
    하기 위해서다.
    """
    pause_after_output()


def choose_menu(labels):
    """최상위 메뉴 → 고른 인덱스(종료면 None). tty가 아니면 평문으로 묻는다."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return pick_line("무엇을 할까요", labels)
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
