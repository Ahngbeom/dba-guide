#!/usr/bin/env python3
"""DB 장애 대응 게임 러너 — 실제 로컬 MySQL에 장애를 주입하고 복구를 판정한다.

슈팅게임의 월드/스테이지 진행 구조를 빌리되, 플레이 자체는 진짜 트러블슈팅이다.
엔진이 `shooting/lab`의 컨테이너에 장애를 주입하면, 플레이어는 **자기 터미널의
진짜 mysql 클라이언트**로 진단·복구하고, 엔진은 두 가지를 감시해 판정한다.

  1. DB 상태 폴링   — 목표 상태에 도달했는가 (`state` 목표)
  2. mysql.general_log — 어떤 명령으로 도달했는가 (금지 행동 제약)

상태만 보면 "범인만 정확히 KILL"과 "전부 쓸어버리기"가 똑같이 정상 복구다.
실무에선 전혀 다른 이야기이므로, 결과뿐 아니라 방법도 채점한다.

진단 목표(`quiz`)는 상태로 증명할 수 없어 exam.py의 채점기를 그대로 재사용한다.

외부 의존성 없음(Python3 표준 라이브러리만 사용). DB 접근은 `docker exec ... mysql`.

사용법:
    ./shoot                 스테이지를 골라 플레이
    ./shoot --list          스테이지 목록
    ./shoot doctor          사전 점검(docker/포트/클라이언트)
    ./shoot up | down       랩 기동 / 정리
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGES_DIR = REPO_ROOT / "shooting" / "stages"
LAB_DIR = REPO_ROOT / "shooting" / "lab"
COMPOSE_FILE = LAB_DIR / "compose.yaml"
PROGRESS_DIR = REPO_ROOT / ".shooting-progress"   # 비커밋 — .gitignore
PROGRESS_FILE = PROGRESS_DIR / "results.jsonl"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tui import (  # noqa: E402
    bar, clamp_scroll, cwidth, hscroll, hslice, is_backspace, is_enter,
    is_idle, key_char, put, read_key, wrap,
)
from exam import grade_mcq, grade_short, shuffle_choices  # noqa: E402

# 컨테이너 이름은 compose.yaml의 container_name과 맞춰야 한다.
CONTAINERS = {"primary": "dbshoot-primary", "replica": "dbshoot-replica"}

POLL_SECONDS = 2.0        # DB 상태 폴링 주기

# TUI 내장 SQL 콘솔이 쓰는 접속 정보. shooting/lab/seed/03-users.sql 과 맞춰야 한다.
PLAYER_USER = "dba"
PLAYER_PASSWORD = "shoot"
PLAYER_DB = "shop"
CONSOLE_TIMEOUT = 15      # 초. 락 대기로 막힌 질의가 TUI를 얼리지 않게 한다.
CONSOLE_MAX_OUTPUT = 2000  # 질의 1회당 담을 최대 출력 줄 수
CONSOLE_SCROLLBACK = 4000  # 콘솔이 보관하는 최대 줄 수
HSCROLL_STEP = 20          # 결과 가로 스크롤 한 번에 미는 칸 수
OBJECTIVE_TYPES = ("state", "quiz")
EXPECT_OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "contains", "in")

# 플레이어(dba)가 친 질의만 골라 읽고, 읽은 즉시 로그를 비운다.
#
# 비우는 게 핵심이다. mysql.general_log는 CSV 엔진이라 인덱스가 없어 조회할 때마다
# 전체 스캔인데, 그냥 두면 엔진 접속 흔적이 사이클마다 쌓여(접속당 3행) 10분이면
# 수천 행이 되고 스캔이 점점 느려진다. 읽고 비우면 크기가 상수로 고정된다.
#
# 한 접속 안에서 순서가 맞아떨어진다 — Connect/배너/SET은 로그에 남지만,
# 그 뒤 TRUNCATE가 자기 흔적까지 함께 지우고 나간다.
# 대신 매 호출이 "직전 호출 이후의 새 명령"만 돌려주므로 호출부가 누적해야 한다.
#
# 개행/탭은 SQL 단계에서 공백으로 눌러 TSV 파싱이 깨지지 않게 한다.
PLAYER_LOG_SQL = (
    "SELECT REPLACE(REPLACE(REPLACE("
    "CONVERT(argument USING utf8mb4), '\\r', ' '), '\\n', ' '), '\\t', ' ') "
    "FROM mysql.general_log "
    "WHERE user_host LIKE 'dba%' AND command_type = 'Query' "
    # mysql 클라이언트가 접속할 때 자동으로 보내는 배너 질의는 플레이어가 친 게
    # 아니므로 뺀다.
    "AND argument NOT LIKE 'select @@version_comment%' "
    "ORDER BY event_time; "
    "TRUNCATE TABLE mysql.general_log"
)


class LabError(RuntimeError):
    """랩(도커/MySQL) 조작 실패."""


# --------------------------------------------------------------------------- #
# 순수 로직 (UI·도커와 분리 — 테스트 대상)
# --------------------------------------------------------------------------- #
def parse_tsv(stdout):
    """`mysql -N -B` 출력을 행 목록으로. 빈 줄은 버린다."""
    rows = []
    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        rows.append(line.split("\t"))
    return rows


def first_scalar(rows, default=None):
    """첫 행 첫 컬럼. 결과가 없으면 default."""
    if rows and rows[0]:
        return rows[0][0]
    return default


_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def coerce(value):
    """비교를 위해 숫자처럼 보이는 문자열을 숫자로 바꾼다.

    mysql 배치 출력은 전부 문자열이라 이 단계가 없으면 "0" == 0 이 거짓이 된다.
    """
    if value is None or isinstance(value, (int, float)):
        return value
    s = str(value).strip()
    if s == "NULL":
        return None
    if _NUM_RE.match(s):
        return float(s) if "." in s else int(s)
    return s


def evaluate_expect(spec, value):
    """`{"op": ..., "value": ...}` 기대값 판정. 알 수 없는 연산자는 False."""
    spec = spec or {}
    op = spec.get("op", "eq")
    want = spec.get("value")
    got = coerce(value)

    if op == "contains":
        return str(want) in str("" if value is None else value)
    if op == "in":
        return got in [coerce(v) for v in (want or [])]

    want_c = coerce(want)
    if op == "eq":
        return got == want_c
    if op == "ne":
        return got != want_c
    if op in ("lt", "lte", "gt", "gte"):
        if not isinstance(got, (int, float)) or not isinstance(want_c, (int, float)):
            return False
        if op == "lt":
            return got < want_c
        if op == "lte":
            return got <= want_c
        if op == "gt":
            return got > want_c
        return got >= want_c
    return False


def update_hold(state, satisfied, now, hold_seconds):
    """`hold_seconds` 유지 판정 상태 머신 → (새 상태, 충족 완료 여부).

    조건이 한 번이라도 끊기면 타이머를 처음부터 다시 잰다. 이게 없으면
    폴링 사이에 스쳐 지나간 순간적 정상 상태로 스테이지가 클리어돼버린다.
    """
    if not satisfied:
        return {"since": None}, False
    since = state.get("since") if state else None
    if since is None:
        since = now
    return {"since": since}, (now - since) >= (hold_seconds or 0)


def hold_remaining(state, now, hold_seconds):
    """유지까지 남은 초(표시용). 조건 미충족이면 전체 시간."""
    since = (state or {}).get("since")
    if since is None:
        return float(hold_seconds or 0)
    return max(0.0, (hold_seconds or 0) - (now - since))


_KILL_RE = re.compile(
    r"^\s*KILL\s+(?:(?:CONNECTION|QUERY)\s+)?(\d+)\s*;?\s*$", re.IGNORECASE)


def parse_kill_targets(commands):
    """플레이어 명령 목록에서 KILL 대상 스레드 id를 뽑는다.

    `KILL 12` / `KILL CONNECTION 12` / `KILL QUERY 12` 를 모두 인식한다.
    """
    out = []
    for cmd in commands or []:
        m = _KILL_RE.match(cmd or "")
        if m:
            out.append(int(m.group(1)))
    return out


def count_extra_kills(kill_targets, allowed_pids):
    """범인 외 세션을 KILL 한 횟수.

    상태 검증만으로는 "범인만 정확히 죽이기"와 "전부 쓸어버리기"가 구분되지
    않는다. 이 함수가 결과 대신 방법을 본다.
    """
    allowed = {int(p) for p in (allowed_pids or [])}
    return sum(1 for pid in (kill_targets or []) if int(pid) not in allowed)


def detect_violations(constraints, ctx):
    """금지 행동 위반 목록. ctx는 감시 결과 묶음(도커/로그와 분리해 테스트 가능)."""
    found = []
    for c in constraints or []:
        kind = c.get("detect")
        label = c.get("label", c.get("id", "?"))
        if kind == "container_restart":
            if ctx.get("restarted"):
                found.append({"id": c.get("id"), "label": label,
                              "detail": "컨테이너가 재시작되었습니다"})
        elif kind == "kill_precision":
            extra = count_extra_kills(ctx.get("kill_targets"),
                                      ctx.get("allowed_pids"))
            if extra > c.get("max_extra_kills", 0):
                found.append({"id": c.get("id"), "label": label,
                              "detail": f"범인 외 세션 {extra}건을 KILL했습니다"})
    return found


def fmt_mmss(seconds):
    """초 → MM:SS."""
    total = max(0, int(seconds or 0))
    return f"{total // 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------- #
# 감시 단계 계획 (순수 로직)
# --------------------------------------------------------------------------- #
# 감시를 한 번에 몰아서 하면 docker 호출 4개가 연달아 일어나 UI가 ~240ms 멈춘다.
# 그동안 키 입력이 처리되지 않아 "키가 안 먹는다"처럼 느껴진다.
# 그래서 루프 한 바퀴에 **단계 하나**(docker 호출 1회, ~70ms)만 수행한다.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def watch_targets(stage):
    """재시작을 감시할 컨테이너 이름 집합."""
    targets = {"primary"}
    for step in stage.get("setup") or []:
        targets.add(step.get("on", "primary"))
    return targets


def watch_steps(stage):
    """감시 단계 목록. 한 단계 = docker 호출 1회."""
    steps = []
    for obj in stage.get("objectives") or []:
        if obj.get("type") == "state":
            steps.append({"kind": "state", "id": obj["id"],
                          "label": obj.get("label", obj["id"])})
    steps.append({"kind": "commands", "label": "명령 로그"})
    for target in sorted(watch_targets(stage)):
        steps.append({"kind": "restart", "target": target,
                      "label": f"{target} 재시작 감지"})
    return steps


def init_watch(stage):
    """감시 진행 상태."""
    return {"steps": watch_steps(stage), "index": 0, "last_complete": None,
            "spin": 0, "error": None, "current": None}


def advance_watch(watch, now, poll_seconds=None):
    """다음에 수행할 단계를 정한다 → (새 상태, 단계 또는 None).

    한 호출에 **최대 한 단계**만 돌려준다. 한 주기를 다 돌면 다음 주기까지 쉰다.
    """
    if poll_seconds is None:
        poll_seconds = POLL_SECONDS
    w = dict(watch)
    steps = w.get("steps") or []
    if not steps:
        return w, None
    if w["index"] >= len(steps):
        if (w["last_complete"] is not None
                and now - w["last_complete"] < poll_seconds):
            return w, None          # 주기 사이 휴식
        w["index"] = 0
    step = steps[w["index"]]
    w["index"] += 1
    w["spin"] = (w.get("spin", 0) + 1) % len(SPINNER)
    w["current"] = step["label"]
    if w["index"] >= len(steps):
        w["last_complete"] = now
    return w, step


def objective_marks(stage, session):
    """목표 진행을 한 줄로 압축 → "[x][x][ ][?]".

    콘솔이 전체화면을 덮으므로, 요약줄로 문맥을 유지한다.
    """
    out = []
    for obj in stage.get("objectives") or []:
        st = (session.get("states") or {}).get(obj["id"]) or {}
        if st.get("done"):
            out.append("[x]")
        elif obj.get("type") == "quiz":
            out.append("[?]")
        else:
            out.append("[ ]")
    return "".join(out)


def history_move(history, index, delta):
    """질의 기록 탐색 → (새 인덱스, 표시할 문자열).

    인덱스는 '뒤에서 몇 번째'다. 0이면 편집 중인 새 줄(빈 문자열).
    """
    if not history:
        return 0, None
    new = min(max(index + delta, 0), len(history))
    if new == 0:
        return 0, ""
    return new, history[len(history) - new]


def clip_output(text, max_lines=CONSOLE_MAX_OUTPUT):
    """질의 출력이 화면을 삼키지 않도록 자른다 → (줄 목록, 잘렸는지).

    `SELECT * FROM orders`는 20만 행이다. 암묵적 LIMIT을 붙이면 의미가 조용히
    바뀌므로, 실행은 그대로 두고 표시만 자른 뒤 잘렸다고 알린다.
    """
    lines = (text or "").rstrip("\n").split("\n") if text else []
    if len(lines) > max_lines:
        return lines[:max_lines], True
    return lines, False


# --------------------------------------------------------------------------- #
# SQL 콘솔 자동완성 (순수 로직)
# --------------------------------------------------------------------------- #
# MySQL은 존재하지 않는 객체도 권한 오류로 답한다("SELECT command denied ...").
# 그래서 `performence_schema` 같은 오타를 만나면 사람이 철자가 아니라 GRANT를
# 의심하러 간다. 오류 메시지를 손보는 대신 오타가 나지 않게 하는 쪽을 택했다.
_IDENT_RE = re.compile(r"[A-Za-z0-9_$.]+$")


def completion_prefix(text, pos):
    """커서 앞의 식별자 토큰 → (시작 인덱스, 토큰). 없으면 (pos, "")."""
    pos = min(max(int(pos), 0), len(text or ""))
    m = _IDENT_RE.search((text or "")[:pos])
    if not m:
        return pos, ""
    return m.start(), m.group(0)


def common_prefix(strings):
    """후보들의 최장 공통 접두사."""
    items = list(strings or [])
    if not items:
        return ""
    first, last = min(items), max(items)
    for i, ch in enumerate(first):
        if i >= len(last) or last[i] != ch:
            return first[:i]
    return first


def completion_candidates(token, schemas, tables_by_schema, default_schema):
    """토큰 모양에 맞는 후보 목록(정렬됨).

    점이 없으면  → 스키마 이름 + 기본 스키마의 테이블
    `sch.부분`   → 그 스키마의 테이블 (`sch.table` 형태로 돌려준다)
    """
    token = token or ""
    schemas = sorted(schemas or [])
    tables_by_schema = tables_by_schema or {}
    if "." in token:
        schema, _, partial = token.rpartition(".")
        return sorted(f"{schema}.{t}"
                      for t in tables_by_schema.get(schema, [])
                      if t.startswith(partial))
    out = [s for s in schemas if s.startswith(token)]
    out += [t for t in tables_by_schema.get(default_schema, [])
            if t.startswith(token)]
    return sorted(set(out))


def apply_completion(text, pos, start, candidates, schemas=()):
    """자동완성 결과를 반영 → (새 텍스트, 새 커서, 보여줄 후보 목록).

    셸 관례를 따른다 — 후보가 하나면 완성하고, 여럿이면 공통 접두사까지만
    확장한 뒤 목록을 보여준다. 스키마를 완성했을 땐 뒤에 '.'을 붙여 다음 Tab이
    곧바로 테이블을 잇도록 한다.
    """
    text = text or ""
    if not candidates:
        return text, pos, []
    if len(candidates) == 1:
        done = candidates[0]
        if done in set(schemas):
            done += "."
        return text[:start] + done + text[pos:], start + len(done), []
    shared = common_prefix(candidates)
    current = text[start:pos]
    if len(shared) > len(current):
        return (text[:start] + shared + text[pos:], start + len(shared),
                candidates)
    return text, pos, candidates


def max_line_width(lines):
    """가장 넓은 줄의 표시 폭. 넓은 결과 안내를 띄울지 판단한다."""
    return max((cwidth(ln) for ln in (lines or [])), default=0)


def spinner_frame(watch):
    return SPINNER[(watch.get("spin") or 0) % len(SPINNER)]


def data_age(watch, now):
    """마지막으로 한 주기를 끝낸 뒤 지난 시간(초). 아직이면 None."""
    last = (watch or {}).get("last_complete")
    return None if last is None else max(0.0, now - last)


RANK_ORDER = ("C", "B", "A", "S")


def rank_for(score):
    """보너스 점수(0~4) → 등급. 클리어 기본이 C이고 4개 다 채우면 S."""
    if score >= 4:
        return "S"
    if score == 3:
        return "A"
    if score == 2:
        return "B"
    return "C"


def rank_breakdown(elapsed, target_seconds, hints_used, violations,
                   quiz_correct, quiz_total):
    """등급 산출 근거를 표시용 항목으로 함께 돌려준다.

    반환: (항목 리스트[(라벨, 값, 보너스여부)], 점수, 등급)
    """
    items = [
        ("소요 시간", f"{fmt_mmss(elapsed)} (목표 {fmt_mmss(target_seconds)})",
         bool(target_seconds) and elapsed <= target_seconds),
        ("힌트 사용", f"{hints_used}회", hints_used == 0),
        ("금지 행동", f"{violations}건", violations == 0),
        ("진단 정확도", f"{quiz_correct}/{quiz_total}",
         quiz_total == 0 or quiz_correct == quiz_total),
    ]
    score = sum(1 for _, _, ok in items if ok)
    return items, score, rank_for(score)


def validate_stage(stage):
    """스테이지 정의의 형식 오류 목록. 정상이면 빈 리스트.

    exam.py의 validate_bank와 같은 역할 — 콘텐츠 작성 실수를 실행 전에 잡는다.
    """
    errs = []
    if not isinstance(stage, dict):
        return ["스테이지가 객체(JSON object)가 아닙니다"]
    for field in ("id", "title", "objectives"):
        if not stage.get(field):
            errs.append(f"필수 필드 누락: {field}")

    for step in stage.get("setup") or []:
        stype = step.get("type")
        if stype not in ("sql", "session", "sessions"):
            errs.append(f"setup: 알 수 없는 type '{stype}'")
        if step.get("on") and step["on"] not in CONTAINERS:
            errs.append(f"setup: 알 수 없는 대상 '{step['on']}'")
        if not step.get("sql"):
            errs.append(f"setup: sql이 비었습니다 ({stype})")
        if stype == "sessions" and int(step.get("count", 0)) < 1:
            errs.append("setup: sessions의 count는 1 이상이어야 합니다")

    objectives = stage.get("objectives") or []
    if not objectives:
        errs.append("objectives가 비었습니다")
    seen = set()
    for obj in objectives:
        oid = obj.get("id")
        if not oid:
            errs.append("objective: id 누락")
        elif oid in seen:
            errs.append(f"objective: id 중복 '{oid}'")
        else:
            seen.add(oid)

        otype = obj.get("type")
        if otype not in OBJECTIVE_TYPES:
            errs.append(f"objective '{oid}': 알 수 없는 type '{otype}'")
        elif otype == "state":
            if not obj.get("query"):
                errs.append(f"objective '{oid}': query 누락")
            if obj.get("on") and obj["on"] not in CONTAINERS:
                errs.append(f"objective '{oid}': 알 수 없는 대상 '{obj['on']}'")
            op = (obj.get("expect") or {}).get("op", "eq")
            if op not in EXPECT_OPS:
                errs.append(f"objective '{oid}': 알 수 없는 연산자 '{op}'")
        elif otype == "quiz":
            errs.extend(_validate_question(oid, obj.get("question")))

    for c in stage.get("constraints") or []:
        if c.get("detect") not in ("container_restart", "kill_precision"):
            errs.append(f"constraint '{c.get('id')}': 알 수 없는 detect")
    return errs


def _validate_question(oid, q):
    """quiz 목표에 들어있는 문항 검증(exam.py 문항 스키마와 동일)."""
    if not isinstance(q, dict):
        return [f"objective '{oid}': question 누락"]
    errs = []
    qtype = q.get("type")
    if not q.get("q"):
        errs.append(f"objective '{oid}': 질문 텍스트(q) 누락")
    if qtype == "mcq":
        choices = q.get("choices") or []
        if len(choices) < 2:
            errs.append(f"objective '{oid}': 선택지가 2개 미만")
        ans = q.get("answer")
        if not isinstance(ans, int) or not (0 <= ans < len(choices)):
            errs.append(f"objective '{oid}': answer 인덱스가 범위를 벗어남")
    elif qtype == "short":
        if not (q.get("accept") or []):
            errs.append(f"objective '{oid}': accept(정답 목록) 누락")
    else:
        errs.append(f"objective '{oid}': 알 수 없는 문항 type '{qtype}'")
    return errs


def discover_stages():
    """`shooting/stages/*.json` 경로를 정렬해 반환."""
    if not STAGES_DIR.is_dir():
        return []
    return sorted(STAGES_DIR.glob("*.json"))


def load_stage(path):
    """스테이지 JSON을 읽고 검증한다."""
    with open(path, encoding="utf-8") as f:
        stage = json.load(f)
    errs = validate_stage(stage)
    if errs:
        raise ValueError(f"{path}: " + "; ".join(errs))
    return stage


# --------------------------------------------------------------------------- #
# 도커 / MySQL I/O (얇게 유지 — 여기는 테스트하지 않는다)
# --------------------------------------------------------------------------- #
def _docker(*args, timeout=60):
    return subprocess.run(["docker", *args], capture_output=True,
                          text=True, timeout=timeout)


def _compose(*args, timeout=600):
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
        capture_output=True, text=True, timeout=timeout)


def mysql(target, sql, timeout=30, log_off=True):
    """엔진 전용 root 질의 → 행 목록.

    자격증명은 컨테이너 안 /root/.my.cnf 에서 읽으므로 명령줄에 비밀번호가
    노출되지 않는다.

    `log_off`가 켜져 있으면 이 세션의 질의를 general_log에서 뺀다. 이게 없으면
    엔진이 **자기가 읽어야 할 로그를 자기가 오염시킨다** — mysql.general_log는
    CSV 엔진이라 인덱스가 없어 조회할 때마다 전체 스캔인데, 2초마다 도는 폴링
    질의가 계속 쌓이면서 다음 폴링이 점점 느려지는 자기 증폭 구조가 된다.
    """
    if log_off:
        sql = "SET SESSION sql_log_off = 1; " + sql
    container = CONTAINERS.get(target, target)
    p = _docker("exec", container, "mysql", "-N", "-B", "-e", sql,
                timeout=timeout)
    if p.returncode != 0:
        raise LabError((p.stderr or "").strip() or f"{container}: mysql 실행 실패")
    return parse_tsv(p.stdout)


def mysql_spawn(target, user, password, sql):
    """장애 주입용 분리(detached) 세션을 띄운다.

    `--no-defaults`가 꼭 필요하다 — MySQL 클라이언트의 옵션 우선순위는
    명령줄 > 옵션 파일 > 환경변수라서, /root/.my.cnf 의 root 비밀번호가
    MYSQL_PWD를 이겨버린다. 그러면 app 계정 인증이 조용히 실패한다.
    """
    container = CONTAINERS.get(target, target)
    _docker("exec", "-d", "-e", f"MYSQL_PWD={password}", container,
            "mysql", "--no-defaults", "-u", user, "-e", sql)


def run_player_sql(target, sql, timeout=CONSOLE_TIMEOUT):
    """콘솔에서 플레이어가 친 SQL을 실행한다 → (stdout, stderr, timed_out).

    **`mysql()`을 재사용하면 안 된다.** 그 함수는 sql_log_off로 로깅을 끄는데,
    플레이어 명령은 반드시 general_log에 남아야 판정이 성립한다.

    dba 계정으로 접속하는 것도 같은 이유다 — 판정은 '어디서 쳤는가'가 아니라
    'general_log에 누구로 기록됐는가'를 보므로, 이렇게 하면 콘솔에서 친 명령과
    외부 터미널에서 친 명령이 로그상 구분되지 않는다.

    `--no-defaults`가 없으면 컨테이너의 /root/.my.cnf(root 비밀번호)가 MYSQL_PWD를
    이겨 dba 인증이 조용히 실패한다.

    타임아웃이 필요한 이유: 락 인시던트 중에 UPDATE를 치면 서버의
    innodb_lock_wait_timeout(1800초)까지 막힌다. 그대로 두면 TUI가 얼어붙는다.
    """
    container = CONTAINERS.get(target, target)
    try:
        p = _docker("exec", "-e", f"MYSQL_PWD={PLAYER_PASSWORD}", container,
                    "mysql", "--no-defaults", "-u", PLAYER_USER, "-t",
                    "-D", PLAYER_DB, "-e", sql, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", "", True
    return p.stdout, p.stderr, False


def load_schema_names(target="primary"):
    """자동완성용 스키마·테이블 이름 → (스키마 리스트, {스키마: [테이블]}).

    **반드시 엔진 경로(`mysql()`)로 가져와야 한다.** `run_player_sql`을 쓰면
    자동완성용 질의가 general_log에 남아, 플레이어가 치지도 않은 명령이
    판정 대상 목록에 섞인다.
    """
    schemas = [r[0] for r in mysql(
        target, "SELECT SCHEMA_NAME FROM information_schema.schemata") if r]
    tables = {}
    for row in mysql(target, "SELECT TABLE_SCHEMA, TABLE_NAME "
                             "FROM information_schema.tables"):
        if len(row) >= 2:
            tables.setdefault(row[0], []).append(row[1])
    for names in tables.values():
        names.sort()
    return sorted(schemas), tables


def console_names(session, target="primary", ttl=30.0):
    """이름 목록을 캐시해 돌려준다(구축 스테이지에서 만든 객체도 잡히게 TTL)."""
    cache = session.get("console_names")
    now = time.monotonic()
    if cache and now - cache["at"] < ttl:
        return cache["schemas"], cache["tables"]
    try:
        schemas, tables = load_schema_names(target)
    except LabError:
        if cache:
            return cache["schemas"], cache["tables"]
        return [], {}
    session["console_names"] = {"at": now, "schemas": schemas, "tables": tables}
    return schemas, tables


def container_started_at(target):
    """컨테이너 기동 시각(재시작 감지용). 조회 실패 시 None."""
    container = CONTAINERS.get(target, target)
    p = _docker("inspect", "--format", "{{.State.StartedAt}}", container)
    return p.stdout.strip() if p.returncode == 0 else None


def app_session_pids(target):
    """엔진이 띄운 app 세션의 processlist id 집합."""
    rows = mysql(target,
                 "SELECT id FROM information_schema.processlist WHERE user='app'")
    return {int(r[0]) for r in rows if r and r[0].isdigit()}


def kill_app_sessions(target):
    """남아있는 app 세션을 전부 정리(스테이지 재시작 시 깨끗한 출발점)."""
    for pid in app_session_pids(target):
        try:
            mysql(target, f"KILL {pid}")
        except LabError:
            pass  # 이미 사라진 세션


def reset_player_log(target):
    """플레이어 명령 로그를 비운다. 스테이지 시작 시점을 0으로 맞춘다.

    로깅을 켜 둔 채로 TRUNCATE가 가능하므로 off/on 토글이 필요 없다.
    """
    mysql(target, "TRUNCATE TABLE mysql.general_log")


def read_player_commands(target):
    """직전 호출 이후 플레이어가 새로 친 질의. 읽으면서 로그를 비운다.

    누적은 호출부(run_watch_step) 책임이다 — 여기서 반환하는 건 '새 것'뿐이다.
    실패를 삼키지 않는다 — 호출부가 화면에 띄운다.
    """
    return [r[0] for r in mysql(target, PLAYER_LOG_SQL) if r]


def lab_running():
    """primary/replica 컨테이너가 모두 running 인가."""
    for target in ("primary", "replica"):
        p = _docker("inspect", "--format", "{{.State.Running}}",
                    CONTAINERS[target])
        if p.returncode != 0 or p.stdout.strip() != "true":
            return False
    return True


def lab_healthy():
    """두 컨테이너가 모두 healthy 인가."""
    for target in ("primary", "replica"):
        p = _docker("inspect", "--format", "{{.State.Health.Status}}",
                    CONTAINERS[target])
        if p.returncode != 0 or p.stdout.strip() != "healthy":
            return False
    return True


def lab_up(wait_seconds=300):
    """랩을 기동하고 healthy 가 될 때까지 기다린다."""
    print("랩을 기동합니다 (최초 실행은 이미지 내려받기 때문에 몇 분 걸릴 수 있습니다)…")
    p = _compose("up", "-d")
    if p.returncode != 0:
        raise LabError((p.stderr or p.stdout).strip())
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if lab_healthy():
            print("랩 준비 완료.")
            return True
        time.sleep(3)
        print("  … 초기화 대기 중")
    raise LabError("랩이 제한 시간 안에 준비되지 않았습니다. "
                   "`docker compose -f shooting/lab/compose.yaml logs`를 확인하세요.")


def lab_down(remove_volumes=True):
    args = ["down"] + (["-v"] if remove_volumes else [])
    p = _compose(*args)
    if p.returncode != 0:
        raise LabError((p.stderr or p.stdout).strip())


# --------------------------------------------------------------------------- #
# 스테이지 준비 / 감시
# --------------------------------------------------------------------------- #
def setup_stage(stage, log=print):
    """스테이지 setup을 실행하고 감시에 필요한 기준값을 만든다.

    setup은 멱등해야 한다 — 이전 판의 잔재를 먼저 지우고, 데이터를 되돌리고,
    그 다음 장애를 주입한다. 덕분에 스테이지를 순서 무관하게 다시 플레이할 수 있다.

    반환: {"allowed_pids": set, "started_at": {target: str}}
    """
    targets = {"primary"}
    for step in stage.get("setup") or []:
        targets.add(step.get("on", "primary"))

    # 1. 이전 판의 잔재 정리 + 플레이어 명령 로그 초기화 (엔진 공통 동작)
    for t in sorted(targets):
        log(f"[setup] {t}: 이전 세션 정리")
        kill_app_sessions(t)
    reset_player_log("primary")

    # 2. 스테이지가 선언한 단계 실행
    allowed = set()
    for step in stage.get("setup") or []:
        target = step.get("on", "primary")
        stype = step["type"]
        sql = step["sql"]
        if stype == "sql":
            log(f"[setup] {target}: 상태 복원 SQL")
            mysql(target, sql)
            continue

        count = int(step.get("count", 1)) if stype == "sessions" else 1
        name = step.get("name", stype)
        before = app_session_pids(target)
        log(f"[setup] {target}: '{name}' 세션 {count}개 기동")
        for _ in range(count):
            mysql_spawn(target, step.get("user", "app"),
                        step.get("password", "app"), sql)
        spawned = _wait_for_new_sessions(target, before, count)
        if step.get("culprit"):
            # 범인 세션의 pid를 기억해둔다 — 나중에 "범인 외 KILL"을 가려낸다.
            allowed |= spawned

    # 3. 장애가 실제로 걸린 것을 확인한 뒤 출발한다.
    #    세션이 뜬 직후엔 아직 잠금을 잡으러 가기 전이라 상태가 잠깐 정상으로
    #    보인다. 그 창에서 타이머를 시작하면 플레이어가 아무것도 하지 않았는데
    #    hold가 채워져 클리어돼버린다.
    if not _wait_for_incident(stage):
        log("[setup] 경고: 장애 상태가 확인되지 않았습니다 — "
            "이미 목표가 충족된 상태일 수 있습니다.")

    return {
        "allowed_pids": allowed,
        "started_at": {t: container_started_at(t) for t in sorted(targets)},
    }


def _wait_for_new_sessions(target, before, count, timeout=20):
    """새로 뜬 app 세션 pid가 잡힐 때까지 기다린다."""
    deadline = time.monotonic() + timeout
    new = set()
    while time.monotonic() < deadline:
        new = app_session_pids(target) - before
        if len(new) >= count:
            return new
        time.sleep(0.5)
    return new


def _wait_for_incident(stage, timeout=30):
    """모든 state 목표가 '미충족'이 될 때까지 기다린다(=장애가 실제로 걸림)."""
    states = [o for o in stage["objectives"] if o["type"] == "state"]
    if not states:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(not poll_state_objective(o)[1] for o in states):
            return True
        time.sleep(0.5)
    return False


def teardown_stage(stage):
    """플레이 종료 후 주입한 세션을 정리한다."""
    targets = {"primary"}
    for step in stage.get("setup") or []:
        targets.add(step.get("on", "primary"))
    for t in sorted(targets):
        try:
            kill_app_sessions(t)
        except LabError:
            pass


def poll_state_objective(obj):
    """state 목표의 현재 값과 충족 여부."""
    target = obj.get("on", "primary")
    try:
        rows = mysql(target, obj["query"])
    except LabError as e:
        return None, False, str(e)
    value = first_scalar(rows)
    return value, evaluate_expect(obj.get("expect"), value), None


def run_watch_step(step, stage, session, baseline):
    """감시 단계 **하나**를 실행한다(docker 호출 1회).

    실패하면 사람이 읽을 오류 메시지를, 정상이면 None을 돌려준다.
    예외를 삼키지 않고 돌려주는 게 핵심이다 — 감시가 죽었는데 화면이 평온하면
    플레이어는 '느린 건지 고장난 건지' 알 수 없다.
    """
    now = time.monotonic()
    try:
        kind = step["kind"]
        if kind == "state":
            obj = _objective_by_id(stage, step["id"])
            st = session["states"][obj["id"]]
            if st["done"]:
                return None
            value = first_scalar(mysql(obj.get("on", "primary"), obj["query"]))
            st["value"], st["error"] = value, None
            ok = evaluate_expect(obj.get("expect"), value)
            st["hold"], st["done"] = update_hold(
                st["hold"], ok, now, obj.get("hold_seconds", 0))

        elif kind == "commands":
            # 로그는 읽으면서 비워지므로 '새 것'만 온다 → 누적은 여기서.
            session["commands"].extend(read_player_commands("primary"))
            recompute_violations(stage, session, baseline)

        elif kind == "restart":
            target = step["target"]
            was = (baseline.get("started_at") or {}).get(target)
            cur = container_started_at(target)
            if cur is None:
                raise LabError("컨테이너 상태를 읽을 수 없습니다")
            if was and cur != was:
                session["restarted"] = True
            recompute_violations(stage, session, baseline)
        return None
    except LabError as e:
        if step["kind"] == "state":
            session["states"][step["id"]]["error"] = str(e)
        return f"{step['label']}: {e}"


def recompute_violations(stage, session, baseline):
    """지금까지 모은 감시 재료로 금지 행동을 다시 판정한다."""
    ctx = {
        "kill_targets": parse_kill_targets(session.get("commands")),
        "allowed_pids": baseline.get("allowed_pids", set()),
        "restarted": session.get("restarted", False),
    }
    session["violations"] = detect_violations(stage.get("constraints"), ctx)


def _objective_by_id(stage, oid):
    for obj in stage["objectives"]:
        if obj["id"] == oid:
            return obj
    raise KeyError(oid)


# --------------------------------------------------------------------------- #
# 진행 기록 (비커밋)
# --------------------------------------------------------------------------- #
def save_progress(stage, rank, score, elapsed, hints_used, violations):
    try:
        PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "stage": stage.get("id"),
            "title": stage.get("title"),
            "rank": rank,
            "score": score,
            "elapsed": round(elapsed, 1),
            "hints": hints_used,
            "violations": violations,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 기록 실패가 플레이를 막지는 않는다


# --------------------------------------------------------------------------- #
# 게임 세션 상태
# --------------------------------------------------------------------------- #
def init_session(stage, rng=None):
    """플레이 런타임 상태를 만든다."""
    states = {}
    for obj in stage["objectives"]:
        st = {"done": False, "hold": {"since": None}, "value": None,
              "error": None, "correct": None, "prompted": False}
        if obj["type"] == "quiz" and obj["question"].get("type") == "mcq":
            q = obj["question"]
            if rng is not None:
                # order = 표시순서(원본 인덱스), answer = 섞인 뒤의 표시 인덱스
                _, answer, order = shuffle_choices(q["choices"], q["answer"], rng)
            else:
                order, answer = list(range(len(q["choices"]))), q["answer"]
            st["order"], st["answer"] = order, answer
        states[obj["id"]] = st
    return {
        "started": time.monotonic(),
        "states": states,
        "hints_used": 0,
        "violations": [],
        "commands": [],
        "restarted": False,
        "watch_error": None,
        "finished": False,
    }


def quiz_totals(stage, session):
    """진단 문항 (정답 수, 전체 수)."""
    total = correct = 0
    for obj in stage["objectives"]:
        if obj["type"] != "quiz":
            continue
        total += 1
        if session["states"][obj["id"]].get("correct"):
            correct += 1
    return correct, total


def all_done(stage, session):
    return all(session["states"][o["id"]]["done"] for o in stage["objectives"])


def refresh(stage, session, baseline):
    """감시 한 주기를 한 번에 수행한다(라인 모드용).

    curses 모드는 이걸 쓰지 않고 `advance_watch`로 한 단계씩 나눠 돌린다 —
    UI가 멈추지 않아야 하기 때문이다. 라인 모드는 어차피 입력에서 블로킹하므로
    한 번에 도는 편이 단순하다.
    """
    error = None
    for step in watch_steps(stage):
        error = run_watch_step(step, stage, session, baseline) or error
    session["watch_error"] = error
    return session


def summarize(stage, session):
    """결과 요약(등급 산출 근거 포함)."""
    elapsed = time.monotonic() - session["started"]
    correct, total = quiz_totals(stage, session)
    items, score, rank = rank_breakdown(
        elapsed, stage.get("target_seconds"), session["hints_used"],
        len(session["violations"]), correct, total)
    return {"elapsed": elapsed, "items": items, "score": score, "rank": rank,
            "quiz": (correct, total)}


# --------------------------------------------------------------------------- #
# 라인 모드 (curses 폴백)
# --------------------------------------------------------------------------- #
def _connect_hint(stage):
    return stage.get("connect_hint",
                     "MYSQL_PWD=shoot mysql -h127.0.0.1 -P3306 -udba")


def run_line(stage, session, baseline):
    """tty가 아니거나 curses가 없을 때의 단순 진행 화면."""
    print(f"\n== {stage.get('title')} ({stage.get('id')}) ==")
    print(f"\n{stage.get('brief', '')}\n")
    print(f"접속: {_connect_hint(stage)}\n")
    print("다른 터미널에서 위 명령으로 접속해 대응하세요.")
    print("이 창은 상태만 표시합니다. Ctrl+C로 중단.\n")

    last = None
    try:
        while not all_done(stage, session):
            refresh(stage, session, baseline)
            for obj in stage["objectives"]:
                if obj["type"] == "quiz":
                    st = session["states"][obj["id"]]
                    if not st["done"]:
                        _line_ask(obj, st)
            snapshot = _line_snapshot(stage, session)
            if snapshot != last:
                print(snapshot)
                last = snapshot
            if all_done(stage, session):
                break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        return None
    return summarize(stage, session)


def _line_snapshot(stage, session):
    elapsed = time.monotonic() - session["started"]
    lines = [f"\n[{fmt_mmss(elapsed)}] 목표"]
    now = time.monotonic()
    for obj in stage["objectives"]:
        st = session["states"][obj["id"]]
        mark = "x" if st["done"] else " "
        extra = ""
        if obj["type"] == "state" and not st["done"]:
            extra = f"  현재값={st['value']}"
            hold = obj.get("hold_seconds", 0)
            if hold and st["hold"].get("since") is not None:
                extra += f" (유지까지 {hold_remaining(st['hold'], now, hold):.0f}s)"
        lines.append(f"  [{mark}] {obj.get('label', obj['id'])}{extra}")
    for v in session["violations"]:
        lines.append(f"  ! 금지 행동: {v['label']} — {v['detail']}")
    if session.get("watch_error"):
        lines.append(f"  ! 감시 오류: {session['watch_error']}")
    return "\n".join(lines)


def _line_ask(obj, st):
    q = obj["question"]
    print(f"\n>> 상황 보고: {q['q']}")
    if q["type"] == "mcq":
        order = st.get("order") or list(range(len(q["choices"])))
        for i, idx in enumerate(order, 1):
            print(f"   {i}) {q['choices'][idx]}")
        raw = input("   번호 입력: ").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= len(order)):
            print("   잘못된 입력입니다.")
            return
        st["correct"] = grade_mcq(int(raw) - 1, st.get("answer", q["answer"]))
    else:
        raw = input("   답 입력: ").strip()
        st["correct"] = grade_short(raw, q.get("accept") or [])
    st["done"] = True
    print("   → " + ("정답" if st["correct"] else "오답"))
    if q.get("explain"):
        print(f"   {q['explain']}")


# --------------------------------------------------------------------------- #
# curses 모드
# --------------------------------------------------------------------------- #
KEY_TIMEOUT_MS = 80      # 낮을수록 키 반응이 빠르다(스피너도 이 주기로 돈다)


def run_curses(stage, session, baseline):
    import curses

    def _driver(stdscr):
        curses.curs_set(0)
        _init_screen(curses)
        stdscr.timeout(KEY_TIMEOUT_MS)
        watch = init_watch(stage)

        while True:
            now = time.monotonic()

            # 감시는 한 바퀴에 한 단계만 — docker 호출 1회(~70ms)라 UI가 멈추지 않는다.
            watch, step = advance_watch(watch, now)
            if step is not None:
                err = run_watch_step(step, stage, session, baseline)
                watch = dict(watch, error=err)
                session["watch_error"] = err

            if all_done(stage, session):
                return summarize(stage, session)

            # after_seconds가 지난 미응답 진단 문항은 한 번 자동으로 띄운다.
            pending = _due_quiz(stage, session)
            if pending is not None:
                session["states"][pending["id"]]["prompted"] = True
                _quiz_screen(stdscr, curses, pending,
                             session["states"][pending["id"]])
                stdscr.timeout(KEY_TIMEOUT_MS)
                continue

            _draw_play(stdscr, curses, stage, session, watch)
            kind, key = read_key(stdscr, curses)
            stdscr.timeout(KEY_TIMEOUT_MS)   # read_key가 nodelay를 건드리므로 복구
            if is_idle(kind, key):
                continue

            # read_key는 wide=False면 정수를 준다 — 반드시 key_char로 정규화한다.
            ch = (key_char(key) or "").lower()
            if ch == "q":
                return None
            if ch == "r":
                nxt = _next_unanswered_quiz(stage, session)
                if nxt:
                    _quiz_screen(stdscr, curses, nxt,
                                 session["states"][nxt["id"]])
                    stdscr.timeout(KEY_TIMEOUT_MS)
                else:
                    _notice(stdscr, curses, "답할 상황 보고가 남아있지 않습니다.")
                    stdscr.timeout(KEY_TIMEOUT_MS)
            elif ch == "c":
                watch, cleared = _console_screen(stdscr, curses, stage,
                                                 session, baseline, watch)
                stdscr.timeout(KEY_TIMEOUT_MS)
                if cleared:
                    return summarize(stage, session)
            elif ch == "h":
                _hint_screen(stdscr, curses, stage, session)
                stdscr.timeout(KEY_TIMEOUT_MS)

    return curses.wrapper(_driver)


def _init_screen(curses):
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
    except curses.error:
        pass
    try:
        # 기본 ESCDELAY가 1000ms라 Esc가 '안 먹는 것'처럼 느껴진다.
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass                         # Python 3.9 미만이거나 터미널이 거부하는 경우


def _due_quiz(stage, session):
    """자동으로 띄워야 할 진단 문항(after_seconds 경과 + 미응답 + 미제시).

    trigger가 없는 문항은 자동으로 띄우지 않는다 — 플레이어가 상황을 보기도 전에
    문항이 튀어나오면 '상황 보고'가 아니라 그냥 퀴즈가 된다. 그런 문항은
    플레이어가 r 키로 직접 연다.
    """
    elapsed = time.monotonic() - session["started"]
    for obj in stage["objectives"]:
        if obj["type"] != "quiz":
            continue
        st = session["states"][obj["id"]]
        if st["done"] or st.get("prompted"):
            continue
        trigger = obj.get("trigger") or {}
        if "after_seconds" not in trigger:
            continue
        if elapsed >= trigger["after_seconds"]:
            return obj
    return None


def _next_unanswered_quiz(stage, session):
    for obj in stage["objectives"]:
        if obj["type"] == "quiz" and not session["states"][obj["id"]]["done"]:
            return obj
    return None


def _draw_play(stdscr, curses, stage, session, watch=None):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    elapsed = time.monotonic() - session["started"]
    now = time.monotonic()
    summary = summarize(stage, session)

    bar(stdscr, curses, 0, w,
        f" {stage.get('id')}  {stage.get('title')} "
        f"   {fmt_mmss(elapsed)}   예상 등급 {summary['rank']} ")

    row = 2
    put(stdscr, curses, row, 1, "접속", w - 2, curses.A_DIM)
    put(stdscr, curses, row, 8, "c 키로 SQL 콘솔", w - 9,
        curses.color_pair(4) | curses.A_BOLD)
    row += 1
    put(stdscr, curses, row, 8, f"또는 {_connect_hint(stage)}", w - 9,
        curses.A_DIM)
    row += 2

    for line in wrap(stage.get("brief", ""), w - 4):
        put(stdscr, curses, row, 2, line, w - 4)
        row += 1
    row += 1

    put(stdscr, curses, row, 1, "목표", w - 2, curses.A_BOLD)
    row += 1
    for obj in stage["objectives"]:
        st = session["states"][obj["id"]]
        if st["done"]:
            mark, attr = "[x]", curses.color_pair(1)
        elif obj["type"] == "quiz":
            mark, attr = "[?]", curses.color_pair(3)
        else:
            mark, attr = "[ ]", 0
        put(stdscr, curses, row, 2, mark, 4, attr)
        put(stdscr, curses, row, 6, obj.get("label", obj["id"]), w - 8)

        detail = ""
        if obj["type"] == "state" and not st["done"]:
            if st["error"]:
                detail = "조회 실패"
            else:
                detail = f"현재 {st['value']}"
                hold = obj.get("hold_seconds", 0)
                if hold:
                    left = hold_remaining(st["hold"], now, hold)
                    detail += (f" · 유지 {left:.0f}s 남음"
                               if st["hold"].get("since") is not None
                               else f" · {hold}s 유지 필요")
        elif obj["type"] == "quiz" and not st["done"]:
            detail = "r 키로 보고"
        if detail:
            x = max(8, w - 2 - cwidth(detail))
            put(stdscr, curses, row, x, detail, w - 1 - x, curses.A_DIM)
        row += 1

    if session["violations"]:
        row += 1
        for v in session["violations"]:
            put(stdscr, curses, row, 1,
                f"⚠ 금지 행동: {v['label']} — {v['detail']}", w - 2,
                curses.color_pair(2) | curses.A_BOLD)
            row += 1

    # 감시 인디케이터 — 70ms짜리 단계를 깜빡여도 읽을 수 없으므로,
    # '움직임(살아있음) · 신선도(느려지는 중) · 오류(고장)'를 지속 표시한다.
    if watch is not None:
        row += 1
        err = watch.get("error")
        if err:
            put(stdscr, curses, row, 1, f"⚠ 감시 오류: {err}", w - 2,
                curses.color_pair(2) | curses.A_BOLD)
        else:
            age = data_age(watch, now)
            fresh = "갱신 중…" if age is None else f"{age:.0f}초 전 갱신"
            put(stdscr, curses, row, 1,
                f"{spinner_frame(watch)} 감시 중 · {fresh}"
                f" · {watch.get('current') or ''}", w - 2, curses.A_DIM)

    hints = stage.get("hints") or []
    bar(stdscr, curses, h - 1, w,
        f" c SQL 콘솔   r 상황 보고   "
        f"h 힌트({session['hints_used']}/{len(hints)})   q 포기 ")
    stdscr.refresh()


def _console_screen(stdscr, curses, stage, session, baseline, watch):
    """TUI 내장 SQL 콘솔 → (새 watch 상태, 클리어됐는지).

    질의당 새 접속을 쓰는 무상태 콘솔이다(트랜잭션·SET SESSION은 유지되지 않는다).
    판정은 바뀌지 않는다 — dba 계정으로 접속하므로 general_log 귀속이 외부
    터미널과 동일하다.

    콘솔이 열려 있는 동안에도 감시를 계속 돌린다. 여기서 장애를 고쳤는데 화면을
    나가야만 반영되면 곤란하기 때문이다.
    """
    lines = session.setdefault("console_lines", [
        f"{PLAYER_USER}@{PLAYER_DB} 로 접속됩니다. 질의 끝의 세미콜론은 생략해도 됩니다.",
        "트랜잭션 유지가 필요하면 외부 터미널을 쓰세요(질의마다 새 접속입니다).",
        "",
    ])
    history = session.setdefault("console_history", [])
    buf = list(session.get("console_draft", ""))
    pos = len(buf)
    hist_idx = 0
    scroll = 0            # 0 = 맨 아래(최신)
    hoffset = 0           # 결과 가로 스크롤(넓은 테이블용)
    stdscr.timeout(KEY_TIMEOUT_MS)

    def _emit(new_lines):
        lines.extend(new_lines)
        del lines[:-CONSOLE_SCROLLBACK]

    while True:
        now = time.monotonic()
        watch, step = advance_watch(watch, now)
        if step is not None:
            err = run_watch_step(step, stage, session, baseline)
            watch = dict(watch, error=err)
            session["watch_error"] = err
        if all_done(stage, session):
            session["console_draft"] = "".join(buf)
            return watch, True

        h, w = stdscr.getmaxyx()
        body_h = max(1, h - 4)                 # 상태줄 + 구분선 + 입력줄 + 키안내
        view = clamp_scroll(scroll, len(lines), body_h)

        stdscr.erase()
        bar(stdscr, curses, 0, w,
            f" {stage.get('id')}  {stage.get('title')}  "
            f"{fmt_mmss(now - session['started'])}  "
            f"{objective_marks(stage, session)} ")

        start = max(0, len(lines) - body_h - view)
        shown = lines[start:start + body_h]
        widest = max_line_width(shown)
        for i, line in enumerate(shown):
            put(stdscr, curses, 1 + i, 1, hslice(line, hoffset, w - 2), w - 2)
        if widest > w - 2 or hoffset:
            more = "▶" if widest - hoffset > w - 2 else " "
            put(stdscr, curses, h - 3, 1,
                f"{'◀' if hoffset else ' '} 가로 {hoffset}칸 "
                f"(폭 {widest}) {more}", w - 2, curses.A_DIM)

        visible, col = hscroll("".join(buf), pos, w - 4)
        put(stdscr, curses, h - 2, 1, "> ", 2, curses.color_pair(4))
        put(stdscr, curses, h - 2, 3, visible, w - 4, curses.A_BOLD)
        bar(stdscr, curses, h - 1, w,
            " Enter 실행  Tab 완성  ↑↓ 기록  PgUp/PgDn 세로  "
            "^B/^F 가로  Esc 돌아가기 ")
        try:
            curses.curs_set(1)
            stdscr.move(h - 2, min(3 + col, w - 2))
        except curses.error:
            pass
        stdscr.refresh()

        kind, key = read_key(stdscr, curses, wide=True)
        stdscr.timeout(KEY_TIMEOUT_MS)
        if is_idle(kind, key):
            continue
        if kind == "esc":
            curses.curs_set(0)
            session["console_draft"] = "".join(buf)
            return watch, False
        if kind in ("alt", "ctrl"):
            # 보조 바인딩. 터미널이 온전히 전달하면 동작하지만 주 키는 아니다 —
            # 아래 Ctrl+B/F 주석 참고.
            if key in (curses.KEY_LEFT, "b", "B"):
                hoffset = max(0, hoffset - HSCROLL_STEP)
            elif key in (curses.KEY_RIGHT, "f", "F"):
                hoffset += HSCROLL_STEP
            continue
        if kind != "key" or key is None:
            continue

        # 결과 가로 스크롤의 **주 키**는 Ctrl+B / Ctrl+F 다.
        # 단일 바이트라 ESCDELAY와 무관하게 항상 도착한다. 수식키+화살표는
        # 다중 바이트 이스케이프 시퀀스라, Esc 응답성을 위해 낮춰둔
        # set_escdelay(25) 때문에 쪼개져 단독 Esc로 오해될 수 있다.
        #
        # wide=True면 get_wch()가 '\x06' 같은 **문자열**을 준다 — 정수 6과
        # 직접 비교하면 조용히 죽는다. 반드시 key_char로 정규화한다.
        ctrl = key_char(key)
        if ctrl == "\x02":                # Ctrl+B — 왼쪽으로
            hoffset = max(0, hoffset - HSCROLL_STEP)
            continue
        if ctrl == "\x06":                # Ctrl+F — 오른쪽으로
            hoffset += HSCROLL_STEP
            continue

        if is_enter(key):
            sql = "".join(buf).strip()
            if not sql:
                continue
            history.append(sql)
            hist_idx = 0
            _emit([f"> {sql}"])
            curses.curs_set(0)
            _emit(_run_console_sql(stdscr, curses, sql, w - 2))
            buf, pos, scroll, hoffset = [], 0, 0, 0
            session["console_draft"] = ""
            stdscr.timeout(KEY_TIMEOUT_MS)
            continue

        if key_char(key) == "\t":
            text = "".join(buf)
            begin, token = completion_prefix(text, pos)
            schemas, tables = console_names(session)
            cands = completion_candidates(token, schemas, tables, PLAYER_DB)
            text, pos, show = apply_completion(text, pos, begin, cands, schemas)
            buf = list(text)
            if show:
                _emit(_format_candidates(show, w - 4))
                scroll = 0
            continue

        if key == curses.KEY_UP:
            hist_idx, text = history_move(history, hist_idx, +1)
            if text is not None:
                buf, pos = list(text), len(text)
        elif key == curses.KEY_DOWN:
            hist_idx, text = history_move(history, hist_idx, -1)
            if text is not None:
                buf, pos = list(text), len(text)
        elif key == curses.KEY_PPAGE:
            scroll = clamp_scroll(view + body_h, len(lines), body_h)
        elif key == curses.KEY_NPAGE:
            scroll = clamp_scroll(view - body_h, len(lines), body_h)
        elif key == curses.KEY_LEFT:
            pos = max(0, pos - 1)
        elif key == curses.KEY_RIGHT:
            pos = min(len(buf), pos + 1)
        elif key == curses.KEY_HOME:
            pos = 0
        elif key == curses.KEY_END:
            pos = len(buf)
        elif key == curses.KEY_DC:
            if pos < len(buf):
                del buf[pos]
        elif is_backspace(key):
            if pos > 0:
                del buf[pos - 1]
                pos -= 1
        else:
            ch = key_char(key)
            if ch and ch.isprintable():
                buf.insert(pos, ch)
                pos += 1


CONSOLE_MAX_CANDIDATES = 60


def _format_candidates(candidates, width):
    """자동완성 후보를 스크롤백에 넣을 줄 목록으로."""
    shown = candidates[:CONSOLE_MAX_CANDIDATES]
    lines = wrap("  ".join(shown), width)
    if len(candidates) > len(shown):
        lines.append(f"  … 외 {len(candidates) - len(shown)}개")
    return lines + [""]


def _run_console_sql(stdscr, curses, sql, width):
    """질의를 실행하고 화면에 넣을 줄 목록을 만든다."""
    h, w = stdscr.getmaxyx()
    bar(stdscr, curses, h - 1, w, " 실행 중… ")
    stdscr.refresh()

    stdout, stderr, timed_out = run_player_sql("primary", sql)
    if timed_out:
        return [f"! 질의가 {CONSOLE_TIMEOUT}초를 넘겨 중단했습니다.",
                "  잠금 대기에 막혔을 수 있습니다 — 지금이 바로 그런 상황입니다.",
                "  누가 막고 있는지부터 확인해 보세요.", ""]
    out, clipped = clip_output(stdout)
    if clipped:
        out.append(f"… 결과가 {CONSOLE_MAX_OUTPUT}줄에서 잘렸습니다.")
    widest = max_line_width(out)
    if widest > width:
        # performance_schema.data_locks 는 한 줄이 254칸이다. 가로로 끝까지
        # 미는 건 괴로우므로 실무 정석인 세로 출력(\G)을 함께 알려준다.
        out.append(f"⚠ 행이 {widest}칸으로 화면({width}칸)보다 넓습니다.")
        out.append("  Ctrl+F / Ctrl+B 로 좌우로 밀거나, "
                   "끝에 \\G 를 붙여 세로로 보세요.")
    for line in (stderr or "").rstrip("\n").split("\n"):
        if line.strip():
            out.append(line)
    if not out:
        out.append("(결과 없음)")
    out.append("")
    return out


def _notice(stdscr, curses, text):
    """짧은 안내를 띄우고 아무 키나 기다린다."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    bar(stdscr, curses, 0, w, " 안내 ")
    row = 2
    for line in wrap(text, w - 4):
        put(stdscr, curses, row, 2, line, w - 4)
        row += 1
    bar(stdscr, curses, h - 1, w, " 아무 키나 눌러 계속 ")
    stdscr.refresh()
    stdscr.timeout(-1)
    read_key(stdscr, curses)


def _quiz_screen(stdscr, curses, obj, st):
    """진단 문항 화면. 상태로 증명할 수 없는 '원인 파악'을 여기서 채점한다.

    제출하지 않고 나갈 수 있다(exam.py의 `Esc 닫기(임시저장)` 규약과 동일).
    고르던 위치와 입력하던 내용은 st에 보존되므로 r 키로 다시 열면 이어서 한다.
    """
    q = obj["question"]
    is_mcq = q["type"] == "mcq"
    order = st.get("order") or list(range(len(q.get("choices") or [])))
    cur = st.get("cursor", 0)
    typed = st.get("draft", "")
    stdscr.timeout(-1)               # 답하는 동안은 폴링하지 않는다(블로킹 입력)

    def _save():
        st["cursor"], st["draft"] = cur, typed

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bar(stdscr, curses, 0, w, " 상황 보고 ")
        row = 2
        for line in wrap(q["q"], w - 4):
            put(stdscr, curses, row, 2, line, w - 4, curses.A_BOLD)
            row += 1
        row += 1

        if is_mcq:
            for i, idx in enumerate(order):
                sel = (i == cur)
                put(stdscr, curses, row, 2, "▶" if sel else " ", 2,
                    curses.color_pair(4))
                put(stdscr, curses, row, 4, f"{i + 1}) {q['choices'][idx]}",
                    w - 6, curses.A_REVERSE if sel else 0)
                row += 1
            bar(stdscr, curses, h - 1, w,
                " ↑↓ 또는 숫자 선택   Enter 제출   Esc/b 제출 없이 닫기 ")
        else:
            put(stdscr, curses, row, 2, "> " + typed + "_", w - 4,
                curses.A_BOLD)
            bar(stdscr, curses, h - 1, w,
                " 입력 후 Enter 제출   Esc 제출 없이 닫기(입력 보존) ")
        stdscr.refresh()

        kind, key = read_key(stdscr, curses, wide=not is_mcq)
        if kind == "esc":
            _save()
            return
        if kind != "key" or key is None:
            continue

        if is_enter(key):
            if is_mcq:
                st["correct"] = grade_mcq(cur, st.get("answer", q["answer"]))
            else:
                st["correct"] = grade_short(typed, q.get("accept") or [])
            st["done"] = True
            _save()
            _feedback(stdscr, curses, st["correct"], q)
            return

        if is_mcq:
            ch = (key_char(key) or "").lower()
            if key == curses.KEY_UP or ch == "k":
                cur = (cur - 1) % len(order)
            elif key == curses.KEY_DOWN or ch == "j":
                cur = (cur + 1) % len(order)
            elif ch.isdigit() and 1 <= int(ch) <= len(order):
                cur = int(ch) - 1
            elif ch in ("b", "q"):       # 주관식에선 쓸 수 없다(입력 문자라서)
                _save()
                return
        else:
            if is_backspace(key):
                typed = typed[:-1]
            else:
                ch = key_char(key)
                if ch and ch.isprintable():
                    typed += ch


def _feedback(stdscr, curses, correct, q):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    bar(stdscr, curses, 0, w, " 상황 보고 ")
    attr = curses.color_pair(1) if correct else curses.color_pair(2)
    put(stdscr, curses, 2, 2, "정답" if correct else "오답", w - 4,
        attr | curses.A_BOLD)
    row = 4
    for line in wrap(q.get("explain", ""), w - 4):
        put(stdscr, curses, row, 2, line, w - 4)
        row += 1
    bar(stdscr, curses, h - 1, w, " 아무 키나 눌러 계속 ")
    stdscr.refresh()
    stdscr.timeout(-1)
    read_key(stdscr, curses)


def _hint_screen(stdscr, curses, stage, session):
    hints = stage.get("hints") or []
    if session["hints_used"] >= len(hints):
        return
    text = hints[session["hints_used"]]
    session["hints_used"] += 1
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    bar(stdscr, curses, 0, w,
        f" 힌트 {session['hints_used']}/{len(hints)}  (등급 보너스 상실) ")
    row = 2
    for line in wrap(text, w - 4):
        put(stdscr, curses, row, 2, line, w - 4, curses.color_pair(3))
        row += 1
    bar(stdscr, curses, h - 1, w, " 아무 키나 눌러 계속 ")
    stdscr.refresh()
    stdscr.timeout(-1)
    read_key(stdscr, curses)


# --------------------------------------------------------------------------- #
# 결과 출력
# --------------------------------------------------------------------------- #
def print_result(stage, result):
    print(f"\n{'=' * 46}")
    print(f"  STAGE CLEAR — {stage.get('title')}")
    print(f"{'=' * 46}")
    for label, value, ok in result["items"]:
        print(f"  {label:<10} {value:<24} {'+' if ok else '-'}")
    print(f"{'-' * 46}")
    print(f"  RANK  {result['rank']}   ({result['score']}/4)")
    if stage.get("debrief"):
        print(f"\n  후일담\n")
        for line in stage["debrief"].split("\n"):
            print(f"    {line}")
    print()


# --------------------------------------------------------------------------- #
# 서브커맨드
# --------------------------------------------------------------------------- #
def cmd_list():
    stages = discover_stages()
    if not stages:
        print("스테이지가 없습니다.")
        return 1
    print("\n사용 가능한 스테이지\n")
    for path in stages:
        try:
            stage = load_stage(path)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  [오류] {path.name}: {e}")
            continue
        kind = "🔥" if stage.get("kind") == "incident" else "🔧"
        print(f"  {kind} {stage.get('id'):<24} {stage.get('title')}")
        print(f"     {stage.get('brief', '')[:70]}")
    print()
    return 0


def cmd_doctor():
    ok = True
    print("\n사전 점검\n")

    p = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                       capture_output=True, text=True)
    if p.returncode == 0:
        print(f"  [ok] docker            {p.stdout.strip()}")
    else:
        print("  [!!] docker            실행 중이 아닙니다 (Docker Desktop을 켜세요)")
        ok = False

    if COMPOSE_FILE.exists():
        print(f"  [ok] compose 파일      {COMPOSE_FILE.relative_to(REPO_ROOT)}")
    else:
        print(f"  [!!] compose 파일      없음: {COMPOSE_FILE}")
        ok = False

    mysql_bin = subprocess.run(["which", "mysql"], capture_output=True,
                               text=True)
    if mysql_bin.returncode == 0:
        print(f"  [ok] mysql 클라이언트  {mysql_bin.stdout.strip()}")
    else:
        print("  [!!] mysql 클라이언트  없음 — 플레이어가 접속할 수단이 필요합니다")
        ok = False

    stages = discover_stages()
    bad = 0
    for path in stages:
        try:
            load_stage(path)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  [!!] 스테이지 정의     {e}")
            bad += 1
    if not bad:
        print(f"  [ok] 스테이지 정의     {len(stages)}개 정상")
    ok = ok and not bad

    if lab_running():
        print(f"  [ok] 랩 상태           기동 중"
              f"{' (healthy)' if lab_healthy() else ' (준비 중)'}")
    else:
        print("  [--] 랩 상태           내려가 있음 — `./shoot up`으로 기동")

    print("\n" + ("모두 정상입니다." if ok else "위 항목을 먼저 해결하세요.") + "\n")
    return 0 if ok else 1


def choose_stage(stages):
    """스테이지가 여러 개면 번호로 고르게 한다."""
    if len(stages) == 1:
        return stages[0]
    print("\n스테이지를 고르세요\n")
    for i, path in enumerate(stages, 1):
        try:
            stage = load_stage(path)
            print(f"  {i}) {stage.get('id')}  {stage.get('title')}")
        except (ValueError, json.JSONDecodeError):
            print(f"  {i}) [오류] {path.name}")
    print()
    while True:
        raw = input("번호 (q=종료): ").strip()
        if raw in ("q", "Q"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(stages):
            return stages[int(raw) - 1]
        print("잘못된 입력입니다.")


def cmd_play(target=None, force_line=False):
    stages = discover_stages()
    if not stages:
        print("스테이지가 없습니다. shooting/stages/*.json 을 확인하세요.")
        return 1

    if target:
        path = Path(target)
        if not path.exists():
            matches = [p for p in stages if p.stem == target]
            if not matches:
                print(f"스테이지를 찾을 수 없습니다: {target}")
                return 1
            path = matches[0]
    else:
        path = choose_stage(stages)
        if path is None:
            return 0

    try:
        stage = load_stage(path)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"스테이지 정의 오류: {e}")
        return 1

    if not lab_running():
        print("랩이 내려가 있습니다.")
        try:
            lab_up()
        except LabError as e:
            print(f"랩 기동 실패: {e}")
            return 1
    elif not lab_healthy():
        print("랩이 아직 준비 중입니다. 잠시 기다립니다…")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not lab_healthy():
            time.sleep(3)

    print(f"\n장애를 주입합니다 — {stage.get('title')}")
    try:
        baseline = setup_stage(stage)
    except LabError as e:
        print(f"장애 주입 실패: {e}")
        return 1

    session = init_session(stage)
    use_curses = sys.stdin.isatty() and sys.stdout.isatty() and not force_line
    try:
        if use_curses:
            try:
                result = run_curses(stage, session, baseline)
            except Exception:
                # 조용히 넘기지 않는다 — 이 폴백이 화면 코드의 버그를 감춘 적이
                # 있다(put()의 필수 인자 누락이 라인 모드 전환으로만 보였다).
                traceback.print_exc()
                print("\ncurses 화면에서 오류가 발생해 라인 모드로 전환합니다.\n")
                result = run_line(stage, session, baseline)
        else:
            result = run_line(stage, session, baseline)
    finally:
        teardown_stage(stage)

    if result is None:
        print("\n스테이지를 포기했습니다.")
        return 0

    print_result(stage, result)
    save_progress(stage, result["rank"], result["score"], result["elapsed"],
                  session["hints_used"], len(session["violations"]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="shoot", description="DB 장애 대응 게임")
    parser.add_argument("command", nargs="?", default="play",
                        help="play(기본) | up | down | doctor")
    parser.add_argument("stage", nargs="?",
                        help="스테이지 id 또는 JSON 경로")
    parser.add_argument("--list", action="store_true", help="스테이지 목록")
    parser.add_argument("--line", action="store_true",
                        help="curses 대신 라인 모드로 실행")
    parser.add_argument("--keep-volumes", action="store_true",
                        help="down 시 볼륨을 남긴다")
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list()

    cmd = args.command
    stage_arg = args.stage
    # `./shoot 1-3-lock-contention` 처럼 스테이지를 바로 준 경우
    if cmd not in ("play", "up", "down", "doctor"):
        stage_arg, cmd = cmd, "play"

    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "up":
        try:
            lab_up()
            return 0
        except LabError as e:
            print(f"랩 기동 실패: {e}")
            return 1
    if cmd == "down":
        remove = not args.keep_volumes
        if remove and sys.stdin.isatty():
            ans = input("볼륨(시드 데이터 포함)까지 삭제합니다. 계속할까요? [y/N] ")
            if ans.strip().lower() not in ("y", "yes"):
                print("취소했습니다.")
                return 0
        try:
            lab_down(remove_volumes=remove)
            print("랩을 정리했습니다.")
            return 0
        except LabError as e:
            print(f"랩 정리 실패: {e}")
            return 1

    return cmd_play(stage_arg, force_line=args.line)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        sys.exit(130)
