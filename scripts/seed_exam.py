#!/usr/bin/env python3
"""챕터 Markdown에서 시험 문제은행 JSON '초안'을 생성한다.

이것은 하이브리드 워크플로의 1단계다: 여기서 나온 초안(스텁)에 사람이
오답지·정답(accept)·모범답안(reference)·해설을 보완해 최종 JSON을 만든다.
모든 자동 생성 문항에는 `"_draft": true`와 TODO 표시가 붙는다.

추출 규칙:
  - `## 체크리스트`의 `- [ ]` 항목      → essay 스텁(역량 자기점검)
  - `## 실습 예제` 코드블록의 `-- 결과:` / `-- ERROR:` 주석 줄
                                          → short 스텁(명령어 채우기)

`<!-- dbms:X -->` 마커 컨텍스트를 읽어 문항에 dbms 필드를 자동 태깅한다.
마커·코드펜스 파싱은 filter_dbms.py의 정규식/스택 규칙을 그대로 재사용한다.

사용법:
    python3 scripts/seed_exam.py <chapter.md> [> exams/<tier>/<name>.json]
"""
import json
import re
import sys
from pathlib import Path

# 마커/코드펜스 파싱 규칙 재사용 (중복 구현 금지)
from filter_dbms import OPEN_RE, CLOSE_RE, FENCE_RE

VALID_DBMS = ("postgresql", "mysql", "oracle")

# 헤딩 표기 흔들림 허용: 번호 有/無, "주요 개념/문법"↔"주요 명령어/문법"
H_CHECKLIST = re.compile(r"^##\s*(?:\d+\.\s*)?체크리스트\s*$")
H_PRACTICE = re.compile(r"^##\s*(?:\d+\.\s*)?실습\s*예제\s*$")
H_ANY_H2 = re.compile(r"^##\s+")
CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*(.+?)\s*$")
RESULT_COMMENT_RE = re.compile(r"--\s*(결과|ERROR)\s*[:：]?\s*(.*)$")


def current_dbms(stack):
    """마커 스택에서 현재 유효한 DBMS 컨텍스트(가장 안쪽)를 반환."""
    for name in reversed(stack):
        if name in VALID_DBMS:
            return name
    return "neutral"


def slugify(text, limit=32):
    text = re.sub(r"[`*_>#|]", "", text)
    text = re.sub(r"[^0-9A-Za-z가-힣]+", "-", text).strip("-").lower()
    return text[:limit] or "item"


def parse_chapter(lines):
    """챕터 라인들을 순회하며 (section, dbms, kind, payload) 이벤트를 만든다.

    filter_dbms.filter_lines와 동일한 스택/펜스 규칙:
      - 마커는 코드펜스 밖에서만 토글된다.
      - 스택은 펜스를 가로질러 유지되므로 펜스 내부 코드에도 dbms 컨텍스트가 적용된다.
    """
    section = None      # None | "checklist" | "practice"
    stack = []
    in_fence = False
    events = []
    for line in lines:
        is_fence_toggle = bool(FENCE_RE.match(line))
        if not in_fence and not is_fence_toggle:
            if OPEN_RE.match(line):
                stack.append(OPEN_RE.match(line).group(1))
                continue
            m = CLOSE_RE.match(line)
            if m:
                if stack and stack[-1] == m.group(1):
                    stack.pop()
                continue
            # 헤딩으로 섹션 전환(코드펜스 밖에서만)
            if H_CHECKLIST.match(line):
                section = "checklist"
                continue
            if H_PRACTICE.match(line):
                section = "practice"
                continue
            if H_ANY_H2.match(line):
                section = None
                continue
        if is_fence_toggle:
            in_fence = not in_fence
            continue
        dbms = current_dbms(stack)
        if section == "checklist" and not in_fence:
            cb = CHECKBOX_RE.match(line)
            if cb:
                events.append(("checklist", dbms, cb.group(1)))
        elif section == "practice" and in_fence:
            rc = RESULT_COMMENT_RE.search(line)
            if rc:
                code = line.split("--", 1)[0].strip()
                events.append(("practice", dbms, (code, rc.group(2).strip())))
    return events


def build_questions(events, slug):
    questions = []
    seen_ids = {}

    def uniq(base):
        n = seen_ids.get(base, 0) + 1
        seen_ids[base] = n
        return base if n == 1 else f"{base}-{n}"

    for kind, dbms, payload in events:
        if kind == "checklist":
            item = payload
            questions.append({
                "id": uniq(f"{slug}-chk-{slugify(item)}"),
                "type": "essay",
                "dbms": dbms,
                "q": f"다음 역량을 점검하세요: {item}",
                "reference": "TODO: 모범답안을 작성하세요.",
                "keywords": [],
                "_draft": True,
            })
        else:  # practice
            code, hint = payload
            if not code:
                continue
            desc = hint or "아래 실습의 해당 단계"
            questions.append({
                "id": uniq(f"{slug}-cmd-{slugify(code)}"),
                "type": "short",
                "dbms": dbms,
                "q": f"다음을 수행하는 명령/구문을 작성하세요: {desc}",
                "accept": [code, "TODO: 허용 정답을 검토·보완하세요."],
                "explain": "",
                "_draft": True,
            })
    return questions


def seed_chapter(md_path):
    path = Path(md_path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    title = "제목 미상"
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break
    slug = path.stem
    events = parse_chapter(lines)
    questions = build_questions(events, slug)
    # chapter 경로는 저장소 루트 기준 상대경로로 (가능하면)
    try:
        repo_root = Path(__file__).resolve().parent.parent
        chapter_rel = str(path.resolve().relative_to(repo_root))
    except ValueError:
        chapter_rel = str(path)
    return {
        "chapter": chapter_rel,
        "title": title,
        "_seed_note": "seed_exam.py로 생성된 초안입니다. 오답지·accept·reference·해설을 "
                      "보완하고 _draft/TODO 표시를 제거하세요.",
        "questions": questions,
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(f"usage: {sys.argv[0]} <chapter.md>", file=sys.stderr)
        return 2
    bank = seed_chapter(argv[0])
    print(json.dumps(bank, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
