#!/usr/bin/env python3
"""shooting.py 순수 로직 단위 테스트 (표준 라이브러리 unittest).

Docker도 MySQL도 띄우지 않는다 — 가짜 로그 행과 가짜 쿼리 결과를 주입해
판정 로직만 검증한다.

실행:
    python3 -m unittest discover -s tests
"""
import builtins
import contextlib
import io
import json
import shutil
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import shooting  # noqa: E402


class ParseTsvTest(unittest.TestCase):
    def test_splits_tabs_and_drops_blank_lines(self):
        self.assertEqual(shooting.parse_tsv("a\tb\n\nc\td\n"),
                         [["a", "b"], ["c", "d"]])

    def test_empty_output(self):
        self.assertEqual(shooting.parse_tsv(""), [])
        self.assertEqual(shooting.parse_tsv(None), [])

    def test_first_scalar(self):
        self.assertEqual(shooting.first_scalar([["7", "x"]]), "7")
        self.assertIsNone(shooting.first_scalar([]))
        self.assertEqual(shooting.first_scalar([], "기본"), "기본")


class CoerceTest(unittest.TestCase):
    def test_numeric_strings_become_numbers(self):
        self.assertEqual(shooting.coerce("0"), 0)
        self.assertEqual(shooting.coerce("-12"), -12)
        self.assertEqual(shooting.coerce("1.5"), 1.5)

    def test_null_becomes_none(self):
        self.assertIsNone(shooting.coerce("NULL"))

    def test_plain_text_untouched(self):
        self.assertEqual(shooting.coerce(" PAID "), "PAID")


class EvaluateExpectTest(unittest.TestCase):
    def test_eq_across_string_and_number(self):
        # mysql 배치 출력은 전부 문자열이라 이 변환이 없으면 항상 거짓이 된다.
        self.assertTrue(shooting.evaluate_expect({"op": "eq", "value": 0}, "0"))
        self.assertFalse(shooting.evaluate_expect({"op": "eq", "value": 0}, "6"))

    def test_eq_on_text(self):
        self.assertTrue(
            shooting.evaluate_expect({"op": "eq", "value": "PAID"}, "PAID"))
        self.assertFalse(
            shooting.evaluate_expect({"op": "eq", "value": "PAID"}, "NEW"))

    def test_default_op_is_eq(self):
        self.assertTrue(shooting.evaluate_expect({"value": "PAID"}, "PAID"))

    def test_comparisons(self):
        self.assertTrue(shooting.evaluate_expect({"op": "lt", "value": 1}, "0.5"))
        self.assertTrue(shooting.evaluate_expect({"op": "lte", "value": 1}, "1"))
        self.assertTrue(shooting.evaluate_expect({"op": "gt", "value": 1}, "2"))
        self.assertTrue(shooting.evaluate_expect({"op": "gte", "value": 2}, "2"))
        self.assertFalse(shooting.evaluate_expect({"op": "gt", "value": 1}, "1"))

    def test_comparison_against_non_numeric_is_false(self):
        self.assertFalse(shooting.evaluate_expect({"op": "lt", "value": 1}, "ON"))

    def test_ne_contains_in(self):
        self.assertTrue(shooting.evaluate_expect({"op": "ne", "value": 0}, "3"))
        self.assertTrue(
            shooting.evaluate_expect({"op": "contains", "value": "Yes"},
                                     "Slave_IO_Running: Yes"))
        self.assertTrue(
            shooting.evaluate_expect({"op": "in", "value": [0, 1]}, "1"))
        self.assertFalse(
            shooting.evaluate_expect({"op": "in", "value": [0, 1]}, "2"))

    def test_unknown_op_is_false(self):
        self.assertFalse(shooting.evaluate_expect({"op": "매치", "value": 1}, "1"))

    def test_none_value_does_not_crash(self):
        self.assertFalse(shooting.evaluate_expect({"op": "eq", "value": 0}, None))


class UpdateHoldTest(unittest.TestCase):
    """상태가 N초간 '연속으로' 유지돼야 인정한다."""

    def test_not_satisfied_keeps_timer_cleared(self):
        state, done = shooting.update_hold({"since": None}, False, 100.0, 10)
        self.assertIsNone(state["since"])
        self.assertFalse(done)

    def test_timer_starts_on_first_satisfaction(self):
        state, done = shooting.update_hold({"since": None}, True, 100.0, 10)
        self.assertEqual(state["since"], 100.0)
        self.assertFalse(done)

    def test_done_after_hold_elapsed(self):
        state, done = shooting.update_hold({"since": 100.0}, True, 110.0, 10)
        self.assertTrue(done)

    def test_interruption_resets_the_timer(self):
        # 9초 유지하다가 한 번 끊기면 처음부터 다시 재야 한다.
        state, _ = shooting.update_hold({"since": 100.0}, True, 109.0, 10)
        state, done = shooting.update_hold(state, False, 109.5, 10)
        self.assertIsNone(state["since"])
        self.assertFalse(done)
        state, done = shooting.update_hold(state, True, 110.0, 10)
        self.assertEqual(state["since"], 110.0)
        self.assertFalse(done)

    def test_zero_hold_is_immediate(self):
        _, done = shooting.update_hold({"since": None}, True, 100.0, 0)
        self.assertTrue(done)

    def test_hold_remaining(self):
        self.assertEqual(shooting.hold_remaining({"since": None}, 100.0, 10), 10)
        self.assertEqual(shooting.hold_remaining({"since": 100.0}, 104.0, 10), 6)
        self.assertEqual(shooting.hold_remaining({"since": 100.0}, 120.0, 10), 0)


class ParseKillTargetsTest(unittest.TestCase):
    def test_plain_kill(self):
        self.assertEqual(shooting.parse_kill_targets(["KILL 72"]), [72])

    def test_connection_and_query_forms(self):
        self.assertEqual(
            shooting.parse_kill_targets(
                ["KILL CONNECTION 7", "KILL QUERY 8", "kill connection 9"]),
            [7, 8, 9])

    def test_trailing_semicolon_and_spacing(self):
        self.assertEqual(shooting.parse_kill_targets(["  kill   72 ;  "]), [72])

    def test_ignores_non_kill_commands(self):
        cmds = ["SELECT COUNT(*) FROM performance_schema.data_lock_waits",
                "SHOW PROCESSLIST"]
        self.assertEqual(shooting.parse_kill_targets(cmds), [])

    def test_does_not_match_kill_inside_other_text(self):
        # 문자열 안에 KILL이 들어간 질의를 KILL로 오인하면 안 된다.
        self.assertEqual(
            shooting.parse_kill_targets(
                ["SELECT 'KILL 72' FROM dual",
                 "SELECT CONCAT('KILL ', id) FROM information_schema.processlist"]),
            [])

    def test_empty_and_none(self):
        self.assertEqual(shooting.parse_kill_targets([]), [])
        self.assertEqual(shooting.parse_kill_targets(None), [])
        self.assertEqual(shooting.parse_kill_targets([None, ""]), [])


class KillPrecisionTest(unittest.TestCase):
    """상태로는 구분되지 않는 '방법'을 채점하는 부분."""

    def test_killing_only_the_culprit_is_clean(self):
        self.assertEqual(shooting.count_extra_kills([72], [72]), 0)

    def test_killing_bystanders_counts(self):
        self.assertEqual(shooting.count_extra_kills([72, 73, 74], [72]), 2)

    def test_kill_all_counts_every_bystander(self):
        self.assertEqual(
            shooting.count_extra_kills([70, 71, 72, 73], [72]), 3)

    def test_no_kills_at_all(self):
        self.assertEqual(shooting.count_extra_kills([], [72]), 0)

    def test_same_pid_on_another_server_is_not_the_culprit(self):
        # pid는 서버마다 따로 매겨진다. 서버를 함께 보지 않으면 replica의
        # 범인 12번을 근거로 primary의 무고한 12번을 죽인 것이 무사통과한다.
        culprit = [("replica", 12)]
        self.assertEqual(
            shooting.count_extra_kills([("replica", 12)], culprit), 0)
        self.assertEqual(
            shooting.count_extra_kills([("primary", 12)], culprit), 1)


class DetectViolationsTest(unittest.TestCase):
    CONSTRAINTS = [
        {"id": "no-restart", "label": "DB 재시작 금지",
         "detect": "container_restart"},
        {"id": "no-kill-all", "label": "무차별 KILL 금지",
         "detect": "kill_precision", "max_extra_kills": 0},
    ]

    def test_clean_run_has_no_violations(self):
        ctx = {"kill_targets": [72], "allowed_pids": [72], "restarted": False}
        self.assertEqual(shooting.detect_violations(self.CONSTRAINTS, ctx), [])

    def test_restart_detected(self):
        ctx = {"kill_targets": [], "allowed_pids": [72], "restarted": True}
        found = shooting.detect_violations(self.CONSTRAINTS, ctx)
        self.assertEqual([v["id"] for v in found], ["no-restart"])

    def test_indiscriminate_kill_detected(self):
        ctx = {"kill_targets": [70, 71, 72], "allowed_pids": [72],
               "restarted": False}
        found = shooting.detect_violations(self.CONSTRAINTS, ctx)
        self.assertEqual([v["id"] for v in found], ["no-kill-all"])
        self.assertIn("2건", found[0]["detail"])

    def test_tolerance_can_be_raised(self):
        constraints = [{"id": "loose", "label": "느슨",
                        "detect": "kill_precision", "max_extra_kills": 2}]
        ctx = {"kill_targets": [70, 71, 72], "allowed_pids": [72],
               "restarted": False}
        self.assertEqual(shooting.detect_violations(constraints, ctx), [])

    def test_no_constraints(self):
        self.assertEqual(shooting.detect_violations(None, {}), [])


class RankTest(unittest.TestCase):
    def test_rank_thresholds(self):
        self.assertEqual(shooting.rank_for(4), "S")
        self.assertEqual(shooting.rank_for(3), "A")
        self.assertEqual(shooting.rank_for(2), "B")
        self.assertEqual(shooting.rank_for(1), "C")
        self.assertEqual(shooting.rank_for(0), "C")

    def test_perfect_run_is_s(self):
        items, score, rank = shooting.rank_breakdown(
            elapsed=120, target_seconds=300, hints_used=0, violations=0,
            quiz_correct=2, quiz_total=2)
        self.assertEqual((score, rank), (4, "S"))
        self.assertTrue(all(ok for _, _, ok in items))

    def test_slow_but_otherwise_clean_is_a(self):
        _, score, rank = shooting.rank_breakdown(
            elapsed=900, target_seconds=300, hints_used=0, violations=0,
            quiz_correct=2, quiz_total=2)
        self.assertEqual((score, rank), (3, "A"))

    def test_indiscriminate_kill_costs_a_grade(self):
        _, clean, _ = shooting.rank_breakdown(120, 300, 0, 0, 2, 2)
        _, messy, _ = shooting.rank_breakdown(120, 300, 0, 1, 2, 2)
        self.assertEqual(clean - messy, 1)

    def test_no_quiz_gives_the_bonus(self):
        _, _, rank = shooting.rank_breakdown(120, 300, 0, 0, 0, 0)
        self.assertEqual(rank, "S")

    def test_missing_target_seconds_gives_the_bonus(self):
        """기준이 없으면 감점하지 않는다 — 문항 없는 스테이지와 같은 원칙.

        예전에는 시간 보너스가 항상 실패해 시간 무제한 스테이지에서 S가
        구조적으로 불가능했다.
        """
        items, score, rank = shooting.rank_breakdown(10, None, 0, 0, 0, 0)
        self.assertEqual((score, rank), (4, "S"))
        self.assertTrue(items[0][2])                    # 시간 항목이 보너스

    def test_missing_target_seconds_shows_no_limit_label(self):
        items, _, _ = shooting.rank_breakdown(10, None, 0, 0, 0, 0)
        self.assertIn("목표 없음", items[0][1])
        self.assertNotIn("00:00", items[0][1])          # 목표 00:00로 보이면 오해

    def test_target_seconds_zero_is_also_no_limit(self):
        # 0은 "0초 안에 끝내라"가 아니라 값이 없는 것과 같이 읽는다.
        _, score, _ = shooting.rank_breakdown(10, 0, 0, 0, 0, 0)
        self.assertEqual(score, 4)


class FmtTest(unittest.TestCase):
    def test_mmss(self):
        self.assertEqual(shooting.fmt_mmss(0), "00:00")
        self.assertEqual(shooting.fmt_mmss(65), "01:05")
        self.assertEqual(shooting.fmt_mmss(3599), "59:59")
        self.assertEqual(shooting.fmt_mmss(None), "00:00")
        self.assertEqual(shooting.fmt_mmss(-5), "00:00")


def _minimal_stage(**over):
    stage = {
        "id": "1-1-test",
        "title": "테스트",
        "objectives": [
            {"id": "o1", "type": "state", "on": "primary",
             "query": "SELECT 1", "expect": {"op": "eq", "value": 1}},
        ],
    }
    stage.update(over)
    return stage


class ValidateStageTest(unittest.TestCase):
    def test_minimal_stage_is_valid(self):
        self.assertEqual(shooting.validate_stage(_minimal_stage()), [])

    def test_missing_required_fields(self):
        errs = shooting.validate_stage({"objectives": []})
        self.assertTrue(any("id" in e for e in errs))
        self.assertTrue(any("title" in e for e in errs))

    def test_duplicate_objective_ids(self):
        obj = {"id": "dup", "type": "state", "query": "SELECT 1",
               "expect": {"op": "eq", "value": 1}}
        errs = shooting.validate_stage(
            _minimal_stage(objectives=[dict(obj), dict(obj)]))
        self.assertTrue(any("중복" in e for e in errs))

    def test_unknown_objective_type(self):
        errs = shooting.validate_stage(
            _minimal_stage(objectives=[{"id": "x", "type": "여기없음"}]))
        self.assertTrue(any("type" in e for e in errs))

    def test_state_objective_needs_query(self):
        errs = shooting.validate_stage(
            _minimal_stage(objectives=[{"id": "x", "type": "state"}]))
        self.assertTrue(any("query" in e for e in errs))

    def test_unknown_expect_op(self):
        errs = shooting.validate_stage(_minimal_stage(objectives=[
            {"id": "x", "type": "state", "query": "SELECT 1",
             "expect": {"op": "비슷함", "value": 1}}]))
        self.assertTrue(any("연산자" in e for e in errs))

    def test_unknown_container_target(self):
        errs = shooting.validate_stage(_minimal_stage(objectives=[
            {"id": "x", "type": "state", "on": "어딘가", "query": "SELECT 1",
             "expect": {"op": "eq", "value": 1}}]))
        self.assertTrue(any("대상" in e for e in errs))

    def test_mcq_answer_out_of_range(self):
        errs = shooting.validate_stage(_minimal_stage(objectives=[
            {"id": "q", "type": "quiz", "question": {
                "type": "mcq", "q": "?", "choices": ["a", "b"], "answer": 5}}]))
        self.assertTrue(any("answer" in e for e in errs))

    def test_short_question_needs_accept(self):
        errs = shooting.validate_stage(_minimal_stage(objectives=[
            {"id": "q", "type": "quiz",
             "question": {"type": "short", "q": "?"}}]))
        self.assertTrue(any("accept" in e for e in errs))

    def test_setup_step_validation(self):
        errs = shooting.validate_stage(_minimal_stage(
            setup=[{"type": "없는타입", "sql": "SELECT 1"}]))
        self.assertTrue(any("type" in e for e in errs))

        errs = shooting.validate_stage(_minimal_stage(
            setup=[{"type": "sessions", "sql": "SELECT 1", "count": 0}]))
        self.assertTrue(any("count" in e for e in errs))

    def test_unknown_constraint_detector(self):
        errs = shooting.validate_stage(_minimal_stage(
            constraints=[{"id": "c", "detect": "텔레파시"}]))
        self.assertTrue(any("detect" in e for e in errs))

    def test_wait_gtid_sync_step_needs_no_sql(self):
        # 이 단계는 대기 자체가 내용이라 실행할 SQL이 없다.
        errs = shooting.validate_stage(_minimal_stage(
            setup=[{"type": "wait_gtid_sync", "on": "replica",
                    "source": "primary", "timeout_seconds": 60}]))
        self.assertEqual(errs, [])

    def test_wait_gtid_sync_rejects_unknown_source(self):
        errs = shooting.validate_stage(_minimal_stage(
            setup=[{"type": "wait_gtid_sync", "on": "replica",
                    "source": "어딘가"}]))
        self.assertTrue(any("source" in e for e in errs))

    def test_sql_step_still_requires_sql(self):
        # wait_gtid_sync 예외가 다른 단계의 검사까지 풀어버리면 안 된다.
        errs = shooting.validate_stage(_minimal_stage(
            setup=[{"type": "sql", "on": "primary"}]))
        self.assertTrue(any("sql" in e for e in errs))


class TolerateErrorTest(unittest.TestCase):
    """조회 대상이 아직 없는 목표를 '고장'이 아니라 '미충족'으로 읽는 경로.

    복제를 붙이기 전의 빈 replica에서 shop.orders를 세면 당연히 에러다.
    그걸 빨간 '감시 오류'로 띄우면 플레이어가 감시가 죽은 줄로 오인한다.
    """

    def setUp(self):
        self._real_mysql = shooting.mysql

        def boom(*a, **kw):
            raise shooting.LabError("Unknown database 'shop'")

        shooting.mysql = boom
        self.addCleanup(setattr, shooting, "mysql", self._real_mysql)

    def _run(self, **extra):
        obj = {"id": "o1", "type": "state", "on": "replica",
               "query": "SELECT COUNT(*) FROM shop.orders",
               "expect": {"op": "eq", "value": 200000}}
        obj.update(extra)
        stage = _minimal_stage(objectives=[obj])
        session = shooting.init_session(stage)
        step = {"kind": "state", "id": "o1", "label": "복제 데이터"}
        err = shooting.run_watch_step(step, stage, session, {})
        return err, session["states"]["o1"]

    def test_tolerated_error_reads_as_unmet_without_alarming(self):
        err, st = self._run(tolerate_error=True)
        self.assertIsNone(err)
        self.assertIsNone(st["error"])
        self.assertFalse(st["done"])

    def test_untolerated_error_still_surfaces(self):
        err, st = self._run()
        self.assertIn("Unknown database", err)
        self.assertIn("Unknown database", st["error"])


class WatchPlanTest(unittest.TestCase):
    """감시를 한 바퀴에 한 단계씩 나눠 도는 계획 로직.

    한 번에 몰아서 하면 docker 호출 4개가 연달아 일어나 UI가 ~240ms 멈추고,
    그동안 키 입력이 처리되지 않아 '키가 안 먹는다'처럼 느껴진다.
    """

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")

    def test_one_step_per_state_objective_plus_log_and_restart(self):
        steps = shooting.watch_steps(self.stage)
        kinds = [s["kind"] for s in steps]
        self.assertEqual(kinds.count("state"), 2)   # 대기 세션 + 주문 상태
        self.assertEqual(kinds.count("commands"), 1)
        self.assertEqual(kinds.count("restart"), 1)

    def test_every_step_has_a_label_for_the_indicator(self):
        for step in shooting.watch_steps(self.stage):
            self.assertTrue(step.get("label"))

    def test_watch_targets_includes_setup_containers(self):
        self.assertEqual(shooting.watch_targets(self.stage), {"primary"})
        stage = _minimal_stage(setup=[{"type": "sql", "on": "replica",
                                       "sql": "SELECT 1"}])
        self.assertEqual(shooting.watch_targets(stage), {"primary", "replica"})

    def test_watch_targets_includes_objective_containers(self):
        # 장애를 넣은 서버와 플레이어가 고쳐야 할 서버가 다를 수 있다.
        # 목표 대상이 빠지면 그 서버의 명령 로그가 통째로 감시되지 않는다.
        stage = _minimal_stage(objectives=[
            {"id": "a", "type": "state", "on": "replica",
             "query": "SELECT 1", "expect": {"op": "eq", "value": 1}}])
        self.assertEqual(shooting.watch_targets(stage), {"primary", "replica"})

    def test_command_log_is_watched_on_every_container(self):
        # primary만 읽으면 플레이어가 replica에서 한 일이 보이지 않아
        # kill_precision이 영원히 걸리지 않고 회고 타임라인도 비어버린다.
        stage = _minimal_stage(setup=[{"type": "sql", "on": "replica",
                                       "sql": "SELECT 1"}])
        steps = shooting.watch_steps(stage)
        watched = {s["target"] for s in steps if s["kind"] == "commands"}
        self.assertEqual(watched, {"primary", "replica"})

    def test_advance_returns_exactly_one_step_at_a_time(self):
        watch = shooting.init_watch(self.stage)
        total = len(watch["steps"])
        seen = []
        now = 1000.0
        for _ in range(total):
            watch, step = shooting.advance_watch(watch, now)
            self.assertIsNotNone(step)
            seen.append(step)
            now += 0.07                      # docker 호출 1회 정도
        self.assertEqual(len(seen), total)

    def test_rests_until_next_cycle_after_finishing_one(self):
        watch = shooting.init_watch(self.stage)
        now = 1000.0
        for _ in range(len(watch["steps"])):
            watch, _ = shooting.advance_watch(watch, now)
            now += 0.07
        # 주기를 막 끝냈으므로 쉬어야 한다
        watch2, step = shooting.advance_watch(watch, now, poll_seconds=2.0)
        self.assertIsNone(step)
        # 주기가 지나면 다시 처음부터
        watch3, step = shooting.advance_watch(watch2, now + 2.5,
                                              poll_seconds=2.0)
        self.assertIsNotNone(step)
        self.assertEqual(step, watch3["steps"][0])

    def test_spinner_advances_every_step(self):
        watch = shooting.init_watch(self.stage)
        frames = []
        now = 1000.0
        for _ in range(4):
            watch, _ = shooting.advance_watch(watch, now)
            frames.append(shooting.spinner_frame(watch))
            now += 0.07
        self.assertEqual(len(set(frames)), 4)   # 움직여야 '살아있음'이 보인다

    def test_data_age(self):
        watch = shooting.init_watch(self.stage)
        self.assertIsNone(shooting.data_age(watch, 1000.0))
        watch["last_complete"] = 1000.0
        self.assertAlmostEqual(shooting.data_age(watch, 1004.0), 4.0)

    def test_empty_step_list_is_safe(self):
        watch = {"steps": [], "index": 0, "last_complete": None, "spin": 0}
        watch, step = shooting.advance_watch(watch, 1000.0)
        self.assertIsNone(step)


class _FakeCurses:
    """_confirm_quit 같은 화면 함수를 돌리기 위한 최소 curses 대역."""
    error = type("error", (Exception,), {})
    A_REVERSE = A_BOLD = A_DIM = 0

    @staticmethod
    def color_pair(_n):
        return 0


class _FakeScreen:
    """미리 정해둔 키를 한 번 돌려주는 화면 대역. 그린 텍스트를 모아둔다."""

    def __init__(self, key):
        self._key = key
        self.drawn = []

    def erase(self):
        pass

    def refresh(self):
        pass

    def timeout(self, _ms):
        pass

    def nodelay(self, _flag):
        pass

    def getmaxyx(self):
        return 24, 80

    def addstr(self, _y, _x, text, _attr=0):
        self.drawn.append(text)

    def getch(self):
        return self._key


class ConfirmQuitTest(unittest.TestCase):
    """포기 확인 화면 — 문항 화면의 q(닫기) 손버릇이 판을 날리지 않게 한다."""

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        self.session = shooting.init_session(self.stage)

    def _confirm(self, key):
        screen = _FakeScreen(key)
        ok = shooting._confirm_quit(screen, _FakeCurses(), self.stage,
                                    self.session)
        return ok, "".join(screen.drawn)

    def test_y_confirms(self):
        for key in (ord("y"), ord("Y")):
            ok, _ = self._confirm(key)
            self.assertTrue(ok, key)

    def test_everything_else_cancels(self):
        # Enter조차 승낙이 아니다 — 화면을 안 읽고 누른 키가 곧 포기가 되면
        # 확인 단계를 넣은 의미가 없다.
        for key in (ord("n"), ord("q"), 10, 27, -1):
            ok, _ = self._confirm(key)
            self.assertFalse(ok, key)

    def test_screen_states_the_cost(self):
        # 포기의 비용(해설이 붙지 않음)이 화면에 적혀 있어야 한다.
        _, text = self._confirm(ord("n"))
        self.assertIn("해설", text)


class ClientHandoffTest(unittest.TestCase):
    """진짜 mysql 클라이언트로 넘기는 경로."""

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")

    def test_objective_marks_reflects_progress(self):
        session = shooting.init_session(self.stage)
        self.assertEqual(shooting.objective_marks(self.stage, session),
                         "[?][?][ ][ ]")
        session["states"]["diagnose-symptom"]["done"] = True
        session["states"]["unblock"]["done"] = True
        self.assertEqual(shooting.objective_marks(self.stage, session),
                         "[x][?][x][ ]")

    def test_objective_marks_on_empty_session(self):
        self.assertEqual(shooting.objective_marks(self.stage, {}),
                         "[?][?][ ][ ]")

    def test_command_uses_player_credentials(self):
        cmd = shooting.mysql_client_command(self.stage)
        self.assertEqual(cmd[0], "mysql")
        self.assertIn(f"-u{shooting.PLAYER_USER}", cmd)
        self.assertIn(f"-D{shooting.PLAYER_DB}", cmd)
        self.assertIn(f"-h{shooting.PLAYER_HOST}", cmd)
        self.assertIn(f"-P{shooting.PLAYER_PORT}", cmd)

    def test_wide_rows_switch_to_vertical(self):
        # data_locks 는 한 줄이 254칸이다 — 이 옵션이 빠지면 화면에서 잘린다.
        self.assertIn("--auto-vertical-output",
                      shooting.mysql_client_command(self.stage))

    def test_prompt_carries_stage_context(self):
        cmd = shooting.mysql_client_command(self.stage)
        prompt = [a for a in cmd if a.startswith("--prompt=")]
        self.assertEqual(len(prompt), 1)
        self.assertIn(self.stage["id"], prompt[0])

    def test_pager_only_when_available(self):
        without = shooting.mysql_client_command(self.stage)
        self.assertFalse([a for a in without if a.startswith("--pager=")])
        withp = shooting.mysql_client_command(self.stage, pager="less -SFX")
        self.assertIn("--pager=less -SFX", withp)

    def test_password_never_on_the_command_line(self):
        # 비밀번호는 MYSQL_PWD 환경변수로 넘긴다 — ps 에 노출되면 안 된다.
        cmd = shooting.mysql_client_command(self.stage, pager="less")
        self.assertFalse([a for a in cmd
                          if shooting.PLAYER_PASSWORD in a and a != "mysql"])

    def test_credentials_match_the_seed_grants(self):
        # shooting/lab/seed/03-users.sql 과 어긋나면 조용히 인증 실패한다.
        seed = (REPO_ROOT / "shooting" / "lab" / "seed"
                / "03-users.sql").read_text(encoding="utf-8")
        self.assertIn(f"'{shooting.PLAYER_USER}'@'%'", seed)
        self.assertIn(f"IDENTIFIED BY '{shooting.PLAYER_PASSWORD}'", seed)
        self.assertIn(f"ON {shooting.PLAYER_DB}.*", seed)

    def test_connect_hint_derives_from_the_same_constants(self):
        hint = shooting._connect_hint({})
        for part in (shooting.PLAYER_HOST, shooting.PLAYER_PORT,
                     shooting.PLAYER_USER, shooting.PLAYER_DB):
            self.assertIn(part, hint)

    def test_banner_lists_only_unfinished_objectives(self):
        session = shooting.init_session(self.stage)
        done, rest = self.stage["objectives"][0], self.stage["objectives"][1:]
        session["states"][done["id"]]["done"] = True
        banner = shooting.client_banner(self.stage, session)
        self.assertNotIn(f"[ ] {done.get('label', done['id'])}", banner)
        for o in rest:
            self.assertIn(f"[ ] {o.get('label', o['id'])}", banner)

    def test_banner_is_returned_not_printed(self):
        # curses가 화면을 잡고 있는 동안 print()하면 게임 화면 위에 겹쳐 찍힌다.
        # 그래서 배너는 문자열로 나와 endwin() 뒤에 출력돼야 한다.
        session = shooting.init_session(self.stage)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            banner = shooting.client_banner(self.stage, session)
        self.assertEqual(buf.getvalue(), "")
        self.assertIn(self.stage["id"], banner)
        self.assertIn("exit", banner)

    def test_run_in_terminal_prints_banner_after_endwin(self):
        order = []

        class FakeCurses:
            def def_prog_mode(self):
                order.append("def_prog_mode")

            def endwin(self):
                order.append("endwin")

            def reset_prog_mode(self):
                order.append("reset_prog_mode")

        class FakeScreen:
            def clear(self):
                pass

            def refresh(self):
                pass

        def fake_run(cmd, env=None):
            order.append("run")
            return types.SimpleNamespace(returncode=0)

        real_print = builtins.print
        builtins.print = lambda *a, **k: order.append("print")
        real_run = shooting.subprocess.run
        shooting.subprocess.run = fake_run
        try:
            rc = shooting.run_in_terminal(FakeScreen(), FakeCurses(),
                                          ["true"], banner="hello")
        finally:
            builtins.print = real_print
            shooting.subprocess.run = real_run

        self.assertEqual(rc, 0)
        self.assertEqual(order, ["def_prog_mode", "endwin", "print", "run",
                                 "reset_prog_mode"])


class RecordEventTest(unittest.TestCase):
    def test_appends_in_order(self):
        s = {}
        shooting.record_event(s, "command", "SHOW PROCESSLIST", 41)
        shooting.record_event(s, "command", "KILL 213", 138)
        self.assertEqual([e["text"] for e in s["events"]],
                         ["SHOW PROCESSLIST", "KILL 213"])

    def test_duplicate_commands_are_kept(self):
        # 같은 질의를 두 번 친 것도 타임라인에서는 의미가 있다.
        s = {}
        shooting.record_event(s, "command", "SHOW PROCESSLIST", 10)
        shooting.record_event(s, "command", "SHOW PROCESSLIST", 55)
        self.assertEqual(len(s["events"]), 2)

    def test_unique_suppresses_repeats(self):
        # 금지 행동은 폴링마다 다시 감지된다 — 그대로 두면 타임라인이 도배된다.
        s = {}
        for at in (10, 12, 14):
            shooting.record_event(s, "violation", "무차별 KILL 금지", at,
                                  unique=True)
        self.assertEqual(len(s["events"]), 1)
        self.assertEqual(s["events"][0]["at"], 10)   # 최초 발생 시각을 남긴다

    def test_negative_time_is_clamped(self):
        s = {}
        shooting.record_event(s, "hint", "힌트", -5)
        self.assertEqual(s["events"][0]["at"], 0.0)


class BuildNoteTest(unittest.TestCase):
    """포스트모템 초안 — 사실은 채우고 분석은 비운다."""

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        self.session = shooting.init_session(self.stage)
        self.session["states"]["diagnose-symptom"].update(done=True,
                                                          correct=True)
        self.session["states"]["diagnose-view"].update(done=True,
                                                       correct=False)
        self.session["states"]["unblock"]["done"] = True
        self.session["hints_used"] = 1
        self.session["violations"] = [{"id": "no-kill-all",
                                       "label": "무차별 KILL 금지",
                                       "detail": "범인 외 세션 3건을 KILL했습니다"}]
        shooting.record_event(self.session, "command", "KILL 213", 138)
        self.note = shooting.build_note(
            self.stage, self.session,
            shooting.summarize(self.stage, self.session), "2026-07-29")

    def test_follows_the_chapter_template(self):
        for section in ("## 요약", "## 타임라인", "## 근본 원인 분석 (5 Whys)",
                        "## 잘된 점 (What went well)",
                        "## 아쉬운 점 (What went wrong)",
                        "## 재발 방지 액션 아이템",
                        "## 비난 없음(Blameless) 노트"):
            self.assertIn(section, self.note)

    def test_analysis_sections_stay_blank(self):
        # 이걸 채워주면 회고 연습이 되지 않는다. 스테이지 해설은 노트를 쓴 뒤에 본다.
        self.assertIn("- 근본 원인: <!-- 직접 채우세요 -->", self.note)
        self.assertIn("1. 왜? → ", self.note)
        self.assertIn("5. 왜? → ", self.note)
        self.assertIn("- [ ] <!--", self.note)

    def test_debrief_answer_is_not_leaked(self):
        self.assertNotIn(self.stage["debrief"].split("\n")[0], self.note)

    def test_summary_says_no_limit_when_stage_is_untimed(self):
        # "(목표 00:00)"으로 보이면 0초 안에 끝냈어야 한다는 오해를 준다.
        stage = dict(self.stage)
        stage.pop("target_seconds", None)
        note = shooting.build_note(
            stage, self.session, shooting.summarize(stage, self.session),
            "2026-07-29")
        self.assertIn("(목표 없음)", note)
        self.assertNotIn("(목표 00:00)", note)

    def test_timeline_is_sorted_and_marked(self):
        lines = [ln for ln in self.note.splitlines()
                 if ln.startswith("- 00:") or ln.startswith("- 02:")]
        self.assertTrue(lines[0].startswith("- 00:00"))
        self.assertIn("🔥 장애 발생", lines[0])
        self.assertIn("KILL 213", "\n".join(lines))

    def test_wrong_quiz_becomes_the_mistake_note(self):
        self.assertIn("**오답**", self.note)
        self.assertIn("data_lock_waits", self.note)     # 정답
        self.assertIn("금지 행동", self.note)
        self.assertIn("힌트 1회 사용", self.note)

    def test_wrong_answer_is_not_listed_as_a_win(self):
        wins = self.note.split("## 잘된 점 (What went well)")[1] \
                        .split("## 아쉬운 점")[0]
        self.assertIn("상황 식별", wins)
        self.assertNotIn("확인 경로 파악", wins)

    def test_clean_run_has_no_empty_regret_bullet_list(self):
        session = shooting.init_session(self.stage)
        for o in self.stage["objectives"]:
            session["states"][o["id"]]["done"] = True
            if o["type"] == "quiz":
                session["states"][o["id"]]["correct"] = True
        note = shooting.build_note(
            self.stage, session,
            shooting.summarize(self.stage, session), "2026-07-29")
        regret = note.split("## 아쉬운 점 (What went wrong)")[1] \
                     .split("## 재발 방지")[0]
        self.assertIn("<!-- 직접 채우세요 -->", regret)


class DebriefAttachmentTest(unittest.TestCase):
    """해설은 초안이 아니라 **편집기를 닫은 뒤** 노트에 붙는다.

    초안에 넣으면 근본 원인·5 Whys를 빈칸으로 둔 의미가 사라진다
    (같은 파일 안에 정답이 있으면 스크롤 한 번이다).
    """

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")

    def test_section_carries_the_debrief_and_a_blank_review_slot(self):
        section = shooting.debrief_section(self.stage)
        self.assertIn(shooting.DEBRIEF_MARKER, section)
        self.assertIn(self.stage["debrief"].split("\n")[0], section)
        self.assertIn("## 대조 메모", section)

    def test_no_debrief_means_no_section(self):
        self.assertIsNone(shooting.debrief_section({}))
        self.assertIsNone(shooting.debrief_section({"debrief": "   "}))

    def _note(self, tmp):
        p = Path(tmp) / "note.md"
        p.write_text("# 내 회고\n\n## 근본 원인 분석 (5 Whys)\n1. 왜? → 내가 쓴 답\n",
                     encoding="utf-8")
        return p

    def test_appends_once_and_is_idempotent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self._note(tmp)
            self.assertTrue(shooting.append_debrief(p, self.stage))
            # 노트를 다시 열어 편집해도 해설이 중복되면 안 된다.
            self.assertFalse(shooting.append_debrief(p, self.stage))
            body = p.read_text(encoding="utf-8")
            self.assertEqual(body.count(shooting.DEBRIEF_MARKER), 1)
            # 내가 쓴 내용은 그대로 남는다
            self.assertIn("1. 왜? → 내가 쓴 답", body)
            # 해설은 내 분석 **뒤에** 온다
            self.assertLess(body.index("내가 쓴 답"),
                            body.index(shooting.DEBRIEF_MARKER))

    def test_safe_on_missing_path_and_none(self):
        self.assertFalse(shooting.append_debrief(None, self.stage))
        self.assertFalse(
            shooting.append_debrief("/no/such/dir/x.md", self.stage))

    def test_stage_without_debrief_appends_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self._note(tmp)
            before = p.read_text(encoding="utf-8")
            self.assertFalse(shooting.append_debrief(p, {"id": "x"}))
            self.assertEqual(p.read_text(encoding="utf-8"), before)


class NoteFilesTest(unittest.TestCase):
    def test_collect_puts_current_stage_first_then_newest(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for stage_id, names in [
                ("1-3-lock", ["20260701-100000-B.md", "20260705-100000-A.md"]),
                ("2-1-repl", ["20260703-100000-S.md"]),
            ]:
                (root / stage_id).mkdir(parents=True)
                for n in names:
                    (root / stage_id / n).write_text("x", encoding="utf-8")

            got = shooting.collect_notes(root, "1-3-lock")
            self.assertEqual([p.parent.name for p in got],
                             ["1-3-lock", "1-3-lock", "2-1-repl"])
            # 같은 스테이지 안에서는 최신이 먼저
            self.assertTrue(got[0].name.startswith("20260705"))

    def test_collect_on_missing_dir(self):
        self.assertEqual(shooting.collect_notes("/no/such/dir", "x"), [])

    def test_heading_shows_stage_time_and_rank(self):
        head = shooting.note_heading(
            Path("/n/1-3-lock-contention/20260729-011122-S.md"))
        self.assertIn("1-3-lock-contention", head)
        self.assertIn("20260729-011122", head)
        self.assertIn("RANK S", head)

    def test_note_path_layout(self):
        p = shooting.note_path("1-3-lock", "20260729-011122", "A")
        self.assertEqual(p.parent.name, "1-3-lock")
        self.assertEqual(p.name, "20260729-011122-A.md")
        self.assertEqual(p.parent.parent, shooting.NOTES_DIR)


class ShippedStagesTest(unittest.TestCase):
    """저장소에 들어있는 스테이지 정의가 실제로 통과하는지."""

    def test_all_stage_files_are_valid(self):
        paths = shooting.discover_stages()
        self.assertTrue(paths, "shooting/stages 에 스테이지가 없습니다")
        for path in paths:
            with self.subTest(stage=path.name):
                with open(path, encoding="utf-8") as f:
                    stage = json.load(f)
                self.assertEqual(shooting.validate_stage(stage), [])

    def test_lock_stage_marks_exactly_one_culprit_step(self):
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        culprits = [s for s in stage["setup"] if s.get("culprit")]
        self.assertEqual(len(culprits), 1)
        # 범인 세션을 표시해두지 않으면 "범인 외 KILL" 판정이 성립하지 않는다.
        self.assertEqual(culprits[0]["name"], "villain")

    def test_metadata_lock_stage_spares_the_blocked_alter(self):
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-4-metadata-lock.json")
        by_name = {s.get("name"): s for s in stage["setup"] if s.get("name")}
        # 잠금을 쥔 쪽만 범인이다. 막혀 있는 ALTER는 피해자이므로 표시하지
        # 않아야 "배포를 KILL해버린" 실수가 위반으로 잡힌다.
        self.assertTrue(by_name["long-reader"].get("culprit"))
        self.assertFalse(by_name["deploy-alter"].get("culprit"))

    def test_replication_lag_stage_puts_the_culprit_on_the_replica(self):
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "2-2-replication-lag.json")
        culprits = [s for s in stage["setup"] if s.get("culprit")]
        self.assertEqual(len(culprits), 1)
        # 이 스테이지의 요점은 "범인이 primary에 있다"는 습관을 깨는 것이다.
        # replica로 옮겨두지 않으면 1-3과 같은 스테이지가 되어버린다.
        self.assertEqual(culprits[0]["on"], "replica")

    def test_engine_bookkeeping_stays_out_of_the_binlog(self):
        # primary의 로그 비우기가 binlog에 실리면 replica에서도 실행되어,
        # 플레이어가 replica에서 친 명령이 감시 주기마다 증발한다.
        self.assertIn(shooting.NO_BINLOG, shooting.PLAYER_LOG_SQL)

    def test_missing_index_stage_has_no_culprit(self):
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "4-1-missing-index.json")
        # 이 스테이지에는 범인이 없다 — 아무도 남을 막고 있지 않다.
        # 그래서 allowed_pids가 비고, 어떤 KILL이든 위반으로 잡힌다.
        # "장애 = KILL" 반사를 깨는 것이 스테이지의 목적이므로 이게 핵심 불변조건이다.
        self.assertFalse([s for s in stage["setup"] if s.get("culprit")])
        self.assertIn("kill_precision",
                      [c["detect"] for c in stage["constraints"]])


class SessionTest(unittest.TestCase):
    def test_init_session_prepares_state_per_objective(self):
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        session = shooting.init_session(stage)
        self.assertEqual(set(session["states"]),
                         {o["id"] for o in stage["objectives"]})
        self.assertFalse(shooting.all_done(stage, session))
        self.assertEqual(shooting.quiz_totals(stage, session), (0, 2))

    def test_quiz_totals_counts_correct_answers(self):
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        session = shooting.init_session(stage)
        session["states"]["diagnose-symptom"]["correct"] = True
        self.assertEqual(shooting.quiz_totals(stage, session), (1, 2))

    def test_all_done_requires_every_objective(self):
        stage = _minimal_stage(objectives=[
            {"id": "a", "type": "state", "query": "SELECT 1",
             "expect": {"op": "eq", "value": 1}},
            {"id": "b", "type": "state", "query": "SELECT 2",
             "expect": {"op": "eq", "value": 2}}])
        session = shooting.init_session(stage)
        session["states"]["a"]["done"] = True
        self.assertFalse(shooting.all_done(stage, session))
        session["states"]["b"]["done"] = True
        self.assertTrue(shooting.all_done(stage, session))


class DoctorTest(unittest.TestCase):
    """docker가 없는 머신에서도 `./shoot doctor`는 진단을 내놔야 한다.

    README가 "docker 확인은 `./shoot doctor`"라고 안내하므로, 이 명령이
    docker 부재로 죽으면 정확히 필요한 순간에 쓸 수 없다.
    """

    @contextlib.contextmanager
    def _no_docker(self):
        """PATH에서 docker가 사라지고 실행 시도는 FileNotFoundError가 되는 상황."""
        real_which, real_run = shooting.shutil.which, shooting.subprocess.run

        def fake_which(name, *a, **k):
            return None if name == "docker" else real_which(name, *a, **k)

        def fake_run(cmd, *a, **k):
            if cmd and cmd[0] == "docker":
                raise FileNotFoundError(2, "No such file or directory", "docker")
            return real_run(cmd, *a, **k)

        shooting.shutil.which, shooting.subprocess.run = fake_which, fake_run
        try:
            yield
        finally:
            shooting.shutil.which, shooting.subprocess.run = real_which, real_run

    def _run_doctor(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = shooting.cmd_doctor()
        return rc, buf.getvalue()

    def test_reports_missing_docker_instead_of_crashing(self):
        with self._no_docker():
            rc, out = self._run_doctor()
        self.assertEqual(rc, 1)
        self.assertIn("[!!] docker", out)
        self.assertIn("설치되어 있지 않습니다", out)

    def test_skips_the_lab_probe_when_docker_is_absent(self):
        # "내려가 있음"으로 찍으면 `./shoot up`으로 해결된다는 오진이 된다.
        with self._no_docker():
            _, out = self._run_doctor()
        self.assertIn("확인 불가", out)
        self.assertNotIn("내려가 있음", out)

    def test_still_checks_the_rest(self):
        # docker가 없어도 나머지 항목은 계속 점검한다.
        with self._no_docker():
            _, out = self._run_doctor()
        self.assertIn("compose 파일", out)
        self.assertIn("스테이지 정의", out)

    def test_docker_available_reads_the_path(self):
        with self._no_docker():
            self.assertFalse(shooting.docker_available())
        self.assertEqual(shooting.docker_available(),
                         shutil.which("docker") is not None)

    def test_docker_helper_raises_lab_error_not_file_not_found(self):
        # LabError로 승격해야 이미 그것을 잡고 있는 호출자들이 살아난다.
        with self._no_docker():
            with self.assertRaises(shooting.LabError) as ctx:
                shooting._docker("info")
            self.assertIn("docker CLI", str(ctx.exception))
            with self.assertRaises(shooting.LabError):
                shooting._compose("ps")

    def test_lab_predicates_absorb_the_error(self):
        # cmd_play 의 `if not lab_running():` 는 bool을 기대한다.
        with self._no_docker():
            self.assertFalse(shooting.lab_running())
            self.assertFalse(shooting.lab_healthy())
            self.assertIsNone(shooting.container_started_at("primary"))


class DrawPlayCostTest(unittest.TestCase):
    """플레이 화면은 매 프레임(80ms) 다시 그려진다 — 여기서 I/O를 하면 안 된다."""

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")

    def test_session_carries_the_notes_count(self):
        # 노트는 스테이지가 끝난 뒤에만 생기므로 플레이 중에는 변하지 않는다.
        session = shooting.init_session(self.stage)
        self.assertEqual(session["notes_count"], 0)

    def test_draw_does_not_touch_the_filesystem(self):
        class Curses:
            error = type("error", (Exception,), {})
            A_REVERSE = A_BOLD = A_DIM = 0

            @staticmethod
            def color_pair(_n):
                return 0

        class Screen:
            def erase(self):
                pass

            def refresh(self):
                pass

            def getmaxyx(self):
                return 24, 80

            def addstr(self, _y, _x, _text, _attr=0):
                pass

        session = shooting.init_session(self.stage)
        session["notes_count"] = 7
        calls = []
        real = shooting.collect_notes
        shooting.collect_notes = lambda *a, **k: calls.append(a) or []
        try:
            for _ in range(5):
                shooting._draw_play(Screen(), Curses(), self.stage, session,
                                    shooting.init_watch(self.stage))
        finally:
            shooting.collect_notes = real
        self.assertEqual(calls, [], "그리기 경로에서 노트 디렉터리를 훑고 있다")


if __name__ == "__main__":
    unittest.main()
