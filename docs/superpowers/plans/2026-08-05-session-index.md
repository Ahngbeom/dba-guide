# `{{session_index}}` 치환자 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sessions` setup 단계가 띄우는 세션들이 서로 다른 SQL을 받을 수 있게 하는 치환자 `{{session_index}}`를 엔진에 넣고, pg-1-1의 피해자 세션이 겹치지 않도록 고친다.

**Architecture:** `render_stage`는 로드 시점에 `vars`만 치환하고 모르는 자리는 원문 그대로 통과시킨다(`_render_value`의 `values.get(name, 원문)`). 그 성질을 이용해 `{{session_index}}`를 `setup_stage`까지 살려 보내고, 세션을 띄우는 루프가 자기 번호로 치환한다. 치환되지 않는 자리에 쓰는 실수는 `validate_stage`가 로드 시점에 막는다.

**Tech Stack:** Python 3 표준 라이브러리만 (`re`, `unittest`). 외부 패키지 금지 — 저장소 규약.

## Global Constraints

- 외부 의존성 없음. Python3 표준 라이브러리만 사용한다.
- 모든 주석·문서·오류 메시지는 한국어로 쓴다.
- 테스트는 도커도 MySQL/PostgreSQL도 띄우지 않는다. 전체 스위트는 `python3 -m unittest discover -s tests`로 돌리고 항상 전부 통과해야 한다.
- 예약 이름은 정확히 `session_index`이며, 0-기반이다.
- 치환자가 허용되는 자리는 `type: "sessions"`인 setup 단계의 `sql` 필드 하나뿐이다.
- 기존 스테이지 4개(1-3, 3-1, 4-1, 4-3)의 `sessions` 사용은 손대지 않는다.

**설계 문서:** `docs/superpowers/specs/2026-08-05-session-index-design.md`

## File Structure

| 파일 | 책임 | 변경 |
|---|---|---|
| `scripts/shooting.py` | 엔진. 치환·검증·세션 기동 | 수정 (상수 1, 함수 2 추가, `setup_stage`·`_validate_vars` 수정) |
| `tests/test_shooting.py` | 단위 테스트 | 수정 (테스트 클래스 2개 추가, pg 스테이지 테스트 1개 추가) |
| `shooting/stages/pg-1-1-idle-in-transaction.json` | 스테이지 정의 | 수정 (피해자 `sql`, `_comment`) |
| `docs/shooting-game.md` | 스테이지 작성 규약 | 수정 (`setup` 절에 치환자 설명) |

새 파일 없음. `shooting.py`는 3,200줄로 크지만 저장소가 일관되게 단일 모듈로 유지해 왔으므로 분할하지 않는다.

---

### Task 1: `render_session_sql` — 세션별 치환 (순수 함수)

**Files:**
- Modify: `scripts/shooting.py` (`_render_value` 정의 직후, 현재 841행 뒤)
- Test: `tests/test_shooting.py` (`RenderStageTest` 클래스 뒤)

**Interfaces:**
- Consumes: `_render_value(node, values)`, `_PLACEHOLDER_RE` — 기존 것
- Produces:
  - `SESSION_INDEX_VAR = "session_index"` (모듈 상수)
  - `render_session_sql(sql, index) -> str`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_shooting.py`의 `class RenderStageTest` 블록이 끝난 직후(현재 `class SessionShuffleTest` 바로 위)에 붙인다.

```python
class RenderSessionSqlTest(unittest.TestCase):
    """`sessions` 단계는 세션마다 다른 SQL을 받을 수 있어야 한다.

    이게 없으면 '각자 다른 대상을 잡는 세션 N개'를 선언적으로 쓸 수 없어,
    스테이지가 SQL 안의 random() 같은 것으로 때우게 된다(pg-1-1이 그랬다).
    """

    def test_substitutes_the_session_number(self):
        sql = "UPDATE orders SET status='PAID' WHERE id = 1 + {{session_index}}"
        self.assertEqual(
            shooting.render_session_sql(sql, 0),
            "UPDATE orders SET status='PAID' WHERE id = 1 + 0")
        self.assertEqual(
            shooting.render_session_sql(sql, 3),
            "UPDATE orders SET status='PAID' WHERE id = 1 + 3")

    def test_leaves_other_placeholders_alone(self):
        # 이 함수가 도는 시점에 vars 는 이미 render_stage 가 치환했다. 그런데도
        # 남은 자리를 먹어버리면 SQL이 조용히 깨지므로, 모르는 이름은 건드리지 않는다.
        sql = "SELECT {{rows}} + {{session_index}}"
        self.assertEqual(shooting.render_session_sql(sql, 2),
                         "SELECT {{rows}} + 2")

    def test_sql_without_the_placeholder_is_unchanged(self):
        # sessions 를 쓰는 기존 스테이지 넷은 세션별 차이가 필요 없다.
        sql = "CALL shop.recent_orders_worker()"
        self.assertEqual(shooting.render_session_sql(sql, 7), sql)

    def test_the_reserved_name_is_what_the_engine_substitutes(self):
        # 상수와 치환자 문자열이 어긋나면 검증은 통과하는데 치환이 안 된다.
        sql = "SELECT {{" + shooting.SESSION_INDEX_VAR + "}}"
        self.assertEqual(shooting.render_session_sql(sql, 5), "SELECT 5")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_shooting.RenderSessionSqlTest -v`
Expected: 4개 전부 ERROR — `AttributeError: module 'shooting' has no attribute 'render_session_sql'`

- [ ] **Step 3: 최소 구현을 넣는다**

`scripts/shooting.py`에서 `_render_value` 함수가 끝난 바로 다음(현재 841행, `def render_stage` 앞)에 넣는다.

```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_shooting.RenderSessionSqlTest -v`
Expected: `Ran 4 tests ... OK`

- [ ] **Step 5: 전체 스위트가 여전히 초록인지 본다**

Run: `python3 -m unittest discover -s tests`
Expected: `OK` (기존 434개 + 4개 = 438개)

- [ ] **Step 6: 커밋**

```bash
git add scripts/shooting.py tests/test_shooting.py
git commit -m "$(cat <<'EOF'
Add a per-session placeholder for sessions setup steps

The sessions step spawns count sessions with one identical SQL string, so
"N sessions each grabbing a different target" — the ordinary shape of lock
contention — cannot be written declaratively. render_session_sql fills
{{session_index}} with the session's number and leaves every other
placeholder alone.

Nothing calls it yet.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `setup_stage`가 세션마다 치환해서 띄우게 한다

**Files:**
- Modify: `scripts/shooting.py:1653-1660` (`setup_stage`의 세션 기동 루프)
- Test: `tests/test_shooting.py` (Task 1이 추가한 `RenderSessionSqlTest` 뒤)

**Interfaces:**
- Consumes: `render_session_sql(sql, index)` — Task 1
- Produces: 없음(동작 변경). `setup_stage(stage, log=print)` 시그니처는 그대로다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`RenderSessionSqlTest` 클래스 뒤에 붙인다. 도커 경계만 대신하고, 확인하는 것은 `db_spawn`이 **실제로 받은 SQL 목록**이다.

```python
class SetupStageSessionIndexTest(unittest.TestCase):
    """세션 기동 루프가 번호를 넘기는지.

    순수 함수만 검증하면 배선 누락은 보이지 않는다 — 진단 문항 셔플이 정확히
    그렇게 오래 살아남았다(cmd_play 가 init_session 에 rng 를 안 넘겼다).
    """

    STAGE = {
        "id": "t-1-x", "title": "t",
        "setup": [{"type": "sessions", "on": "primary", "name": "victims",
                   "count": 4,
                   "sql": "UPDATE t SET s='P' WHERE id = 1 + {{session_index}}"}],
        "objectives": [{"id": "o", "type": "state", "on": "primary",
                        "query": "SELECT 1", "expect": {"op": "eq", "value": 0}}],
    }

    @contextlib.contextmanager
    def _fake_lab(self):
        """db_spawn 이 받은 SQL만 모으고, 나머지 도커 호출은 무해하게 만든다."""
        spawned = []
        names = ("kill_app_sessions", "reset_player_log", "app_session_pids",
                 "db_spawn", "_wait_for_new_sessions", "_wait_for_incident",
                 "container_started_at")
        real = {n: getattr(shooting, n) for n in names}
        shooting.kill_app_sessions = lambda target: None
        shooting.reset_player_log = lambda target: None
        shooting.app_session_pids = lambda target: set()
        shooting.db_spawn = (lambda target, user, password, sql, idle_seconds=0:
                             spawned.append(sql))
        shooting._wait_for_new_sessions = (
            lambda target, before, count, timeout=20: set(range(count)))
        shooting._wait_for_incident = lambda stage, timeout=30: True
        shooting.container_started_at = lambda target: "t0"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield spawned
        finally:
            for n, fn in real.items():
                setattr(shooting, n, fn)

    def test_each_session_gets_its_own_number(self):
        with self._fake_lab() as spawned:
            shooting.setup_stage(self.STAGE, log=lambda *a: None)
        self.assertEqual(spawned, [
            "UPDATE t SET s='P' WHERE id = 1 + 0",
            "UPDATE t SET s='P' WHERE id = 1 + 1",
            "UPDATE t SET s='P' WHERE id = 1 + 2",
            "UPDATE t SET s='P' WHERE id = 1 + 3",
        ])

    def test_sessions_without_the_placeholder_are_untouched(self):
        stage = dict(self.STAGE, setup=[
            dict(self.STAGE["setup"][0], count=3, sql="SELECT 1")])
        with self._fake_lab() as spawned:
            shooting.setup_stage(stage, log=lambda *a: None)
        self.assertEqual(spawned, ["SELECT 1"] * 3)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_shooting.SetupStageSessionIndexTest -v`
Expected: `test_each_session_gets_its_own_number`가 FAIL —
`AssertionError: ["UPDATE t SET s='P' WHERE id = 1 + {{session_index}}", ...] != [...]`
(네 번 모두 원문 그대로 들어간다). `test_sessions_without_the_placeholder_are_untouched`는 이미 통과한다 — 고치면서 깨뜨리기 쉬운 것을 지키는 안전망이라 정상이다.

- [ ] **Step 3: 최소 구현**

`scripts/shooting.py:1657-1660`의 루프를 바꾼다.

바꾸기 전:
```python
        for _ in range(count):
            db_spawn(target, step.get("user", "app"),
                        step.get("password", "app"), sql,
                        step.get("idle_seconds", 0))
```

바꾼 뒤:
```python
        for i in range(count):
            # 세션 번호를 여기서 넣는다 — `render_stage`가 로드 시점에 할 수 없는
            # 유일한 치환이다(그때는 몇 번째 세션인지가 아직 없다).
            db_spawn(target, step.get("user", "app"),
                        step.get("password", "app"),
                        render_session_sql(sql, i),
                        step.get("idle_seconds", 0))
```

`session`(단수) 단계도 이 루프를 `count=1`로 지나가지만, 검증이 그 자리의 `{{session_index}}`를 거부하므로(Task 3) 실제로는 치환할 것이 없다. 조건을 붙이지 않는 편이 단순하다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_shooting.SetupStageSessionIndexTest -v`
Expected: `Ran 2 tests ... OK`

- [ ] **Step 5: 전체 스위트**

Run: `python3 -m unittest discover -s tests`
Expected: `OK` (440개)

- [ ] **Step 6: 커밋**

```bash
git add scripts/shooting.py tests/test_shooting.py
git commit -m "$(cat <<'EOF'
Fill the session number when spawning a sessions step

Wires render_session_sql into setup_stage's spawn loop. A sessions step can
now hand each of its sessions a different statement; steps that do not use
the placeholder are unaffected.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 검증 — 예약 이름과 잘못된 자리를 로드 시점에 막는다

**Files:**
- Modify: `scripts/shooting.py` (`_placeholders_in` 정의 직후에 헬퍼 추가, `_validate_vars`의 마지막 블록 교체 — 현재 911-914행)
- Test: `tests/test_shooting.py` (`SetupStageSessionIndexTest` 뒤)

**Interfaces:**
- Consumes: `SESSION_INDEX_VAR`(Task 1), `_placeholders_in(node)`, `_var_names(spec)` — 기존
- Produces: `_stage_outside_session_sql(stage) -> dict`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
class SessionIndexValidationTest(unittest.TestCase):
    """치환되지 않을 자리에 쓴 `{{session_index}}`는 로드 시점에 잡아야 한다.

    놓치면 원문 `{{session_index}}`가 그대로 SQL이나 화면에 나간다 — 이 저장소가
    치환자 이름에 ASCII 제한을 두지 않은 것도 같은 이유였다(치환도 안 되고 오류도
    안 나는 상태가 최악이다).
    """

    def _errs(self, stage):
        return shooting.validate_stage(stage)

    def test_allowed_inside_a_sessions_sql(self):
        stage = _minimal_stage(setup=[
            {"type": "sessions", "on": "primary", "count": 2,
             "sql": "UPDATE t SET s='P' WHERE id = {{session_index}}"}])
        self.assertEqual(self._errs(stage), [])

    def test_rejected_in_a_singular_session_step(self):
        # 단수 session 은 하나뿐이라 번호에 의미가 없다.
        stage = _minimal_stage(setup=[
            {"type": "session", "on": "primary",
             "sql": "UPDATE t SET s='P' WHERE id = {{session_index}}"}])
        self.assertTrue(any("session_index" in e for e in self._errs(stage)),
                        self._errs(stage))

    def test_rejected_in_a_state_objective(self):
        stage = _minimal_stage(objectives=[
            {"id": "o", "type": "state", "on": "primary",
             "query": "SELECT count(*) FROM t WHERE id = {{session_index}}",
             "expect": {"op": "eq", "value": 0}}])
        self.assertTrue(any("session_index" in e for e in self._errs(stage)),
                        self._errs(stage))

    def test_rejected_in_prose(self):
        # brief·hints·debrief 에 쓰면 플레이어 화면에 원문이 그대로 뜬다.
        stage = _minimal_stage(hints=["{{session_index}}번 세션을 보라"])
        self.assertTrue(any("session_index" in e for e in self._errs(stage)),
                        self._errs(stage))

    def test_rejected_in_another_field_of_the_sessions_step(self):
        # 허용되는 것은 sql 필드 하나뿐이다.
        stage = _minimal_stage(setup=[
            {"type": "sessions", "on": "primary", "count": 2,
             "name": "victim-{{session_index}}", "sql": "SELECT 1"}])
        self.assertTrue(any("session_index" in e for e in self._errs(stage)),
                        self._errs(stage))

    def test_vars_cannot_shadow_the_reserved_name(self):
        stage = _minimal_stage(
            vars={"session_index": {"type": "int", "min": 1, "max": 3}},
            setup=[{"type": "sessions", "on": "primary", "count": 2,
                    "sql": "SELECT {{session_index}}"}])
        self.assertTrue(any("예약" in e for e in self._errs(stage)),
                        self._errs(stage))

    def test_the_reserved_name_is_not_reported_as_undefined(self):
        # vars 에 없다고 '정의되지 않은 변수'로 잡히면 스테이지를 못 쓴다.
        stage = _minimal_stage(setup=[
            {"type": "sessions", "on": "primary", "count": 2,
             "sql": "SELECT {{session_index}}"}])
        self.assertFalse([e for e in self._errs(stage) if "정의되지 않은" in e],
                         self._errs(stage))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_shooting.SessionIndexValidationTest -v`
Expected: 7개 중 5개 FAIL. `test_allowed_inside_a_sessions_sql`·`test_the_reserved_name_is_not_reported_as_undefined`는 "정의되지 않은 변수를 참조합니다: {{session_index}}" 때문에 실패하고, 나머지 잘못된-자리 테스트 4개는 **엉뚱한 이유로 통과할 수 있다**(같은 '정의되지 않은 변수' 오류가 우연히 `session_index` 문자열을 담고 있다). 그래서 Step 3에서 예약 처리를 먼저 넣고 Step 4에서 다시 확인한다.

- [ ] **Step 3: 예약 처리와 자리 검사를 넣는다**

먼저 `_placeholders_in` 함수가 끝난 직후(현재 881행, `def _validate_vars` 앞)에 헬퍼를 넣는다.

```python
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
```

그다음 `_validate_vars`의 마지막 블록(현재 911-914행)을 교체한다.

바꾸기 전:
```python
    known = _var_names(spec)
    for ref in sorted(_placeholders_in(stage) - known):
        errs.append(f"정의되지 않은 변수를 참조합니다: {{{{{ref}}}}}")
    return errs
```

바꾼 뒤:
```python
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
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_shooting.SessionIndexValidationTest -v`
Expected: `Ran 7 tests ... OK`

잘못된-자리 테스트 4개가 이제 **의도한 오류 메시지**로 통과하는지 눈으로 확인한다:

Run: `python3 -c "import sys; sys.path.insert(0,'scripts'); import shooting; print(shooting.validate_stage({'id':'x','title':'t','objectives':[{'id':'o','type':'state','query':'SELECT {{session_index}}','expect':{'op':'eq','value':0}}]}))"`
Expected: `['{{session_index}}는 type이 sessions인 setup 단계의 sql 에서만 쓸 수 있습니다 (다른 자리에서는 치환되지 않습니다)']` — '정의되지 않은 변수' 오류는 없어야 한다.

- [ ] **Step 5: 전체 스위트**

Run: `python3 -m unittest discover -s tests`
Expected: `OK` (447개). 기존 스테이지 14개는 이 이름을 쓰지 않으므로 `load_stage` 검증에 영향이 없다.

- [ ] **Step 6: 커밋**

```bash
git add scripts/shooting.py tests/test_shooting.py
git commit -m "$(cat <<'EOF'
Reject {{session_index}} where it would never be substituted

The name is only filled while spawning a sessions step, so anywhere else —
a singular session, a state query, a hint — the literal {{session_index}}
would reach the server or the player's screen. validate_stage now allows the
name in a sessions step's sql, refuses it everywhere else, and refuses a
vars declaration that shadows it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: pg-1-1의 피해자를 겹치지 않게 하고 문서를 맞춘다

**Files:**
- Modify: `shooting/stages/pg-1-1-idle-in-transaction.json` (`setup`의 세 번째 단계 — `name: "payment-api"`)
- Modify: `docs/shooting-game.md` (`setup` 절, `sessions` 설명 부근)
- Test: `tests/test_shooting.py` (`PostgresStageTest` 클래스 안, `test_variables_are_all_declared` 앞)

**Interfaces:**
- Consumes: `render_session_sql(sql, index)`(Task 1), `render_stage(stage, rng)` — 기존
- Produces: 없음

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`PostgresStageTest` 클래스 안, `test_variables_are_all_declared` 메서드 **앞**에 넣는다.

```python
    def test_victims_take_distinct_rows_inside_the_lock(self):
        """피해자가 서로 다른 행을 잡아야 pg_blocking_pids 가 범인만 지목한다.

        실측(설계 문서 참고): 넷이 같은 행을 노리면 막힌 네 줄 중 범인이 등장하는
        것은 한 줄뿐이고, 마지막 대기자의 blockers 에는 범인이 아예 없다.
        PostgreSQL이 같은 튜플의 대기자를 tuple lock 으로 직렬화하기 때문이다.
        그 상태에서는 '사슬의 뿌리를 끊어라'라는 이 스테이지의 교훈이 정확히
        반대를 가리킨다.

        범위 관계도 함께 고정한다 — payments 와 rows 는 따로 선언된 두 변수라,
        한쪽만 넓히면 피해자가 잠긴 구간 밖으로 나가 아무에게도 막히지 않는다.
        """
        base = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages"
            / "pg-1-1-idle-in-transaction.json")
        for seed in range(100):
            st = shooting.render_stage(base, random.Random(seed))
            culprit = next(s for s in st["setup"] if s.get("culprit"))
            victims = next(s for s in st["setup"]
                           if s["type"] == "sessions")
            locked_to = int(re.search(r"id <= (\d+)", culprit["sql"]).group(1))
            ids = []
            for i in range(int(victims["count"])):
                sql = shooting.render_session_sql(victims["sql"], i)
                ids.append(int(re.search(r"id = (\d+)", sql).group(1)))
            self.assertEqual(len(set(ids)), len(ids), (seed, ids))
            self.assertTrue(all(1 <= v <= locked_to for v in ids),
                            (seed, ids, locked_to))
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_shooting.PostgresStageTest.test_victims_take_distinct_rows_inside_the_lock -v`
Expected: ERROR — `AttributeError: 'NoneType' object has no attribute 'group'`. 현재 피해자 SQL은 `WHERE id = 1 + floor(random() * 50)::int` 라서 `id = (\d+)` 정규식이 걸리지 않는다. 이 실패 자체가 "행이 실행 시점에야 정해진다 = 겹칠 수 있다"는 뜻이다.

- [ ] **Step 3: 스테이지를 고친다**

`shooting/stages/pg-1-1-idle-in-transaction.json`에서 `"name": "payment-api"` 단계의 `_comment`와 `sql`을 교체한다.

바꾸기 전:
```json
      "_comment": "피해자들. 배치가 잠근 범위 안의 행을 건드리려다 막힌다. **세션마다 다른 행을 고르는 것이 중요하다** — 전부 같은 행을 노리면 서로도 막아서 pg_blocking_pids가 범인 하나가 아니라 사슬 전체를 지목한다(실측). 그러면 도구가 지목한 세션을 끊었다가 kill_precision에 걸리는 부당한 함정이 된다. idle_seconds를 주지 않는 이유는 이미 잠금 대기로 붙잡혀 있기 때문이고, 범인이 사라지면 각자 커밋하고 스스로 빠져나간다 — 그래서 복구를 확인하려고 플레이어가 손으로 UPDATE할 필요가 없다.",
      "sql": "UPDATE orders SET status = 'PAID' WHERE id = 1 + floor(random() * {{rows}})::int"
```

바꾼 뒤:
```json
      "_comment": "피해자들. 배치가 잠근 범위 안의 행을 건드리려다 막힌다. **세션마다 다른 행을 잡는 것이 판정의 전제다** — {{session_index}}가 그것을 구조적으로 보장한다(예전에는 random()이라 최악 조합에서 41% 확률로 겹쳤다). 겹치면 PostgreSQL이 같은 튜플의 대기자를 tuple lock으로 직렬화하는 탓에 pg_blocking_pids가 사슬 중간을 지목한다. 실측: 넷이 같은 행을 노렸을 때 막힌 네 줄 중 범인이 등장하는 것은 한 줄뿐이었고 마지막 대기자의 blockers에는 범인이 아예 없었다 — 도구가 피해자를 가리키니 그것을 끊었다가 kill_precision에 걸리는 부당한 함정이 된다. (MySQL 1-3이 피해자 전원을 같은 행에 몰아넣고도 멀쩡한 것은 InnoDB가 blocker로 락 보유자를 보고하기 때문이다.) payments가 최대 6이고 rows가 최소 30이라 id 1~6은 항상 잠긴 구간 안이다. idle_seconds를 주지 않는 이유는 이미 잠금 대기로 붙잡혀 있기 때문이고, 범인이 사라지면 각자 커밋하고 스스로 빠져나간다 — 그래서 복구를 확인하려고 플레이어가 손으로 UPDATE할 필요가 없다.",
      "sql": "UPDATE orders SET status = 'PAID' WHERE id = 1 + {{session_index}}"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_shooting.PostgresStageTest -v`
Expected: `Ran 9 tests ... OK`

- [ ] **Step 5: 문서를 맞춘다**

`docs/shooting-game.md`의 `setup` 절에서 세션 단계 필드를 나열하는 문단(현재 404-405행)을 찾는다. 그 문단은 이렇게 끝난다:

```markdown
세션 단계의 필드: `on`(기본 `primary`), `user`/`password`(기본 `app`/`app`),
`name`(로그 표시용), `count`(`sessions`만), `culprit`, `idle_seconds`.
```

바로 그 뒤, `idle_seconds`를 설명하는 문단 **앞**에 다음을 넣는다.

```markdown
`sessions` 단계의 `sql` 안에서는 **`{{session_index}}`** 를 쓸 수 있다. 엔진이
세션을 띄우면서 0, 1, 2… 로 채우므로 "각자 다른 대상을 잡는 세션 N개"를 선언적으로
쓸 수 있다. 이 이름은 `vars`에 선언하지 않으며(선언하면 검증 실패), **`sessions`
단계의 `sql` 밖에서 쓰면 검증이 거부한다** — 다른 자리에서는 치환될 기회가 없어
원문이 그대로 SQL이나 플레이어 화면에 나가기 때문이다.

세션들이 같은 행을 노려도 되는지는 **벤더에 따라 다르다.** MySQL 1-3은 피해자
전원을 같은 행에 몰아넣는다 — InnoDB는 대기자를 같은 레코드 락 큐에 붙이고
blocker로 **락 보유자**를 보고하므로 진단 화면이 범인 하나를 가리킨다. PostgreSQL은
같은 튜플의 대기자를 **tuple lock으로 직렬화**하므로 `pg_blocking_pids`가 사슬
중간을 지목한다. 실측(피해자 4개가 같은 행):

```
    pid  state                 wait    blocked_by
    140  idle in transaction   Client  -              ← 범인
    153  active                Lock    140
    162  active                Lock    153
    169  active                Lock    153,162
    175  active                Lock    153,162,169
```

막힌 네 줄 중 범인이 등장하는 것은 한 줄뿐이고, pid 175의 blockers에는 범인이
없다. '사슬의 뿌리를 끊어라'를 가르치는 스테이지에서 이건 부당한 함정이므로,
PostgreSQL 잠금 스테이지의 피해자는 `{{session_index}}`로 행을 갈라야 한다.
```

- [ ] **Step 6: 전체 스위트**

Run: `python3 -m unittest discover -s tests`
Expected: `OK` (448개)

- [ ] **Step 7: 랩에서 실제로 고쳐졌는지 확인한다**

랩이 떠 있어야 한다(`docker ps | grep dbshoot-postgres`). 없으면 `./shoot up --with-postgresql`.

```bash
python3 - <<'PY'
import sys, time, random
sys.path.insert(0, "scripts")
import shooting as S
stage = S.load_stage("shooting/stages/pg-1-1-idle-in-transaction.json")
stage = S.render_stage(stage, random.Random(1))
S.kill_app_sessions("postgres"); time.sleep(1)
S.setup_stage(stage)
time.sleep(4)
rows = S.db_query("postgres", """
    SELECT pid, state, coalesce(array_to_string(pg_blocking_pids(pid), ','), '')
    FROM pg_stat_activity WHERE usename='app' ORDER BY backend_start""")
culprits = [r[0] for r in rows if r[1] == "idle in transaction"]
blockers = set()
for r in rows:
    blockers |= {p for p in r[2].split(",") if p}
for r in rows:
    print(f"{r[0]:>7} {r[1]:22} {r[2] or '-'}")
print("\n범인 외에 지목된 pid:", sorted(blockers - set(culprits)) or "없음")
S.kill_app_sessions("postgres")
PY
```

Expected: 마지막 줄이 `범인 외에 지목된 pid: 없음`. 모든 피해자의 `blocked_by`가 범인 pid 하나여야 한다.

여러 판에서도 그런지 보려면 위 스크립트의 `random.Random(1)`을 2, 3, 4로 바꿔 몇 번 더 돌린다(예전에는 시드에 따라 겹쳤다).

- [ ] **Step 8: 랩 정리**

랩을 계속 쓸 것이 아니면 내린다. 볼륨까지 지울지 물어본다.

Run: `./shoot down`

- [ ] **Step 9: 커밋**

```bash
git add shooting/stages/pg-1-1-idle-in-transaction.json docs/shooting-game.md tests/test_shooting.py
git commit -m "$(cat <<'EOF'
Give each stalled payment its own row

The victims picked a row with random(), so in the declared parameter range
they collided up to 41% of the time — and the stage's own comment called
that state an unfair trap. {{session_index}} makes distinctness structural.

Measured with four victims on one row: pg_blocking_pids named the culprit in
one of the four blocked rows, and the last waiter's blockers did not include
it at all. PostgreSQL serialises same-tuple waiters through a tuple lock, so
the diagnostic query the stage recommends pointed at victims three times and
at the culprit once — the exact inverse of the lesson.

The doc now records that this is where the vendors diverge: InnoDB reports
the lock holder, which is why MySQL 1-3 can put every victim on one row.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage**

| 스펙 항목 | 구현 태스크 |
|---|---|
| 치환자 `{{session_index}}`, 0-기반 | Task 1 |
| 렌더링 시점(`setup_stage`의 기동 루프) | Task 2 |
| 검증 규칙 1(자리 제한) | Task 3 |
| 검증 규칙 2(예약 이름 가림) | Task 3 |
| pg-1-1 피해자 SQL 교체 + `_comment` | Task 4 |
| `docs/shooting-game.md` 문서화 | Task 4 Step 5 |
| 테스트 1(순수 치환) | Task 1 Step 1 |
| 테스트 2(setup_stage 배선) | Task 2 Step 1 |
| 테스트 3(자리 제한) | Task 3 Step 1 |
| 테스트 4(예약어 가림) | Task 3 Step 1 |
| 테스트 5(스테이지 불변식) | Task 4 Step 1 |
| 랩 실측 재확인 | Task 4 Step 7 |

빠진 항목 없음.

**2. Placeholder scan** — "TBD"·"적절히 처리"·"위와 유사"류 없음. 모든 코드 단계에 실제 코드가 있다.

**3. Type consistency** — `SESSION_INDEX_VAR`(문자열 `"session_index"`), `render_session_sql(sql, index) -> str`, `_stage_outside_session_sql(stage) -> dict` 세 이름이 Task 1·2·3·4에서 동일하게 쓰인다. 테스트가 부르는 `shooting.render_session_sql`·`shooting.SESSION_INDEX_VAR`·`shooting.validate_stage`·`shooting.setup_stage`·`shooting.render_stage`는 전부 모듈 최상위에 있다. `_minimal_stage`는 `tests/test_shooting.py:291`의 기존 헬퍼이고 `**over`로 임의 키를 덮어쓰므로 Task 3의 사용법(`setup=`, `objectives=`, `hints=`, `vars=`)이 모두 동작한다.

**주의 사항 하나** — Task 3 Step 2의 예상 실패는 "5개 FAIL"이 아닐 수 있다. 잘못된-자리 테스트 4개가 기존 '정의되지 않은 변수' 오류로 **우연히 통과**할 수 있어서, Step 4에서 오류 메시지를 눈으로 확인하는 단계를 따로 두었다. 그 확인을 건너뛰면 자리 검사가 실제로 도는지 알 수 없다.
