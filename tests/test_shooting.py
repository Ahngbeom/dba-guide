#!/usr/bin/env python3
"""shooting.py 순수 로직 단위 테스트 (표준 라이브러리 unittest).

Docker도 MySQL도 띄우지 않는다 — 가짜 로그 행과 가짜 쿼리 결과를 주입해
판정 로직만 검증한다.

실행:
    python3 -m unittest discover -s tests
"""
import json
import sys
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

    def test_missing_target_seconds_loses_time_bonus(self):
        _, score, _ = shooting.rank_breakdown(10, None, 0, 0, 0, 0)
        self.assertEqual(score, 3)


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


class ConsoleTest(unittest.TestCase):
    """TUI 내장 SQL 콘솔의 순수 로직."""

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

    def test_history_walks_backwards_then_returns_to_new_line(self):
        hist = ["SELECT 1", "SELECT 2", "SELECT 3"]
        idx, text = shooting.history_move(hist, 0, +1)
        self.assertEqual((idx, text), (1, "SELECT 3"))    # 가장 최근부터
        idx, text = shooting.history_move(hist, idx, +1)
        self.assertEqual((idx, text), (2, "SELECT 2"))
        idx, text = shooting.history_move(hist, idx, -1)
        self.assertEqual((idx, text), (1, "SELECT 3"))
        idx, text = shooting.history_move(hist, idx, -1)
        self.assertEqual((idx, text), (0, ""))            # 편집 중이던 새 줄

    def test_history_clamps_at_the_oldest_entry(self):
        hist = ["a", "b"]
        idx, text = shooting.history_move(hist, 2, +1)
        self.assertEqual((idx, text), (2, "a"))

    def test_history_empty_is_safe(self):
        self.assertEqual(shooting.history_move([], 0, +1), (0, None))

    def test_clip_output_passes_short_results(self):
        lines, clipped = shooting.clip_output("a\nb\nc")
        self.assertEqual(lines, ["a", "b", "c"])
        self.assertFalse(clipped)

    def test_clip_output_truncates_huge_results(self):
        # SELECT * FROM orders 는 20만 행이다. 암묵적 LIMIT을 붙여 의미를 바꾸는
        # 대신 표시만 자른다.
        lines, clipped = shooting.clip_output("\n".join(str(i)
                                                        for i in range(5000)),
                                              max_lines=100)
        self.assertEqual(len(lines), 100)
        self.assertTrue(clipped)

    def test_clip_output_empty(self):
        self.assertEqual(shooting.clip_output(""), ([], False))
        self.assertEqual(shooting.clip_output(None), ([], False))

    def test_console_credentials_match_the_seed_grants(self):
        # shooting/lab/seed/03-users.sql 과 어긋나면 콘솔이 조용히 인증 실패한다.
        seed = (REPO_ROOT / "shooting" / "lab" / "seed"
                / "03-users.sql").read_text(encoding="utf-8")
        self.assertIn(f"'{shooting.PLAYER_USER}'@'%'", seed)
        self.assertIn(f"IDENTIFIED BY '{shooting.PLAYER_PASSWORD}'", seed)
        self.assertIn(f"ON {shooting.PLAYER_DB}.*", seed)


class CompletionTest(unittest.TestCase):
    """콘솔 Tab 자동완성.

    MySQL은 존재하지 않는 객체도 권한 오류로 답하기 때문에
    (`performence_schema` 오타 → "SELECT command denied"), 사람이 철자가 아니라
    GRANT를 의심하게 된다. 오타가 애초에 나지 않게 하는 게 이 기능의 목적이다.
    """

    SCHEMAS = ["information_schema", "mysql", "performance_schema", "shop",
               "sys"]
    TABLES = {
        "performance_schema": ["data_lock_waits", "data_locks", "threads"],
        "shop": ["orders"],
    }

    def _complete(self, text):
        start, token = shooting.completion_prefix(text, len(text))
        cands = shooting.completion_candidates(token, self.SCHEMAS,
                                               self.TABLES, "shop")
        new, pos, show = shooting.apply_completion(text, len(text), start,
                                                   cands, self.SCHEMAS)
        return new, show

    def test_prefix_finds_identifier_before_cursor(self):
        start, token = shooting.completion_prefix("SELECT * FROM perf", 18)
        self.assertEqual(token, "perf")
        self.assertEqual(start, 14)

    def test_prefix_handles_qualified_names(self):
        text = "SELECT * FROM performance_schema.data_"
        _, token = shooting.completion_prefix(text, len(text))
        self.assertEqual(token, "performance_schema.data_")

    def test_prefix_empty_after_space(self):
        start, token = shooting.completion_prefix("SELECT * FROM ", 14)
        self.assertEqual((start, token), (14, ""))

    def test_single_schema_match_appends_dot(self):
        # 스키마를 완성하면 다음 Tab이 바로 테이블을 잇도록 '.'을 붙인다.
        new, show = self._complete("SELECT * FROM perf")
        self.assertEqual(new, "SELECT * FROM performance_schema.")
        self.assertEqual(show, [])

    def test_multiple_matches_extend_to_common_prefix_and_list(self):
        new, show = self._complete("SELECT * FROM performance_schema.data_")
        self.assertEqual(new, "SELECT * FROM performance_schema.data_lock")
        self.assertEqual(show, ["performance_schema.data_lock_waits",
                                "performance_schema.data_locks"])

    def test_misspelled_schema_yields_nothing(self):
        # 이번 사건의 재현 — 오타 스키마에는 후보가 없어야 한다.
        new, show = self._complete("SELECT * FROM performence_schema.data_")
        self.assertEqual(new, "SELECT * FROM performence_schema.data_")
        self.assertEqual(show, [])

    def test_default_schema_tables_complete_without_qualification(self):
        new, _ = self._complete("SELECT * FROM ord")
        self.assertEqual(new, "SELECT * FROM orders")

    def test_completion_inserts_before_trailing_text(self):
        text = "SELECT * FROM perf WHERE 1"
        start, token = shooting.completion_prefix(text, 18)
        cands = shooting.completion_candidates(token, self.SCHEMAS,
                                               self.TABLES, "shop")
        new, pos, _ = shooting.apply_completion(text, 18, start, cands,
                                                self.SCHEMAS)
        self.assertEqual(new, "SELECT * FROM performance_schema. WHERE 1")
        self.assertEqual(new[:pos], "SELECT * FROM performance_schema.")

    def test_common_prefix(self):
        self.assertEqual(shooting.common_prefix(["data_lock_waits",
                                                 "data_locks"]), "data_lock")
        self.assertEqual(shooting.common_prefix(["abc"]), "abc")
        self.assertEqual(shooting.common_prefix(["ab", "cd"]), "")
        self.assertEqual(shooting.common_prefix([]), "")

    def test_no_candidates_leaves_text_untouched(self):
        text, pos, show = shooting.apply_completion("SELECT x", 8, 7, [])
        self.assertEqual((text, pos, show), ("SELECT x", 8, []))


class WideResultTest(unittest.TestCase):
    def test_max_line_width_uses_display_width(self):
        self.assertEqual(shooting.max_line_width(["abc", "abcdef"]), 6)
        self.assertEqual(shooting.max_line_width(["복제"]), 4)
        self.assertEqual(shooting.max_line_width([]), 0)
        self.assertEqual(shooting.max_line_width(None), 0)


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


if __name__ == "__main__":
    unittest.main()
