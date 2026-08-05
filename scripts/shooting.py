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
import random
import re
import shlex
import shutil
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
NOTES_DIR = PROGRESS_DIR / "notes"                # 포스트모템 노트(비커밋)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tui import (  # noqa: E402
    bar, cwidth, is_affirmative, is_backspace, is_enter, is_idle, key_char, pick,
    put, read_key, wrap,
)
from exam import grade_mcq, grade_short, shuffle_choices  # noqa: E402

# 컨테이너 이름은 compose.yaml의 container_name과 맞춰야 한다.
CONTAINERS = {"primary": "dbshoot-primary", "replica": "dbshoot-replica",
              "postgres": "dbshoot-postgres"}
# PostgreSQL 서비스는 compose 프로파일 뒤에 있다 — MySQL 스테이지만 하는 사람이
# 이미지 내려받기와 기동 시간을 치를 이유가 없다.
POSTGRES_PROFILE = "postgresql"

# 컨테이너가 곧 벤더다 — target 이름 하나로 어느 DBMS인지 정해진다. 덕분에 엔진의
# I/O 함수들은 **시그니처를 바꾸지 않고** 내부에서만 갈라진다. 호출부 12곳은
# 자기가 어느 DBMS를 상대하는지 알 필요가 없다.
TARGET_VENDOR = {"primary": "mysql", "replica": "mysql",
                 "postgres": "postgresql"}
# 순서는 가이드 본문의 벤더 순서이자 exam.py 의 VALID_DBMS 순서와 같다 —
# 두 도구의 선택 화면이 서로 다른 순서를 보이면 같은 저장소처럼 느껴지지 않는다.
VENDORS = ("postgresql", "mysql")
DBMS_TITLES = {"postgresql": "PostgreSQL", "mysql": "MySQL"}

POLL_SECONDS = 2.0        # DB 상태 폴링 주기

# 플레이어 접속 정보. shooting/lab/seed/03-users.sql 및 compose 포트 매핑과 맞춰야 한다.
PLAYER_USER = "dba"
PLAYER_PASSWORD = "shoot"
PLAYER_DB = "shop"
PLAYER_HOST = "127.0.0.1"
# 서버별 게시 포트. compose.yaml의 ports와 맞춰야 한다 — 어긋나면 조용히 엉뚱한
# 서버에 붙는다(테스트가 두 파일을 대조한다).
PLAYER_PORTS = {"primary": "3306", "replica": "3307", "postgres": "5432"}
# -S 긴 줄 자르기 / -F 한 화면이면 즉시 종료 / -X 나갈 때 화면 지우지 않기
CLIENT_PAGER = "less -SFX"
OBJECTIVE_TYPES = ("state", "quiz")
CONSTRAINT_DETECTS = ("container_restart", "kill_precision",
                      "forbidden_command")
EXPECT_OPS = ("eq", "ne", "lt", "lte", "gt", "gte", "contains", "in")
# wait_gtid_sync만 sql 없이 동작한다(대기 자체가 단계의 내용이다).
SETUP_TYPES = ("sql", "session", "sessions", "wait_gtid_sync")

# 엔진의 장부질(로그 비우기)은 binlog에 남기지 않는다.
#
# 이게 없으면 primary에서 도는 `TRUNCATE TABLE mysql.general_log`가 binlog에 실려
# **replica에서도 그대로 실행된다.** 감시는 primary 로그를 먼저 읽고(=비우고)
# replica 로그를 나중에 읽으므로, 그 사이 복제가 전달한 TRUNCATE가 replica의
# 로그를 지워버린다 — 플레이어가 replica에서 친 명령이 매 주기 증발한다.
# 복제를 구성하기 전에는 드러날 수 없던 함정이다.
NO_BINLOG = "SET SESSION sql_log_bin = 0; "

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
    NO_BINLOG +
    "SELECT REPLACE(REPLACE(REPLACE("
    "CONVERT(argument USING utf8mb4), '\\r', ' '), '\\n', ' '), '\\t', ' ') "
    "FROM mysql.general_log "
    # Execute까지 읽는 이유: 준비된 구문(prepared statement)은 Query 행에
    # `EXECUTE k USING @pid`만 남기고, **실제로 실행된 문장**은 Execute 행에
    # 들어간다(`KILL 999999`). Query만 보면 그렇게 친 KILL이 kill_precision에도
    # 타임라인에도 남지 않는다. 평범한 명령은 Query에만 남으므로 중복되지 않는다.
    # Prepare 행(`KILL ?`)은 파라미터가 치환되기 전이라 넣지 않는다.
    "WHERE user_host LIKE 'dba%' AND command_type IN ('Query', 'Execute') "
    # mysql 클라이언트가 접속할 때 자동으로 보내는 배너 질의는 플레이어가 친 게
    # 아니므로 뺀다.
    "AND argument NOT LIKE 'select @@version_comment%' "
    "ORDER BY event_time; "
    "TRUNCATE TABLE mysql.general_log"
)

# PostgreSQL 쪽 같은 것. 판정 구조는 그대로 옮겨오지만 두 가지가 다르다.
#
# 1. 명령 이력이 **테이블이 아니라 로그 파일**이다. file_fdw 외부 테이블로 읽는다
#    (pg-seed/03-logview.sql). 그래서 비우기는 SQL이 아니라 파일 truncate 다.
# 2. `sql_log_off`에 해당하는 것이 **필요 없다.** 엔진은 postgres 계정으로 붙으므로
#    아래 user_name 필터에 자연히 걸러진다. MySQL에서 자기 오염이 문제였던 건
#    감시가 계정이 아니라 같은 서버의 로그 테이블을 통째로 읽기 때문이었다.
PG_LOG_FILE = "/var/lib/postgresql/data/log/pg.csv"
# 랩이 데이터베이스를 따로 만들지 않아 기본 postgres 안의 public 스키마를 쓴다.
# MySQL 쪽 PLAYER_DB('shop')와 이름이 다른 이유다.
PG_PLAYER_DB = "postgres"
PG_ADMIN_USER = "postgres"
PG_PLAYER_LOG_SQL = (
    # 'statement: SELECT 1' 에서 접두사를 떼어 MySQL 쪽과 같은 모양(순수 SQL)으로
    # 맞춘다 — parse_kill_targets 나 forbidden_command 정규식이 그대로 통한다.
    "SELECT regexp_replace("
    "regexp_replace(message, '^(statement|execute [^:]*): ', ''), "
    "'[\\r\\n\\t]', ' ', 'g') "
    "FROM command_log "
    f"WHERE user_name = '{PLAYER_USER}' "
    # execute 를 함께 읽는 이유는 MySQL 쪽 Execute 와 같다 — 준비된 구문의 실제
    # 문장은 'execute <name>: ...' 로 남고 statement 행에는 없다.
    "AND (message LIKE 'statement:%' OR message LIKE 'execute %') "
    "ORDER BY log_time"
)


class LabError(RuntimeError):
    """랩(도커/MySQL) 조작 실패."""


# --------------------------------------------------------------------------- #
# 순수 로직 (UI·도커와 분리 — 테스트 대상)
# --------------------------------------------------------------------------- #
def vendor_of(target):
    """target 서버가 어느 DBMS인가. 모르는 이름은 MySQL로 본다(기존 동작 유지)."""
    return TARGET_VENDOR.get(target, "mysql")


def default_target(stage):
    """스테이지의 기준 서버. `on`을 적지 않은 단계·목표가 여기로 간다.

    MySQL 스테이지는 지금까지처럼 primary 다. PostgreSQL 스테이지에서 이게
    없으면 `on`을 생략한 순간 조용히 MySQL primary 로 가서, 붙지도 않는 서버에
    질의를 던지게 된다.
    """
    return "postgres" if stage.get("dbms") == "postgresql" else "primary"


def normalize_targets(stage):
    """생략된 `on`/`source`를 스테이지 기준 서버로 채운 사본.

    기본값을 **읽는 쪽마다** 풀면 `.get("on", "primary")`가 흩어져 있어 한 군데만
    놓쳐도 조용히 엉뚱한 서버로 간다. 불러올 때 한 번 채워 두면 그 아래로는
    전부 명시값만 보게 된다.
    """
    fallback = default_target(stage)
    if fallback == "primary":
        return stage           # MySQL 스테이지는 손대지 않는다
    out = dict(stage)
    out["setup"] = [dict(st, on=st.get("on", fallback),
                         **({"source": st.get("source", fallback)}
                            if st.get("type") == "wait_gtid_sync" else {}))
                    for st in stage.get("setup") or []]
    out["objectives"] = [dict(o, on=o.get("on", fallback))
                         if o.get("type") == "state" else o
                         for o in stage.get("objectives") or []]
    return out


def pg_psql_command(sql, user=PG_ADMIN_USER, database=PG_PLAYER_DB):
    """컨테이너 안에서 돌릴 psql 인자 목록(순수 함수).

    -X                  ~/.psqlrc 무시. 플레이어가 남긴 설정이 출력 형식을 바꾸면
                        parse_tsv 가 조용히 깨진다.
    -qtA -F'\t'         헤더·정렬·푸터 없이 탭 구분 → `mysql -N -B`와 같은 모양.
    -v ON_ERROR_STOP=1  한 문장이라도 실패하면 종료 코드가 0이 아니게 → LabError.

    주의: 한 -c 에 여러 문장을 넣으면 psql 규칙상 **하나의 트랜잭션**으로 묶인다
    (MySQL은 문장마다 자동 커밋이라 여기서 갈린다). 문장별 커밋이나 트랜잭션
    블록에서 못 도는 명령(VACUUM 등)이 필요하면 setup 의 sql 단계를 나눠라.
    """
    return ["psql", "-U", user, "-d", database, "-X", "-qtA", "-F", "\t",
            "-v", "ON_ERROR_STOP=1", "-c", sql]


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
# PostgreSQL은 세션 종료가 문장이 아니라 **함수**다. 문장 전체를 대조하는 위 정규식
# 으로는 잡히지 않고, 한 문장이 여러 세션을 죽일 수도 있어 findall 로 훑는다.
_PG_KILL_RE = re.compile(
    r"pg_(?:terminate|cancel)_backend\s*\(\s*(\d+)\s*\)", re.IGNORECASE)


def parse_kill_targets(commands):
    """플레이어 명령 목록에서 KILL 대상 스레드 id를 뽑는다.

    `KILL 12` / `KILL CONNECTION 12` / `KILL QUERY 12` 를 모두 인식하고,
    PostgreSQL의 `pg_terminate_backend(12)` / `pg_cancel_backend(12)` 도 읽는다.

    **한계**: PostgreSQL에서는 `SELECT pg_terminate_backend(pid) FROM
    pg_stat_activity WHERE ...` 처럼 pid를 적지 않고 한 문장으로 쓸어버릴 수
    있다. 여기서 뽑는 것은 문장에 적힌 숫자뿐이라 그 방식은 보이지 않는다.
    MySQL에는 그런 형태가 없어 생기지 않던 구멍이다. 그렇게 쓸어버리는 것을
    감점 대상으로 삼으려면 스테이지에서 `forbidden_command` 로 그 모양을 직접
    막아라 — 이쪽은 "범인만 골라 죽였는가"를 보는 도구지 쓸기 탐지기가 아니다.
    """
    out = []
    for cmd in commands or []:
        m = _KILL_RE.match(cmd or "")
        if m:
            out.append(int(m.group(1)))
        out.extend(int(x) for x in _PG_KILL_RE.findall(cmd or ""))
    return out


def kill_session_sql(pid, vendor="mysql"):
    """세션 하나를 끊는 문장(순수 함수)."""
    if vendor == "postgresql":
        return f"SELECT pg_terminate_backend({int(pid)})"
    return f"KILL {int(pid)}"


def count_extra_kills(kill_targets, allowed_pids):
    """범인 외 세션을 KILL 한 횟수.

    상태 검증만으로는 "범인만 정확히 죽이기"와 "전부 쓸어버리기"가 구분되지
    않는다. 이 함수가 결과 대신 방법을 본다.

    양쪽 원소는 그냥 "같은 세션이면 같은 값"이기만 하면 된다. 단일 서버
    스테이지에서는 pid 정수를, 서버가 둘 이상인 스테이지에서는 `(대상, pid)`
    쌍을 넣는다 — pid는 서버마다 따로 매겨지므로 replica의 12번과 primary의
    12번을 같은 세션으로 착각하면 판정이 조용히 틀린다.
    """
    allowed = set(allowed_pids or ())
    return sum(1 for target in (kill_targets or ()) if target not in allowed)


def count_matching_commands(commands, pattern):
    """플레이어 명령 중 정규식에 걸리는 개수. 패턴이 없거나 깨졌으면 0.

    판 도중에 터지지 않게 여기서 삼킨다 — 깨진 패턴은 `validate_stage`가 미리
    잡으므로, 여기까지 온 것은 사람이 손으로 고친 정의뿐이다.
    """
    if not pattern:
        return 0
    try:
        rx = re.compile(pattern)
    except re.error:
        return 0
    return sum(1 for cmd in (commands or ()) if cmd and rx.search(cmd))


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
        elif kind == "forbidden_command":
            # "이 명령으로 때우는 것은 복구가 아니다"를 산문이 아니라 판정으로
            # 만든다. 막지는 않는다 — 제약은 등급만 깎는다.
            hits = count_matching_commands(ctx.get("commands"),
                                           c.get("pattern"))
            if hits > c.get("max_matches", 0):
                found.append({"id": c.get("id"), "label": label,
                              "detail": f"해당 명령을 {hits}회 사용했습니다"})
    return found


def fmt_mmss(seconds):
    """초 → MM:SS."""
    total = max(0, int(seconds or 0))
    return f"{total // 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------- #
# 포스트모템 타임라인 (순수 로직)
# --------------------------------------------------------------------------- #
def record_event(session, kind, text, at, unique=False):
    """타임라인 이벤트를 기록한다 → 실제로 추가됐는지.

    `unique=True`면 같은 (kind, text)를 한 번만 남긴다 — 금지 행동 위반은 폴링마다
    다시 감지되므로 그대로 두면 타임라인이 같은 줄로 도배된다. 반대로 플레이어
    명령은 같은 질의를 두 번 친 것도 의미가 있으므로 중복을 허용한다.
    """
    events = session.setdefault("events", [])
    if unique and any(e["kind"] == kind and e["text"] == text for e in events):
        return False
    events.append({"at": max(0.0, float(at)), "kind": kind, "text": text})
    return True


def elapsed_of(session):
    """세션 시작 이후 경과 초."""
    return time.monotonic() - session["started"]


def note_path(stage_id, stamp, rank):
    """노트 저장 경로. 파일명만으로 스테이지·시각·등급이 읽힌다."""
    return NOTES_DIR / stage_id / f"{stamp}-{rank}.md"


def collect_notes(notes_dir, current_stage_id=None):
    """과거 노트 경로 목록 — 현재 스테이지 먼저, 각 그룹 안에서는 최신순.

    "다음 스테이지에서 지난 기록을 꺼내 본다"가 이 정렬의 이유다.
    """
    base = Path(notes_dir)
    if not base.is_dir():
        return []
    paths = sorted(base.glob("*/*.md"),
                   key=lambda p: (p.parent.name != (current_stage_id or ""),
                                  p.parent.name,
                                  p.name),
                   reverse=False)
    # 같은 스테이지 안에서는 최신이 먼저 오도록 뒤집는다.
    out = []
    for stage_id in dict.fromkeys(p.parent.name for p in paths):
        same = [p for p in paths if p.parent.name == stage_id]
        out.extend(sorted(same, key=lambda p: p.name, reverse=True))
    return out


def note_heading(path):
    """`less`로 이어 붙일 때 각 노트 앞에 넣을 구분선."""
    stem = Path(path).stem                     # <YYYYMMDD-HHMMSS>-<rank>
    stamp, _, rank = stem.rpartition("-")
    return (f"{'═' * 70}\n"
            f"  {Path(path).parent.name}   {stamp}   RANK {rank}\n"
            f"{'═' * 70}")


_EVENT_MARK = {"incident": "🔥", "command": ">", "objective": "✓",
               "quiz": "?", "hint": "…", "violation": "!"}


def build_note(stage, session, result, today):
    """세션에서 포스트모템 초안(Markdown)을 만든다.

    `03-advanced/09-incident-response-and-postmortem.md`가 가르치는 템플릿을
    그대로 따른다 — 장애 직후 그 템플릿으로 회고를 쓰는 것이 그 챕터의 실습이다.

    **관찰된 사실만 채우고 분석은 비워 둔다.** 근본 원인·5 Whys·재발 방지를
    미리 채워주면 회고 연습이 되지 않는다. 스테이지의 정답 해설(debrief)은
    노트를 쓴 뒤에 보여준다.
    """
    correct, total = quiz_totals(stage, session)
    out = [f"# 포스트모템: {today} {stage.get('title', '')}"
           f" ({stage.get('id', '')})", ""]

    target = stage.get("target_seconds")
    out += ["## 요약",
            f"- 영향: {fmt_mmss(result['elapsed'])} 동안 대응 "
            + (f"(목표 {fmt_mmss(target)})" if target else "(목표 없음)"),
            f"- 등급: {result['rank']} ({result['score']}/4)",
            "- 근본 원인: <!-- 직접 채우세요 -->"]
    if stage.get("_seed") is not None:
        # 변주가 있는 스테이지는 시드가 없으면 이 노트가 어떤 판을 말하는지
        # 나중에 알 수 없다.
        out.append(f"- 이 판 재현: `./shoot {stage.get('id')} "
                   f"--seed {stage['_seed']}`")
    out.append("")

    out.append("## 타임라인")
    for e in sorted(session.get("events") or [], key=lambda x: x["at"]):
        mark = _EVENT_MARK.get(e["kind"], "-")
        out.append(f"- {fmt_mmss(e['at'])} {mark} {e['text']}")
    out.append("")

    out += ["## 근본 원인 분석 (5 Whys)"]
    out += [f"{i}. 왜? → " for i in range(1, 6)]
    out.append("")

    out.append("## 잘된 점 (What went well)")
    for o in stage["objectives"]:
        st = session["states"][o["id"]]
        # 틀린 진단은 '달성'했어도 잘된 점이 아니다 — 아래 '아쉬운 점'으로 간다.
        # 물어보지 못하고 건너뛴 문항도 마찬가지다(회고가 거짓말을 하면 안 된다).
        if st["done"] and st.get("correct") is not False and not st.get("skipped"):
            out.append(f"- {o.get('label', o['id'])}")
    if stage.get("target_seconds") and result["elapsed"] <= stage["target_seconds"]:
        out.append("- 목표 시간 안에 복구했다")
    out.append("- <!-- 더 있으면 추가하세요 -->")
    out.append("")

    # 오답노트는 별도 문서로 분리하지 않고 여기에 녹인다 —
    # 회고 하나로 통합하는 편이 나중에 다시 볼 때 쓸모 있다.
    out.append("## 아쉬운 점 (What went wrong)")
    wrong = False
    for obj in stage["objectives"]:
        st = session["states"][obj["id"]]
        if obj["type"] != "quiz" or st.get("correct") is not False:
            continue
        wrong = True
        q = obj["question"]
        out.append(f"- **오답** «{obj.get('label', obj['id'])}» {q['q']}")
        if q.get("type") == "mcq":
            out.append(f"  - 정답: {q['choices'][q['answer']]}")
        elif q.get("accept"):
            out.append(f"  - 정답: {q['accept'][0]}")
        if q.get("explain"):
            out.append(f"  - {q['explain']}")
    for v in session.get("violations") or []:
        wrong = True
        out.append(f"- **금지 행동** {v['label']} — {v['detail']}")
    if session.get("hints_used"):
        wrong = True
        out.append(f"- 힌트 {session['hints_used']}회 사용")
    if not wrong:
        out.append("- <!-- 직접 채우세요 -->")
    out.append("")

    out += ["## 재발 방지 액션 아이템",
            "- [ ] <!-- 담당자·기한 없이는 실행되지 않는다 -->", "",
            "## 비난 없음(Blameless) 노트", "", ""]

    out += ["---",
            f"진단 정확도 {correct}/{total} · "
            f"금지 행동 {len(session.get('violations') or [])}건 · "
            f"힌트 {session.get('hints_used', 0)}회",
            "이 노트는 `n` 키로 다음 스테이지에서도 다시 꺼내 볼 수 있습니다.",
            # 포기한 판에는 해설('더 읽을 곳')이 붙지 않는다. 회고를 쓰다 막히는
            # 것은 그때가 더 흔하므로, 템플릿의 출처는 노트 자체가 들고 있는다.
            f"위 템플릿과 5 Whys는 {POSTMORTEM_CHAPTER} 의 실습입니다."]
    return "\n".join(out) + "\n"


DEBRIEF_MARKER = "## 스테이지 해설 (공식)"


def debrief_section(stage):
    """노트 끝에 덧붙일 해설 절(Markdown). `debrief`가 없으면 None.

    **초안에는 넣지 않는다.** 편집기를 닫은 뒤에 덧붙여야 근본 원인·5 Whys를
    스스로 쓰게 되고, 그러면서도 나중에 다시 꺼내 볼 때는 내 분석과 공식 해설이
    한 문서에 나란히 남는다.
    """
    body = (stage.get("debrief") or "").strip()
    if not body:
        return None
    return "\n".join([
        "", "---", "", DEBRIEF_MARKER, "",
        "<!-- 노트를 쓴 뒤에 덧붙었습니다. 위에 쓴 내 분석과 대조해 보세요. -->",
        "", body, "",
        "## 대조 메모", "",
        "<!-- 내 5 Whys와 공식 해설이 어긋난 지점을 적어두면 다음에 도움이 됩니다. -->",
        "", ""])


def client_targets(stage):
    """`c` 키로 붙을 수 있는 서버 목록. primary가 먼저 온다.

    스테이지가 실제로 건드리는 서버만 내놓는다 — 락 스테이지에서 replica를
    제시하면 갈 이유 없는 선택지가 하나 늘 뿐이다.
    """
    targets = watch_targets(stage)
    first = default_target(stage)
    return [first] + sorted(t for t in targets if t != first)


def client_command(stage, pager=None, target="primary"):
    """대화형 DB 클라이언트 인자 목록. target 에 따라 mysql / psql 이 된다.

    직접 만든 콘솔을 대체한다. readline·히스토리·컬럼 완성·페이저·자동 세로
    출력을 전부 클라이언트가 제공하므로 우리가 다시 만들 이유가 없다.

    `--auto-rehash`(스키마·테이블·컬럼 완성)는 기본값이라 주지 않는다.

    `target`은 붙을 서버다. 복제 스테이지에서는 범인이 replica에 있을 수 있어
    primary만으로는 현장에 갈 수 없다. 판정은 달라지지 않는다 — 어느 포트로 붙든
    `dba` 계정이므로 `general_log` 귀속이 같고, 명령 로그는 이미 서버별로 읽는다.
    """
    # 서버가 하나뿐인 스테이지에서는 대상 표기가 잡음이므로 붙이지 않는다.
    label = (f"{stage.get('id', 'shoot')}@{target}"
             if len(client_targets(stage)) > 1 else stage.get("id", "shoot"))
    port = PLAYER_PORTS.get(target, PLAYER_PORTS["primary"])
    if vendor_of(target) == "postgresql":
        # 페이저는 인자가 아니라 PAGER 환경변수로 준다(client_env 참고).
        return [
            "psql", "-h", PLAYER_HOST, "-p", port,
            "-U", PLAYER_USER, "-d", PG_PLAYER_DB,
            # 행이 터미널보다 넓으면 알아서 세로로 바꾼다(mysql 의 auto-vertical).
            "-P", "expanded=auto",
            "-v", f"PROMPT1=[{label}] psql> ",
            "-v", f"PROMPT2=[{label}] psql| ",
        ]
    cmd = [
        "mysql",
        f"-h{PLAYER_HOST}", f"-P{port}",
        f"-u{PLAYER_USER}", f"-D{PLAYER_DB}",
        # 행이 터미널보다 넓으면 알아서 \G 세로 출력으로 바꾼다.
        "--auto-vertical-output",
        f"--prompt=[{label}] mysql> ",
    ]
    if pager:
        cmd.append(f"--pager={pager}")
    return cmd


def client_name(stage):
    """이 스테이지에서 `c` 키가 실제로 띄우는 클라이언트 이름.

    화면 라벨은 반드시 이걸로 만든다 — `client_command()`가 벤더에 따라 고르는
    바이너리와 라벨이 각자 판단하면 조용히 갈라진다(실제로 갈라져 있었다).
    """
    return "psql" if vendor_of(default_target(stage)) == "postgresql" else "mysql"


def client_error_hint(target):
    """클라이언트 실행이 실패했을 때 보여줄 안내(순수 함수).

    MySQL 쪽 함정 — 사용자의 `~/.my.cnf`에 `password=`가 있으면 `MYSQL_PWD`를
    이겨 인증을 가로챈다 — 은 psql 과 아무 상관이 없다. 없는 함정을 지어내는 대신
    PostgreSQL 쪽은 확인 가능한 것만 안내한다.
    """
    if vendor_of(target) == "postgresql":
        return ("접속에 실패했습니다. PostgreSQL 랩이 떠 있는지 확인하세요 —\n"
                "`./shoot doctor` 로 점검하고, 없으면\n"
                "`./shoot up --with-postgresql` 로 띄웁니다.")
    return ("접속에 실패했습니다. ~/.my.cnf 에 password= 설정이 있으면\n"
            "MYSQL_PWD 보다 우선해 인증을 가로챌 수 있습니다\n"
            "(MySQL 옵션 우선순위: 명령줄 > 옵션 파일 > 환경변수).")


def play_footer(stage, session):
    """플레이 화면 하단 바 문구(순수 함수).

    문자열을 `_draw_play` 안에 두면 "화면이 실제로 무엇을 안내하는가"를 테스트할
    수 없다 — 라벨이 벤더를 따라가는지 지키려면 밖으로 꺼내야 한다.
    """
    hints = stage.get("hints") or []
    notes = session.get("notes_count", 0)
    return (f" c {client_name(stage)} 접속   r 상황 보고   "
            f"n 지난 기록({notes})   "
            f"h 힌트({session['hints_used']}/{len(hints)})   q 포기 ")


def client_env(target, pager=None):
    """클라이언트에 넘길 환경변수. 비밀번호를 명령줄에 노출하지 않는다."""
    env = dict(os.environ)
    if vendor_of(target) == "postgresql":
        env["PGPASSWORD"] = PLAYER_PASSWORD
        if pager:
            env["PAGER"] = pager
    else:
        env["MYSQL_PWD"] = PLAYER_PASSWORD
    return env


# --------------------------------------------------------------------------- #
# 감시 단계 계획 (순수 로직)
# --------------------------------------------------------------------------- #
# 감시를 한 번에 몰아서 하면 docker 호출 4개가 연달아 일어나 UI가 ~240ms 멈춘다.
# 그동안 키 입력이 처리되지 않아 "키가 안 먹는다"처럼 느껴진다.
# 그래서 루프 한 바퀴에 **단계 하나**(docker 호출 1회, ~70ms)만 수행한다.
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def watch_targets(stage):
    """스테이지가 건드리는 컨테이너 이름 집합.

    목표(`objectives`)의 대상까지 넣는 이유는, 장애를 주입한 서버와 플레이어가
    손을 대야 하는 서버가 다를 수 있기 때문이다 — 복제 스테이지가 그렇다.
    여기 빠진 서버는 명령 로그도 재시작도 감시되지 않는다.
    """
    fallback = default_target(stage)
    targets = {fallback}
    for step in stage.get("setup") or []:
        targets.add(step.get("on", fallback))
    for obj in stage.get("objectives") or []:
        if obj.get("type") == "state":
            targets.add(obj.get("on", fallback))
    return targets


def watch_steps(stage):
    """감시 단계 목록. 한 단계 = docker 호출 1회."""
    steps = []
    for obj in stage.get("objectives") or []:
        if obj.get("type") == "state":
            steps.append({"kind": "state", "id": obj["id"],
                          "label": obj.get("label", obj["id"])})
    # 명령 로그는 서버마다 따로 있다. primary만 읽으면 플레이어가 replica에서
    # 한 일이 통째로 보이지 않아, 그 서버에 범인이 있는 스테이지에서
    # kill_precision이 영원히 걸리지 않고 포스트모템 타임라인도 비어버린다.
    for target in sorted(watch_targets(stage)):
        steps.append({"kind": "commands", "target": target,
                      "label": f"{target} 명령 로그"})
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

    **기준이 없는 항목은 감점하지 않는다.** `target_seconds`가 없으면 시간
    보너스를, 문항이 없으면 진단 보너스를 그냥 준다. 시간 제한을 두지 않는
    스테이지(구축형 등)를 만들 수 있어야 하는데, 없다고 해서 S가 구조적으로
    불가능해지면 그 선택지가 사라진다. `validate_stage`에서 필수로 막지 않는
    것도 같은 이유다.
    """
    timed = bool(target_seconds)
    items = [
        ("소요 시간",
         f"{fmt_mmss(elapsed)} (목표 {fmt_mmss(target_seconds)})" if timed
         else f"{fmt_mmss(elapsed)} (목표 없음)",
         not timed or elapsed <= target_seconds),
        ("힌트 사용", f"{hints_used}회", hints_used == 0),
        ("금지 행동", f"{violations}건", violations == 0),
        ("진단 정확도", f"{quiz_correct}/{quiz_total}",
         quiz_total == 0 or quiz_correct == quiz_total),
    ]
    score = sum(1 for _, _, ok in items if ok)
    return items, score, rank_for(score)


# --------------------------------------------------------------------------- #
# 스테이지 파라미터 변주 (순수 로직)
# --------------------------------------------------------------------------- #
# 같은 스테이지를 두 번째로 하면 진단이 아니라 정답 암기가 된다. 그래서 숫자를
# 흔든다 — 다만 **장애의 성격은 고정한다.** 범인의 종류나 위치가 바뀌면 quiz 정답과
# hints·debrief가 전부 거짓이 되므로, 그건 변주가 아니라 새 스테이지다.
#
# 어려운 부분은 판정 근거가 함께 움직여야 한다는 것이다. 잠긴 구간을 흔들면 목표가
# 보는 행도 그 안으로 따라와야 한다. 파생값을 `{{lock.from + 250}}` 같은 식으로
# 쓰려면 표현식 평가기가 필요한데, 표준 라이브러리만 쓰는 이 저장소에 그건 과한
# 기계장치다. 대신 **관계를 타입으로 선언한다** — `int_in`이 그 역할을 한다.
VAR_TYPES = ("int", "span", "int_in", "choice")
# 이름에 제한을 두지 않는다. ASCII만 받으면 `{{잠금구간}}` 같은 이름은 자리로
# 인식조차 되지 않아, 치환도 안 되고 오류도 안 난 채 원문이 화면에 그대로 뜬다.
# 한국어 저장소에서 반드시 밟게 되는 함정이라 무엇이든 받아서 이름을 검증한다.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _is_placeholder(value):
    """아직 렌더링되지 않은 변수 자리인가.

    `count` 같은 숫자 필드도 변주 대상이라, 검증 시점에는 값이 `{{n}}`일 수 있다.
    그 자리에서 int()로 읽으려 하면 정의를 못 읽고 죽는다.
    """
    return isinstance(value, str) and bool(_PLACEHOLDER_RE.search(value))


def make_vars(spec, rng):
    """`vars` 선언 → 치환값 사전. `span`은 `<이름>.from`/`.to`로 펼쳐진다.

    이름순으로 뽑으므로 JSON 안의 선언 순서를 바꿔도 같은 시드면 같은 결과가
    나온다 — 그러지 않으면 키 하나 옮긴 것만으로 재현이 깨진다.
    """
    spec = spec or {}
    values, spans = {}, {}
    # span을 먼저 만들어야 int_in이 참조할 수 있다.
    for name in sorted(n for n, d in spec.items() if d.get("type") == "span"):
        decl = spec[name]
        length = int(decl["length"])
        start = rng.randint(int(decl["min"]), int(decl["max"]) - length + 1)
        spans[name] = (start, start + length - 1)
        values[f"{name}.from"], values[f"{name}.to"] = spans[name]
    for name in sorted(n for n, d in spec.items() if d.get("type") != "span"):
        decl = spec[name]
        kind = decl.get("type")
        if kind == "int":
            values[name] = rng.randint(int(decl["min"]), int(decl["max"]))
        elif kind == "choice":
            values[name] = rng.choice(list(decl["values"]))
        elif kind == "int_in":
            low, high = spans[decl["of"]]
            values[name] = rng.randint(low, high)
    return values


def _render_value(node, values):
    """중첩 구조 안의 모든 문자열에서 `{{이름}}`을 바꾼다."""
    if isinstance(node, str):
        return _PLACEHOLDER_RE.sub(
            lambda m: str(values.get(m.group(1), m.group(0))), node)
    if isinstance(node, list):
        return [_render_value(v, values) for v in node]
    if isinstance(node, dict):
        return {k: _render_value(v, values) for k, v in node.items()}
    return node


# `sessions` 단계가 세션마다 채워 주는 이름. 스테이지의 `vars` 이름과 부딪히지
# 않도록 길게 잡았다 — `{{i}}` 였다면 흔한 변수 이름과 겹친다.
SESSION_INDEX_VAR = "session_index"


def render_session_sql(sql, index):
    """`sessions` 단계 SQL의 `{{session_index}}`를 세션 번호(0-기반)로 바꾼다.

    `render_stage`는 로드 시점에 `vars`만 치환하고 **모르는 자리는 원문 그대로
    통과시킨다.** 그래서 이 이름은 손대지 않은 채 `setup_stage`까지 살아남고,
    세션을 실제로 띄우는 그 자리에서야 자기 번호를 받는다.

    남은 다른 자리는 건드리지 않는다 — 먹어버리면 SQL이 조용히 깨진다.
    """
    return _render_value(sql, {SESSION_INDEX_VAR: index})


def render_stage(stage, rng):
    """`vars`를 뽑아 스테이지 사본의 `{{...}}`를 실제 값으로 바꾼다.

    `vars`가 없으면 원본을 그대로 돌려준다 — 기존 스테이지는 아무 영향도 받지 않는다.
    원본은 건드리지 않는다(사본을 만든다).

    `expect.value`가 숫자여야 하는 자리에 문자열이 들어가도 괜찮다. `coerce()`가
    비교 전에 숫자로 되돌린다.
    """
    if not stage.get("vars"):
        return stage
    return _render_value(stage, make_vars(stage["vars"], rng))


def _var_names(spec):
    """치환에 쓸 수 있는 이름 집합."""
    names = set()
    for name, decl in (spec or {}).items():
        if decl.get("type") == "span":
            names |= {f"{name}.from", f"{name}.to"}
        else:
            names.add(name)
    return names


def _placeholders_in(node):
    """중첩 구조 전체에서 참조된 `{{이름}}`을 모은다."""
    if isinstance(node, str):
        return set(_PLACEHOLDER_RE.findall(node))
    if isinstance(node, list):
        return set().union(*(_placeholders_in(v) for v in node)) if node else set()
    if isinstance(node, dict):
        out = set()
        for key, value in node.items():
            if key != "vars":          # 선언 자체는 참조가 아니다
                out |= _placeholders_in(value)
        return out
    return set()


def _stage_outside_session_sql(stage):
    """`sessions` 단계의 `sql`만 뺀 스테이지 사본.

    `{{session_index}}`가 허용되는 자리는 그 하나뿐이므로, 그 자리를 지운 사본에서
    이름이 또 나오면 전부 잘못 쓴 것이다. 자리 정보를 잃는 `_placeholders_in`을
    그대로 재사용하기 위한 방법이다.
    """
    setup = [{k: v for k, v in step.items() if k != "sql"}
             if step.get("type") == "sessions" else step
             for step in stage.get("setup") or []]
    return dict(stage, setup=setup)


def _validate_vars(stage):
    """`vars` 선언과 `{{...}}` 참조의 형식 오류 목록."""
    spec = stage.get("vars")
    errs = []
    for name, decl in (spec or {}).items():
        if not isinstance(decl, dict):
            errs.append(f"vars '{name}': 객체가 아닙니다")
            continue
        kind = decl.get("type")
        if kind not in VAR_TYPES:
            errs.append(f"vars '{name}': 알 수 없는 type '{kind}'")
            continue
        if kind == "span":
            length = int(decl.get("length", 0))
            if length < 1:
                errs.append(f"vars '{name}': span의 length는 1 이상이어야 합니다")
            elif length > int(decl.get("max", 0)) - int(decl.get("min", 0)) + 1:
                errs.append(f"vars '{name}': span의 length가 min~max 범위보다 큽니다")
        elif kind == "int_in":
            of = decl.get("of")
            if (spec.get(of) or {}).get("type") != "span":
                errs.append(f"vars '{name}': int_in의 of는 span이어야 합니다 "
                            f"('{of}')")
        elif kind == "choice" and not (decl.get("values") or []):
            errs.append(f"vars '{name}': choice의 values가 비었습니다")
        elif kind == "int" and int(decl.get("min", 0)) > int(decl.get("max", 0)):
            errs.append(f"vars '{name}': int의 min이 max보다 큽니다")

    if SESSION_INDEX_VAR in (spec or {}):
        errs.append(f"vars '{SESSION_INDEX_VAR}': 엔진이 예약한 이름입니다 "
                    f"(sessions 단계가 세션 번호로 채웁니다)")

    # 예약 이름은 vars 에 없어도 '정의되지 않은 변수'가 아니다.
    known = _var_names(spec) | {SESSION_INDEX_VAR}
    for ref in sorted(_placeholders_in(stage) - known):
        errs.append(f"정의되지 않은 변수를 참조합니다: {{{{{ref}}}}}")

    # 대신 자리를 좁게 막는다 — 허용된 자리 밖에서는 영원히 치환되지 않으므로,
    # 원문 `{{session_index}}`가 그대로 SQL이나 플레이어 화면에 나간다.
    if SESSION_INDEX_VAR in _placeholders_in(_stage_outside_session_sql(stage)):
        errs.append(f"{{{{{SESSION_INDEX_VAR}}}}}는 type이 sessions인 setup 단계의 "
                    f"sql 에서만 쓸 수 있습니다 (다른 자리에서는 치환되지 않습니다)")
    return errs


# 모든 스테이지가 끝나면 회고를 쓴다 — `build_note()`가 만드는 초안의 템플릿과
# 빈칸으로 남긴 5 Whys가 바로 이 챕터의 실습이다. 그래서 이 링크는 어느 한
# 스테이지의 주제가 아니라 **게임의 구조**에 딸려 있다. 스테이지 14개의
# `chapters`에 적어 넣으면 같은 상수를 14벌 복사하는 것이고, `connect_hint`가
# 정확히 그렇게 썩었다.
POSTMORTEM_CHAPTER = "03-advanced/09-incident-response-and-postmortem.md"


def chapter_title(repo_root, rel):
    """챕터 파일의 첫 제목(`# …`). 못 읽으면 None.

    파일명이 아니라 제목을 보여줘야 '무엇을 더 읽으라는 것인지'가 전달된다.
    """
    try:
        with open(Path(repo_root) / rel, encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return None


def chapter_reading_list(stage, repo_root=None):
    """클리어 후 보여줄 '더 읽을 곳'.

    읽기(챕터) → 확인(`./exam`) → 겪기(`./shoot`)에서 마지막 축만 앞의 둘과
    끊겨 있었다. 장애를 막 겪은 직후가 그 주제를 읽기에 가장 좋은 때다.

    회고 챕터는 스테이지가 아니라 **게임의 구조**에 딸려 있어 항상 붙는다
    (`POSTMORTEM_CHAPTER` 주석 참고).
    """
    chapters = list(stage.get("chapters") or [])
    if POSTMORTEM_CHAPTER not in chapters:
        chapters.append(POSTMORTEM_CHAPTER)
    root = repo_root or REPO_ROOT
    lines = []
    for rel in chapters:
        title = chapter_title(root, rel)
        lines.append(f"    - {title} ({rel})" if title else f"    - {rel}")
    return "\n".join(lines)


def _validate_chapters(stage, repo_root):
    """`chapters` 형식과(루트를 주면) 존재 여부."""
    chapters = stage.get("chapters")
    if chapters is None:
        return []
    if not isinstance(chapters, list):
        return ["chapters: 목록이어야 합니다"]
    errs = []
    for rel in chapters:
        if not isinstance(rel, str) or not rel.endswith(".md"):
            errs.append(f"chapters: 마크다운 경로가 아닙니다 ({rel!r})")
            continue
        # 저장소 밖을 가리키면 다른 사람 기계에서 깨진다.
        if rel.startswith("/") or ".." in Path(rel).parts:
            errs.append(f"chapters: 저장소 안의 상대 경로여야 합니다 ({rel})")
            continue
        if repo_root and not (Path(repo_root) / rel).is_file():
            errs.append(f"chapters: 없는 파일입니다 ({rel})")
    return errs


def validate_stage(stage, repo_root=None):
    """스테이지 정의의 형식 오류 목록. 정상이면 빈 리스트.

    exam.py의 validate_bank와 같은 역할 — 콘텐츠 작성 실수를 실행 전에 잡는다.
    """
    errs = []
    if not isinstance(stage, dict):
        return ["스테이지가 객체(JSON object)가 아닙니다"]
    for field in ("id", "title", "objectives"):
        if not stage.get(field):
            errs.append(f"필수 필드 누락: {field}")

    if "connect_hint" in stage:
        # 조용히 무시하면 적어 넣은 사람은 왜 화면에 안 뜨는지 알 길이 없다.
        errs.append("connect_hint: 더 이상 쓰지 않습니다 — 접속 명령은 "
                    "PLAYER_* 상수에서 만들어집니다(손으로 적은 13벌이 "
                    "전부 어긋나 있었습니다)")

    dbms = stage.get("dbms")
    if dbms and dbms not in VENDORS:
        errs.append(f"알 수 없는 dbms '{dbms}' (가능: {', '.join(VENDORS)})")
    else:
        # 대상 서버가 스테이지의 DBMS와 어긋나면 조용히 엉뚱한 서버에 붙는다.
        # 스테이지를 쓰다 `on`을 빠뜨리거나 잘못 적기 쉬운 자리라 여기서 막는다.
        want = "postgresql" if dbms == "postgresql" else "mysql"
        for t in sorted(watch_targets(stage)):
            if t in CONTAINERS and vendor_of(t) != want:
                errs.append(f"dbms가 {want}인데 대상 '{t}'는 "
                            f"{vendor_of(t)} 서버입니다")

    for step in stage.get("setup") or []:
        stype = step.get("type")
        if stype not in SETUP_TYPES:
            errs.append(f"setup: 알 수 없는 type '{stype}'")
        if step.get("on") and step["on"] not in CONTAINERS:
            errs.append(f"setup: 알 수 없는 대상 '{step['on']}'")
        if stype == "wait_gtid_sync":
            if step.get("source") and step["source"] not in CONTAINERS:
                errs.append(f"setup: 알 수 없는 source '{step['source']}'")
        elif not step.get("sql"):
            errs.append(f"setup: sql이 비었습니다 ({stype})")
        if stype == "sessions" and not _is_placeholder(step.get("count")):
            try:
                if int(step.get("count", 0)) < 1:
                    errs.append("setup: sessions의 count는 1 이상이어야 합니다")
            except (TypeError, ValueError):
                errs.append("setup: sessions의 count가 숫자가 아닙니다")

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
        detect = c.get("detect")
        if detect not in CONSTRAINT_DETECTS:
            errs.append(f"constraint '{c.get('id')}': 알 수 없는 detect")
        elif detect == "forbidden_command":
            pattern = c.get("pattern")
            if not pattern:
                errs.append(f"constraint '{c.get('id')}': pattern이 필요합니다")
            else:
                try:
                    re.compile(pattern)
                except re.error as e:
                    errs.append(f"constraint '{c.get('id')}': "
                                f"pattern이 정규식으로 읽히지 않습니다 ({e})")

    errs.extend(_validate_vars(stage))
    errs.extend(_validate_chapters(stage, repo_root))
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
    # 챕터 링크는 실제 파일을 확인한다 — 챕터 파일명이 바뀌면 조용히 썩기 때문이다.
    errs = validate_stage(stage, repo_root=REPO_ROOT)
    if errs:
        raise ValueError(f"{path}: " + "; ".join(errs))
    return normalize_targets(stage)


# --------------------------------------------------------------------------- #
# 도커 / MySQL I/O (얇게 유지 — 여기는 테스트하지 않는다)
# --------------------------------------------------------------------------- #
DOCKER_MISSING = ("docker CLI를 찾을 수 없습니다 — Docker Desktop을 설치하고 "
                  "PATH에 docker가 있는지 확인하세요.")


def docker_available():
    """docker CLI가 PATH에 있는가.

    `_docker`가 던지는 것은 잡을 수 있지만, 진단(`./shoot doctor`)은 "없다"와
    "있지만 안 떠 있다"를 구분해 보여줘야 하므로 먼저 물어볼 수단이 필요하다.
    """
    return shutil.which("docker") is not None


def _docker(*args, timeout=60):
    # docker가 없으면 FileNotFoundError가 그대로 올라가 트레이스백으로 죽는다.
    # LabError로 승격해 두면 이미 그것을 잡고 있는 호출자들이 그대로 살아난다.
    #
    # errors="replace"가 없으면 UTF-8로 못 읽는 바이트 하나에 판이 통째로 죽는다.
    # 감시는 플레이어가 친 명령을 general_log에서 그대로 읽어오는데, 그 안에
    # 어떤 바이트가 들어올지 엔진이 통제할 수 없다(멀티바이트 문자가 잘려 들어오는
    # 경우가 실제로 있었다). 글자 하나 깨지는 것이 판을 끝내는 것보다 낫다.
    try:
        return subprocess.run(["docker", *args], capture_output=True,
                              text=True, errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise LabError(DOCKER_MISSING) from None


def _compose(*args, timeout=600):
    try:
        return subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
            capture_output=True, text=True, errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise LabError(DOCKER_MISSING) from None


def db_query(target, sql, timeout=30, log_off=True):
    """엔진 전용 관리자 질의 → 행 목록. target이 어느 DBMS인지는 여기서 갈린다.

    자격증명은 컨테이너 안 /root/.my.cnf 에서 읽으므로 명령줄에 비밀번호가
    노출되지 않는다.

    `log_off`가 켜져 있으면 이 세션의 질의를 general_log에서 뺀다. 이게 없으면
    엔진이 **자기가 읽어야 할 로그를 자기가 오염시킨다** — mysql.general_log는
    CSV 엔진이라 인덱스가 없어 조회할 때마다 전체 스캔인데, 2초마다 도는 폴링
    질의가 계속 쌓이면서 다음 폴링이 점점 느려지는 자기 증폭 구조가 된다.
    """
    container = CONTAINERS.get(target, target)
    if vendor_of(target) == "postgresql":
        # log_off 는 여기서 의미가 없다 — 엔진이 postgres 계정으로 붙으므로
        # 감시 질의의 user_name 필터가 이미 걸러낸다(PG_PLAYER_LOG_SQL 참고).
        p = _docker("exec", container, *pg_psql_command(sql), timeout=timeout)
    else:
        if log_off:
            sql = "SET SESSION sql_log_off = 1; " + sql
        p = _docker("exec", container, "mysql", "-N", "-B", "-e", sql,
                    timeout=timeout)
    if p.returncode != 0:
        raise LabError((p.stderr or "").strip() or f"{container}: 질의 실패")
    return parse_tsv(p.stdout)


def spawn_command(user, sql, idle_seconds=0, vendor="mysql"):
    """장애 주입 세션을 띄울 컨테이너 안 명령(순수 함수).

    `--no-defaults`가 꼭 필요하다 — MySQL 클라이언트의 옵션 우선순위는
    명령줄 > 옵션 파일 > 환경변수라서, /root/.my.cnf 의 root 비밀번호가
    MYSQL_PWD를 이겨버린다. 그러면 app 계정 인증이 조용히 실패한다.

    `idle_seconds`를 주면 sql을 실행한 뒤 그만큼 **접속만 붙잡고 논다**
    (`Command=Sleep`). 커넥션 풀 누수를 재현하려면 이게 필요하다 —
    `mysql -e`는 질의가 끝나면 바로 끊고, `SELECT SLEEP()`은 `Command=Query`로
    잡혀 진짜 유휴 커넥션과 구분되지 않는다. 파이프가 열려 있는 동안 클라이언트가
    stdin을 기다리므로 서버 쪽에서 유휴로 보인다.
    """
    if vendor == "postgresql":
        # -h 로 TCP를 강제한다. 유닉스 소켓으로 가면 로컬 신뢰 인증에 걸려
        # **PGPASSWORD 없이도 붙어버리고**, 계정을 틀려도 알아채지 못한다.
        base = ["psql", "-h", "127.0.0.1", "-U", user, "-d", PG_PLAYER_DB,
                "-X", "-q", "-v", "ON_ERROR_STOP=1"]
        rest = ["-c", sql]
    else:
        base = ["mysql", "--no-defaults", "-u", user]
        rest = ["-e", sql]
    if not idle_seconds:
        return base + rest
    # 스테이지 SQL에는 따옴표가 흔하다 — 셸에 그대로 넘기면 깨진다.
    tail = ("" if vendor == "postgresql" else f" -D {PLAYER_DB}")
    script = (f"{{ printf '%s;\\n' {shlex.quote(sql)}; "
              f"sleep {int(idle_seconds)}; }} | "
              + " ".join(base) + tail)
    return ["sh", "-c", script]


def db_spawn(target, user, password, sql, idle_seconds=0):
    """장애 주입용 분리(detached) 세션을 띄운다."""
    container = CONTAINERS.get(target, target)
    vendor = vendor_of(target)
    env = "PGPASSWORD" if vendor == "postgresql" else "MYSQL_PWD"
    _docker("exec", "-d", "-e", f"{env}={password}", container,
            *spawn_command(user, sql, idle_seconds, vendor))


def run_in_terminal(stdscr, curses, cmd, env=None, on_error=None, banner=None):
    """curses를 잠시 내리고 외부 도구를 이 터미널에 띄운다 → 종료 코드.

    편집기·페이저·DB 클라이언트를 curses 안에 다시 만들지 않기 위한 공용 통로다.
    직접 만들면 readline·검색·히스토리를 전부 재구현하게 되고 결과는 늘 원본보다
    못하다(실제로 SQL 콘솔에서 그렇게 됐다).

    `banner`는 **반드시 endwin() 뒤에** 찍는다 — curses가 화면을 잡고 있는 동안
    print()로 쓰면 curses의 화면 모델 밖에서 터미널에 직접 나가 게임 화면 위에
    겹쳐 찍힌다. 그래서 문자열로 받아 여기서 출력한다.

    종료 코드가 0이 아니면 화면을 지우기 전에 멈춰 오류 메시지를 읽게 한다.
    """
    curses.def_prog_mode()
    curses.endwin()
    if banner:
        print(banner)
    rc = None
    try:
        rc = subprocess.run(cmd, env=env).returncode
    except FileNotFoundError:
        print(f"\n실행할 수 없습니다: {cmd[0]} — 설치되어 있는지 확인하세요.")
    except KeyboardInterrupt:
        rc = 130
    if rc not in (0, 130):
        if on_error:
            print("\n" + on_error)
        try:
            input("\n계속하려면 Enter를 누르세요… ")
        except (EOFError, KeyboardInterrupt):
            pass
    curses.reset_prog_mode()
    stdscr.clear()
    stdscr.refresh()
    return rc


def client_banner(stage, session, target="primary"):
    """mysql 프롬프트 위에 남길 문맥 요약(순수 함수).

    클라이언트로 넘어가도 "지금 뭘 고쳐야 하는지"를 잃지 않게 하는 것이 목적이라
    남은 목표만 나열한다. 출력은 호출자가 endwin() 뒤에 한다.
    """
    where = (f"  →  {target} :{PLAYER_PORTS.get(target, '?')}"
             if len(client_targets(stage)) > 1 else "")
    lines = ["", "─" * 60,
             f" {stage.get('id')}  {stage.get('title')}   "
             f"{fmt_mmss(elapsed_of(session))}   "
             f"{objective_marks(stage, session)}{where}"]
    lines += [f"   [ ] {o.get('label', o['id'])}" for o in stage["objectives"]
              if not session["states"][o["id"]]["done"]]
    lines += [" 게임 화면으로 돌아가려면  exit  (또는 \\q)", "─" * 60, ""]
    return "\n".join(lines)


def open_db_client(stdscr, curses, stage, session, target="primary"):
    """진짜 DB 클라이언트(mysql / psql)를 띄운다.

    판정은 그대로다 — 클라이언트도 dba 계정으로 접속하므로 general_log 귀속이
    외부 터미널과 동일하다. 지속 세션이라 트랜잭션·SET SESSION도 살아있다.
    어느 서버로 붙든 마찬가지다: 명령 로그는 이미 서버별로 읽는다.
    """
    pager = CLIENT_PAGER if shutil.which("less") else None
    return run_in_terminal(
        stdscr, curses, client_command(stage, pager, target),
        env=client_env(target, pager),
        banner=client_banner(stage, session, target),
        on_error=client_error_hint(target))


def write_note_draft(stage, session, result):
    """초안을 파일로 쓰고 경로를 돌려준다."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = note_path(stage.get("id", "unknown"), stamp, result["rank"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_note(stage, session, result, time.strftime("%Y-%m-%d")),
        encoding="utf-8")
    return path


def _find_editor():
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor:
        return editor
    for cand in ("vim", "vi", "nano"):
        if shutil.which(cand):
            return cand
    return None


def notes_text(paths):
    """과거 노트를 구분선과 함께 하나로 이어 붙인다."""
    body = []
    for p in paths:
        body.append(note_heading(p))
        try:
            body.append(Path(p).read_text(encoding="utf-8"))
        except OSError as e:
            body.append(f"(읽을 수 없습니다: {e})")
    return "\n\n".join(body)


def page_text(text):
    """텍스트를 페이저로 넘긴다(curses 밖에서 호출).

    뷰어를 curses로 만들지 않는다 — `less`가 스크롤·검색(`/`)을 이미 다 한다.
    목록 UI조차 필요 없다: 이어 붙여 넘기면 끝이다.
    """
    pager = os.environ.get("PAGER") or ("less -R" if shutil.which("less")
                                        else None)
    if not pager:
        print(text)
        return 0
    try:
        proc = subprocess.Popen(shlex.split(pager), stdin=subprocess.PIPE,
                                text=True)
        proc.communicate(text)
        return proc.returncode
    except (OSError, KeyboardInterrupt):
        print(text)
        return 0


def open_notes_pager(stdscr, curses, paths):
    """플레이 중 `n` 키 — curses를 내리고 과거 노트를 페이저로 본다."""
    curses.def_prog_mode()
    curses.endwin()
    page_text(notes_text(paths))
    curses.reset_prog_mode()
    stdscr.clear()
    stdscr.refresh()


def offer_note(stage, session, result):
    """결과 화면 뒤에 회고 작성을 권한다(curses가 이미 내려간 뒤).

    스테이지의 정답 해설은 **이 다음에** 보여준다 — 해설을 먼저 보면 5 Whys를
    스스로 쓸 이유가 사라진다.
    """
    if not sys.stdin.isatty():
        return None
    try:
        ans = input("정리 노트(포스트모템)를 작성하시겠습니까? [Y/n] ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if ans.lower() in ("n", "no"):
        return None

    path = write_note_draft(stage, session, result)
    editor = _find_editor()
    if not editor:
        print(f"\n편집기를 찾지 못했습니다(VISUAL/EDITOR 미설정).\n"
              f"초안을 저장했습니다: {path}")
        return path
    try:
        subprocess.run(shlex.split(editor) + [str(path)])
    except (OSError, KeyboardInterrupt):
        pass
    print(f"\n노트를 저장했습니다: {path}")
    return path


def append_debrief(path, stage):
    """노트 끝에 해설 절을 덧붙인다 → 실제로 덧붙였는지.

    멱등하다 — 이미 붙어 있으면 건너뛴다. 노트를 다시 열어 편집하는 경우에도
    해설이 중복되지 않아야 한다.
    """
    if not path:
        return False
    section = debrief_section(stage)
    if not section:
        return False
    p = Path(path)
    try:
        current = p.read_text(encoding="utf-8")
    except OSError:
        return False
    if DEBRIEF_MARKER in current:
        return False
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(section)
    except OSError:
        return False
    return True


def offer_review_edit(path):
    """해설을 덧붙인 뒤 '대조 메모'를 쓸 기회를 준다(기본값 아니오)."""
    if not sys.stdin.isatty():
        return False
    try:
        ans = input("대조 메모를 남기시겠습니까? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if ans not in ("y", "yes"):
        return False
    editor = _find_editor()
    if not editor:
        print(f"편집기를 찾지 못했습니다. 직접 열어 주세요: {path}")
        return False
    try:
        subprocess.run(shlex.split(editor) + [str(path)])
    except (OSError, KeyboardInterrupt):
        pass
    return True


# 2-1·2-2는 replica의 shop을 지우고 primary binlog를 **처음부터 재생해** 다시
# 만든다. 그래서 시작 비용이 누적 트랜잭션 수에 비례한다. 실측: GTID 148,868에서
# 2-2의 120초 대기가 시간 초과로 실패했고, 갓 띄운 랩은 22개였다. 그 사이에서
# 실패 쪽과 넉넉히 떨어진 곳에 경고선을 둔다.
BINLOG_WARN_GTIDS = 50000


def count_gtids(text):
    """`uuid:1-5:10-12,uuid2:1-3` 형태의 GTID 집합이 담은 트랜잭션 수.

    바이트가 아니라 이 숫자가 재생 비용을 좌우한다. 형태가 아니면 0을 돌려준다 —
    진단용 표시라 못 읽었다고 죽을 이유가 없다.
    """
    total = 0
    for chunk in (text or "").replace("\n", "").split(","):
        parts = chunk.strip().split(":")
        for span in parts[1:]:                 # 첫 조각은 UUID
            low, _, high = span.partition("-")
            try:
                total += int(high) - int(low) + 1 if high else 1
            except ValueError:
                continue
    return total


def binlog_backlog(target="primary"):
    """(GTID 수, binlog 바이트). 조회 실패는 (0, 0) — 진단을 막지 않는다."""
    try:
        gtids = count_gtids(first_scalar(
            db_query(target, "SELECT REPLACE(@@GLOBAL.gtid_executed, '\\n', '')")))
        rows = db_query(target, "SHOW BINARY LOGS")
        size = sum(int(r[1]) for r in rows if len(r) > 1 and r[1].isdigit())
        return gtids, size
    except (LabError, ValueError):
        return 0, 0


def container_started_at(target):
    """컨테이너 기동 시각(재시작 감지용). 조회 실패 시 None."""
    container = CONTAINERS.get(target, target)
    try:
        p = _docker("inspect", "--format", "{{.State.StartedAt}}", container)
    except LabError:
        return None                    # "조회 실패 시 None" 계약을 지킨다
    return p.stdout.strip() if p.returncode == 0 else None


def app_session_pids(target):
    """엔진이 띄운 app 세션의 processlist id 집합."""
    if vendor_of(target) == "postgresql":
        sql = "SELECT pid FROM pg_stat_activity WHERE usename = 'app'"
    else:
        sql = "SELECT id FROM information_schema.processlist WHERE user='app'"
    rows = db_query(target, sql)
    return {int(r[0]) for r in rows if r and r[0].isdigit()}


def kill_app_sessions(target):
    """남아있는 app 세션을 전부 정리(스테이지 재시작 시 깨끗한 출발점)."""
    for pid in app_session_pids(target):
        try:
            db_query(target, kill_session_sql(pid, vendor_of(target)))
        except LabError:
            pass  # 이미 사라진 세션


def reset_player_log(target):
    """플레이어 명령 로그를 비운다. 스테이지 시작 시점을 0으로 맞춘다.

    로깅을 켜 둔 채로 TRUNCATE가 가능하므로 off/on 토글이 필요 없다.
    binlog에서 빼는 이유는 NO_BINLOG 주석 참고 — 복제되면 replica의 로그까지 지운다.
    """
    if vendor_of(target) == "postgresql":
        # 로그가 테이블이 아니라 파일이라 SQL로 비울 수 없다. 로깅 수집기는
        # 이어쓰기(O_APPEND)라 0으로 잘라도 그대로 계속 기록한다(실측).
        _docker("exec", CONTAINERS.get(target, target),
                "truncate", "-s", "0", PG_LOG_FILE)
        return
    db_query(target, NO_BINLOG + "TRUNCATE TABLE mysql.general_log")


def read_player_commands(target):
    """직전 호출 이후 플레이어가 새로 친 질의. 읽으면서 로그를 비운다.

    누적은 호출부(run_watch_step) 책임이다 — 여기서 반환하는 건 '새 것'뿐이다.
    실패를 삼키지 않는다 — 호출부가 화면에 띄운다.
    """
    if vendor_of(target) == "postgresql":
        # 읽기와 비우기를 **한 번의 docker exec**로 묶는다. 두 번으로 나누면 그
        # 사이에 들어온 플레이어 명령이 통째로 사라진다(MySQL 쪽은 두 문장이 한
        # 접속 안에서 이어져 창이 훨씬 좁다).
        script = (" ".join(shlex.quote(a) for a in
                           pg_psql_command(PG_PLAYER_LOG_SQL))
                  + f"; truncate -s 0 {shlex.quote(PG_LOG_FILE)}")
        p = _docker("exec", CONTAINERS.get(target, target), "sh", "-c", script)
        if p.returncode != 0:
            raise LabError((p.stderr or "").strip() or "명령 로그 조회 실패")
        return [r[0] for r in parse_tsv(p.stdout) if r]
    return [r[0] for r in db_query(target, PLAYER_LOG_SQL) if r]


def wait_gtid_sync(target, source, timeout_seconds):
    """`target`이 `source`의 현재 GTID를 모두 적용할 때까지 기다린다 → 성공 여부.

    엔진이 자동으로 해주는 "모든 state 목표가 미충족일 때까지 대기"로는 이걸
    표현할 수 없다. 그 대기는 **장애가 걸렸는지**를 보는데, 여기서 기다리는 것은
    장애를 걸기 **전에** 끝나 있어야 하는 초기 동기화이기 때문이다. 복제 지연
    스테이지에서 이 대기가 없으면 "초기 20만 행이 아직 흐르는 중"과 "장애로
    밀렸다"가 섞여 판정이 흔들린다.

    gtid_executed는 UUID가 여럿이면 개행으로 나뉘어 나온다. `mysql -N -B` 출력을
    줄 단위로 자르는 parse_tsv에 그대로 넣으면 첫 UUID만 남으므로 SQL에서 미리
    개행을 없앤다.
    """
    gtid = first_scalar(
        db_query(source, "SELECT REPLACE(@@GLOBAL.gtid_executed, '\\n', '')"))
    if not gtid:
        return True                     # 원본이 비었으면 따라잡을 것도 없다
    # 클라이언트 타임아웃이 먼저 터지면 대기 결과를 읽지 못한다 — 여유를 둔다.
    rows = db_query(target,
                 f"SELECT WAIT_FOR_EXECUTED_GTID_SET('{gtid}', {timeout_seconds})",
                 timeout=timeout_seconds + 15)
    # 0 = 다 따라잡음, 1 = 시간 초과.
    return first_scalar(rows) == "0"


def _all_containers_are(field, want):
    """두 컨테이너의 inspect 필드가 모두 want 인가 → bool.

    docker 부재도 "아니다"의 한 경우로 흡수한다 — 이 두 술어의 호출자는
    bool을 기대하므로(`cmd_play`의 `if not lab_running():`) 예외를 올리면
    트레이스백이 된다. 실제 조치는 그 다음 `lab_up()`이 LabError로 안내한다.
    """
    try:
        for target in ("primary", "replica"):
            p = _docker("inspect", "--format", field, CONTAINERS[target])
            if p.returncode != 0 or p.stdout.strip() != want:
                return False
    except LabError:
        return False
    return True


def container_running(target):
    """컨테이너 하나가 떠 있는가 → bool. docker 부재도 '아니다'로 흡수한다."""
    try:
        p = _docker("inspect", "--format", "{{.State.Running}}",
                    CONTAINERS.get(target, target))
    except LabError:
        return False
    return p.returncode == 0 and p.stdout.strip() == "true"


def container_healthy(target):
    """컨테이너 하나가 healthy 인가 → bool. docker 부재도 '아니다'로 흡수한다.

    `container_running`으로는 부족한 자리가 있다 — 컨테이너는 **뜨자마자** running
    이지만, 그때 postgres는 아직 initdb로 시드를 넣는 중이다. 그 창에서 질의를
    던지면 실패한다.
    """
    try:
        p = _docker("inspect", "--format", "{{.State.Health.Status}}",
                    CONTAINERS.get(target, target))
    except LabError:
        return False
    return p.returncode == 0 and p.stdout.strip() == "healthy"


def wait_until(predicate, timeout_seconds, poll_seconds=3.0, on_wait=None):
    """`predicate()`가 참이 될 때까지 기다린다 → 참이 됐는가.

    같은 모양의 폴링 루프가 랩 기동과 플레이 전 대기로 흩어져 있었다. 세 번째
    사본을 만들기 전에 모은다 — `tui.pick()`이 세 벌로 갈라졌다가 합쳐진 자리와
    같은 교훈이다.

    `on_wait`는 한 번 쉴 때마다 부른다. 기다리는 동안 화면이 조용하면 멈춘 것처럼
    보이기 때문이다. 조건을 **먼저** 보므로 timeout이 0이어도 한 번은 확인한다.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        if on_wait:
            on_wait()
        time.sleep(poll_seconds)


def lab_running():
    """primary/replica 컨테이너가 모두 running 인가."""
    return _all_containers_are("{{.State.Running}}", "true")


def lab_healthy():
    """두 컨테이너가 모두 healthy 인가."""
    return _all_containers_are("{{.State.Health.Status}}", "healthy")


def lab_up(wait_seconds=300, with_postgres=False):
    """랩을 기동하고 healthy 가 될 때까지 기다린다."""
    print("랩을 기동합니다 (최초 실행은 이미지 내려받기 때문에 몇 분 걸릴 수 있습니다)…")
    args = ["--profile", POSTGRES_PROFILE] if with_postgres else []
    p = _compose(*args, "up", "-d")
    if p.returncode != 0:
        raise LabError((p.stderr or p.stdout).strip())
    # postgres를 함께 띄웠다면 **그것까지** 준비돼야 '준비 완료'다. lab_healthy()는
    # primary/replica만 보므로, 빼먹으면 `./shoot up --with-postgresql`이 아직
    # initdb로 시드를 넣고 있는 서버를 두고 완료를 선언한다.
    def ready():
        return lab_healthy() and (not with_postgres
                                  or container_healthy("postgres"))

    if wait_until(ready, wait_seconds,
                  on_wait=lambda: print("  … 초기화 대기 중")):
        print("랩 준비 완료.")
        return True
    raise LabError("랩이 제한 시간 안에 준비되지 않았습니다. "
                   "`docker compose -f shooting/lab/compose.yaml logs`를 확인하세요.")


def lab_down(remove_volumes=True):
    # 프로파일을 함께 줘야 postgres 컨테이너까지 정리된다 — 빼먹으면 랩을
    # 내렸는데 하나가 남아 다음 `up`에서 헷갈린다.
    args = ["--profile", POSTGRES_PROFILE, "down"] + (["-v"] if remove_volumes else [])
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
    targets = watch_targets(stage)

    # 1. 이전 판의 잔재 정리 + 플레이어 명령 로그 초기화 (엔진 공통 동작)
    #    로그는 서버마다 따로 있으므로 감시할 서버를 모두 비운다 — 하나라도
    #    남겨두면 지난 판의 명령이 이번 판의 위반으로 둔갑한다.
    for t in sorted(targets):
        log(f"[setup] {t}: 이전 세션 정리")
        kill_app_sessions(t)
        reset_player_log(t)

    # 2. 스테이지가 선언한 단계 실행
    allowed = set()
    for step in stage.get("setup") or []:
        target = step.get("on", "primary")
        stype = step["type"]
        sql = step.get("sql")           # wait_gtid_sync 단계에는 SQL이 없다
        if stype == "sql":
            log(f"[setup] {target}: 상태 복원 SQL")
            db_query(target, sql)
            continue

        if stype == "wait_gtid_sync":
            source = step.get("source", "primary")
            secs = int(step.get("timeout_seconds", 60))
            log(f"[setup] {target}: {source} 따라잡기 대기 (최대 {secs}초)")
            if not wait_gtid_sync(target, source, secs):
                # 조용히 넘어가면 초기 동기화가 덜 끝난 채로 장애를 주입하게 되고,
                # 플레이어는 자기가 만들지 않은 지연을 진단하게 된다.
                raise LabError(
                    f"{target}가 {secs}초 안에 {source}를 따라잡지 못했습니다 "
                    f"— 복제 상태를 확인하세요.")
            continue

        count = int(step.get("count", 1)) if stype == "sessions" else 1
        name = step.get("name", stype)
        before = app_session_pids(target)
        log(f"[setup] {target}: '{name}' 세션 {count}개 기동")
        for i in range(count):
            # 세션 번호를 여기서 넣는다 — `render_stage`가 로드 시점에 할 수 없는
            # 유일한 치환이다(그때는 몇 번째 세션인지가 아직 없다).
            db_spawn(target, step.get("user", "app"),
                        step.get("password", "app"),
                        render_session_sql(sql, i),
                        step.get("idle_seconds", 0))
        spawned = _wait_for_new_sessions(target, before, count)
        if step.get("culprit"):
            # 범인 세션의 pid를 기억해둔다 — 나중에 "범인 외 KILL"을 가려낸다.
            # 어느 서버의 pid인지까지 함께 남긴다(서버마다 pid가 겹친다).
            allowed |= {(target, pid) for pid in spawned}

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
    for t in sorted(watch_targets(stage)):
        try:
            kill_app_sessions(t)
        except LabError:
            pass


def poll_state_objective(obj):
    """state 목표의 현재 값과 충족 여부."""
    target = obj.get("on", "primary")
    try:
        rows = db_query(target, obj["query"])
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
            try:
                value = first_scalar(
                    db_query(obj.get("on", "primary"), obj["query"]))
            except LabError:
                if not obj.get("tolerate_error"):
                    raise
                # 조회 대상이 아직 없는 것도 '미충족'의 한 형태다. 복제를 붙이기
                # 전의 빈 replica에서 shop.orders를 세면 당연히 에러인데, 그걸
                # 빨간 '감시 오류'로 띄우면 고장으로 오인된다.
                value = None
            st["value"], st["error"] = value, None
            ok = evaluate_expect(obj.get("expect"), value)
            st["hold"], st["done"] = update_hold(
                st["hold"], ok, now, obj.get("hold_seconds", 0))
            if st["done"]:
                record_event(session, "objective",
                             f"{obj.get('label', obj['id'])} 달성 (값 {value})",
                             elapsed_of(session), unique=True)

        elif kind == "commands":
            # 로그는 읽으면서 비워지므로 '새 것'만 온다 → 누적은 여기서.
            target = step["target"]
            fresh = read_player_commands(target)
            session["commands"].extend(fresh)
            # KILL은 서버별로 귀속해둔다 — pid는 서버마다 따로 매겨진다.
            session["kills"].extend((target, pid)
                                    for pid in parse_kill_targets(fresh))
            # 시각은 폴링 도착 시각(±2초)을 쓴다. general_log의 event_time을
            # 끌어오면 read_player_commands의 반환 형태가 바뀌어 판정 경로
            # (parse_kill_targets)와 그 테스트까지 건드려야 한다.
            # 포스트모템 타임라인은 분 단위면 충분하다.
            multi = len(watch_targets(stage)) > 1
            for sql in fresh:
                # 서버가 둘 이상이면 "어느 서버에서 쳤는가"가 회고의 핵심 정보다.
                # 하나뿐이면 접두어가 잡음이므로 붙이지 않는다.
                record_event(session, "command",
                             f"[{target}] {sql}" if multi else sql,
                             elapsed_of(session))
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
        "kill_targets": session.get("kills") or [],
        "allowed_pids": baseline.get("allowed_pids", set()),
        "restarted": session.get("restarted", False),
        "commands": session.get("commands") or [],
    }
    session["violations"] = detect_violations(stage.get("constraints"), ctx)
    for v in session["violations"]:
        record_event(session, "violation", f"{v['label']} — {v['detail']}",
                     elapsed_of(session), unique=True)


def _objective_by_id(stage, oid):
    for obj in stage["objectives"]:
        if obj["id"] == oid:
            return obj
    raise KeyError(oid)


# --------------------------------------------------------------------------- #
# 진행 기록 (비커밋)
# --------------------------------------------------------------------------- #
def read_progress(path=None):
    """results.jsonl 원문. 없거나 읽을 수 없으면 빈 문자열.

    파싱은 `best_ranks`가 한다 — 파일 접근과 해석을 나눠야 해석 쪽을 테스트할 수
    있다(exam.py의 `read_results`와 같은 구조).
    """
    try:
        return Path(path or PROGRESS_FILE).read_text(encoding="utf-8")
    except OSError:
        return ""


def best_ranks(text):
    """스테이지별 최고 등급 → {스테이지 id: 등급}.

    손상된 줄은 건너뛴다 — 개인 학습 기록이 깨졌다고 플레이를 막을 이유가 없다.

    등급 비교는 반드시 `RANK_ORDER`로 한다. 문자열 비교로는 "S" > "C"가 우연히
    맞아떨어져 넘어가지만 "B" > "A"도 참이 되어 조용히 틀린다.
    """
    best = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        stage_id, rank = rec.get("stage"), rec.get("rank")
        if not stage_id or rank not in RANK_ORDER:
            continue
        prev = best.get(stage_id)
        if prev is None or RANK_ORDER.index(rank) > RANK_ORDER.index(prev):
            best[stage_id] = rank
    return best


def last_seed(text, stage_id=None):
    """마지막으로 플레이한 (스테이지 id, 시드). 없으면 None.

    `stage_id`를 주면 그 스테이지의 마지막 판을 찾는다.

    시각(`at`)이 아니라 **파일 순서**를 따른다 — 기록은 append-only라 그게 곧
    시간 순서이고, 시각 문자열로 정렬하면 시계가 되돌아간 기계에서 엉뚱한 판이
    나온다. 시드가 없는 옛 기록(#36 이전)은 건너뛴다.
    """
    found = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        sid, seed = rec.get("stage"), rec.get("seed")
        if not sid or not isinstance(seed, int):
            continue
        if stage_id and sid != stage_id:
            continue
        found = (sid, seed)
    return found


def stage_rank_badge(stage_id, best):
    """목록에 붙일 최고 등급 표시. 클리어한 적 없으면 빈 문자열."""
    rank = (best or {}).get(stage_id)
    return f"[{rank}]" if rank else ""


def progress_record(stage, rank, score, elapsed, hints_used, violations):
    """results.jsonl에 남길 한 줄(순수 함수)."""
    return {
        "stage": stage.get("id"),
        "title": stage.get("title"),
        "rank": rank,
        "score": score,
        "elapsed": round(elapsed, 1),
        "hints": hints_used,
        "violations": violations,
        # 변주가 있는 스테이지는 시드가 있어야 같은 판을 다시 열 수 있다.
        "seed": stage.get("_seed"),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def save_progress(stage, rank, score, elapsed, hints_used, violations):
    try:
        PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
        rec = progress_record(stage, rank, score, elapsed, hints_used,
                              violations)
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 기록 실패가 플레이를 막지는 않는다


# --------------------------------------------------------------------------- #
# 게임 세션 상태
# --------------------------------------------------------------------------- #
def init_session(stage, rng=None):
    """플레이 런타임 상태를 만든다.

    `rng`를 주면 mcq 보기를 섞는다 — 스테이지 문항도 `exam.py`의 작성 규약을
    따라 정답을 `answer: 0`에 적으므로, 섞지 않으면 정답이 항상 1번이 된다.
    `exam.py`와 같은 스키마이므로 문항별 `shuffle: false` 탈출구도 그대로 받는다
    ('위 모두 정답' 류 보기가 있으면 순서를 고정해야 한다).
    """
    states = {}
    for obj in stage["objectives"]:
        st = {"done": False, "hold": {"since": None}, "value": None,
              "error": None, "correct": None, "prompted": False,
              # 물어보지 못하고 닫힌 문항. 오답과 구분해야 채점에서 뺄 수 있다.
              "skipped": False}
        if obj["type"] == "quiz" and obj["question"].get("type") == "mcq":
            q = obj["question"]
            if rng is not None and q.get("shuffle", True):
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
        # KILL 대상은 `(대상 서버, pid)` 쌍으로 쌓는다 — pid만으로는 서버가
        # 다른 동명이인을 구분할 수 없다.
        "kills": [],
        "restarted": False,
        "watch_error": None,
        "finished": False,
        # 하단 바에 띄울 지난 노트 개수. 노트는 스테이지가 끝난 뒤에만 생기므로
        # 플레이 중에는 변하지 않는다 — 그래서 매 프레임 세지 않고 여기 담아둔다.
        # 실제 값은 화면을 띄우는 쪽(run_curses)이 시작 시 한 번 채운다.
        "notes_count": 0,
        # 포스트모템 타임라인의 재료. 첫 줄은 장애 발생 시점이다.
        "events": [{"at": 0.0, "kind": "incident", "text": "장애 발생"}],
    }


def quiz_totals(stage, session):
    """진단 문항 (정답 수, 전체 수).

    물어보지 못한 문항(`skipped`)은 세지 않는다 — 답할 기회가 없었던 것을 오답으로
    깎으면 안 된다. `target_seconds`가 없을 때 시간으로 감점하지 않는 것과 같은
    원칙이다(기준이 없으면 감점하지 않는다).
    """
    total = correct = 0
    for obj in stage["objectives"]:
        if obj["type"] != "quiz":
            continue
        st = session["states"][obj["id"]]
        if st.get("skipped"):
            continue
        total += 1
        if st.get("correct"):
            correct += 1
    return correct, total


def skip_quiz(session, objective_id):
    """문항을 '건너뜀'으로 닫는다.

    비대화형 실행(파이프)에서는 `input()`을 쓸 수 없는데, 그렇다고 미완료로 두면
    `all_done`이 영원히 거짓이라 라인 모드 루프가 끝나지 않는다. 완료로 닫되
    정답/오답 어느 쪽도 아니라고 표시해 채점에서 빠지게 한다.
    """
    st = session["states"][objective_id]
    st["done"] = True
    st["skipped"] = True
    st["correct"] = None
    return st


def _state_objectives_done(stage, session):
    """모든 `state` 목표가 충족됐는가(문항 제외)."""
    return all(session["states"][o["id"]]["done"]
               for o in stage["objectives"] if o["type"] == "state")


def line_quizzes_to_ask(stage, session, elapsed, interactive=True):
    """라인 모드에서 **지금** 물어볼 문항 목록.

    curses 모드에는 `r` 키가 있어 플레이어가 원할 때 문항을 연다. 라인 모드에는
    그런 입력 통로가 없으므로 엔진이 시점을 정해야 하는데, 예전에는 폴링 주기마다
    미응답 문항을 전부 물어봤다 — `trigger`가 무시돼 상황을 보기도 전에 문항이
    뜨고, 답하지 않으면 2초마다 다시 물었다.

    두 시점에만 묻는다.
      1. `trigger.after_seconds`가 지난 문항 (curses의 자동 발동과 같은 규칙)
      2. `state` 목표를 모두 끝낸 뒤 남은 문항 — 답하지 않으면 판이 끝나지 않으므로
         이때는 물어야 한다

    대화형 터미널이 아니면 아무것도 묻지 않는다(호출부가 `skip_quiz`로 닫는다).
    """
    if not interactive:
        return []
    endgame = _state_objectives_done(stage, session)
    out = []
    for obj in stage["objectives"]:
        if obj["type"] != "quiz":
            continue
        if session["states"][obj["id"]]["done"]:
            continue
        after = (obj.get("trigger") or {}).get("after_seconds")
        if endgame or (after is not None and elapsed >= after):
            out.append(obj)
    return out


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
    """수동 접속 명령. 접속 정보의 단일 출처는 위 PLAYER_* 상수다.

    스테이지가 이 명령을 직접 적을 수 없다. 예전에는 `connect_hint` 필드로
    덮어쓸 수 있었고, 13개 스테이지가 저마다 한 벌씩 들고 있다가 **13개 전부**
    여기서 만드는 것과 어긋났다(전부 `-D` 가 빠져, 안내대로 붙은 플레이어는 기본
    데이터베이스 없이 시작했다). 포트·계정이 바뀌면 조용히 썩는 자리라
    `validate_stage`가 그 필드를 아예 거부한다.
    """
    first = default_target(stage)
    if vendor_of(first) == "postgresql":
        hint = (f"PGPASSWORD={PLAYER_PASSWORD} psql -h{PLAYER_HOST} "
                f"-p{PLAYER_PORTS[first]} -U{PLAYER_USER} -d{PG_PLAYER_DB}")
    else:
        hint = (f"MYSQL_PWD={PLAYER_PASSWORD} mysql -h{PLAYER_HOST} "
                f"-P{PLAYER_PORTS[first]} -u{PLAYER_USER} -D{PLAYER_DB}")
    others = [t for t in client_targets(stage) if t != first]
    if others:
        # 범인이 replica에 있는 스테이지에서 primary 명령만 띄우면 현장을 놓친다.
        hint += "   (" + ", ".join(
            f"{t}는 {'-p' if vendor_of(t) == 'postgresql' else '-P'}"
            f"{PLAYER_PORTS[t]}" for t in others) + ")"
    return hint


def ensure_streaming_output(stream=None):
    """비대화형 출력을 라인 버퍼링으로 바꾼다 → 실제로 바꿨는지.

    stdout이 tty가 아니면 Python은 블록 버퍼링을 쓴다. 라인 모드는 "이 창은
    상태만 표시합니다"라고 안내하는 **감시 창**인데, 파이프나 로그 파일로 넘기는
    순간 버퍼가 찰 때까지 한 글자도 나오지 않아 그 목적이 사라진다. 진행이 멈춘
    것인지 출력이 안 나오는 것인지도 구분할 수 없다.

    스트림이 `reconfigure`를 지원하지 않거나 거부해도 조용히 넘어간다 — 출력이
    덜 매끄러운 것이 판을 못 하게 만들 이유는 아니다.
    """
    stream = sys.stdout if stream is None else stream
    try:
        if stream.isatty():
            return False                # tty는 이미 라인 버퍼링이다
        stream.reconfigure(line_buffering=True)
        return True
    except (AttributeError, ValueError, OSError):
        return False


def run_line(stage, session, baseline):
    """tty가 아니거나 curses가 없을 때의 단순 진행 화면."""
    print(f"\n== {stage.get('title')} ({stage.get('id')}) ==")
    print(f"\n{stage.get('brief', '')}\n")
    print(f"접속: {_connect_hint(stage)}\n")
    print("다른 터미널에서 위 명령으로 접속해 대응하세요.")
    print("이 창은 상태만 표시합니다. Ctrl+C로 중단.\n")

    interactive = sys.stdin.isatty()
    if not interactive:
        print("(대화형 터미널이 아니라 상황 보고 문항은 건너뜁니다.)\n")

    last = None
    try:
        while not all_done(stage, session):
            refresh(stage, session, baseline)

            elapsed = time.monotonic() - session["started"]
            for obj in line_quizzes_to_ask(stage, session, elapsed, interactive):
                session["states"][obj["id"]]["prompted"] = True
                if not _line_ask(obj, session["states"][obj["id"]]):
                    # 입력이 끊겼다(EOF). 더 물어봐야 남은 문항도 마찬가지이므로
                    # 전부 닫고 상태 감시만 이어간다.
                    interactive = False
                    break
            if not interactive:
                for o in stage["objectives"]:
                    if o["type"] == "quiz" and not session["states"][o["id"]]["done"]:
                        skip_quiz(session, o["id"])

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
        elif st.get("skipped"):
            # 답한 것과 똑같이 [x]로 보이면 회고할 때 오해한다.
            extra = "  (건너뜀 — 채점 제외)"
        lines.append(f"  [{mark}] {obj.get('label', obj['id'])}{extra}")
    for v in session["violations"]:
        lines.append(f"  ! 금지 행동: {v['label']} — {v['detail']}")
    if session.get("watch_error"):
        lines.append(f"  ! 감시 오류: {session['watch_error']}")
    return "\n".join(lines)


def _line_ask(obj, st, prompt=input):
    """라인 모드 문항 하나를 묻는다 → 답을 받았는가(False면 입력이 끊긴 것).

    잘못된 입력은 **이 안에서** 다시 묻는다. 예전에는 그냥 돌아가버려서 바깥
    폴링 루프가 2초 뒤 같은 문항을 또 띄웠다 — 무한 재촉이 된다.
    """
    q = obj["question"]
    print(f"\n>> 상황 보고: {q['q']}")
    is_mcq = q["type"] == "mcq"
    order = st.get("order") or list(range(len(q.get("choices") or [])))
    if is_mcq:
        for i, idx in enumerate(order, 1):
            print(f"   {i}) {q['choices'][idx]}")

    while True:
        try:
            raw = prompt("   번호 입력: " if is_mcq else "   답 입력: ").strip()
        except (EOFError, KeyboardInterrupt):
            # 파이프로 실행됐거나 입력이 닫혔다. 여기서 예외를 올리면 판이
            # 트레이스백으로 죽는다 — 감시는 계속할 수 있으므로 신호만 돌려준다.
            print("\n   (입력을 받을 수 없어 건너뜁니다.)")
            return False
        if not is_mcq:
            st["correct"] = grade_short(raw, q.get("accept") or [])
            break
        if raw.isdigit() and 1 <= int(raw) <= len(order):
            st["correct"] = grade_mcq(int(raw) - 1, st.get("answer", q["answer"]))
            break
        print(f"   1~{len(order)} 사이의 번호를 입력하세요.")

    st["done"] = True
    print("   → " + ("정답" if st["correct"] else "오답"))
    if q.get("explain"):
        print(f"   {q['explain']}")
    return True


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
        # 한 번만 센다 — 이 값은 플레이 중 변하지 않는데, 매 프레임(80ms) 세면
        # 초당 12번 디렉터리를 훑게 된다.
        session["notes_count"] = len(collect_notes(NOTES_DIR, stage.get("id")))

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
                _answer_quiz(stdscr, curses, pending, session)
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
                if _confirm_quit(stdscr, curses, stage, session):
                    return None
                stdscr.timeout(KEY_TIMEOUT_MS)
                continue
            if ch == "r":
                nxt = _next_unanswered_quiz(stage, session)
                if nxt:
                    _answer_quiz(stdscr, curses, nxt, session)
                    stdscr.timeout(KEY_TIMEOUT_MS)
                else:
                    _notice(stdscr, curses, "답할 상황 보고가 남아있지 않습니다.")
                    stdscr.timeout(KEY_TIMEOUT_MS)
            elif ch == "c":
                target = _pick_client_target(stdscr, curses, stage)
                if target:
                    open_db_client(stdscr, curses, stage, session, target)
                    # 클라이언트 안에서 고쳤을 수 있다 — 복귀 즉시 폴링되도록 리셋.
                    watch = init_watch(stage)
                stdscr.timeout(KEY_TIMEOUT_MS)
            elif ch == "n":
                notes = collect_notes(NOTES_DIR, stage.get("id"))
                if notes:
                    open_notes_pager(stdscr, curses, notes)
                else:
                    _notice(stdscr, curses,
                            "아직 작성한 정리 노트가 없습니다. "
                            "스테이지를 끝내면 회고를 쓸 수 있습니다.")
                stdscr.timeout(KEY_TIMEOUT_MS)
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


def _pick_client_target(stdscr, curses, stage):
    """`c` 키로 붙을 서버를 고른다 → 대상 이름(취소면 None).

    서버가 하나뿐인 스테이지에서는 화면을 띄우지 않고 바로 돌려준다 — 선택지가
    하나인 질문은 물어볼 이유가 없다.
    """
    targets = client_targets(stage)
    if len(targets) == 1:
        return targets[0]

    idx = pick(stdscr, curses, "어느 서버에 접속할까요",
               [f"{t}  ({PLAYER_HOST}:{PLAYER_PORTS[t]})" for t in targets],
               footer=" ↑↓ 또는 숫자 선택   Enter 접속   Esc/q 취소 ")
    return None if idx is None else targets[idx]


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
    put(stdscr, curses, row, 8, f"c 키로 {client_name(stage)} 접속", w - 9,
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

    bar(stdscr, curses, h - 1, w, play_footer(stage, session))
    stdscr.refresh()


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


def _confirm_quit(stdscr, curses, stage, session):
    """포기 전 확인 화면 → 정말 포기하는가.

    문항 화면에서는 q가 '제출 없이 닫기'라, 그 손버릇이 플레이 화면에서 판을
    통째로 날릴 수 있다. 포기한 판에는 해설이 붙지 않으므로(의도된 설계) 실수
    비용이 크다 — 그 비용을 화면에 적어두고 y를 받을 때만 진행한다.
    """
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    bar(stdscr, curses, 0, w, " 스테이지 포기 ")

    row = 2
    put(stdscr, curses, row, 2, "정말 포기하시겠습니까?", w - 4,
        curses.color_pair(3) | curses.A_BOLD)
    row += 2
    put(stdscr, curses, row, 2,
        f"지금까지 {fmt_mmss(elapsed_of(session))}  "
        f"{objective_marks(stage, session)}", w - 4)
    row += 2
    for line in ("포기한 판에는 스테이지 해설이 붙지 않습니다.",
                 "(회고 노트는 포기해도 쓸 수 있습니다 — 오히려 그때가 더 필요합니다.)",
                 "스테이지는 언제든 다시 플레이할 수 있습니다."):
        for wrapped in wrap(line, w - 4):
            put(stdscr, curses, row, 2, wrapped, w - 4, curses.A_DIM)
            row += 1

    bar(stdscr, curses, h - 1, w, " y 포기   그 외 아무 키나 계속 플레이 ")
    stdscr.refresh()
    stdscr.timeout(-1)
    _, key = read_key(stdscr, curses)
    return is_affirmative(key)


def _answer_quiz(stdscr, curses, obj, session):
    """진단 문항 화면을 띄우고, 답했으면 타임라인에 남긴다."""
    st = session["states"][obj["id"]]
    _quiz_screen(stdscr, curses, obj, st)
    if st["done"]:
        record_event(session, "quiz",
                     f"상황 보고 «{obj.get('label', obj['id'])}» — "
                     + ("정답" if st["correct"] else "오답"),
                     elapsed_of(session), unique=True)


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
    record_event(session, "hint", f"힌트 {session['hints_used']}번 사용",
                 elapsed_of(session))
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
    """등급표만 출력한다. 후일담(정답 해설)은 노트를 쓴 뒤에 보여준다."""
    print(f"\n{'=' * 46}")
    print(f"  STAGE CLEAR — {stage.get('title')}")
    print(f"{'=' * 46}")
    for label, value, ok in result["items"]:
        print(f"  {label:<10} {value:<24} {'+' if ok else '-'}")
    print(f"{'-' * 46}")
    print(f"  RANK  {result['rank']}   ({result['score']}/4)")
    print()


def print_debrief(stage):
    if stage.get("debrief"):
        print(f"\n  후일담\n")
        for line in stage["debrief"].split("\n"):
            print(f"    {line}")
        print()
    # 장애를 막 겪은 직후가 그 주제를 읽기에 가장 좋은 때다.
    reading = chapter_reading_list(stage)
    if reading:
        print("  더 읽을 곳\n")
        print(reading)
        print()


# --------------------------------------------------------------------------- #
# 서브커맨드
# --------------------------------------------------------------------------- #
def cmd_list():
    stages = discover_stages()
    if not stages:
        print("스테이지가 없습니다.")
        return 1
    best = best_ranks(read_progress())
    print("\n사용 가능한 스테이지")
    dbms_groups = group_by_dbms([(p, _safe_load(p)) for p in stages])
    for dbms, entries in dbms_groups:
        # DBMS가 하나뿐이면 머리글을 붙이지 않는다 — 목록의 모든 줄에 해당하는
        # 이름은 정보가 아니라 잡음이다.
        if len(dbms_groups) > 1:
            print(f"\n[{DBMS_TITLES.get(dbms, dbms)}]")
        for world, items in group_by_world(entries):
            print(f"\n  월드 {world} · {WORLD_TITLES.get(world, '미분류')}")
            for path, stage in items:
                if not stage:
                    print(f"    [오류] {path.name}: 정의를 읽을 수 없습니다")
                    continue
                kind = "🔥" if stage.get("kind") == "incident" else "🔧"
                badge = stage_rank_badge(stage.get("id"), best)
                print(f"    {kind} {stage.get('id'):<26} {stage.get('title')}"
                      f"{'  ' + badge if badge else ''}")
                print(f"       {stage.get('brief', '')[:66]}")
    print()
    return 0


def cmd_notes(stage_id=None):
    """게임 밖에서 지난 정리 노트를 본다."""
    notes = collect_notes(NOTES_DIR, stage_id)
    if stage_id:
        notes = [p for p in notes if p.parent.name == stage_id] or notes
    if not notes:
        print("\n아직 작성한 정리 노트가 없습니다.")
        print(f"스테이지를 끝내면 {NOTES_DIR.relative_to(REPO_ROOT)}/ 에 쌓입니다.\n")
        return 0
    page_text(notes_text(notes))
    return 0


def cmd_replay(stage_id=None):
    """지난 판을 같은 시드로 다시 연다.

    시드는 기록과 노트에 남지만, 지금까지는 파일을 열어 찾아 손으로 옮겨 쳐야
    했다. 회고를 쓰다 "그때 그 판을 다시"가 되는 순간 그 사이가 끊긴다.
    """
    found = last_seed(read_progress(), stage_id)
    if not found:
        where = f"'{stage_id}'의 " if stage_id else ""
        print(f"\n{where}지난 기록을 찾을 수 없습니다.")
        print("한 판 끝내면 시드가 기록되어 다시 열 수 있습니다.")
        print("(시드는 `vars`나 mcq 진단 문항이 있는 스테이지에만 의미가 있습니다.)\n")
        return 1
    sid, seed = found
    print(f"\n지난 판을 다시 엽니다 — {sid}  (시드 {seed})")
    return cmd_play(sid, seed=seed)


def cmd_doctor():
    ok = True
    # 경고는 실패와 구분한다 — 실행을 막지는 않지만 알고 있어야 하는 것들.
    warn = False
    print("\n사전 점검\n")

    # 진단 명령이 진단 대상의 부재로 죽으면 안 된다 — docker가 아예 없는 상태가
    # 바로 이 명령을 실행하는 가장 흔한 이유다.
    has_docker = docker_available()
    if not has_docker:
        print("  [!!] docker            설치되어 있지 않습니다 "
              "(Docker Desktop을 설치하세요)")
        ok = False
    else:
        p = _docker("info", "--format", "{{.ServerVersion}}")
        if p.returncode == 0:
            print(f"  [ok] docker            {p.stdout.strip()}")
        else:
            print("  [!!] docker            실행 중이 아닙니다 "
                  "(Docker Desktop을 켜세요)")
            ok = False

    if COMPOSE_FILE.exists():
        print(f"  [ok] compose 파일      {COMPOSE_FILE.relative_to(REPO_ROOT)}")
    else:
        print(f"  [!!] compose 파일      없음: {COMPOSE_FILE}")
        ok = False

    # `which`를 서브프로세스로 부르지 않는다 — 최소 이미지에는 그것도 없다.
    mysql_bin = shutil.which("mysql")
    if mysql_bin:
        print(f"  [ok] mysql 클라이언트  {mysql_bin}")
    else:
        print("  [!!] mysql 클라이언트  없음 — 플레이어가 접속할 수단이 필요합니다")
        ok = False

    # PostgreSQL은 아직 선택 사항이다 — 없다고 해서 점검을 실패시키지 않는다.
    if shutil.which("psql"):
        print(f"  [ok] psql 클라이언트    {shutil.which('psql')}")
    else:
        print("  [--] psql 클라이언트    없음 — PostgreSQL 스테이지를 하려면 필요합니다")

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

    # docker가 없으면 랩 상태를 물어볼 수 없다. "내려가 있음"으로 찍으면
    # `./shoot up`으로 해결된다는 오진을 준다.
    if not has_docker:
        print("  [--] 랩 상태           확인 불가 — docker를 먼저 설치하세요")
    elif lab_running():
        print(f"  [ok] 랩 상태           기동 중"
              f"{' (healthy)' if lab_healthy() else ' (준비 중)'}")
        # 복제 스테이지는 binlog를 처음부터 재생하므로 누적이 크면 시작조차
        # 못 한다. 원인을 모르면 '복제가 고장났나' 하고 엉뚱한 곳을 판다.
        gtids, size = binlog_backlog()
        if gtids >= BINLOG_WARN_GTIDS:
            warn = True
            print(f"  [주의] binlog 누적     GTID {gtids:,}개 "
                  f"({size / 1048576:.0f}MB) — 복제 스테이지(2-1·2-2)가 시간 "
                  f"초과로 실패할 수 있습니다.\n"
                  f"                         `./shoot down && ./shoot up`으로 "
                  f"초기화하세요.")
        elif gtids:
            print(f"  [ok] binlog 누적       GTID {gtids:,}개 "
                  f"({size / 1048576:.0f}MB)")
    else:
        print("  [--] 랩 상태           내려가 있음 — `./shoot up`으로 기동")

    if has_docker:
        pg = _docker("inspect", "--format", "{{.State.Status}}",
                     CONTAINERS["postgres"])
        if pg.returncode == 0 and pg.stdout.strip() == "running":
            print("  [ok] PostgreSQL 랩     기동 중")
        else:
            print("  [--] PostgreSQL 랩     내려가 있음 — "
                  "`./shoot up --with-postgresql`")

    if not ok:
        verdict = "위 항목을 먼저 해결하세요."
    elif warn:
        # 경고는 종료 코드를 바꾸지 않는다 — 플레이를 막지 않기 때문이다.
        verdict = "플레이에는 문제가 없지만 위 주의 항목을 확인하세요."
    else:
        verdict = "모두 정상입니다."
    print("\n" + verdict + "\n")
    return 0 if ok else 1


# 월드는 그동안 스테이지 JSON에만 있고 아무도 읽지 않았다. 이름을 줘야 선택
# 화면에서 '어디까지 왔는지'가 보인다. 번호가 아니라 다루는 주제로 묶는다.
WORLD_TITLES = {
    1: "잠금과 대기",
    2: "복제",
    3: "자원 고갈",
    4: "성능과 실행 계획",
}
UNKNOWN_WORLD = 0            # 정의를 못 읽은 스테이지가 모이는 자리


def filter_stages_by_dbms(entries, dbms):
    """`--dbms`로 좁힌 목록. None이면 그대로 통과시킨다(exam.py와 같은 규칙)."""
    if not dbms:
        return entries
    return [(p, st) for p, st in entries if stage_dbms(st) == dbms]


def stage_dbms(stage):
    """스테이지의 DBMS. 적지 않았으면 MySQL이다(기존 스테이지가 그렇다)."""
    return (stage or {}).get("dbms") or "mysql"


def stage_seed_matters(stage):
    """이 스테이지에서 시드가 실제로 판을 가르는가.

    `vars`가 있으면 파라미터가 시드로 갈리고(render_stage), mcq 진단 문항이
    있으면 보기 순서가 시드로 섞인다(init_session) — 둘 다 없으면 시드는
    기록될 뿐 아무것도 바꾸지 않는다.
    """
    if stage.get("vars"):
        return True
    return any(o.get("type") == "quiz"
               and (o.get("question") or {}).get("type") == "mcq"
               for o in stage.get("objectives", []))


def group_by_dbms(entries):
    """[(경로, 스테이지|None)] → [(dbms, [(경로, 스테이지)])].

    VENDORS 순서를 따르고, 모르는 값은 뒤에 이름순으로 붙인다. 정의를 못 읽은
    파일은 MySQL로 묶인다 — 목록에서 빼면 "파일은 있는데 안 보인다"가 된다.
    """
    buckets = {}
    for path, stage in entries:
        buckets.setdefault(stage_dbms(stage), []).append((path, stage))
    known = [v for v in VENDORS if v in buckets]
    return [(v, buckets[v])
            for v in known + sorted(set(buckets) - set(VENDORS))]


def dbms_menu_label(dbms, entries, best):
    """DBMS 선택 목록의 한 줄. 그 DBMS에서 받은 **최고** 등급을 함께 보여준다."""
    ranks = [best.get((s or {}).get("id")) for _, s in entries]
    ranks = [r for r in ranks if r in RANK_ORDER]
    badge = f"  [{max(ranks, key=RANK_ORDER.index)}]" if ranks else ""
    return f"{DBMS_TITLES.get(dbms, dbms)}   {len(entries)}개{badge}"


def group_by_world(entries):
    """[(경로, 스테이지|None)] → [(월드, [(경로, 스테이지)])] (월드·스테이지 순).

    정의를 못 읽은 파일은 월드를 알 수 없지만 **목록에서 빼지 않는다** — 파일은
    있는데 안 보이는 상태가 더 헷갈린다.
    """
    buckets = {}
    for path, stage in entries:
        world = (stage or {}).get("world", UNKNOWN_WORLD)
        buckets.setdefault(world, []).append((path, stage))
    out = []
    for world in sorted(buckets):
        items = sorted(buckets[world],
                       key=lambda e: ((e[1] or {}).get("stage", 0),
                                      Path(e[0]).stem))
        out.append((world, items))
    return out


def world_menu_label(world, entries, best):
    """월드 선택 목록의 한 줄. 그 월드에서 받은 **최고** 등급을 함께 보여준다."""
    title = WORLD_TITLES.get(world, "미분류")
    ranks = [best.get((s or {}).get("id")) for _, s in entries]
    ranks = [r for r in ranks if r in RANK_ORDER]
    badge = ""
    if ranks:
        badge = f"  [{max(ranks, key=RANK_ORDER.index)}]"
    return f"월드 {world} · {title}   {len(entries)}개{badge}"


def stage_menu_label(path, stage, best):
    """선택 목록에 뿌릴 한 줄. `stage`가 None이면 정의 오류로 표시한다.

    깨진 파일을 목록에서 빼면 "파일은 있는데 목록에 없다"가 되어 더 헷갈린다.
    """
    if not stage:
        return f"[오류] {Path(path).stem}  (정의를 읽을 수 없습니다)"
    kind = "🔥" if stage.get("kind") == "incident" else "🔧"
    badge = stage_rank_badge(stage.get("id"), best)
    line = f"{kind} {stage.get('id', ''):<24} {stage.get('title', '')}"
    return f"{line}  {badge}" if badge else line


def _safe_load(path):
    """정의를 읽되 실패는 None으로. 목록을 그리다 죽지 않게 한다."""
    try:
        return load_stage(path)
    except (ValueError, json.JSONDecodeError, OSError):
        return None


def _choose_stage_curses(labels):
    """curses 선택 화면 → 고른 인덱스(취소면 None)."""
    import curses

    def _driver(stdscr):
        _init_screen(curses)
        return pick(stdscr, curses, "스테이지를 고르세요", labels,
                    footer=" ↑↓ 또는 숫자 선택   Enter 시작   Esc/q 종료 ")

    return curses.wrapper(_driver)


def _choose_stage_line(stages, labels):
    """평문 폴백. tty가 아니거나 curses를 쓸 수 없을 때."""
    print("\n스테이지를 고르세요\n")
    for i, label in enumerate(labels, 1):
        print(f"  {i}) {label}")
    print()
    while True:
        try:
            raw = input("번호 (q=종료): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if raw in ("q", "Q"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(stages):
            return stages[int(raw) - 1]
        print("잘못된 입력입니다.")


def _pick_world_then_stage(stdscr, curses, groups, best, heading, can_go_up):
    """월드 → 스테이지 선택 → 경로. 위 단계로 나가면 None.

    `can_go_up`은 안내 문구만 바꾼다 — DBMS 단계가 없을 때 "DBMS 선택으로"라고
    적으면 있지도 않은 화면을 가리키게 된다.
    """
    back = "DBMS 선택으로" if can_go_up else "종료"
    while True:
        w_idx = pick(
            stdscr, curses, heading,
            [world_menu_label(w, items, best) for w, items in groups],
            footer=f" ↑↓ 또는 숫자 선택   Enter 들어가기   Esc/q {back} ")
        if w_idx is None:
            return None
        world, items = groups[w_idx]
        s_idx = pick(
            stdscr, curses,
            f"월드 {world} · {WORLD_TITLES.get(world, '미분류')}",
            [stage_menu_label(p, st, best) for p, st in items],
            footer=" ↑↓ 또는 숫자 선택   Enter 시작   Esc/q 월드 선택으로 ")
        if s_idx is not None:
            return items[s_idx][0]
        # 스테이지 선택에서 나가면 월드 선택으로 되돌아간다.


def _choose_in_worlds_curses(dbms_groups, best):
    """DBMS → 월드 → 스테이지 3단계 선택 → 경로(취소면 None).

    `exam.py`가 DBMS → 티어 → 챕터로 내려가는 것과 같은 방식이다. 단계마다
    `tui.pick()`을 다시 부르면 되므로 공용 선택기는 손대지 않는다.

    **DBMS가 하나뿐이면 그 단계를 건너뛴다.** 선택지가 하나인 화면은 고를 것이
    없는데 Enter만 요구하는 잡음이다 — 스테이지가 전부 MySQL인 동안에는 화면이
    지금까지와 똑같이 보인다.
    """
    import curses
    solo = len(dbms_groups) == 1

    def _driver(stdscr):
        _init_screen(curses)
        while True:
            if solo:
                dbms, entries = dbms_groups[0]
            else:
                d_idx = pick(
                    stdscr, curses, "어느 DBMS로 할까요",
                    [dbms_menu_label(d, e, best) for d, e in dbms_groups],
                    footer=" ↑↓ 또는 숫자 선택   Enter 들어가기   Esc/q 종료 ")
                if d_idx is None:
                    return None
                dbms, entries = dbms_groups[d_idx]
            heading = ("어느 월드부터 할까요" if solo else
                       f"{DBMS_TITLES.get(dbms, dbms)} · 어느 월드부터 할까요")
            path = _pick_world_then_stage(
                stdscr, curses, group_by_world(entries), best, heading,
                can_go_up=not solo)
            if path is not None:
                return path
            if solo:
                return None      # 위 단계가 없으니 나가면 그대로 종료
            # DBMS 선택으로 되돌아간다.

    return curses.wrapper(_driver)


def choose_stage(stages, dbms=None):
    """스테이지가 여러 개면 골라서 하나를 돌려준다(종료면 None).

    `dbms`를 주면 그 DBMS만 남기고 선택 단계도 건너뛴다 — `exam.py`의 `--dbms`가
    해당 선택 화면을 건너뛰는 것과 같다.
    """
    if len(stages) == 1:
        return stages[0]
    best = best_ranks(read_progress())
    entries = filter_stages_by_dbms([(p, _safe_load(p)) for p in stages], dbms)
    if not entries:
        print(f"{DBMS_TITLES.get(dbms, dbms)} 스테이지가 아직 없습니다.")
        return None
    if len(entries) == 1:
        return entries[0][0]
    dbms_groups = group_by_dbms(entries)

    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            return _choose_in_worlds_curses(dbms_groups, best)
        except Exception:
            # 조용히 넘기지 않는다 — 이 폴백이 화면 코드의 버그를 감춘 적이 있다.
            traceback.print_exc()
            print("\n선택 화면에서 오류가 발생해 목록 입력으로 전환합니다.\n")

    # 평문 폴백은 한 단계로 둔다 — 번호를 세 번 묻는 것이 더 번거롭다.
    solo = len(dbms_groups) == 1
    flat, labels = [], []
    for dbms, entries in dbms_groups:
        for world, items in group_by_world(entries):
            for path, stage in items:
                flat.append(path)
                tag = "" if solo else f"[{DBMS_TITLES.get(dbms, dbms)}] "
                labels.append(f"{tag}[월드 {world}] "
                              f"{stage_menu_label(path, stage, best)}")
    return _choose_stage_line(flat, labels)


def cmd_play(target=None, force_line=False, seed=None, dbms=None):
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
        path = choose_stage(stages, dbms)
        if path is None:
            return 0

    try:
        stage = load_stage(path)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"스테이지 정의 오류: {e}")
        return 1

    # 시드는 `vars`가 있을 때뿐 아니라 mcq 진단 문항의 보기 순서도 가른다
    # (init_session). 둘 다 없을 때도 기록은 남긴다 — 나중에 그 스테이지에
    # 변주나 mcq가 생겨도 지난 기록의 형식이 달라지지 않는다.
    if seed is None:
        seed = random.randrange(1, 1_000_000)
    stage = dict(render_stage(stage, random.Random(seed)), _seed=seed)
    if stage_seed_matters(stage):
        print(f"이번 판 시드: {seed}  "
              f"(같은 판을 다시 하려면 --seed {seed})")

    # PostgreSQL 스테이지는 프로파일 뒤에 있는 컨테이너를 쓴다. 기본 `./shoot up`
    # 으로는 뜨지 않으므로, 여기서 걸러 주지 않으면 setup 단계가 docker exec 실패로
    # 무너지고 원인이 드러나지 않는다.
    if stage.get("dbms") == "postgresql":
        if not container_running("postgres"):
            print("이 스테이지는 PostgreSQL 랩이 필요합니다.\n"
                  "  ./shoot up --with-postgresql")
            return 1
        # running 은 컨테이너가 **뜨자마자** 참이 된다. 그 시점의 postgres는 아직
        # initdb로 시드(20만 행)를 넣는 중이라, 여기서 기다리지 않으면 setup의 첫
        # `docker exec psql`이 실패하고 플레이어는 자기가 만들지 않은 오류를 본다.
        if not container_healthy("postgres"):
            print("PostgreSQL 랩이 아직 준비 중입니다. 잠시 기다립니다…")
            if not wait_until(lambda: container_healthy("postgres"), 120,
                              on_wait=lambda: print("  … 초기화 대기 중")):
                print("PostgreSQL 랩이 제한 시간 안에 준비되지 않았습니다.\n"
                      "  docker compose -f shooting/lab/compose.yaml"
                      " --profile postgresql logs postgres")
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
        wait_until(lab_healthy, 120)

    print(f"\n장애를 주입합니다 — {stage.get('title')}")
    try:
        baseline = setup_stage(stage)
    except LabError as e:
        print(f"장애 주입 실패: {e}")
        return 1

    # 세션은 **장애 주입이 끝난 뒤에** 만든다 — `init_session`이 등급 타이머를
    # 시작하므로, 앞에서 만들면 setup에 걸린 시간이 소요 시간에 얹힌다(복제
    # 스테이지는 binlog를 처음부터 재생하느라 분 단위다).
    #
    # 시드는 변주와 **똑같이** 넘긴다. 이게 없으면 보기가 한 번도 섞이지 않고,
    # 저장소의 mcq 문항은 전부 `answer: 0`으로 작성되므로 진단 문항의 정답이
    # 항상 1번이 된다 — 등급 네 항목 중 '진단 정확도'가 공짜가 된다.
    # 새 `Random`을 주는 이유는 하나를 돌려 쓰면 소비 순서가 서로에게 새어 나가,
    # 스테이지에 변수를 하나 추가한 것만으로 지난 판의 보기 순서가 바뀌기 때문이다.
    session = init_session(stage, random.Random(seed))
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
        # 포기한 판이야말로 회고가 필요하다 — 등급표 없이 노트만 권한다.
        # 해설은 붙이지 않는다: 붙이면 '포기'가 정답을 얻는 지름길이 된다.
        # 스테이지는 다시 플레이할 수 있으므로 클리어했을 때만 해설을 준다.
        print("\n스테이지를 포기했습니다.")
        offer_note(stage, session, summarize(stage, session))
        return 0

    print_result(stage, result)
    save_progress(stage, result["rank"], result["score"], result["elapsed"],
                  session["hints_used"], len(session["violations"]))

    # 순서가 중요하다 — 내 분석을 먼저 쓰게 하고, 그 다음에 해설을 붙인다.
    note = offer_note(stage, session, result)
    appended = append_debrief(note, stage) if note else False
    print_debrief(stage)
    if appended:
        print(f"  해설을 노트에도 덧붙였습니다: {note}\n")
        offer_review_edit(note)
    return 0


def main(argv=None):
    # 라인 모드 화면이 아니라 **여기서** 켠다. 장애 주입(setup)이 먼저 돌고,
    # 복제 스테이지처럼 그게 몇 분 걸리면 그동안의 진행이 통째로 묻힌다.
    ensure_streaming_output()
    parser = argparse.ArgumentParser(
        prog="shoot", description="DB 장애 대응 게임")
    parser.add_argument("command", nargs="?", default="play",
                        help="play(기본) | replay | up | down | doctor | notes")
    parser.add_argument("stage", nargs="?",
                        help="스테이지 id 또는 JSON 경로")
    parser.add_argument("--list", action="store_true", help="스테이지 목록")
    parser.add_argument("--line", action="store_true",
                        help="curses 대신 라인 모드로 실행")
    parser.add_argument("--keep-volumes", action="store_true",
                        help="down 시 볼륨을 남긴다")
    parser.add_argument("--with-postgresql", action="store_true",
                        help="up 시 PostgreSQL 랩도 함께 기동한다")
    parser.add_argument("--dbms", choices=VENDORS,
                        help="해당 DBMS 스테이지만. 선택 화면을 건너뛴다")
    parser.add_argument("--seed", type=int,
                        help="스테이지 변주 시드. 같은 값이면 같은 판이 나온다")
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list()

    cmd = args.command
    stage_arg = args.stage
    # `./shoot 1-3-lock-contention` 처럼 스테이지를 바로 준 경우
    if cmd not in ("play", "up", "down", "doctor", "notes", "replay"):
        stage_arg, cmd = cmd, "play"

    if cmd == "doctor":
        return cmd_doctor()
    if cmd == "notes":
        return cmd_notes(stage_arg)
    if cmd == "replay":
        return cmd_replay(stage_arg)
    if cmd == "up":
        try:
            lab_up(with_postgres=args.with_postgresql)
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

    return cmd_play(stage_arg, force_line=args.line, seed=args.seed,
                    dbms=args.dbms)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n중단했습니다.")
        sys.exit(130)
