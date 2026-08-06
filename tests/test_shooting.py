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
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import shooting  # noqa: E402
import tui  # noqa: E402


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
        self._real_db_query = shooting.db_query

        def boom(*a, **kw):
            raise shooting.LabError("Unknown database 'shop'")

        shooting.db_query = boom
        self.addCleanup(setattr, shooting, "mysql", self._real_db_query)

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
    # 실제 ncurses 값과 같게 둔다 — 화면 코드가 정수 상수로 먼저 판별하기 때문에
    # 여기서 어긋나면 방향키가 일반 문자로 잘못 해석된다.
    KEY_UP, KEY_DOWN = 259, 258

    @staticmethod
    def color_pair(_n):
        return 0


class _FakeScreen:
    """미리 정해둔 키를 한 번 돌려주는 화면 대역. 그린 텍스트를 모아둔다.

    같은 키를 계속 돌려주므로, 화면 함수가 그 키를 처리하지 못하고 루프를 돌면
    테스트가 끝나지 않는다 — 처리 누락이 곧 드러난다.
    """

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


class ClientTargetTest(unittest.TestCase):
    """`c` 키가 어느 서버로 붙는가.

    범인이 replica에 있는 스테이지(2-2)에서 primary로만 붙을 수 있으면
    게임 안에서는 현장에 갈 수 없다.
    """

    def setUp(self):
        self.single = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        self.multi = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "2-2-replication-lag.json")

    def test_single_server_stage_offers_only_primary(self):
        self.assertEqual(shooting.client_targets(self.single), ["primary"])

    def test_replication_stage_offers_both_servers(self):
        self.assertEqual(shooting.client_targets(self.multi),
                         ["primary", "replica"])

    def test_command_targets_the_requested_server(self):
        cmd = shooting.client_command(self.multi, target="replica")
        self.assertIn(f"-P{shooting.PLAYER_PORTS['replica']}", cmd)
        self.assertNotIn(f"-P{shooting.PLAYER_PORTS['primary']}", cmd)

    def test_prompt_names_the_server_when_there_is_a_choice(self):
        prompt = [a for a in shooting.client_command(
            self.multi, target="replica") if a.startswith("--prompt=")][0]
        self.assertIn("replica", prompt)

    def test_prompt_stays_clean_on_single_server_stages(self):
        # 서버가 하나뿐이면 대상 표기는 잡음이다.
        prompt = [a for a in shooting.client_command(self.single)
                  if a.startswith("--prompt=")][0]
        self.assertNotIn("primary", prompt)

    def test_ports_match_the_compose_file(self):
        # 포트의 단일 출처가 어긋나면 조용히 엉뚱한 서버에 붙는다.
        compose = (REPO_ROOT / "shooting" / "lab" / "compose.yaml").read_text(
            encoding="utf-8")
        published = re.findall(r'"127\.0\.0\.1:(\d+):3306"', compose)
        self.assertEqual(published,
                         [shooting.PLAYER_PORTS["primary"],
                          shooting.PLAYER_PORTS["replica"]])

    def test_connect_hint_mentions_the_replica_port(self):
        # 서버가 둘인 스테이지에서 primary 명령만 띄우면 현장을 놓친다.
        self.assertIn(shooting.PLAYER_PORTS["replica"],
                      shooting._connect_hint(self.multi))

    def test_banner_says_which_server(self):
        session = shooting.init_session(self.multi)
        banner = shooting.client_banner(self.multi, session, target="replica")
        self.assertIn("replica", banner)

    def _pick(self, stage, key):
        screen = _FakeScreen(key)
        return shooting._pick_client_target(screen, _FakeCurses(), stage), screen

    def test_single_server_stage_skips_the_picker(self):
        # 선택지가 하나인 질문은 물어볼 이유가 없다 — 화면도 그리지 않는다.
        target, screen = self._pick(self.single, ord("1"))
        self.assertEqual(target, "primary")
        self.assertEqual(screen.drawn, [])

    def test_number_key_picks_the_server(self):
        self.assertEqual(self._pick(self.multi, ord("1"))[0], "primary")
        self.assertEqual(self._pick(self.multi, ord("2"))[0], "replica")

    def test_quit_cancels(self):
        self.assertIsNone(self._pick(self.multi, ord("q"))[0])


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
        cmd = shooting.client_command(self.stage)
        self.assertEqual(cmd[0], "mysql")
        self.assertIn(f"-u{shooting.PLAYER_USER}", cmd)
        self.assertIn(f"-D{shooting.PLAYER_DB}", cmd)
        self.assertIn(f"-h{shooting.PLAYER_HOST}", cmd)
        self.assertIn(f"-P{shooting.PLAYER_PORTS['primary']}", cmd)

    def test_wide_rows_switch_to_vertical(self):
        # data_locks 는 한 줄이 254칸이다 — 이 옵션이 빠지면 화면에서 잘린다.
        self.assertIn("--auto-vertical-output",
                      shooting.client_command(self.stage))

    def test_prompt_carries_stage_context(self):
        cmd = shooting.client_command(self.stage)
        prompt = [a for a in cmd if a.startswith("--prompt=")]
        self.assertEqual(len(prompt), 1)
        self.assertIn(self.stage["id"], prompt[0])

    def test_pager_only_when_available(self):
        without = shooting.client_command(self.stage)
        self.assertFalse([a for a in without if a.startswith("--pager=")])
        withp = shooting.client_command(self.stage, pager="less -SFX")
        self.assertIn("--pager=less -SFX", withp)

    def test_password_never_on_the_command_line(self):
        # 비밀번호는 MYSQL_PWD 환경변수로 넘긴다 — ps 에 노출되면 안 된다.
        cmd = shooting.client_command(self.stage, pager="less")
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
        for part in (shooting.PLAYER_HOST, shooting.PLAYER_PORTS["primary"],
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
            self.assertFalse(shooting.container_healthy("postgres"))
            self.assertIsNone(shooting.container_started_at("primary"))


class LineQuizScheduleTest(unittest.TestCase):
    """라인 모드에서 '언제 문항을 띄우는가'.

    예전에는 폴링 주기(2초)마다 미응답 문항 전부를 물어봤다. trigger가 무시돼
    상황을 보기도 전에 문항이 튀어나왔고, 답하지 않으면 2초마다 다시 물었다.
    """

    def setUp(self):
        # diagnose-symptom(trigger 25s) / diagnose-view(trigger 없음) /
        # unblock(state) / orders-flow(state)
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        self.session = shooting.init_session(self.stage)

    def _ask(self, elapsed, interactive=True):
        return [o["id"] for o in shooting.line_quizzes_to_ask(
            self.stage, self.session, elapsed, interactive)]

    def _finish_states(self):
        for obj in self.stage["objectives"]:
            if obj["type"] == "state":
                self.session["states"][obj["id"]]["done"] = True

    def test_nothing_before_the_trigger_fires(self):
        self.assertEqual(self._ask(elapsed=5), [])

    def test_triggered_quiz_appears_after_its_delay(self):
        self.assertEqual(self._ask(elapsed=30), ["diagnose-symptom"])

    def test_untriggered_quiz_waits_for_the_state_objectives(self):
        # trigger가 없는 문항은 상황을 다 정리한 뒤에 묻는다. 먼저 물으면
        # '상황 보고'가 아니라 그냥 퀴즈가 된다.
        self.assertNotIn("diagnose-view", self._ask(elapsed=30))
        self._finish_states()
        self.assertIn("diagnose-view", self._ask(elapsed=30))

    def test_answered_quiz_is_not_asked_again(self):
        self._finish_states()
        for oid in ("diagnose-symptom", "diagnose-view"):
            self.session["states"][oid]["done"] = True
        self.assertEqual(self._ask(elapsed=30), [])

    def test_nothing_is_asked_without_a_terminal(self):
        self._finish_states()
        self.assertEqual(self._ask(elapsed=30, interactive=False), [])


class LineAskTest(unittest.TestCase):
    """문항 한 건을 묻는 부분 — 재질문은 이 안에서 끝나야 한다."""

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        self.session = shooting.init_session(self.stage)
        self.mcq = self.stage["objectives"][0]        # diagnose-symptom
        self.short = self.stage["objectives"][1]      # diagnose-view

    def _ask(self, obj, answers):
        """answers를 차례로 돌려주는 가짜 input으로 물어본다."""
        fed = iter(answers)

        def prompt(_label):
            try:
                return next(fed)
            except StopIteration:
                raise EOFError
        st = self.session["states"][obj["id"]]
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            ok = shooting._line_ask(obj, st, prompt=prompt)
        return ok, st, buf.getvalue()

    def test_bad_input_is_retried_in_place(self):
        # 예전에는 잘못된 입력이면 그냥 돌아가 바깥 루프가 2초 뒤 다시 물었다.
        ok, st, out = self._ask(self.mcq, ["아무거나", "0", "99", "1"])
        self.assertTrue(ok)
        self.assertTrue(st["done"])
        self.assertIn("사이의 번호를 입력하세요", out)

    def test_answer_is_graded(self):
        # 섞인 표시 순서가 아니라 원본 순서이므로 1번이 정답이다.
        _, st, _ = self._ask(self.mcq, ["1"])
        self.assertTrue(st["correct"])
        _, st2, _ = self._ask(self.mcq, ["2"])
        self.assertFalse(st2["correct"])

    def test_short_answer_accepts_the_documented_forms(self):
        _, st, _ = self._ask(self.short, ["data_lock_waits"])
        self.assertTrue(st["correct"])

    def test_eof_reports_instead_of_raising(self):
        ok, st, _ = self._ask(self.short, [])
        self.assertFalse(ok)
        self.assertFalse(st["done"])   # 닫는 것은 호출부(skip_quiz)의 몫


class SkippedQuizTest(unittest.TestCase):
    """물어볼 수 없었던 문항은 '틀림'이 아니라 '건너뜀'이다.

    비대화형(파이프) 실행에서는 input()을 쓸 수 없다. 그렇다고 문항을 미완료로
    두면 all_done이 영원히 거짓이라 루프가 끝나지 않는다.
    """

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        self.session = shooting.init_session(self.stage)

    def test_skipping_completes_the_objective(self):
        shooting.skip_quiz(self.session, "diagnose-symptom")
        st = self.session["states"]["diagnose-symptom"]
        self.assertTrue(st["done"])
        self.assertTrue(st["skipped"])
        self.assertIsNone(st["correct"])

    def test_skipped_quizzes_leave_the_run_finishable(self):
        for oid in ("diagnose-symptom", "diagnose-view"):
            shooting.skip_quiz(self.session, oid)
        for obj in self.stage["objectives"]:
            if obj["type"] == "state":
                self.session["states"][obj["id"]]["done"] = True
        self.assertTrue(shooting.all_done(self.stage, self.session))

    def test_skipped_quizzes_do_not_count_as_wrong(self):
        # 감점하지 않는다 — target_seconds가 없을 때 시간으로 감점하지 않는 것과
        # 같은 원칙(기준이 없으면 감점하지 않는다).
        for oid in ("diagnose-symptom", "diagnose-view"):
            shooting.skip_quiz(self.session, oid)
        self.assertEqual(shooting.quiz_totals(self.stage, self.session), (0, 0))
        _, score, rank = shooting.rank_breakdown(10, 300, 0, 0, 0, 0)
        self.assertEqual((score, rank), (4, "S"))

    def test_snapshot_marks_skipped_quizzes(self):
        # 답한 문항과 똑같이 [x]로만 보이면 나중에 회고할 때 오해한다.
        shooting.skip_quiz(self.session, "diagnose-symptom")
        snap = shooting._line_snapshot(self.stage, self.session)
        self.assertIn("건너뜀", snap)

    def test_skipped_quiz_is_not_praised_in_the_note(self):
        # 답하지 않은 문항이 '잘된 점'에 올라오면 회고가 거짓말을 한다.
        shooting.skip_quiz(self.session, "diagnose-symptom")
        note = shooting.build_note(
            self.stage, self.session,
            shooting.summarize(self.stage, self.session), "2026-07-31")
        well = note.split("## 잘된 점")[1].split("## 아쉬운 점")[0]
        self.assertNotIn("상황 식별", well)


class MakeVarsTest(unittest.TestCase):
    """스테이지 파라미터 변주 — 표현식 평가기 없이 타입으로 관계를 선언한다."""

    SPEC = {
        "lock": {"type": "span", "min": 1, "max": 1000, "length": 100},
        "victim": {"type": "int_in", "of": "lock"},
        "workers": {"type": "int", "min": 5, "max": 9},
        "mark": {"type": "choice", "values": ["HOLD", "FROZEN"]},
    }

    def _vars(self, seed=1):
        return shooting.make_vars(self.SPEC, random.Random(seed))

    def test_span_exposes_from_and_to(self):
        v = self._vars()
        self.assertEqual(v["lock.to"] - v["lock.from"] + 1, 100)
        self.assertGreaterEqual(v["lock.from"], 1)
        self.assertLessEqual(v["lock.to"], 1000)

    def test_int_in_always_lands_inside_its_span(self):
        # 이 관계가 이 기능의 전부다 — 어긋나면 판정이 조용히 깨진다.
        for seed in range(200):
            v = shooting.make_vars(self.SPEC, random.Random(seed))
            self.assertGreaterEqual(v["victim"], v["lock.from"], seed)
            self.assertLessEqual(v["victim"], v["lock.to"], seed)

    def test_int_and_choice_respect_their_declarations(self):
        for seed in range(50):
            v = shooting.make_vars(self.SPEC, random.Random(seed))
            self.assertIn(v["workers"], range(5, 10))
            self.assertIn(v["mark"], ["HOLD", "FROZEN"])

    def test_same_seed_reproduces_exactly(self):
        self.assertEqual(self._vars(4821), self._vars(4821))

    def test_different_seeds_actually_vary(self):
        seen = {tuple(sorted(shooting.make_vars(self.SPEC,
                                               random.Random(s)).items()))
                for s in range(30)}
        self.assertGreater(len(seen), 1)

    def test_key_order_does_not_change_the_draw(self):
        # 선언 순서를 바꿨다고 같은 시드의 결과가 달라지면 재현이 깨진다.
        reordered = dict(reversed(list(self.SPEC.items())))
        self.assertEqual(shooting.make_vars(self.SPEC, random.Random(7)),
                         shooting.make_vars(reordered, random.Random(7)))

    def test_no_vars_is_empty(self):
        self.assertEqual(shooting.make_vars(None, random.Random(1)), {})


class RenderStageTest(unittest.TestCase):
    STAGE = {
        "id": "x", "title": "t",
        "vars": {"lock": {"type": "span", "min": 10, "max": 20, "length": 5},
                 "victim": {"type": "int_in", "of": "lock"}},
        "brief": "{{lock.from}}부터 막혔다",
        "setup": [{"type": "sql",
                   "sql": "UPDATE t SET s='X' WHERE id BETWEEN {{lock.from}} AND {{lock.to}}"}],
        "objectives": [{"id": "o", "type": "state",
                        "query": "SELECT s FROM t WHERE id = {{victim}}",
                        "expect": {"op": "eq", "value": "{{victim}}"}}],
        "hints": ["범인은 {{lock.from}}~{{lock.to}} 구간이다"],
    }

    def test_stage_without_vars_is_untouched(self):
        plain = {"id": "p", "setup": [{"sql": "SELECT 1"}]}
        self.assertEqual(shooting.render_stage(plain, random.Random(1)), plain)

    def test_substitutes_everywhere_including_nested(self):
        out = shooting.render_stage(self.STAGE, random.Random(3))
        blob = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("{{", blob)
        self.assertIn("BETWEEN", out["setup"][0]["sql"])

    def test_judging_target_moves_with_the_setup(self):
        # setup이 잠근 구간 안에 목표가 보는 행이 들어 있어야 한다.
        for seed in range(50):
            out = shooting.render_stage(self.STAGE, random.Random(seed))
            lo, hi = (int(n) for n in re.search(
                r"BETWEEN (\d+) AND (\d+)", out["setup"][0]["sql"]).groups())
            victim = int(re.search(r"id = (\d+)", out["objectives"][0]["query"]).group(1))
            self.assertTrue(lo <= victim <= hi, (seed, lo, victim, hi))
            self.assertEqual(out["objectives"][0]["expect"]["value"], str(victim))

    def test_same_seed_renders_identically(self):
        self.assertEqual(shooting.render_stage(self.STAGE, random.Random(9)),
                         shooting.render_stage(self.STAGE, random.Random(9)))

    def test_original_stage_is_not_mutated(self):
        before = json.dumps(self.STAGE, ensure_ascii=False)
        shooting.render_stage(self.STAGE, random.Random(1))
        self.assertEqual(json.dumps(self.STAGE, ensure_ascii=False), before)


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

    def test_the_engine_substitutes_exactly_where_validation_allows(self):
        """엔진과 검증기가 같은 규칙을 말해야 한다.

        검증기는 `{{session_index}}`를 `sessions` 단계의 `sql` 에서만 허용하는데
        엔진은 단수 `session` 단계에도 치환을 걸고 있었다. 검증이 막아 주니 결과는
        같지만, 규칙이 두 곳에 서로 다르게 적혀 있으면 다음 사람이 어느 쪽을 믿을지
        모른다 — 한쪽만 고쳐도 조용히 갈라진다.
        """
        spawned = []
        names = ("kill_app_sessions", "reset_player_log", "app_session_pids",
                 "db_spawn", "_wait_for_new_sessions", "_wait_for_incident",
                 "container_started_at")
        real = {n: getattr(shooting, n) for n in names}
        shooting.kill_app_sessions = lambda t: None
        shooting.reset_player_log = lambda t: None
        shooting.app_session_pids = lambda t: set()
        shooting.db_spawn = (lambda t, u, p, sql, idle_seconds=0:
                             spawned.append(sql))
        shooting._wait_for_new_sessions = (
            lambda t, before, count, timeout=20: set(range(count)))
        shooting._wait_for_incident = lambda stage, timeout=30: True
        shooting.container_started_at = lambda t: "t0"
        stage = _minimal_stage(setup=[
            {"type": "session", "on": "primary",
             "sql": "SELECT '{{session_index}}'"}])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                shooting.setup_stage(stage, log=lambda *a: None)
        finally:
            for n, fn in real.items():
                setattr(shooting, n, fn)
        self.assertEqual(spawned, ["SELECT '{{session_index}}'"])

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


class DefaultDatabaseTest(unittest.TestCase):
    """없는 데이터베이스를 `-D`로 지정하면 접속 자체가 거부된다.

    실측(2-1의 setup 을 그대로 실행한 replica):
        mysql -h127.0.0.1 -P3307 -udba -Dshop
        → ERROR 1049 (42000): Unknown database 'shop'
        -D 를 빼면 → 접속 성공

    2-1은 setup 이 replica 에서 `DROP DATABASE IF EXISTS shop` 을 하고, 그 서버에
    복제를 붙이는 것이 **플레이어의 과제**다. 그래서 `shop` 은 스테이지 대부분
    동안 없고, 하필 replica 접속이 과제의 전부인 그 스테이지에서 `c` 키가 죽었다.
    """

    @contextlib.contextmanager
    def _db_query(self, rows=None, boom=False):
        real = shooting.db_query
        asked = []

        def fake(target, sql, **kw):
            asked.append((target, sql))
            if boom:
                raise shooting.LabError("서버가 대답하지 않습니다")
            return rows

        shooting.db_query = fake
        try:
            yield asked
        finally:
            shooting.db_query = real

    def test_reports_a_present_database(self):
        with self._db_query(rows=[["1"]]) as asked:
            self.assertTrue(shooting.database_exists("primary"))
        self.assertIn(shooting.PLAYER_DB, asked[0][1])

    def test_reports_a_missing_database(self):
        with self._db_query(rows=[["0"]]):
            self.assertFalse(shooting.database_exists("replica"))

    def test_a_failed_probe_counts_as_missing(self):
        # 틀리는 방향을 고른다 — '기본 DB 없이 접속'이 '접속 불가'보다 낫다.
        with self._db_query(boom=True):
            self.assertFalse(shooting.database_exists("replica"))

    def test_postgres_needs_no_probe(self):
        # psql 은 -d postgres 로 붙고 그 데이터베이스는 항상 있다.
        with self._db_query(rows=[["0"]]) as asked:
            self.assertTrue(shooting.database_exists("postgres"))
        self.assertEqual(asked, [])

    def test_command_can_drop_the_default_database(self):
        stage = _minimal_stage()
        with_db = shooting.client_command(stage, target="primary")
        without = shooting.client_command(stage, target="primary",
                                          default_db=False)
        self.assertIn(f"-D{shooting.PLAYER_DB}", with_db)
        self.assertNotIn(f"-D{shooting.PLAYER_DB}", without)
        # 나머지는 그대로여야 한다 — 뺀 것이 하나뿐인지 확인한다.
        self.assertEqual([a for a in with_db
                          if a != f"-D{shooting.PLAYER_DB}"], without)

    def test_the_client_drops_it_when_the_database_is_gone(self):
        """배선 테스트 — 순수 함수만 맞고 호출부가 안 쓰면 아무것도 안 고쳐진다."""
        launched = {}
        real = {n: getattr(shooting, n)
                for n in ("run_in_terminal", "database_exists")}
        shooting.run_in_terminal = (
            lambda stdscr, curses, cmd, **kw: launched.setdefault("cmd", cmd))
        shooting.database_exists = lambda target, *a, **k: False
        try:
            stage = shooting.load_stage(
                REPO_ROOT / "shooting" / "stages" / "2-1-replication-setup.json")
            shooting.open_db_client(None, None, stage,
                                    shooting.init_session(stage), "replica")
        finally:
            for n, fn in real.items():
                setattr(shooting, n, fn)
        self.assertNotIn(f"-D{shooting.PLAYER_DB}", launched["cmd"])
        self.assertIn(f"-P{shooting.PLAYER_PORTS['replica']}", launched["cmd"])

    def test_the_client_keeps_it_when_the_database_is_there(self):
        launched = {}
        real = {n: getattr(shooting, n)
                for n in ("run_in_terminal", "database_exists")}
        shooting.run_in_terminal = (
            lambda stdscr, curses, cmd, **kw: launched.setdefault("cmd", cmd))
        shooting.database_exists = lambda target, *a, **k: True
        try:
            stage = shooting.load_stage(
                REPO_ROOT / "shooting" / "stages" / "1-1-runaway-query.json")
            shooting.open_db_client(None, None, stage,
                                    shooting.init_session(stage), "primary")
        finally:
            for n, fn in real.items():
                setattr(shooting, n, fn)
        self.assertIn(f"-D{shooting.PLAYER_DB}", launched["cmd"])


class ReadmeStageTableTest(unittest.TestCase):
    """README의 스테이지 표는 목록 화면과 같은 것을 말해야 한다.

    표 한가운데 빈 줄이 하나 들어가 4-3·4-2 두 줄이 머리글에서 떨어져 나간 적이
    있다. GFM에서 머리글 없는 행은 표가 아니라 본문 텍스트로 렌더링되므로,
    저장소를 웹에서 보는 사람에게는 파이프 문자가 그대로 노출된다. 마크다운은
    깨져도 조용하다 — 그래서 테스트가 본다.
    """

    def _readme(self):
        return (REPO_ROOT / "README.md").read_text(encoding="utf-8").splitlines()

    def _stage_rows(self):
        """`| 🔥 **1-1 …** |` 꼴 행을 (줄번호, 스테이지번호)로."""
        rx = re.compile(r"^\|\s*[🔥🔧]\s*\*\*([\d-]+)\s")
        return [(i, m.group(1))
                for i, line in enumerate(self._readme())
                if (m := rx.match(line))]

    def test_every_stage_has_a_row(self):
        listed = {num for _, num in self._stage_rows()}
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            num = f"{stage['world']}-{stage['stage']}"
            self.assertIn(num, listed, stage["id"])

    def test_rows_never_drift_away_from_their_header(self):
        # 행이 속한 파이프 블록 안에 구분행(|---|)이 있어야 표로 렌더링된다.
        lines = self._readme()
        for i, num in self._stage_rows():
            top = i
            while top > 0 and lines[top - 1].startswith("|"):
                top -= 1
            block = []
            j = top
            while j < len(lines) and lines[j].startswith("|"):
                block.append(lines[j])
                j += 1
            self.assertTrue(any(set(b) <= set("|- :") for b in block),
                            f"{num} 행(README:{i + 1})이 머리글 없는 표에 있다")

    def test_rows_are_in_stage_order(self):
        """목록 화면은 world·stage 순으로 정렬한다. 문서만 뒤바뀌면 서로 다르다.

        표가 둘(MySQL·PostgreSQL)이고 PostgreSQL 월드 번호는 다시 1부터
        시작하므로, 전체가 아니라 **한 표 안의** 순서를 본다.
        """
        lines = self._readme()
        blocks = {}
        for i, num in self._stage_rows():
            top = i
            while top > 0 and lines[top - 1].startswith("|"):
                top -= 1
            blocks.setdefault(top, []).append(num)
        self.assertTrue(blocks, "README에서 스테이지 표를 찾지 못했다")
        key = lambda n: [int(x) for x in n.split("-")]      # noqa: E731
        for top, nums in blocks.items():
            self.assertEqual(nums, sorted(nums, key=key),
                             f"README:{top + 1} 표의 순서가 스테이지 번호와 다르다")


class PostmortemChapterTest(unittest.TestCase):
    """회고 챕터는 스테이지 하나가 아니라 **게임의 구조**에 딸려 있다.

    README는 "모든 스테이지가 고급 09의 회고 루프와 이어진다"고 하고
    `build_note`도 그 챕터의 템플릿을 따른다고 적어두었는데, 정작 그 경로를
    `chapters`에 넣은 스테이지가 하나도 없어 '더 읽을 곳'에 한 번도 뜨지 않았다.
    14벌로 복사해 넣으면 connect_hint 처럼 썩으므로 엔진이 붙인다.
    """

    def test_the_chapter_file_exists(self):
        self.assertTrue((REPO_ROOT / shooting.POSTMORTEM_CHAPTER).is_file(),
                        shooting.POSTMORTEM_CHAPTER)

    def test_every_stage_points_at_it_after_the_run(self):
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            self.assertIn(shooting.POSTMORTEM_CHAPTER,
                          shooting.chapter_reading_list(stage), path.name)

    def test_it_is_not_listed_twice(self):
        stage = _minimal_stage(chapters=[shooting.POSTMORTEM_CHAPTER])
        listing = shooting.chapter_reading_list(stage)
        self.assertEqual(listing.count(shooting.POSTMORTEM_CHAPTER), 1)

    def test_the_note_points_at_it_too(self):
        """포기해도 노트는 쓴다 — 해설(더 읽을 곳)이 붙지 않는 그 경로가 특히."""
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-1-runaway-query.json")
        session = shooting.init_session(stage)
        result = shooting.summarize(stage, session)
        note = shooting.build_note(stage, session, result, "2026-08-05")
        self.assertIn(shooting.POSTMORTEM_CHAPTER, note)


class ConnectHintIsGeneratedTest(unittest.TestCase):
    """접속 명령의 단일 출처는 `PLAYER_*` 상수다.

    스테이지 13개가 같은 명령을 손으로 들고 있었고, **13개 전부** 자동 생성본과
    어긋나 있었다 — 전부 `-Dshop`이 빠져서, 안내대로 붙은 플레이어는 기본
    데이터베이스 없이 시작했다. 포트나 계정이 바뀌면 13곳이 조용히 썩는 구조였다.
    """

    def test_no_stage_carries_a_hand_written_connect_command(self):
        offenders = [p.name for p in shooting.discover_stages()
                     if "connect_hint" in json.loads(
                         Path(p).read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_the_field_is_rejected_so_it_cannot_come_back_silently(self):
        # 지원을 뗀 필드를 조용히 무시하면 적어 넣은 사람은 왜 안 뜨는지 모른다.
        errs = shooting.validate_stage(
            _minimal_stage(connect_hint="mysql -uroot"))
        self.assertTrue(any("connect_hint" in e for e in errs), errs)

    def test_every_stage_gets_a_usable_generated_hint(self):
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            hint = shooting._connect_hint(stage)
            first = shooting.default_target(stage)
            self.assertIn(shooting.PLAYER_HOST, hint, path.name)
            self.assertIn(shooting.PLAYER_PORTS[first], hint, path.name)
            self.assertIn(shooting.PLAYER_USER, hint, path.name)
            self.assertIn(shooting.PLAYER_PASSWORD, hint, path.name)
            # 명령 이름은 그 스테이지가 실제로 띄우는 클라이언트여야 한다.
            self.assertIn(shooting.client_name(stage), hint, path.name)

    def test_a_second_server_is_still_pointed_at(self):
        # 범인이 replica에 있는 스테이지에서 primary만 안내하면 현장을 놓친다.
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "2-1-replication-setup.json")
        self.assertIn(shooting.PLAYER_PORTS["replica"],
                      shooting._connect_hint(stage))


class ClientLabelTest(unittest.TestCase):
    """화면이 안내하는 클라이언트 이름은 실제로 뜨는 것과 같아야 한다.

    `client_command()`는 처음부터 벤더에 따라 `mysql`/`psql`을 골랐는데 HUD의
    라벨만 `mysql`로 굳어 있었다. PostgreSQL 스테이지에서 화면은 "c 키로 mysql
    접속"이라 하고 `c`를 누르면 psql이 뜬다 — 게다가 바로 아랫줄의 접속 명령
    (`_connect_hint`)은 psql을 제대로 안내해서, 한 화면이 서로 다른 두 클라이언트를
    가리켰다.
    """

    def _stage(self, name):
        return shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / name)

    def test_names_the_client_each_vendor_actually_launches(self):
        self.assertEqual(
            shooting.client_name(self._stage("1-1-runaway-query.json")),
            "mysql")
        self.assertEqual(
            shooting.client_name(
                self._stage("pg-1-1-idle-in-transaction.json")),
            "psql")

    def test_every_stage_footer_names_the_binary_that_will_run(self):
        """라벨과 명령이 각자 벤더를 판단하면 또 갈라진다 — 묶어서 고정한다."""
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            session = shooting.init_session(stage)
            target = shooting.default_target(stage)
            binary = shooting.client_command(stage, None, target)[0]
            self.assertIn(f"c {binary} 접속",
                          shooting.play_footer(stage, session), path.name)

    def test_footer_still_carries_the_other_keys(self):
        stage = self._stage("pg-1-1-idle-in-transaction.json")
        session = shooting.init_session(stage)
        session["notes_count"] = 2
        footer = shooting.play_footer(stage, session)
        for piece in ("r 상황 보고", "지난 기록(2)", "q 포기",
                      f"h 힌트(0/{len(stage['hints'])})"):
            self.assertIn(piece, footer)

    def test_connect_failure_hint_is_vendor_specific(self):
        # MySQL 쪽 함정(~/.my.cnf 가 MYSQL_PWD 를 이긴다)은 psql 과 무관하다.
        mysql_hint = shooting.client_error_hint("primary")
        self.assertIn(".my.cnf", mysql_hint)
        pg_hint = shooting.client_error_hint("postgres")
        self.assertNotIn(".my.cnf", pg_hint)
        self.assertNotIn("MYSQL_PWD", pg_hint)
        self.assertIn("--with-postgresql", pg_hint)


class WaitUntilTest(unittest.TestCase):
    """폴링 대기를 한 군데로 모은 헬퍼.

    같은 모양이 랩 기동과 플레이 전 대기로 흩어져 있었다. `tui.pick()`이
    세 벌로 갈라졌다가 합쳐진 것과 같은 자리다 — 세 번째 사본을 만들기 전에 모은다.
    """

    @contextlib.contextmanager
    def _no_sleep(self):
        """실제로 자지 않는다. 잔 횟수만 센다."""
        real = shooting.time.sleep
        naps = []
        shooting.time.sleep = naps.append
        try:
            yield naps
        finally:
            shooting.time.sleep = real

    def test_true_at_once_does_not_sleep(self):
        with self._no_sleep() as naps:
            self.assertTrue(shooting.wait_until(lambda: True, 10))
        self.assertEqual(naps, [])

    def test_polls_until_the_condition_holds(self):
        calls = []

        def ready():
            calls.append(1)
            return len(calls) >= 3

        with self._no_sleep() as naps:
            self.assertTrue(shooting.wait_until(ready, 10, poll_seconds=0.1))
        self.assertEqual(len(calls), 3)
        self.assertEqual(naps, [0.1, 0.1])

    def test_gives_up_at_the_deadline(self):
        with self._no_sleep():
            self.assertFalse(
                shooting.wait_until(lambda: False, 0, poll_seconds=0.01))

    def test_calls_on_wait_once_per_rest(self):
        # 기다리는 동안 화면이 조용하면 멈춘 것처럼 보인다.
        calls, ticks = [], []

        def ready():
            calls.append(1)
            return len(calls) >= 3

        with self._no_sleep():
            shooting.wait_until(ready, 10, poll_seconds=0.1,
                                on_wait=lambda: ticks.append(1))
        self.assertEqual(len(ticks), 2)


class PostgresReadinessTest(unittest.TestCase):
    """PostgreSQL 랩은 **준비될 때까지** 기다려야 한다.

    `container_running`은 컨테이너가 뜨자마자 참이 되는데, 그 시점의 postgres는
    아직 initdb로 20만 행을 넣는 중이다(`shooting/lab/pg-seed/01-schema.sql`).
    그 창에서 출발하면 `setup_stage`의 첫 `docker exec psql`이 실패하고,
    플레이어는 자기가 만들지 않은 오류를 본다. MySQL 경로에는 이미 healthy
    대기가 있는데 PostgreSQL 경로에만 없었다.
    """

    PG_STAGE = "pg-1-1-idle-in-transaction"

    @contextlib.contextmanager
    def _lab(self, pg_running=True, healthy_after=0):
        """랩을 흉내낸다. postgres는 `healthy_after`번 물어본 뒤에야 healthy."""
        seen = {"health_polls": 0}
        names = ("lab_running", "lab_healthy", "setup_stage", "teardown_stage",
                 "offer_note", "run_line", "container_running",
                 "container_healthy", "lab_up")
        real = {n: getattr(shooting, n) for n in names}
        real_sleep = shooting.time.sleep

        def fake_container_healthy(target):
            if target != "postgres":
                return True
            seen["health_polls"] += 1
            return seen["health_polls"] > healthy_after

        def fake_setup(stage, log=print):
            # 출발 시점에 postgres가 실제로 준비돼 있었는지를 붙잡는다.
            seen["pg_ready_at_setup"] = fake_container_healthy("postgres")
            seen["health_polls"] -= 1          # 이 조회는 계측용이라 세지 않는다
            return {"allowed_pids": set(), "started_at": {}}

        def fake_run_line(stage, session, baseline):
            seen["played"] = True
            return None

        shooting.lab_running = lambda: True
        shooting.lab_healthy = lambda: True
        shooting.container_running = lambda t: (pg_running if t == "postgres"
                                                else True)
        shooting.container_healthy = fake_container_healthy
        shooting.lab_up = lambda *a, **k: True
        shooting.setup_stage = fake_setup
        shooting.teardown_stage = lambda stage: None
        shooting.offer_note = lambda stage, session, result: None
        shooting.run_line = fake_run_line
        shooting.time.sleep = lambda s: None
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                yield seen
        finally:
            seen["stdout"] = buf.getvalue()
            shooting.time.sleep = real_sleep
            for n, fn in real.items():
                setattr(shooting, n, fn)

    def test_waits_until_postgres_is_healthy_before_injecting(self):
        with self._lab(healthy_after=3) as seen:
            rc = shooting.cmd_play(self.PG_STAGE, force_line=True, seed=1)
        self.assertEqual(rc, 0)
        self.assertTrue(seen.get("played"))
        self.assertTrue(seen.get("pg_ready_at_setup"),
                        "postgres가 준비되기 전에 장애를 주입했다")

    def test_a_healthy_lab_starts_without_waiting(self):
        with self._lab(healthy_after=0) as seen:
            shooting.cmd_play(self.PG_STAGE, force_line=True, seed=1)
        self.assertTrue(seen.get("pg_ready_at_setup"))
        self.assertNotIn("준비 중", seen["stdout"])

    def test_a_lab_that_is_down_is_told_to_come_up_not_waited_on(self):
        # 안 떠 있는 것을 기다리면 영원히 기다린다. 그건 안내할 일이다.
        with self._lab(pg_running=False) as seen:
            rc = shooting.cmd_play(self.PG_STAGE, force_line=True, seed=1)
        self.assertEqual(rc, 1)
        self.assertIn("--with-postgresql", seen["stdout"])
        self.assertIsNone(seen.get("played"))

    def test_lab_up_with_postgres_waits_for_it_too(self):
        """`./shoot up --with-postgresql` 이 '준비 완료'라고 거짓말하면 안 된다."""
        polls = {"n": 0}
        names = ("_compose", "lab_healthy", "container_healthy")
        real = {n: getattr(shooting, n) for n in names}
        real_sleep = shooting.time.sleep

        def fake_pg_healthy(target):
            if target != "postgres":
                return True
            polls["n"] += 1
            return polls["n"] > 2

        shooting._compose = lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")
        shooting.lab_healthy = lambda: True
        shooting.container_healthy = fake_pg_healthy
        shooting.time.sleep = lambda s: None
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                shooting.lab_up(wait_seconds=30, with_postgres=True)
        finally:
            shooting.time.sleep = real_sleep
            for n, fn in real.items():
                setattr(shooting, n, fn)
        self.assertGreater(polls["n"], 2,
                           "postgres healthy 를 기다리지 않고 끝냈다")

    def test_lab_up_without_postgres_ignores_it(self):
        # MySQL만 하는 사람이 뜨지도 않은 컨테이너를 기다릴 이유가 없다.
        asked = []
        names = ("_compose", "lab_healthy", "container_healthy")
        real = {n: getattr(shooting, n) for n in names}
        shooting._compose = lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")
        shooting.lab_healthy = lambda: True
        shooting.container_healthy = lambda t: asked.append(t) or True
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                shooting.lab_up(wait_seconds=30, with_postgres=False)
        finally:
            for n, fn in real.items():
                setattr(shooting, n, fn)
        self.assertEqual(asked, [])


class DiagnosisChoiceLengthTest(unittest.TestCase):
    """진단 문항도 `exam.py` 출제 규약을 따르므로 같은 누출을 가진다.

    보기 순서는 그 판의 시드로 섞이니 위치는 정답을 알려주지 않는다. 그런데
    **길이**는 알려준다 — 측정 시점 14문항 중 13개(93%)에서 정답이 가장 긴 보기였다.
    스테이지 문항은 등급의 '진단 정확도' 항목을 좌우하므로, 읽지 않고 고를 수 있으면
    그 항목이 다시 공짜가 된다(셔플로 막은 것과 같은 구멍이 다른 통로로 열린다).

    216문항을 손보는 것은 콘텐츠 작업이라, 그 전에 **더 나빠지지 않게** 못을 박는다.
    아래 숫자는 갚아야 할 빚의 잔액이고 줄어드는 방향으로만 바꾼다.
    같은 규약의 시험 문제은행 쪽 래칫은 `tests/test_exam.py`가 들고 있다.
    """

    BASELINE = 0           # 전부 갚았다
    # `_quiz_screen` 은 보기를 **감싸지 않고 자른다**(`put(..., w - 6)`).
    # 정답이 길다는 것은 곧 정답이 화면에서 잘린다는 뜻이라, 한때 80칸 터미널에서
    # 잘리는 보기 14개 중 12개가 정답이었다. 그래서 이 스테이지 문항은 시험
    # 문제은행과 **반대로** — 오답을 늘리는 게 아니라 정답을 줄여 맞춘다.
    # 잘려 나간 뉘앙스는 `explain` 이 받는다(그쪽은 `wrap()` 으로 감싸진다).
    MAX_DISPLAY_COLUMNS = 74      # 80칸 터미널: 좌우 여백 6칸을 뺀 값

    def _mcqs(self):
        out = []
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            for obj in stage["objectives"]:
                if (obj["type"] == "quiz"
                        and obj["question"].get("type") == "mcq"):
                    out.append((path.name, obj["question"]))
        return out

    def test_no_more_answers_leak_by_being_longest(self):
        mcqs = self._mcqs()
        self.assertTrue(mcqs, "스테이지에 객관식 진단 문항이 없다")
        # 시험 문제은행 쪽과 같은 기준 — 동점은 세지 않는다. 같은 길이의 오답이
        # 있으면 "가장 긴 것"을 고를 수 없고, 화면 폭 예산 때문에 오답을 정답보다
        # 더 길게 만들 수도 없어 동점이 이 형식에서 도달 가능한 최선이다.
        leaky = [name for name, q in mcqs
                 if tui.cwidth(q["choices"][q["answer"]])
                 > max(tui.cwidth(c) for i, c in enumerate(q["choices"])
                       if i != q["answer"])]
        self.assertLessEqual(
            len(leaky), self.BASELINE,
            f"정답이 가장 긴 보기인 문항 {len(leaky)}/{len(mcqs)} "
            f"(기준선 {self.BASELINE}) — 새로 늘리지 마라: {sorted(set(leaky))}")

    def test_no_choice_is_cut_off_on_an_80_column_terminal(self):
        """보기가 잘리면 플레이어는 무엇을 고르는지 모른 채 고른다."""
        too_wide = []
        for name, q in self._mcqs():
            for i, c in enumerate(q["choices"]):
                w = tui.cwidth(f"{i + 1}) {c}") + 4
                if w > self.MAX_DISPLAY_COLUMNS:
                    too_wide.append(f"{name}: {w}칸 — {c[:30]}…")
        self.assertEqual(too_wide, [])

    def test_a_new_stage_does_not_inherit_the_debt(self):
        """빚은 기존 14문항의 것이다. 새 스테이지가 물려받을 이유는 없다."""
        known = {path.name for path in shooting.discover_stages()
                 if shooting.load_stage(path)}
        # 기준선을 세운 시점의 스테이지 목록. 여기 없는 파일은 새로 만든 것이다.
        graded = {
            "1-1-runaway-query.json", "1-2-deadlock.json",
            "1-3-lock-contention.json", "1-4-metadata-lock.json",
            "2-1-replication-setup.json", "2-2-replication-lag.json",
            "3-1-connection-exhaustion.json",
            "3-2-long-transaction-undo.json", "3-3-disk-full.json",
            "3-4-autoincrement-exhausted.json", "4-1-missing-index.json",
            "4-2-slow-query-log.json", "4-3-implicit-conversion.json",
            "pg-1-1-idle-in-transaction.json",
        }
        for name, q in self._mcqs():
            if name in graded:
                continue
            longest = max(tui.cwidth(c) for i, c in enumerate(q["choices"])
                           if i != q["answer"])
            with self.subTest(stage=name):
                self.assertLessEqual(
                    tui.cwidth(q["choices"][q["answer"]]), longest,
                    f"{name}: 새 스테이지의 정답이 가장 긴 보기다 — "
                    f"오답을 정답만큼 구체적으로 써라")
        self.assertTrue(known)


class SessionShuffleTest(unittest.TestCase):
    """진단 문항의 보기 순서는 시드에서 갈라져야 한다.

    섞지 않으면 정답이 작성된 자리에 그대로 남는다. 이 저장소의 mcq 문항은
    시험·스테이지를 통틀어 전부 `answer: 0`으로 쓰여 있으므로(작성 규약이 그렇고
    `exam.py`가 출제할 때 섞는다), 섞지 않으면 **진단 문항의 정답이 항상 1번**이
    된다. 그러면 등급 네 항목 중 '진단 정확도'가 공짜가 되고, 두 번째 판부터
    상황 보고가 읽지 않고 누르는 화면이 된다.
    """

    def _mcqs(self, stage):
        return [o for o in stage["objectives"]
                if o["type"] == "quiz" and o["question"]["type"] == "mcq"]

    def test_choices_do_not_always_keep_the_authored_order(self):
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            if not self._mcqs(stage):
                continue
            graded = set()
            for seed in range(30):
                session = shooting.init_session(stage, random.Random(seed))
                for obj in self._mcqs(stage):
                    graded.add(session["states"][obj["id"]]["answer"])
            self.assertGreater(len(graded), 1, path.name)

    def test_the_graded_index_still_points_at_the_authored_answer(self):
        # 섞고 나서 채점이 어긋나면 정답을 고른 사람이 오답 처리된다.
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            for seed in range(20):
                session = shooting.init_session(stage, random.Random(seed))
                for obj in self._mcqs(stage):
                    st = session["states"][obj["id"]]
                    self.assertEqual(st["order"][st["answer"]],
                                     obj["question"]["answer"],
                                     (path.name, seed))

    def test_same_seed_reproduces_the_same_order(self):
        # `--seed`/`replay`가 되살리는 '같은 판'에는 보기 순서도 포함된다.
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        orders = [{k: v.get("order") for k, v in
                   shooting.init_session(stage, random.Random(4821))["states"].items()}
                  for _ in range(2)]
        self.assertEqual(orders[0], orders[1])

    def test_shuffle_false_pins_the_order(self):
        """`exam.py`와 같은 스키마라고 문서가 약속했으므로 탈출구도 같아야 한다.

        '위 모두 정답' 류 보기가 있으면 섞는 순간 문항이 말이 안 된다. `exam.py`는
        `shuffle: false`로 그 자리를 고정할 수 있는데, 슈팅 쪽만 무시하면 스테이지
        작성자가 적어둔 것이 조용히 아무 일도 하지 않는다.
        """
        stage = _minimal_stage(objectives=[
            {"id": "q", "type": "quiz",
             "question": {"type": "mcq", "shuffle": False,
                          "q": "?", "choices": ["a", "b", "c", "d"],
                          "answer": 0}}])
        for seed in range(20):
            st = shooting.init_session(stage, random.Random(seed))["states"]["q"]
            self.assertEqual(st["order"], [0, 1, 2, 3], seed)
            self.assertEqual(st["answer"], 0, seed)

    def test_without_an_rng_the_authored_order_is_kept(self):
        # 라인 모드 테스트와 문항 화면 테스트가 이 결정적 동작에 기대고 있다.
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-1-runaway-query.json")
        st = shooting.init_session(stage)["states"]["diagnose-symptom"]
        self.assertEqual(st["order"], [0, 1, 2, 3])
        self.assertEqual(st["answer"], 0)


class CmdPlayWiringTest(unittest.TestCase):
    """`cmd_play`가 실제로 시드를 세션까지 넘겨 판을 여는지.

    원래 버그가 정확히 이 자리였다 — `init_session`은 rng를 받을 줄 알았고
    `shuffle_choices`도 import되어 있었는데, `cmd_play`가 넘기지 않아 보기가
    한 번도 섞이지 않았다. 순수 함수만 검증하면 그 배선 누락은 계속 보이지 않는다.

    도커 경계(랩 기동·장애 주입·화면)만 대신하고, 확인하는 것은 `cmd_play`가
    **실제로 만들어 넘긴 세션**이다.
    """

    SEED = 1          # 이 시드에서 보기 순서는 [3, 0, 2, 1] — 원본 순서가 아니다

    @contextlib.contextmanager
    def _fake_lab(self):
        """랩 대신 화면 함수가 받은 (stage, session)을 붙잡는다.

        표준출력도 `seen["stdout"]`에 남긴다 — 지워 없애기만 하면 "시드가
        화면에 뜨는가" 같은 출력 자체를 검증하는 테스트를 못 짠다.
        """
        seen = {}
        real = {name: getattr(shooting, name) for name in
                ("lab_running", "lab_healthy", "setup_stage", "teardown_stage",
                 "offer_note", "run_line")}

        def fake_run_line(stage, session, baseline):
            seen["stage"], seen["session"] = stage, session
            return None                       # 포기 — 결과 화면·기록을 건너뛴다

        def fake_setup(stage, log=print):
            seen["setup_done"] = time.monotonic()
            return {"allowed_pids": set(), "started_at": {}}

        shooting.lab_running = lambda: True
        shooting.lab_healthy = lambda: True
        shooting.setup_stage = fake_setup
        shooting.teardown_stage = lambda stage: None
        shooting.offer_note = lambda stage, session, result: None
        shooting.run_line = fake_run_line
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                yield seen
        finally:
            seen["stdout"] = buf.getvalue()
            for name, fn in real.items():
                setattr(shooting, name, fn)

    def test_play_shuffles_the_choices_it_shows(self):
        with self._fake_lab() as seen:
            rc = shooting.cmd_play("1-1-runaway-query", force_line=True,
                                   seed=self.SEED)
        self.assertEqual(rc, 0)
        st = seen["session"]["states"]["diagnose-symptom"]
        # 원본 순서 그대로면 정답이 늘 1번에 놓인다 — 그게 고치려던 버그다.
        self.assertNotEqual(st["order"], sorted(st["order"]))
        self.assertNotEqual(st["answer"], 0)

    def test_the_clock_starts_after_the_fault_is_injected(self):
        """장애 주입에 걸린 시간은 플레이 시간이 아니다.

        `setup_stage`는 복제 스테이지에서 binlog를 처음부터 재생하느라 분 단위로
        걸린다. 세션을 그보다 먼저 만들면 그 시간이 전부 소요 시간에 얹혀
        `target_seconds` 보너스가 구조적으로 불가능해진다.
        """
        with self._fake_lab() as seen:
            shooting.cmd_play("1-1-runaway-query", force_line=True,
                              seed=self.SEED)
        self.assertGreaterEqual(seen["session"]["started"], seen["setup_done"])

    def test_seed_is_shown_for_an_mcq_stage_without_vars(self):
        """시드는 `vars`가 없어도 mcq 진단 문항이 있으면 판을 가른다
        (init_session이 그 시드로 보기를 섞는다) — 화면에도 그만큼 떠야 한다.

        2-1-replication-setup은 `vars` 없이 mcq 문항만 있는 스테이지다.
        `vars`로만 게이트를 걸면 이런 스테이지에서는 시드가 한 번도 화면에
        뜨지 않는다.
        """
        stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "2-1-replication-setup.json")
        self.assertFalse(stage.get("vars"))
        with self._fake_lab() as seen:
            rc = shooting.cmd_play("2-1-replication-setup", force_line=True,
                                   seed=self.SEED)
        self.assertEqual(rc, 0)
        self.assertIn(f"이번 판 시드: {self.SEED}", seen["stdout"])

    def test_play_reopens_exactly_what_the_seed_describes(self):
        """`--seed`가 되살리는 판에는 변주와 보기 순서가 **둘 다** 들어 있다."""
        base = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-1-runaway-query.json")
        expected_stage = dict(
            shooting.render_stage(base, random.Random(self.SEED)),
            _seed=self.SEED)
        expected = shooting.init_session(expected_stage,
                                         random.Random(self.SEED))
        with self._fake_lab() as seen:
            shooting.cmd_play("1-1-runaway-query", force_line=True,
                              seed=self.SEED)
        self.assertEqual(json.dumps(seen["stage"], ensure_ascii=False),
                         json.dumps(expected_stage, ensure_ascii=False))
        self.assertEqual(
            {k: v.get("order") for k, v in seen["session"]["states"].items()},
            {k: v.get("order") for k, v in expected["states"].items()})


class SeedRecordTest(unittest.TestCase):
    """시드를 남기지 않으면 '어제 그 판'을 다시 열 수 없다 — 회고가 반쪽이 된다."""

    def setUp(self):
        self.stage = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages" / "1-3-lock-contention.json")
        self.session = shooting.init_session(self.stage)

    def test_note_carries_the_seed(self):
        staged = dict(self.stage, _seed=4821)
        note = shooting.build_note(
            staged, self.session, shooting.summarize(staged, self.session),
            "2026-08-03")
        self.assertIn("4821", note)
        self.assertIn("--seed", note)          # 다시 여는 방법까지 적어둔다

    def test_note_without_a_seed_says_nothing_about_it(self):
        note = shooting.build_note(
            self.stage, self.session,
            shooting.summarize(self.stage, self.session), "2026-08-03")
        self.assertNotIn("--seed", note)

    def test_progress_record_carries_the_seed(self):
        written = []
        real = shooting.PROGRESS_FILE
        try:
            shooting.PROGRESS_FILE = Path("/nonexistent-dir/x.jsonl")
            shooting.save_progress(dict(self.stage, _seed=77), "S", 4,
                                   10.0, 0, 0)
        finally:
            shooting.PROGRESS_FILE = real
        # 파일 쓰기는 실패해도 되지만(플레이를 막지 않는다) 레코드 자체를 확인한다.
        rec = shooting.progress_record(dict(self.stage, _seed=77), "S", 4,
                                       10.0, 0, 0)
        self.assertEqual(rec["seed"], 77)
        self.assertEqual(rec["stage"], self.stage["id"])
        del written


class ValidateVarsTest(unittest.TestCase):
    BASE = {"id": "x", "title": "t",
            "objectives": [{"id": "o", "type": "state", "query": "SELECT 1",
                            "expect": {"op": "eq", "value": 1}}]}

    def _errs(self, **extra):
        return shooting.validate_stage({**self.BASE, **extra})

    def test_undefined_placeholder_is_caught(self):
        errs = self._errs(brief="{{nope}}")
        self.assertTrue(any("nope" in e for e in errs), errs)

    def test_non_ascii_placeholder_is_caught_too(self):
        # 한국어 저장소다 — 누군가 반드시 한글 변수명을 쓴다. 인식조차 못 하면
        # 치환도 안 되고 오류도 안 나서 원문이 그대로 화면에 뜬다.
        errs = self._errs(brief="{{없는변수}}")
        self.assertTrue(any("없는변수" in e for e in errs), errs)

    def test_declared_non_ascii_name_works(self):
        errs = self._errs(vars={"잠금": {"type": "int", "min": 1, "max": 9}},
                          brief="{{잠금}}번")
        self.assertEqual(errs, [])
        out = shooting.render_stage(
            {"vars": {"잠금": {"type": "int", "min": 5, "max": 5}},
             "brief": "{{잠금}}번"}, random.Random(1))
        self.assertEqual(out["brief"], "5번")

    def test_int_in_must_point_at_a_span(self):
        errs = self._errs(vars={"a": {"type": "int", "min": 1, "max": 2},
                                "b": {"type": "int_in", "of": "a"}})
        self.assertTrue(any("int_in" in e for e in errs), errs)

    def test_span_longer_than_its_range_is_caught(self):
        errs = self._errs(vars={"s": {"type": "span", "min": 1, "max": 10,
                                      "length": 50}})
        self.assertTrue(any("length" in e for e in errs), errs)

    def test_unknown_var_type_is_caught(self):
        errs = self._errs(vars={"s": {"type": "매직"}})
        self.assertTrue(any("매직" in e for e in errs), errs)

    def test_placeholder_in_a_numeric_field_is_allowed(self):
        # count 같은 숫자 필드도 변주 대상이다. 값은 렌더링 뒤에 정해지므로
        # 검증이 그 자리에서 int()로 읽으려 하면 안 된다.
        errs = shooting.validate_stage({
            **self.BASE,
            "vars": {"n": {"type": "int", "min": 1, "max": 3}},
            "setup": [{"type": "sessions", "count": "{{n}}", "sql": "SELECT 1"}],
        })
        self.assertEqual(errs, [])

    def test_bad_literal_count_is_still_caught(self):
        errs = shooting.validate_stage({
            **self.BASE,
            "setup": [{"type": "sessions", "count": 0, "sql": "SELECT 1"}],
        })
        self.assertTrue(any("count" in e for e in errs), errs)

    def test_span_halves_are_referenceable(self):
        errs = self._errs(vars={"s": {"type": "span", "min": 1, "max": 99,
                                      "length": 5}},
                          brief="{{s.from}}~{{s.to}}")
        self.assertEqual(errs, [])


class GtidCountTest(unittest.TestCase):
    """복제 스테이지의 시작 비용은 재생할 **트랜잭션 수**에 좌우된다."""

    def test_single_range(self):
        self.assertEqual(shooting.count_gtids("uuid:1-6843"), 6843)

    def test_single_transaction_has_no_dash(self):
        self.assertEqual(shooting.count_gtids("uuid:7"), 1)

    def test_multiple_ranges_in_one_uuid(self):
        self.assertEqual(shooting.count_gtids("uuid:1-5:10-12"), 8)

    def test_multiple_uuids(self):
        self.assertEqual(
            shooting.count_gtids("a:1-5,b:1-3"), 8)

    def test_newlines_are_tolerated(self):
        # 여러 UUID면 gtid_executed가 개행으로 나뉘어 나온다.
        self.assertEqual(shooting.count_gtids("a:1-5,\nb:1-3"), 8)

    def test_empty_or_garbage_is_zero(self):
        for text in ("", None, "   ", "not-a-gtid-set"):
            self.assertEqual(shooting.count_gtids(text), 0, repr(text))

    def test_threshold_is_below_the_measured_failure(self):
        # 실측: GTID 148,868에서 2-2의 120초 대기가 시간 초과로 실패했다.
        # 갓 띄운 랩은 22개다. 임계치는 그 사이에서 실패 쪽과 떨어져 있어야 한다.
        self.assertLess(shooting.BINLOG_WARN_GTIDS, 148868)
        self.assertGreater(shooting.BINLOG_WARN_GTIDS, 1000)


class PostgresLabTest(unittest.TestCase):
    """PostgreSQL 랩은 프로파일 뒤에 있다 — MySQL만 하는 사람이 비용을 치르지 않게."""

    def setUp(self):
        self.compose = (REPO_ROOT / "shooting" / "lab"
                        / "compose.yaml").read_text(encoding="utf-8")

    def test_service_is_behind_a_profile(self):
        # 프로파일이 빠지면 ./shoot up 이 조용히 무거워진다.
        self.assertIn('profiles: ["postgresql"]', self.compose)

    def test_logging_is_configured_for_the_watcher(self):
        # 판정 구조 전체가 이 로그에 얹혀 있다.
        for opt in ("logging_collector=on", "log_destination=csvlog",
                    "log_statement=all"):
            self.assertIn(opt, self.compose, opt)

    def test_port_is_loopback_only(self):
        self.assertIn('"127.0.0.1:5432:5432"', self.compose)

    def test_seed_sets_up_the_log_view(self):
        seed = (REPO_ROOT / "shooting" / "lab" / "pg-seed"
                / "03-logview.sql").read_text(encoding="utf-8")
        self.assertIn("file_fdw", seed)
        # log_filename 이 'pg' 여도 csvlog 가 .csv 를 덧붙인다 — 여기가 어긋나면
        # 외부 테이블이 빈 파일을 가리켜 감시가 조용히 아무것도 못 읽는다.
        self.assertIn("/var/lib/postgresql/data/log/pg.csv", seed)

    def test_seed_separates_the_same_three_roles(self):
        seed = (REPO_ROOT / "shooting" / "lab" / "pg-seed"
                / "02-users.sql").read_text(encoding="utf-8")
        for role in ("dba", "app"):
            self.assertIn(f"CREATE ROLE {role} LOGIN", seed)
        # 남의 질의문을 볼 수 없으면 진단이 성립하지 않는다.
        self.assertIn("pg_monitor", seed)
        self.assertIn("pg_signal_backend", seed)

    def test_profile_name_matches_what_the_runner_passes(self):
        self.assertIn(shooting.POSTGRES_PROFILE, self.compose)


class ForbiddenCommandTest(unittest.TestCase):
    """'이 명령으로 때우는 것은 복구가 아니다'를 산문이 아니라 판정으로."""

    CONSTRAINT = [{"id": "no-raise-limit", "label": "한도 올리기로 때우기",
                   "detect": "forbidden_command",
                   "pattern": r"(?i)set\s+global\s+max_connections"}]

    def _check(self, commands):
        return shooting.detect_violations(self.CONSTRAINT,
                                          {"commands": commands})

    def test_clean_play_has_no_violation(self):
        self.assertEqual(self._check(["SHOW PROCESSLIST", "KILL 12"]), [])

    def test_the_shortcut_is_caught(self):
        v = self._check(["SET GLOBAL max_connections = 500"])
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["id"], "no-raise-limit")

    def test_case_and_spacing_do_not_matter(self):
        for cmd in ["set global max_connections=500",
                    "SET   GLOBAL   MAX_CONNECTIONS = 500",
                    "SET GLOBAL max_connections=99;"]:
            self.assertEqual(len(self._check([cmd])), 1, cmd)

    def test_reading_the_variable_is_not_the_shortcut(self):
        # 진단하려고 값을 보는 것까지 감점하면 안 된다.
        self.assertEqual(self._check(["SELECT @@max_connections",
                                      "SHOW VARIABLES LIKE 'max_connections'"]), [])

    def test_allowance_can_be_raised(self):
        c = [{**self.CONSTRAINT[0], "max_matches": 1}]
        cmds = ["SET GLOBAL max_connections = 500"]
        self.assertEqual(shooting.detect_violations(c, {"commands": cmds}), [])
        self.assertEqual(
            len(shooting.detect_violations(c, {"commands": cmds * 2})), 1)

    def test_detail_says_how_many(self):
        v = self._check(["SET GLOBAL max_connections = 1",
                         "SET GLOBAL max_connections = 2"])
        self.assertIn("2", v[0]["detail"])

    def test_missing_commands_is_not_a_crash(self):
        self.assertEqual(shooting.detect_violations(self.CONSTRAINT, {}), [])

    def test_broken_pattern_is_caught_by_validation(self):
        errs = shooting.validate_stage({
            "id": "x", "title": "t",
            "objectives": [{"id": "o", "type": "state", "query": "SELECT 1",
                            "expect": {"op": "eq", "value": 1}}],
            "constraints": [{"id": "bad", "detect": "forbidden_command",
                             "pattern": "SET GLOBAL ((("}],
        })
        self.assertTrue(any("pattern" in e for e in errs), errs)

    def test_pattern_is_required(self):
        errs = shooting.validate_stage({
            "id": "x", "title": "t",
            "objectives": [{"id": "o", "type": "state", "query": "SELECT 1",
                            "expect": {"op": "eq", "value": 1}}],
            "constraints": [{"id": "bad", "detect": "forbidden_command"}],
        })
        self.assertTrue(any("pattern" in e for e in errs), errs)


class LastSeedTest(unittest.TestCase):
    """'그때 그 판을 다시' — 회고와 재도전 사이를 잇는다."""

    def _lines(self, records):
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)

    RECORDS = [
        {"stage": "1-2-deadlock", "seed": 5, "at": "2026-08-03T10:00:00"},
        {"stage": "1-1-runaway-query", "seed": 42, "at": "2026-08-03T11:00:00"},
        {"stage": "1-2-deadlock", "seed": 9, "at": "2026-08-03T12:00:00"},
    ]

    def test_without_a_stage_takes_the_most_recent_play(self):
        self.assertEqual(shooting.last_seed(self._lines(self.RECORDS)),
                         ("1-2-deadlock", 9))

    def test_with_a_stage_takes_that_stage_s_last_seed(self):
        self.assertEqual(
            shooting.last_seed(self._lines(self.RECORDS), "1-1-runaway-query"),
            ("1-1-runaway-query", 42))

    def test_records_without_a_seed_are_skipped(self):
        # #36 이전 기록에는 seed가 없다.
        text = self._lines([
            {"stage": "x", "seed": 3, "at": "2026-08-03T09:00:00"},
            {"stage": "x", "at": "2026-08-03T10:00:00"},
        ])
        self.assertEqual(shooting.last_seed(text), ("x", 3))

    def test_order_follows_the_file_not_the_timestamp(self):
        # 기록은 append-only라 파일 순서가 곧 시간 순서다. 시각 문자열로 정렬하면
        # 시계가 되돌아간 기계에서 엉뚱한 판이 나온다.
        text = self._lines([
            {"stage": "a", "seed": 1, "at": "2099-01-01T00:00:00"},
            {"stage": "b", "seed": 2, "at": "2000-01-01T00:00:00"},
        ])
        self.assertEqual(shooting.last_seed(text), ("b", 2))

    def test_missing_or_broken_input(self):
        for text in ("", None, "not json\n{", '{"stage": "x"}'):
            self.assertIsNone(shooting.last_seed(text), repr(text))

    def test_unknown_stage_returns_nothing(self):
        self.assertIsNone(shooting.last_seed(self._lines(self.RECORDS), "9-9-x"))


class PlayerLogQueryTest(unittest.TestCase):
    """감시가 무엇을 읽는가. 여기서 놓치면 판정이 조용히 헐거워진다."""

    def test_reads_execute_rows_too(self):
        # 준비된 구문은 실제 실행된 문장을 Execute 행에만 남긴다.
        self.assertIn("command_type IN ('Query', 'Execute')",
                      shooting.PLAYER_LOG_SQL)

    def test_does_not_read_prepare_rows(self):
        # Prepare 행은 파라미터가 치환되기 전(`KILL ?`)이라 쓸모가 없다.
        self.assertNotIn("'Prepare'", shooting.PLAYER_LOG_SQL)

    def test_still_drops_the_client_banner(self):
        self.assertIn("select @@version_comment", shooting.PLAYER_LOG_SQL)

    def test_still_clears_the_log_without_replicating(self):
        # 비우지 않으면 CSV 전체 스캔이 세션 길이에 비례해 무거워지고,
        # binlog에 실리면 replica의 로그까지 지운다.
        self.assertIn("TRUNCATE TABLE mysql.general_log",
                      shooting.PLAYER_LOG_SQL)
        self.assertTrue(shooting.PLAYER_LOG_SQL.startswith(shooting.NO_BINLOG))

    def test_kill_via_prepared_statement_is_now_parsed(self):
        # Execute 행이 들어오면 이 형태가 되고, 그러면 정밀도 판정이 성립한다.
        rows = ["PREPARE k FROM ...", "SET @pid := 42",
                "EXECUTE k USING @pid", "KILL 42"]
        self.assertEqual(shooting.parse_kill_targets(rows), [42])


class DockerDecodingTest(unittest.TestCase):
    """감시는 플레이어가 친 명령을 그대로 읽어온다 — 바이트를 통제할 수 없다."""

    def test_undecodable_output_does_not_kill_the_run(self):
        # 실제로 이것 때문에 판이 통째로 죽었다(UnicodeDecodeError).
        # 글자 하나 깨지는 것이 판을 끝내는 것보다 낫다.
        captured = {}

        def fake_run(cmd, **kw):
            captured.update(kw)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        real = shooting.subprocess.run
        shooting.subprocess.run = fake_run
        try:
            shooting._docker("ps")
            self.assertEqual(captured.get("errors"), "replace")
            captured.clear()
            shooting._compose("ps")
            self.assertEqual(captured.get("errors"), "replace")
        finally:
            shooting.subprocess.run = real

    def test_replacement_actually_survives_bad_bytes(self):
        # errors="replace"가 실제로 계약대로 동작하는지(디코딩이 예외 대신 대체).
        self.assertEqual(b"ok\x97".decode("utf-8", errors="replace"), "ok�")
        with self.assertRaises(UnicodeDecodeError):
            b"ok\x97".decode("utf-8")


class SpawnCommandTest(unittest.TestCase):
    """장애 주입 세션을 띄우는 명령 조립."""

    def test_default_runs_sql_and_disconnects(self):
        cmd = shooting.spawn_command("app", "SELECT 1")
        self.assertEqual(cmd, ["mysql", "--no-defaults", "-u", "app",
                               "-e", "SELECT 1"])

    def test_no_defaults_is_always_present(self):
        # /root/.my.cnf 가 MYSQL_PWD를 이겨 app 인증이 조용히 실패한다.
        for cmd in (shooting.spawn_command("app", "SELECT 1"),
                    shooting.spawn_command("app", "SELECT 1", idle_seconds=60)):
            self.assertIn("--no-defaults", " ".join(cmd))

    def test_idle_session_keeps_the_connection_open(self):
        cmd = shooting.spawn_command("app", "SELECT 1", idle_seconds=60)
        self.assertEqual(cmd[:2], ["sh", "-c"])
        script = cmd[2]
        # stdin이 열려 있는 동안 클라이언트가 대기하므로 Command=Sleep이 된다.
        # `mysql -e`는 질의가 끝나면 바로 끊고, SELECT SLEEP()은 Command=Query라
        # 유휴 커넥션과 구분되지 않는다.
        self.assertIn("sleep 60", script)
        self.assertIn("| mysql --no-defaults", script)

    def test_idle_session_quotes_the_sql(self):
        # 스테이지 SQL에는 따옴표가 흔하다 — 셸에 그대로 넘기면 깨진다.
        sql = "UPDATE t SET s='IT''S' WHERE x=\"y\""
        script = shooting.spawn_command("app", sql, idle_seconds=1)[2]
        # mysql 대신 cat으로 받아 셸이 원문을 그대로 복원하는지 본다.
        piped = script.split("| mysql")[0] + "| cat"
        out = subprocess.run(["sh", "-c", piped], capture_output=True,
                             text=True, timeout=10).stdout
        self.assertEqual(out, sql + ";\n")

    def test_idle_seconds_must_be_an_integer_in_the_script(self):
        # 문자열이 그대로 들어가면 셸에서 sleep이 실패한다.
        script = shooting.spawn_command("app", "SELECT 1",
                                        idle_seconds="90")[2]
        self.assertIn("sleep 90", script)


class StreamingOutputTest(unittest.TestCase):
    """비대화형 라인 모드가 '감시 창'으로 쓸모 있으려면 출력이 흘러야 한다.

    stdout이 tty가 아니면 Python은 라인 버퍼링이 아니라 블록 버퍼링을 쓴다.
    그대로 두면 판이 끝날 때까지 한 글자도 나오지 않는다.
    """

    class _Stream:
        def __init__(self, tty):
            self._tty = tty
            self.reconfigured = []

        def isatty(self):
            return self._tty

        def reconfigure(self, **kw):
            self.reconfigured.append(kw)

    def test_pipe_switches_to_line_buffering(self):
        s = self._Stream(tty=False)
        self.assertTrue(shooting.ensure_streaming_output(s))
        self.assertEqual(s.reconfigured, [{"line_buffering": True}])

    def test_terminal_is_left_alone(self):
        # tty는 이미 라인 버퍼링이다 — 건드릴 이유가 없다.
        s = self._Stream(tty=True)
        self.assertFalse(shooting.ensure_streaming_output(s))
        self.assertEqual(s.reconfigured, [])

    def test_stream_without_reconfigure_is_not_fatal(self):
        # 테스트가 stdout을 StringIO로 바꿔치기하는 경우가 있다.
        self.assertFalse(shooting.ensure_streaming_output(io.StringIO()))

    def test_stream_that_refuses_is_not_fatal(self):
        class Stubborn:
            def isatty(self):
                return False

            def reconfigure(self, **_kw):
                raise ValueError("지원하지 않음")

        self.assertFalse(shooting.ensure_streaming_output(Stubborn()))


class BestRanksTest(unittest.TestCase):
    """지난 최고 등급 — S랭크 재도전이라는 동기가 여기서 나온다."""

    def _lines(self, records):
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)

    def test_keeps_the_best_not_the_latest(self):
        # 나중에 C를 받았다고 예전 S가 지워지면 안 된다.
        got = shooting.best_ranks(self._lines([
            {"stage": "1-3", "rank": "S"},
            {"stage": "1-3", "rank": "C"},
        ]))
        self.assertEqual(got, {"1-3": "S"})

    def test_ranks_compare_by_order_not_alphabet(self):
        # 문자열 비교로는 "S" > "C" 가 우연히 맞지만 "B" > "A" 도 참이 된다.
        got = shooting.best_ranks(self._lines([
            {"stage": "x", "rank": "B"},
            {"stage": "x", "rank": "A"},
        ]))
        self.assertEqual(got, {"x": "A"})

    def test_tracks_each_stage_separately(self):
        got = shooting.best_ranks(self._lines([
            {"stage": "1-3", "rank": "A"},
            {"stage": "2-2", "rank": "C"},
        ]))
        self.assertEqual(got, {"1-3": "A", "2-2": "C"})

    def test_broken_lines_are_skipped_not_fatal(self):
        # 기록 파일이 깨졌다고 플레이를 막으면 안 된다.
        text = ('{"stage": "1-3", "rank": "A"}\n'
                'this is not json\n'
                '\n'
                '{"stage": "1-3"}\n'              # rank 없음
                '{"rank": "S"}\n'                 # stage 없음
                '{"stage": "1-3", "rank": "??"}\n'  # 알 수 없는 등급
                '{"stage": "2-2", "rank": "S"}\n')
        self.assertEqual(shooting.best_ranks(text), {"1-3": "A", "2-2": "S"})

    def test_empty_input(self):
        self.assertEqual(shooting.best_ranks(""), {})
        self.assertEqual(shooting.best_ranks(None), {})

    def test_missing_file_reads_as_empty(self):
        # 한 판도 안 한 상태 — 조용히 비어야 한다.
        self.assertEqual(
            shooting.read_progress(Path("/nonexistent/results.jsonl")), "")

    def test_label_shows_rank_only_when_cleared(self):
        best = {"1-3": "S"}
        self.assertIn("S", shooting.stage_rank_badge("1-3", best))
        self.assertEqual(shooting.stage_rank_badge("2-2", best), "")


class WorldGroupingTest(unittest.TestCase):
    """월드는 그동안 JSON에만 있고 코드가 읽지 않았다."""

    def _entries(self, *worlds):
        return [(Path(f"{w}-{i}-x.json"), {"id": f"{w}-{i}-x", "world": w,
                                           "stage": i, "title": f"제목{w}{i}"})
                for w, i in worlds]

    def test_groups_are_ordered_by_world_then_stage(self):
        entries = self._entries((2, 2), (1, 4), (2, 1), (1, 3))
        got = [(w, [s["id"] for _, s in items])
               for w, items in shooting.group_by_world(entries)]
        self.assertEqual(got, [(1, ["1-3-x", "1-4-x"]),
                               (2, ["2-1-x", "2-2-x"])])

    def test_every_shipped_world_has_a_name(self):
        # 이름 없는 월드가 생기면 선택 화면에 번호만 뜬다.
        for path in shooting.discover_stages():
            world = shooting.load_stage(path).get("world")
            self.assertIn(world, shooting.WORLD_TITLES, path.name)

    def test_unknown_world_still_gets_a_label(self):
        # 정의를 빠뜨렸다고 목록에서 사라지면 안 된다.
        label = shooting.world_menu_label(99, self._entries((99, 1)), {})
        self.assertIn("99", label)

    def test_world_label_counts_its_stages(self):
        label = shooting.world_menu_label(1, self._entries((1, 3), (1, 4)), {})
        self.assertIn("2", label)
        self.assertIn(shooting.WORLD_TITLES[1], label)

    def test_world_label_shows_best_rank_across_its_stages(self):
        entries = self._entries((1, 3), (1, 4))
        label = shooting.world_menu_label(1, entries, {"1-3-x": "B",
                                                       "1-4-x": "S"})
        self.assertIn("S", label)     # 가장 높은 것을 보여준다

    def test_world_label_without_any_clear_has_no_rank(self):
        label = shooting.world_menu_label(1, self._entries((1, 3)), {})
        self.assertIsNone(re.search(r"\[[SABC]\]", label))

    def test_broken_definition_lands_in_its_own_group(self):
        # 정의를 못 읽으면 world를 알 수 없다 — 목록에서 빼지 않는다.
        entries = [(Path("9-9-broken.json"), None)]
        groups = shooting.group_by_world(entries)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0][1]), 1)


class ChapterLinkTest(unittest.TestCase):
    """스테이지 → 챕터 연결. 읽기·확인·겪기 세 축을 잇는 마지막 고리다."""

    BASE = {"id": "x", "title": "t",
            "objectives": [{"id": "o", "type": "state", "query": "SELECT 1",
                            "expect": {"op": "eq", "value": 1}}]}

    def test_chapters_must_be_a_list_of_markdown_paths(self):
        for bad in ("02-intermediate/01-transaction-and-locking.md",
                    [123], ["notes.txt"]):
            errs = shooting.validate_stage({**self.BASE, "chapters": bad})
            self.assertTrue(any("chapters" in e for e in errs), (bad, errs))

    def test_absolute_and_escaping_paths_are_rejected(self):
        # 저장소 밖을 가리키면 다른 사람 기계에서 깨진다.
        for bad in ["/etc/passwd.md", "../secrets.md"]:
            errs = shooting.validate_stage({**self.BASE, "chapters": [bad]})
            self.assertTrue(any("chapters" in e for e in errs), (bad, errs))

    def test_missing_file_is_caught_when_a_root_is_given(self):
        errs = shooting.validate_stage(
            {**self.BASE, "chapters": ["02-intermediate/없는챕터.md"]},
            repo_root=REPO_ROOT)
        self.assertTrue(any("없는챕터" in e for e in errs), errs)

    def test_existing_file_passes(self):
        errs = shooting.validate_stage(
            {**self.BASE,
             "chapters": ["02-intermediate/01-transaction-and-locking.md"]},
            repo_root=REPO_ROOT)
        self.assertEqual(errs, [])

    def test_format_check_runs_without_a_root(self):
        # 파일 접근 없이도 형식은 검사한다(순수 테스트가 계속 돌아야 한다).
        self.assertEqual(
            shooting.validate_stage(
                {**self.BASE, "chapters": ["02-intermediate/아무거나.md"]}), [])

    def test_every_shipped_stage_points_at_real_chapters(self):
        # 링크가 조용히 썩는 것을 막는다 — 챕터 파일명이 바뀌면 여기서 걸린다.
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            self.assertTrue(stage.get("chapters"), f"{path.name}: chapters 없음")
            for rel in stage["chapters"]:
                self.assertTrue((REPO_ROOT / rel).is_file(),
                                f"{path.name} → {rel}")

    def test_readme_lists_every_stage(self):
        # 월드 3 스테이지 3개를 추가하면서 README 표를 갱신하지 않았다.
        # 사람이 기억하는 대신 여기서 걸리게 한다.
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            world, num = stage.get("world"), stage.get("stage")
            self.assertIn(f"**{world}-{num} {stage['title']}**", readme,
                          f"README 표에 {stage['id']}가 없습니다")

    def test_readme_and_stage_agree_on_chapters(self):
        # 두 곳에 적힌 링크가 서로 어긋나면 어느 쪽이 맞는지 알 수 없다.
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            row = next((ln for ln in readme.splitlines()
                        if f"**{stage.get('world')}-{stage.get('stage')} "
                           f"{stage['title']}**" in ln), None)
            self.assertIsNotNone(row, stage["id"])
            for rel in stage["chapters"]:
                self.assertIn(rel, row,
                              f"{stage['id']}: README 행에 {rel} 링크가 없습니다")

    def test_reading_list_is_rendered_for_humans(self):
        text = shooting.chapter_reading_list(
            {"chapters": ["02-intermediate/01-transaction-and-locking.md"]})
        self.assertIn("트랜잭션", text)          # 파일명이 아니라 제목이 보인다
        self.assertIn("02-intermediate", text)   # 경로도 함께

    def test_the_section_is_never_empty(self):
        """예전에는 `chapters`가 없으면 None이었다 — 빈 섹션을 띄우지 않으려고.

        회고 챕터가 항상 붙게 되면서 목록이 빌 수 없어졌으므로, 지키려던 것
        ("내용 없는 '더 읽을 곳'을 보여주지 않는다")을 그대로 다시 적는다.
        """
        listing = shooting.chapter_reading_list({})
        self.assertTrue(listing and listing.strip())
        self.assertIn(shooting.POSTMORTEM_CHAPTER, listing)


class StageMenuTest(unittest.TestCase):
    """스테이지 선택 목록의 한 줄."""

    def setUp(self):
        self.path = (REPO_ROOT / "shooting" / "stages"
                     / "1-3-lock-contention.json")
        self.stage = shooting.load_stage(self.path)

    def test_label_carries_id_title_and_kind(self):
        label = shooting.stage_menu_label(self.path, self.stage, {})
        self.assertIn("1-3-lock-contention", label)
        self.assertIn("락 지옥", label)
        self.assertIn("🔥", label)              # kind: incident

    def test_build_stage_kind_uses_the_other_icon(self):
        build = dict(self.stage, kind="build")
        self.assertIn("🔧", shooting.stage_menu_label(self.path, build, {}))

    def test_label_shows_the_best_rank(self):
        label = shooting.stage_menu_label(
            self.path, self.stage, {"1-3-lock-contention": "A"})
        self.assertIn("[A]", label)

    def test_unplayed_stage_has_no_badge(self):
        label = shooting.stage_menu_label(self.path, self.stage, {})
        self.assertIsNone(re.search(r"\[[SABC]\]", label))

    def test_broken_definition_stays_in_the_list(self):
        # 목록에서 빼면 파일이 있는데 안 보이는 상태가 된다 — 오류로 보여준다.
        label = shooting.stage_menu_label(Path("stages/9-9-broken.json"),
                                          None, {})
        self.assertIn("오류", label)
        self.assertIn("9-9-broken", label)


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


class VendorDispatchTest(unittest.TestCase):
    """엔진의 벤더 분기. 컨테이너 이름 하나로 어느 DBMS인지 정해진다."""

    def test_target_decides_vendor(self):
        self.assertEqual(shooting.vendor_of("primary"), "mysql")
        self.assertEqual(shooting.vendor_of("replica"), "mysql")
        self.assertEqual(shooting.vendor_of("postgres"), "postgresql")

    def test_unknown_target_stays_mysql(self):
        """모르는 이름이 PostgreSQL로 새면 기존 스테이지가 조용히 깨진다."""
        self.assertEqual(shooting.vendor_of("무엇인가"), "mysql")

    def test_every_container_has_a_vendor(self):
        for target in shooting.CONTAINERS:
            self.assertIn(target, shooting.TARGET_VENDOR, target)

    def test_default_target_follows_stage_dbms(self):
        self.assertEqual(shooting.default_target(_minimal_stage()), "primary")
        self.assertEqual(
            shooting.default_target(_minimal_stage(dbms="mysql")), "primary")
        self.assertEqual(
            shooting.default_target(_minimal_stage(dbms="postgresql")),
            "postgres")

    def test_kill_sql_per_vendor(self):
        self.assertEqual(shooting.kill_session_sql(7), "KILL 7")
        self.assertEqual(shooting.kill_session_sql(7, "postgresql"),
                         "SELECT pg_terminate_backend(7)")


class NormalizeTargetsTest(unittest.TestCase):
    """생략된 `on`은 불러올 때 한 번 채운다 — 읽는 쪽마다 풀면 놓친다."""

    def _pg_stage(self):
        return {
            "id": "5-1-x", "title": "t", "dbms": "postgresql",
            "setup": [{"type": "sql", "sql": "SELECT 1"},
                      {"type": "sql", "on": "postgres", "sql": "SELECT 2"}],
            "objectives": [
                {"id": "o1", "type": "state", "query": "SELECT 1",
                 "expect": {"op": "eq", "value": 1}},
                {"id": "o2", "type": "quiz", "question": {}},
            ],
        }

    def test_fills_missing_on_for_postgresql(self):
        out = shooting.normalize_targets(self._pg_stage())
        self.assertEqual([st["on"] for st in out["setup"]],
                         ["postgres", "postgres"])
        self.assertEqual(out["objectives"][0]["on"], "postgres")

    def test_leaves_quiz_objectives_alone(self):
        """퀴즈에는 대상 서버가 없다 — 없는 필드를 만들어 붙이지 않는다."""
        out = shooting.normalize_targets(self._pg_stage())
        self.assertNotIn("on", out["objectives"][1])

    def test_mysql_stage_passes_through_untouched(self):
        """기존 12개 스테이지가 이 함수를 통과해도 달라지는 게 없어야 한다."""
        for path in shooting.discover_stages():
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("dbms") == "postgresql":
                continue
            self.assertIs(shooting.normalize_targets(raw), raw, path.name)

    def test_does_not_mutate_the_input(self):
        stage = self._pg_stage()
        shooting.normalize_targets(stage)
        self.assertNotIn("on", stage["setup"][0])

    def test_wait_gtid_sync_source_also_filled(self):
        out = shooting.normalize_targets({
            "id": "x", "dbms": "postgresql",
            "setup": [{"type": "wait_gtid_sync"}]})
        self.assertEqual(out["setup"][0]["source"], "postgres")


class PostgresCommandTest(unittest.TestCase):
    """컨테이너 안에서 돌릴 psql 명령의 모양."""

    def test_output_is_parseable_by_parse_tsv(self):
        cmd = shooting.pg_psql_command("SELECT 1")
        self.assertIn("-qtA", cmd)                 # 헤더·정렬·푸터 없음
        self.assertEqual(cmd[cmd.index("-F") + 1], "\t")

    def test_ignores_psqlrc(self):
        """플레이어가 남긴 ~/.psqlrc 가 출력 형식을 바꾸면 파싱이 깨진다."""
        self.assertIn("-X", shooting.pg_psql_command("SELECT 1"))

    def test_failure_must_reach_the_engine(self):
        cmd = shooting.pg_psql_command("SELECT 1")
        self.assertEqual(cmd[cmd.index("-v") + 1], "ON_ERROR_STOP=1")

    def test_sql_goes_last_after_dash_c(self):
        cmd = shooting.pg_psql_command("SELECT 42")
        self.assertEqual(cmd[-2:], ["-c", "SELECT 42"])

    def test_spawn_forces_tcp(self):
        """유닉스 소켓으로 새면 로컬 신뢰 인증에 걸려 계정이 틀려도 붙는다."""
        cmd = shooting.spawn_command("app", "SELECT 1", vendor="postgresql")
        self.assertEqual(cmd[:2], ["psql", "-h"])
        self.assertEqual(cmd[2], "127.0.0.1")
        self.assertIn("app", cmd)

    def test_spawn_idle_holds_the_connection_open(self):
        cmd = shooting.spawn_command("app", "SELECT 1", idle_seconds=5,
                                     vendor="postgresql")
        self.assertEqual(cmd[0], "sh")
        self.assertIn("sleep 5", cmd[2])
        self.assertIn("psql", cmd[2])
        self.assertNotIn("-D", cmd[2])       # MySQL 전용 플래그가 새면 안 된다

    def test_mysql_spawn_is_unchanged(self):
        """기존 스테이지의 장애 주입 경로가 그대로여야 한다."""
        self.assertEqual(shooting.spawn_command("app", "SELECT 1"),
                         ["mysql", "--no-defaults", "-u", "app",
                          "-e", "SELECT 1"])


class PostgresWatchSqlTest(unittest.TestCase):
    """감시 SQL이 시드의 외부 테이블과 실제로 맞물리는가."""

    def test_reads_the_seeded_foreign_table(self):
        seed = (REPO_ROOT / "shooting/lab/pg-seed/03-logview.sql").read_text(
            encoding="utf-8")
        self.assertIn("command_log", seed)
        self.assertIn("FROM command_log", shooting.PG_PLAYER_LOG_SQL)

    def test_watches_only_the_player(self):
        self.assertIn(f"user_name = '{shooting.PLAYER_USER}'",
                      shooting.PG_PLAYER_LOG_SQL)

    def test_reads_prepared_statement_execution_too(self):
        """준비된 구문의 실제 문장은 statement 가 아니라 execute 행에 남는다."""
        self.assertIn("execute %", shooting.PG_PLAYER_LOG_SQL)

    def test_strips_the_log_prefix(self):
        """'statement: SELECT 1' 을 그대로 넘기면 판정 정규식이 전부 어긋난다."""
        self.assertIn("^(statement|execute [^:]*): ",
                      shooting.PG_PLAYER_LOG_SQL)

    def test_log_path_matches_the_lab(self):
        seed = (REPO_ROOT / "shooting/lab/pg-seed/03-logview.sql").read_text(
            encoding="utf-8")
        self.assertIn(shooting.PG_LOG_FILE, seed)


class PostgresClientTest(unittest.TestCase):
    """플레이어가 `c` 로 여는 클라이언트."""

    def _pg_stage(self):
        return _minimal_stage(
            dbms="postgresql",
            objectives=[{"id": "o1", "type": "state", "on": "postgres",
                         "query": "SELECT 1",
                         "expect": {"op": "eq", "value": 1}}])

    def test_opens_psql_on_the_published_port(self):
        cmd = shooting.client_command(self._pg_stage(), target="postgres")
        self.assertEqual(cmd[0], "psql")
        self.assertEqual(cmd[cmd.index("-p") + 1],
                         shooting.PLAYER_PORTS["postgres"])

    def test_mysql_client_unchanged(self):
        cmd = shooting.client_command(_minimal_stage(), target="primary")
        self.assertEqual(cmd[0], "mysql")
        self.assertIn("--auto-vertical-output", cmd)

    def test_password_never_reaches_the_command_line(self):
        for target in ("primary", "postgres"):
            cmd = shooting.client_command(_minimal_stage(), target=target)
            self.assertNotIn(shooting.PLAYER_PASSWORD, " ".join(cmd), target)

    def test_env_carries_the_right_password_variable(self):
        self.assertIn("PGPASSWORD", shooting.client_env("postgres"))
        self.assertIn("MYSQL_PWD", shooting.client_env("primary"))
        self.assertNotIn("PGPASSWORD", shooting.client_env("primary"))

    def test_postgres_stage_never_offers_mysql_primary(self):
        stage = self._pg_stage()
        self.assertNotIn("primary", shooting.watch_targets(stage))
        self.assertEqual(shooting.client_targets(stage), ["postgres"])

    def test_connect_hint_uses_psql(self):
        hint = shooting._connect_hint(self._pg_stage())
        self.assertIn("psql", hint)
        self.assertIn("PGPASSWORD", hint)


class PostgresKillParsingTest(unittest.TestCase):
    """PostgreSQL은 세션 종료가 문장이 아니라 함수다."""

    def test_reads_terminate_and_cancel(self):
        self.assertEqual(
            shooting.parse_kill_targets(["SELECT pg_terminate_backend(12);",
                                         "select pg_cancel_backend( 34 )"]),
            [12, 34])

    def test_one_statement_can_kill_several(self):
        self.assertEqual(
            shooting.parse_kill_targets(
                ["SELECT pg_terminate_backend(1), pg_terminate_backend(2)"]),
            [1, 2])

    def test_mysql_kill_still_read(self):
        self.assertEqual(shooting.parse_kill_targets(["KILL 5"]), [5])

    def test_blanket_sweep_is_invisible(self):
        """pid를 적지 않는 쓸기는 여기서 보이지 않는다 — 문서화된 한계다.

        MySQL에는 없던 구멍이라, 감점 대상으로 삼으려면 스테이지가
        forbidden_command 로 직접 막아야 한다.
        """
        self.assertEqual(shooting.parse_kill_targets([
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"]), [])


class VendorValidationTest(unittest.TestCase):
    """dbms 와 대상 서버가 어긋나면 조용히 엉뚱한 서버에 붙는다."""

    def test_rejects_unknown_dbms(self):
        errs = shooting.validate_stage(_minimal_stage(dbms="cassandra"))
        self.assertTrue(any("cassandra" in e for e in errs), errs)

    def test_rejects_postgres_stage_pointing_at_mysql(self):
        errs = shooting.validate_stage(_minimal_stage(dbms="postgresql"))
        self.assertTrue(any("primary" in e for e in errs), errs)

    def test_rejects_mysql_stage_pointing_at_postgres(self):
        errs = shooting.validate_stage(_minimal_stage(
            setup=[{"type": "sql", "on": "postgres", "sql": "SELECT 1"}]))
        self.assertTrue(any("postgres" in e for e in errs), errs)

    def test_consistent_postgres_stage_passes(self):
        self.assertEqual(shooting.validate_stage(_minimal_stage(
            dbms="postgresql",
            objectives=[{"id": "o1", "type": "state", "on": "postgres",
                         "query": "SELECT 1",
                         "expect": {"op": "eq", "value": 1}}])), [])

    def test_every_shipped_stage_still_validates(self):
        for path in shooting.discover_stages():
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            self.assertEqual(shooting.validate_stage(raw), [], path.name)


class DbmsMenuTest(unittest.TestCase):
    """선택 화면의 DBMS 단계."""

    def _e(self, *dbms):
        return [(Path(f"{i}.json"),
                 {"id": f"s{i}", "world": 1, "stage": i, "dbms": d})
                for i, d in enumerate(dbms, 1)]

    def test_missing_dbms_means_mysql(self):
        """기존 스테이지는 dbms를 안 적었을 수 있다 — 그게 PostgreSQL로 새면 안 된다."""
        self.assertEqual(shooting.stage_dbms({}), "mysql")
        self.assertEqual(shooting.stage_dbms(None), "mysql")

    def test_groups_follow_the_guide_order(self):
        groups = shooting.group_by_dbms(self._e("mysql", "postgresql"))
        self.assertEqual([d for d, _ in groups], ["postgresql", "mysql"])

    def test_order_agrees_with_exam(self):
        """두 도구가 서로 다른 순서를 보이면 같은 저장소처럼 느껴지지 않는다."""
        import exam
        rank = {d: i for i, d in enumerate(exam.VALID_DBMS)}
        got = [rank[d] for d in shooting.VENDORS]
        self.assertEqual(got, sorted(got))

    def test_unreadable_stage_still_listed(self):
        """정의를 못 읽은 파일을 빼면 '파일은 있는데 안 보인다'가 된다."""
        groups = shooting.group_by_dbms([(Path("x.json"), None)])
        self.assertEqual(groups, [("mysql", [(Path("x.json"), None)])])

    def test_unknown_vendor_goes_last(self):
        groups = shooting.group_by_dbms(self._e("zzz", "mysql"))
        self.assertEqual([d for d, _ in groups], ["mysql", "zzz"])

    def test_label_shows_count_and_best_rank(self):
        label = shooting.dbms_menu_label(
            "mysql", self._e("mysql", "mysql"), {"s1": "A", "s2": "S"})
        self.assertIn("MySQL", label)
        self.assertIn("2개", label)
        self.assertIn("[S]", label)          # 최고 등급

    def test_filter_passes_everything_when_unset(self):
        entries = self._e("mysql", "postgresql")
        self.assertIs(shooting.filter_stages_by_dbms(entries, None), entries)

    def test_filter_narrows_to_one_vendor(self):
        got = shooting.filter_stages_by_dbms(
            self._e("mysql", "postgresql"), "postgresql")
        self.assertEqual([s["id"] for _, s in got], ["s2"])

    def test_cli_rejects_a_vendor_the_engine_cannot_run(self):
        """--dbms 오타를 argparse가 막아야 한다 — 조용히 빈 목록이 되면 안 된다."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            shooting.main(["--dbms", "oracle"])
        self.assertIn("--dbms", err.getvalue())

    def test_every_vendor_has_a_display_name(self):
        for vendor in shooting.VENDORS:
            self.assertIn(vendor, shooting.DBMS_TITLES, vendor)

    def test_both_vendors_ship_so_the_step_appears(self):
        """두 벤더가 모두 있으니 DBMS 단계가 나와야 한다.

        이 단언은 원래 "한 벤더뿐이니 단계가 나오면 안 된다"였고, 첫 PostgreSQL
        스테이지가 들어오는 날 깨지도록 두었다. 그날이 와서 뒤집었다.
        """
        entries = [(p, shooting._safe_load(p))
                   for p in shooting.discover_stages()]
        self.assertEqual([d for d, _ in shooting.group_by_dbms(entries)],
                         ["postgresql", "mysql"])


class PostgresStageTest(unittest.TestCase):
    """첫 PostgreSQL 스테이지가 엔진의 PostgreSQL 경로와 실제로 맞물리는가."""

    def setUp(self):
        paths = [p for p in shooting.discover_stages()
                 if shooting.stage_dbms(shooting._safe_load(p)) == "postgresql"]
        self.assertTrue(paths, "PostgreSQL 스테이지가 없습니다")
        self.stages = [shooting.load_stage(p) for p in paths]

    def test_targets_only_the_postgres_container(self):
        for st in self.stages:
            self.assertEqual(shooting.watch_targets(st), {"postgres"},
                             st["id"])

    def test_omitted_on_is_filled_at_load(self):
        """load_stage 가 채워주지 않으면 setup이 MySQL primary 로 간다."""
        for st in self.stages:
            for step in st.get("setup") or []:
                self.assertEqual(step.get("on"), "postgres", st["id"])

    # 감시가 돌려주는 것은 플레이어가 **친 그대로**의 문장이다(개행·탭만 공백으로
    # 눌린다). 그러니 패턴은 한 가지 표기가 아니라 사람이 실제로 쓰는 폭을 감당해야
    # 한다 — 특히 괄호 안팎의 공백은 붙여넣기에서 흔하다.
    SWEEPS = (
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE state like 'idle%'",
        "SELECT pg_terminate_backend( pid ) FROM pg_stat_activity",
        "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a",
        "select pg_terminate_backend (pid) from pg_stat_activity",
    )
    PRECISE_KILLS = (
        "SELECT pg_terminate_backend(2108)",
        "SELECT pg_terminate_backend( 2108 )",
        "SELECT pg_terminate_backend(2108);",
        "select pg_terminate_backend (2108);",
        "SELECT pg_terminate_backend(  2108  );",
    )

    def test_sweep_pattern_catches_the_blanket_kill(self):
        """2단계에서 확인한 구멍(pid 없는 쓸기)을 스테이지가 직접 막아야 한다."""
        for st in self.stages:
            pats = [c["pattern"] for c in st.get("constraints") or []
                    if c.get("detect") == "forbidden_command"]
            self.assertTrue(pats, st["id"])
            for sweep in self.SWEEPS:
                self.assertTrue(any(re.search(p, sweep) for p in pats),
                                (st["id"], sweep))

    def test_sweep_pattern_lets_a_precise_kill_through(self):
        """정확한 복구가 감점되면 스테이지가 거짓말을 하는 것이다.

        pid를 짚어 끊는 것은 이 스테이지가 가르치려는 **정답**이다. 괄호 뒤에
        공백을 하나 넣었다고 감점되면, 플레이어는 자기가 뭘 잘못했는지 알 길이
        없는 채로 등급을 잃는다.
        """
        for st in self.stages:
            for c in st.get("constraints") or []:
                if c.get("detect") != "forbidden_command":
                    continue
                for kill in self.PRECISE_KILLS:
                    self.assertIsNone(re.search(c["pattern"], kill),
                                      (st["id"], kill))

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
            m = re.search(r"id <= (\d+)", culprit["sql"])
            self.assertIsNotNone(m, culprit["sql"])
            locked_to = int(m.group(1))
            ids = []
            for i in range(int(victims["count"])):
                sql = shooting.render_session_sql(victims["sql"], i)
                # 렌더링은 문자열 치환일 뿐이라 "id = 1 + {{session_index}}"는
                # "id = 1 + N"으로 남는다(실제 값은 실행 시점에 PostgreSQL이
                # 계산한다) — 그래서 산술식을 그대로 읽어 최종 id를 구한다.
                m = re.search(r"id = 1 \+ (\d+)", sql)
                self.assertIsNotNone(m, sql)
                ids.append(1 + int(m.group(1)))
            self.assertEqual(len(set(ids)), len(ids), (seed, ids))
            self.assertTrue(all(1 <= v <= locked_to for v in ids),
                            (seed, ids, locked_to))

    def test_the_two_var_ranges_cannot_drift_apart(self):
        """피해자가 잠긴 구간 안에 있는 것은 **두 선언 사이의 관계**다.

        위 테스트의 `1 <= v` 는 정규식이 `1 + ` 를 읽어 값을 복원하므로 항상 참이고,
        상한도 시드 100개 표본에 기대고 있다. 지키려는 것은 그 표본이 아니라
        `payments` 를 넓히거나 `rows` 를 좁히면 피해자가 구간 밖으로 나간다는
        사실이므로, 선언값에서 직접 본다 — 구간 밖 피해자는 아무에게도 막히지 않아
        '결제가 멈췄다'는 장애가 애초에 성립하지 않는다.
        """
        base = shooting.load_stage(
            REPO_ROOT / "shooting" / "stages"
            / "pg-1-1-idle-in-transaction.json")
        payments, rows = base["vars"]["payments"], base["vars"]["rows"]
        # 피해자 id는 1 .. payments.max, 범인은 1 .. rows.min 이상을 잠근다.
        self.assertLessEqual(payments["max"], rows["min"],
                             f"payments {payments} 가 rows {rows} 를 넘는다")

    def test_variables_are_all_declared(self):
        """{{이름}}이 vars에 없으면 그대로 SQL에 실려 나간다.

        session_index는 예외다 — vars에 선언하면 검증이 거부하는 예약 이름이라
        (validate_stage), 여기서도 이미 알려진 이름으로 쳐준다.
        """
        for st in self.stages:
            declared = set((st.get("vars") or {})) | {shooting.SESSION_INDEX_VAR}
            used = set(re.findall(r"\{\{(\w+)\}\}", json.dumps(st)))
            self.assertEqual(used - declared, set(), st["id"])

    def test_rendering_leaves_no_placeholder(self):
        """render_stage는 vars만 채운다 — session_index는 setup_stage가 세션을
        띄우는 시점에야 채워지므로, 렌더링 직후에는 그 자리만 예외적으로 남는다.

        예약어 하나만 걷어내고 나머지는 원래대로 전수 검사한다 — `{{이름}}`
        모양(`\\w+`)으로 좁히면 `{{pool.from}}`(점 표기 span) 같은 실제로 쓰이는
        형태나 `{{ rows }}`(공백 포함) 가 남아도 통과해버려, 검사가 실제
        `_PLACEHOLDER_RE`(`\\{\\{\\s*([^{}]+?)\\s*\\}\\}`)보다 좁아진다. 예약어를
        걷어낼 때도 같은 정규식을 써야 한다 — 리터럴 `"{{session_index}}"`만
        지우면 `{{ session_index }}`(공백 포함)처럼 엔진이 인정하는 다른
        표기를 쓴 정상 스테이지가 오경보를 낸다.
        """
        needle_re = re.compile(
            r"\{\{\s*" + re.escape(shooting.SESSION_INDEX_VAR) + r"\s*\}\}")
        for st in self.stages:
            out = json.dumps(shooting.render_stage(st, random.Random(5)))
            out = needle_re.sub("", out)
            self.assertNotIn("{{", out, st["id"])

    def test_state_objectives_hold_before_clearing(self):
        """hold_seconds 가 없으면 순간적인 빈틈에 클리어된다."""
        for st in self.stages:
            for o in st["objectives"]:
                if o["type"] == "state":
                    self.assertGreater(o.get("hold_seconds", 0), 0,
                                       f"{st['id']}/{o['id']}")

    def test_culprit_is_marked_for_kill_precision(self):
        """kill_precision 을 걸어두고 범인을 표시하지 않으면 정확한 KILL도 위반이 된다."""
        for st in self.stages:
            detects = {c.get("detect") for c in st.get("constraints") or []}
            if "kill_precision" not in detects:
                continue
            self.assertTrue(
                any(step.get("culprit") for step in st.get("setup") or []),
                st["id"])


class StateObjectiveHoldTest(unittest.TestCase):
    """state 목표는 전부 hold_seconds 를 가져야 한다(저장소 전체 불변식)."""

    def test_every_state_objective_holds(self):
        for path in shooting.discover_stages():
            stage = shooting.load_stage(path)
            for o in stage["objectives"]:
                if o["type"] == "state":
                    self.assertGreater(
                        o.get("hold_seconds", 0), 0,
                        f"{stage['id']}/{o['id']}: 순간적인 빈틈에 클리어된다")


class ImplicitConversionStageTest(unittest.TestCase):
    """4-3. 실측이 두 번 설계를 고친 스테이지라 그 결론을 고정한다."""

    def setUp(self):
        paths = [p for p in shooting.discover_stages()
                 if p.stem == "4-3-implicit-conversion"]
        self.assertTrue(paths, "4-3 스테이지가 없습니다")
        self.stage = shooting.load_stage(paths[0])
        self.setup_sql = "\n".join(st.get("sql") or ""
                                   for st in self.stage["setup"])

    def _objective(self, oid):
        return next(o for o in self.stage["objectives"] if o["id"] == oid)

    def test_load_reads_a_column_outside_the_index(self):
        """COUNT(*)로 두면 커버링 인덱스 스캔이 되어 key=NULL 지문이 안 나온다."""
        self.assertIn("MAX(name)", self.setup_sql)
        self.assertNotIn("COUNT(*) INTO", self.setup_sql)

    def test_judged_query_matches_what_the_player_will_explain(self):
        """판정이 보는 질의와 플레이어가 EXPLAIN 할 질의가 다르면 스테이지가 거짓말을 한다."""
        judged = self._objective("plan-uses-index")["query"]
        self.assertIn("MAX(name) FROM shop.member WHERE code =", judged)
        self.assertIn("MAX(name) INTO @hit FROM shop.member WHERE code =",
                      self.setup_sql)

    def test_plan_is_judged_by_outcome_not_by_a_specific_fix(self):
        """컬럼 타입을 확인하면 한 가지 복구 경로만 인정하게 된다."""
        obj = self._objective("plan-uses-index")
        self.assertIn("EXPLAIN FORMAT=JSON", obj["query"])
        self.assertEqual(obj["expect"]["op"], "contains")
        self.assertIn("access_type", obj["expect"]["value"])

    def test_recovery_is_measured_on_a_recent_window(self):
        """누적 평균으로 보면 장애 구간이 영원히 섞여 회복이 드러나지 않는다."""
        q = self._objective("lookups-fast-again")["query"]
        self.assertIn("INTERVAL", q)
        self.assertIn("COALESCE", q)   # 부하가 끝나면 저절로 충족되면 안 된다

    def test_setup_restores_the_broken_column_type(self):
        """지난 판에서 플레이어가 고쳐놨다면 그대로는 장애가 걸리지 않는다."""
        self.assertIn("MODIFY code VARCHAR(20)", self.setup_sql)

    def test_setup_only_fills_the_table_when_empty(self):
        """80만 행을 판마다 다시 적재하면 시작이 느려진다."""
        self.assertIn("CREATE TABLE IF NOT EXISTS shop.member", self.setup_sql)
        self.assertIn("IF (SELECT COUNT(*) FROM shop.member) = 0 THEN",
                      self.setup_sql)

    def test_load_procedure_runs_as_invoker(self):
        """DEFINER면 processlist.user 가 root 로 보여 세션 추적이 어긋난다."""
        self.assertIn("SQL SECURITY INVOKER", self.setup_sql)

    def test_slow_log_is_left_on_but_useless(self):
        """비어 있는 것을 보여주는 것이 이 판의 첫 함정이다."""
        self.assertIn("SET GLOBAL slow_query_log = ON", self.setup_sql)
        self.assertIn("SET GLOBAL long_query_time = 1", self.setup_sql)

    def test_any_kill_is_a_violation(self):
        """범인 세션이 없으므로 조회 세션을 끊는 것은 전부 위반이다."""
        detects = {c["detect"] for c in self.stage["constraints"]}
        self.assertIn("kill_precision", detects)
        self.assertFalse(any(st.get("culprit") for st in self.stage["setup"]))

    def test_lookup_code_always_exists_in_the_seeded_range(self):
        """존재하지 않는 코드를 고르면 플레이어가 보는 화면이 달라진다.

        시드가 만드는 코드는 100000 + (a*1000 + b), a<=800, b<=1000 이라
        101001..901000 이 빈틈없이 채워진다.
        """
        spec = self.stage["vars"]["code"]
        self.assertGreaterEqual(spec["min"], 101001)
        self.assertLessEqual(spec["max"], 901000)
