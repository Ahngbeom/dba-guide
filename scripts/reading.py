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
from tui import page_text, pick, pick_line  # noqa: E402

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
    메뉴로 돌아갈 수 있어야 한다.
    """
    try:
        lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines(True)
    except OSError as e:
        return f"{rel} 을(를) 읽을 수 없습니다: {e}\n"
    if dbms:
        lines = filter_lines(lines, dbms)
    return "".join(lines)
