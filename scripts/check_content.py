#!/usr/bin/env python3
"""본문과 다른 파일 사이의 정합성을 기계적으로 판정한다.

`docs/release-policy.md`의 「릴리스 전 점검」 중 사람의 판단이 필요 없는
항목만 다룬다. 무엇이 *새* 명령어인지, 어떤 용어가 용어집에 들어가야 하는지
같은 것은 본문의 의미를 읽어야 하므로 여기서 다루지 않는다 — 그 항목들은
점검 목록에 사람 몫으로 남아 있다.

검사 넷:
  links      상대 링크가 실재하는 파일을 가리키는가
  orphans    티어·부록의 모든 문서가 README에서 링크되는가
  structure  챕터가 정해진 네 절을 그 순서로 갖는가
  banks      챕터에 대응하는 문제은행이 있고, 그 은행의 `chapter` 필드가
             챕터 경로와 일치하는가

사용:
    python3 scripts/check_content.py          # 위반이 있으면 종료 코드 1
    python3 scripts/check_content.py --root DIR
    python3 scripts/check_content.py --dbms mysql   # 단일 벤더 뷰의 structure만
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import filter_dbms

REPO_ROOT = Path(__file__).resolve().parent.parent

TIERS = ("01-beginner", "02-intermediate", "03-advanced")
LINKED_DIRS = TIERS + ("appendix",)

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^\s*```")
# 인라인 코드. 펜스와 같은 이유로 링크 검사에서 빼낸다 — 표 셀 안에서는
# 펜스를 쓸 수 없어서, 마크다운 문법을 표로 설명하는 문서는 `[글자](url)`
# 같은 예시를 백틱에 담는다.
INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
HEADING_RE = re.compile(r"^##\s+(?:\d+\.\s*)?(.*?)\s*$")

# 챕터가 갖춰야 하는 절과 그 순서. 각 항목은 허용되는 이름들이다.
#
# 실제 규약은 CLAUDE.md의 문자열보다 느슨하다. 번호 접두사(`## 1. `)를 붙인
# 챕터와 붙이지 않은 챕터가 섞여 있고, 제목 뒤에 부제가 붙기도 하며
# (`## 실습 예제 — PostgreSQL PITR`), 명령어가 아니라 개념을 다루는 챕터는
# `주요 개념/문법`을 쓴다. 여기서 강제하는 것은 **네 절이 이 순서로
# 등장한다**는 것뿐이고, 사이에 다른 절이 끼는 것은 허용한다.
SECTION_ORDER = (
    ("핵심 개념 설명",),
    ("주요 명령어/문법", "주요 개념/문법"),
    ("실습 예제",),
    ("체크리스트",),
)

# 네 절 규약에서 빠지는 문서. 개요는 다른 구조를 쓰고(선수 지식/역량/졸업
# 기준), 치트시트는 한 장짜리 표다.
def _is_chapter(path):
    return (path.suffix == ".md"
            and path.name != "00-overview.md"
            and not path.name.endswith("-commands-cheatsheet.md"))


def markdown_files(root):
    """검사 대상 마크다운. 숨은 디렉터리(.git 등)는 들어가지 않는다."""
    out = []
    for p in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return out


def chapters(root):
    out = []
    for tier in TIERS:
        for p in sorted((root / tier).glob("*.md")):
            if _is_chapter(p):
                out.append(p)
    return out


def link_targets(text):
    """본문에서 링크 대상을 뽑는다. 코드펜스와 인라인 코드 안은 예시이므로 건너뛴다."""
    out = []
    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # 지우지 않고 공백으로 바꾼다 — 붙여 버리면 양옆 글자가 새 링크
        # 모양을 만들 수 있다.
        out += LINK_RE.findall(INLINE_CODE_RE.sub(" ", line))
    return out


def check_links(root):
    """상대 링크가 실재하는 파일·디렉터리를 가리키는가."""
    problems = []
    for f in markdown_files(root):
        for target in link_targets(f.read_text(encoding="utf-8")):
            parsed = urlparse(target)
            # 외부 URL(scheme 있음)과 문서 내 앵커(#...)는 대상이 아니다.
            if parsed.scheme or not parsed.path:
                continue
            dest = f.parent / unquote(parsed.path)
            if not dest.exists():
                problems.append(
                    f"{f.relative_to(root)}: 링크가 가리키는 곳이 없다 → {target}")
    return problems


def check_orphans(root):
    """티어·부록의 문서가 README에서 링크되는가.

    README는 목차이자 학습 로드맵이다. 링크되지 않은 챕터는 사실상 존재하지
    않는 것과 같다.
    """
    readme = root / "README.md"
    if not readme.exists():
        return [f"{readme.name}: 없다"]
    linked = set()
    for target in link_targets(readme.read_text(encoding="utf-8")):
        parsed = urlparse(target)
        if parsed.scheme or not parsed.path:
            continue
        linked.add((readme.parent / unquote(parsed.path)).resolve())

    problems = []
    for d in LINKED_DIRS:
        for p in sorted((root / d).rglob("*.md")):
            if p.resolve() not in linked:
                problems.append(
                    f"{p.relative_to(root)}: README에서 링크되지 않는다")
    return problems


def check_structure(root, dbms=None):
    """챕터가 네 절을 정해진 순서로 갖는가.

    `dbms`를 주면 그 벤더로 필터한 뒤 검사한다 — 단일 벤더 브랜치에서
    실제로 보이는 모습이다. 절 하나가 통째로 다른 벤더의 마커 안에 있으면
    그 브랜치에서만 사라지므로, `main`에서만 보면 드러나지 않는다(#90).

    필터는 `filter_dbms.filter_lines`를 그대로 쓴다 —
    `scripts/generate-branch.sh`가 벤더 브랜치를 만들 때, `reading.py`가
    화면에 뿌릴 때 부르는 바로 그 함수다. 여기서 따로 구현하면 "검사는
    통과했는데 브랜치는 깨진" 상태가 생길 수 있다.
    """
    problems = []
    for p in chapters(root):
        lines = p.read_text(encoding="utf-8").splitlines()
        if dbms is not None:
            try:
                lines = filter_dbms.filter_lines(lines, dbms)
            except ValueError as e:
                problems.append(f"{p.relative_to(root)}: {e}")
                continue
        heads = [m.group(1) for m in
                 (HEADING_RE.match(line) for line in lines) if m]
        at = -1
        for names in SECTION_ORDER:
            for i, head in enumerate(heads):
                if i > at and any(head.startswith(n) for n in names):
                    at = i
                    break
            else:
                problems.append(
                    f"{p.relative_to(root)}: `## {names[0]}` 절이 없거나 "
                    f"순서가 어긋난다")
    return problems


def check_banks(root):
    """챕터에 대응하는 문제은행이 있고, 그 은행의 `chapter` 필드가 맞는가.

    `reading.py`의 `chapter_labels`는 챕터의 저장소 상대경로를
    `exam.best_result_for`에 그대로 넘긴다 — 은행 JSON의 `chapter` 필드가
    그 경로와 같다는 전제 위에서다(`docs/superpowers/specs/
    2026-08-12-reading-exam-handoff-design.md` 2절, 실측 23/23). 이 필드가
    챕터 경로와 어긋나면 읽기 목록에는 시험 기록이 안 보이는데 `exam` 목록
    에는 보이는 식으로, 같은 정보가 화면마다 다르게 보이게 된다.
    `exam.validate_bank`는 `chapter` 필드 자체를 보지 않고, 이 검사도 지금껏
    은행 *파일*이 있는지만 봤다 — 챕터를 옮기면서 은행의 `chapter`를 안
    고치는 실수는 둘 다 잡지 못한다.
    """
    problems = []
    for p in chapters(root):
        rel = p.relative_to(root)
        bank = root / "exams" / p.parent.name / (p.stem + ".json")
        if not bank.exists():
            problems.append(
                f"{rel}: 문제은행이 없다 → {bank.relative_to(root)}")
            continue
        bank_rel = bank.relative_to(root)
        try:
            data = json.loads(bank.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            problems.append(f"{bank_rel}: 읽을 수 없다 → {e}")
            continue
        expect = f"{p.parent.name}/{p.stem}.md"
        got = data.get("chapter") if isinstance(data, dict) else None
        if got != expect:
            problems.append(
                f"{bank_rel}: chapter 필드가 '{expect}'가 아니라 {got!r}이다")
    return problems


CHECKS = (
    ("links", check_links),
    ("orphans", check_orphans),
    ("structure", check_structure),
    ("banks", check_banks),
)


def check_all(root):
    """(검사 이름, 위반 목록) 쌍의 목록. 순서는 CHECKS 순서다."""
    return [(name, fn(Path(root))) for name, fn in CHECKS]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(REPO_ROOT),
                    help="검사할 저장소 경로 (기본: 이 스크립트의 저장소)")
    ap.add_argument("--dbms", choices=("postgresql", "mysql", "oracle"),
                    help="단일 벤더 뷰로 필터한 뒤 structure만 검사한다")
    args = ap.parse_args(argv)

    if args.dbms:
        checks = [(f"structure:{args.dbms}",
                   check_structure(Path(args.root), args.dbms))]
    else:
        checks = check_all(args.root)

    total = 0
    for name, problems in checks:
        if problems:
            total += len(problems)
            print(f"\n[{name}] {len(problems)}건")
            for line in problems:
                print(f"  {line}")
        else:
            print(f"[{name}] ok")

    if total:
        print(f"\n{total}건. 위 항목을 고치세요.")
        return 1
    print("\n모두 통과.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
