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
from tui import pick  # noqa: E402

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
    """메뉴에 뿌릴 줄 목록. 제목을 왼쪽에 맞춰 규모가 세로로 정렬되게 한다."""
    width = max(len(m.title) for m in MODES)
    return [f"{m.title.ljust(width)}   {m.scale()}" for m in MODES]


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
