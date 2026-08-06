#!/usr/bin/env python3
"""exam.py / seed_exam.py 핵심 로직 단위 테스트 (표준 라이브러리 unittest).

실행:
    python3 -m unittest discover -s tests
"""
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import exam  # noqa: E402
import seed_exam  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_case_and_whitespace(self):
        self.assertEqual(exam.normalize_answer("  GRANT   SELECT  "),
                         "grant select")

    def test_trailing_semicolon(self):
        self.assertEqual(exam.normalize_answer("SELECT 1;"),
                         exam.normalize_answer("select 1"))

    def test_none_and_empty(self):
        self.assertEqual(exam.normalize_answer(None), "")
        self.assertEqual(exam.normalize_answer("   "), "")


class GradeShortTest(unittest.TestCase):
    def test_matches_any_accept(self):
        self.assertTrue(exam.grade_short("1NF", ["1nf", "제1정규형"]))
        self.assertTrue(exam.grade_short("제1정규형", ["1nf", "제1정규형"]))

    def test_command_normalization(self):
        self.assertTrue(
            exam.grade_short("GRANT SELECT ON emp TO app;",
                             ["grant select on emp to app"]))

    def test_wrong_answer(self):
        self.assertFalse(exam.grade_short("2NF", ["1nf"]))

    def test_empty_input_is_wrong(self):
        self.assertFalse(exam.grade_short("", ["1nf"]))


class GradeMcqTest(unittest.TestCase):
    def test_correct_and_wrong(self):
        self.assertTrue(exam.grade_mcq(0, 0))
        self.assertFalse(exam.grade_mcq(1, 0))


class FilterByDbmsTest(unittest.TestCase):
    def setUp(self):
        self.qs = [
            {"dbms": "neutral", "q": "n"},
            {"dbms": "postgresql", "q": "pg"},
            {"dbms": "mysql", "q": "my"},
            {"q": "no-field"},  # dbms 필드 없음 → neutral 취급
        ]

    def test_none_keeps_all(self):
        self.assertEqual(len(exam.filter_by_dbms(self.qs, None)), 4)

    def test_keeps_neutral_plus_target(self):
        got = exam.filter_by_dbms(self.qs, "postgresql")
        texts = {q["q"] for q in got}
        self.assertEqual(texts, {"n", "pg", "no-field"})

    def test_missing_field_is_neutral(self):
        got = exam.filter_by_dbms(self.qs, "oracle")
        self.assertIn("no-field", {q["q"] for q in got})


class SummarizeTest(unittest.TestCase):
    def test_score_and_pass(self):
        results = [
            {"type": "mcq", "correct": True},
            {"type": "short", "correct": True},
            {"type": "short", "correct": False},
            {"type": "essay", "correct": True},
            {"type": "essay", "correct": None},  # 건너뜀
        ]
        s = exam.summarize(results)
        self.assertEqual(s["auto_total"], 3)
        self.assertEqual(s["auto_correct"], 2)
        self.assertEqual(s["essay_total"], 2)
        self.assertEqual(s["essay_correct"], 1)
        self.assertAlmostEqual(s["score"], 2 / 3)
        self.assertFalse(s["passed"])  # 66% < 70%

    def test_all_essay_passes(self):
        s = exam.summarize([{"type": "essay", "correct": None}])
        self.assertEqual(s["score"], 1.0)
        self.assertTrue(s["passed"])


class ValidateBankTest(unittest.TestCase):
    def test_good_bank(self):
        bank = {"questions": [
            {"type": "mcq", "q": "?", "choices": ["a", "b"], "answer": 1},
            {"type": "short", "q": "?", "accept": ["x"]},
            {"type": "essay", "q": "?", "reference": "r"},
        ]}
        self.assertEqual(exam.validate_bank(bank), [])

    def test_bad_mcq_answer_index(self):
        bank = {"questions": [
            {"type": "mcq", "q": "?", "choices": ["a", "b"], "answer": 5}]}
        self.assertTrue(exam.validate_bank(bank))

    def test_unknown_type(self):
        bank = {"questions": [{"type": "wat", "q": "?"}]}
        self.assertTrue(exam.validate_bank(bank))

    def test_empty_questions(self):
        self.assertTrue(exam.validate_bank({"questions": []}))


class ResultRecordTest(unittest.TestCase):
    def _bank(self):
        return {"chapter": "01-beginner/01-rdbms-fundamentals.md",
                "title": "01. 관계형 데이터베이스 기초"}

    def _results(self):
        return [
            {"type": "mcq", "correct": True, "q": {"id": "a"}},
            {"type": "mcq", "correct": False, "q": {"id": "b"}},
            {"type": "short", "correct": True, "q": {"id": "c"}},
            {"type": "essay", "correct": None, "q": {"id": "d"}},
        ]

    def test_build_record_fields(self):
        session = {"best": 4}
        rec = exam.build_result_record(self._bank(), self._results(), session,
                                       "postgresql", "2026-07-23T10:00:00")
        self.assertEqual(rec["chapter"], "01-beginner/01-rdbms-fundamentals.md")
        self.assertEqual(rec["dbms"], "postgresql")
        self.assertEqual(rec["auto_total"], 3)
        self.assertEqual(rec["auto_correct"], 2)
        self.assertEqual(rec["grade"], exam.grade_letter(2 / 3))
        self.assertEqual(rec["best_streak"], 4)
        self.assertEqual(rec["wrong_ids"], ["b"])  # 오답 mcq만, essay skip 제외
        self.assertEqual(rec["ts"], "2026-07-23T10:00:00")

    def test_dbms_none_becomes_all(self):
        rec = exam.build_result_record(self._bank(), self._results(), {},
                                       None, "t")
        self.assertEqual(rec["dbms"], "all")


class BestResultForTest(unittest.TestCase):
    def test_picks_highest_score(self):
        recs = [
            {"chapter": "c1", "auto_total": 5, "score": 0.6, "grade": "D"},
            {"chapter": "c1", "auto_total": 5, "score": 0.8, "grade": "B"},
            {"chapter": "c2", "auto_total": 5, "score": 1.0, "grade": "A"},
        ]
        best = exam.best_result_for("c1", recs)
        self.assertEqual(best["score"], 0.8)
        self.assertIsNone(exam.best_result_for("nope", recs))

    def test_ignores_zero_auto(self):
        recs = [{"chapter": "c1", "auto_total": 0, "score": 1.0, "grade": "A"}]
        self.assertIsNone(exam.best_result_for("c1", recs))


class ResultsPathTest(unittest.TestCase):
    def test_results_dir_gitignored_and_in_repo(self):
        # 결과 경로가 저장소 내 .exam-results/ 이고 .gitignore에 등록돼 있어야 함
        self.assertEqual(exam.RESULTS_DIR.name, ".exam-results")
        self.assertEqual(exam.RESULTS_DIR.parent, exam.REPO_ROOT)
        gi = (exam.REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".exam-results/", gi)


class DiscoveryTest(unittest.TestCase):
    """티어/문제은행 탐색 헬퍼(실제 exams/가 있을 때만)."""

    def test_tiers_include_beginner(self):
        tiers = exam.discover_tiers()
        if not tiers:
            self.skipTest("아직 문제은행 없음")
        self.assertIn("01-beginner", tiers)
        # 정렬 보장
        self.assertEqual(tiers, sorted(tiers))

    def test_banks_in_tier(self):
        if "01-beginner" not in exam.discover_tiers():
            self.skipTest("입문 문제은행 없음")
        banks = exam.discover_banks_in("01-beginner")
        self.assertTrue(banks)
        self.assertTrue(all(b.suffix == ".json" for b in banks))

    def test_tier_label(self):
        self.assertEqual(exam.tier_label("01-beginner"), "01-beginner (초급)")
        self.assertEqual(exam.tier_label("99-unknown"), "99-unknown")


class DbmsChoicesTest(unittest.TestCase):
    def test_first_choice_is_all(self):
        # '전체'는 필터값 None → filter_by_dbms가 모든 문항 통과
        label, value = exam.DBMS_CHOICES[0]
        self.assertIsNone(value)

    def test_vendor_choices_map_to_filter(self):
        values = [v for _, v in exam.DBMS_CHOICES]
        self.assertEqual(set(v for v in values if v), set(exam.VALID_DBMS))
        qs = [{"dbms": "neutral"}, {"dbms": "postgresql"}, {"dbms": "mysql"}]
        # 'PostgreSQL' 선택값으로 필터하면 neutral+postgresql만
        self.assertEqual(len(exam.filter_by_dbms(qs, "postgresql")), 2)


class GradeLetterTest(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(exam.grade_letter(1.0), "A")
        self.assertEqual(exam.grade_letter(0.9), "A")
        self.assertEqual(exam.grade_letter(0.89), "B")
        self.assertEqual(exam.grade_letter(0.8), "B")
        self.assertEqual(exam.grade_letter(0.7), "C")
        self.assertEqual(exam.grade_letter(0.6), "D")
        self.assertEqual(exam.grade_letter(0.59), "F")
        self.assertEqual(exam.grade_letter(0.0), "F")

    def test_summarize_includes_grade(self):
        s = exam.summarize([{"type": "mcq", "correct": True},
                            {"type": "mcq", "correct": True}])
        self.assertEqual(s["grade"], "A")
        self.assertEqual(s["auto_wrong"], 0)


class StreakTest(unittest.TestCase):
    def test_next_streak_accumulates_and_resets(self):
        s = 0
        s = exam.next_streak(s, True)   # 1
        s = exam.next_streak(s, True)   # 2
        self.assertEqual(s, 2)
        s = exam.next_streak(s, False)  # 리셋
        self.assertEqual(s, 0)
        s = exam.next_streak(s, True)   # 1
        self.assertEqual(s, 1)

    def test_is_milestone(self):
        self.assertTrue(exam.is_milestone(3))
        self.assertTrue(exam.is_milestone(5))
        self.assertTrue(exam.is_milestone(10))
        self.assertFalse(exam.is_milestone(2))
        self.assertFalse(exam.is_milestone(4))
        self.assertFalse(exam.is_milestone(0))

    def test_line_combo_updates_streak(self):
        # essay 건너뜀(None)은 스트릭 불변
        self.assertEqual(exam._line_combo(None, 3), 3)
        self.assertEqual(exam._line_combo(True, 2), 3)
        self.assertEqual(exam._line_combo(False, 5), 0)


class CursorMotionTest(unittest.TestCase):
    """입력 오버레이 커서 계산(Option+←/→, 줄 이동)."""

    def test_word_left(self):
        c = list("grant select on emp")
        self.assertEqual(exam.word_left(c, len(c)), len("grant select on "))
        self.assertEqual(exam.word_left(c, len("grant select ")), len("grant "))
        self.assertEqual(exam.word_left(c, 0), 0)          # 경계

    def test_word_right(self):
        c = list("grant select on emp")
        self.assertEqual(exam.word_right(c, 0), len("grant"))
        self.assertEqual(exam.word_right(c, len("grant")), len("grant select"))
        self.assertEqual(exam.word_right(c, len(c)), len(c))  # 경계

    def test_word_motion_skips_repeated_spaces(self):
        c = list("a    b")
        self.assertEqual(exam.word_right(c, 1), len(c))     # 공백 건너뛰고 b 끝
        self.assertEqual(exam.word_left(c, len(c)), len("a    "))

    def test_line_start_end(self):
        c = list("one\ntwo\nthree")
        p = c.index("w")                                    # 둘째 줄 중간
        self.assertEqual(exam.line_start(c, p), 4)
        self.assertEqual(exam.line_end(c, p), 7)
        self.assertEqual(exam.line_start(c, 0), 0)
        self.assertEqual(exam.line_end(c, len(c)), len(c))

    def test_move_line_keeps_column(self):
        c = list("one\ntwo\nthree")
        p = 5                                               # 둘째 줄 col 1
        up = exam.move_line(c, p, -1)
        self.assertEqual(up, 1)                             # 첫 줄 col 1
        down = exam.move_line(c, p, 1)
        self.assertEqual(down, 9)                           # 셋째 줄 col 1

    def test_move_line_clamps_to_shorter_line(self):
        c = list("a\nlonger")
        p = len("a\nlong")                                  # 둘째 줄 col 4
        self.assertEqual(exam.move_line(c, p, -1), 1)       # 첫 줄 끝으로 클램프

    def test_move_line_at_edges(self):
        c = list("one\ntwo")
        self.assertEqual(exam.move_line(c, 1, -1), 1)       # 첫 줄에서 위 → 그대로
        self.assertEqual(exam.move_line(c, 5, 1), 5)        # 마지막 줄에서 아래 → 그대로


class _FakeCurses:
    """_input_overlay/_read_key에 주입할 최소 curses 스텁."""
    KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN = 260, 261, 259, 258
    KEY_HOME, KEY_END, KEY_BACKSPACE, KEY_DC, KEY_ENTER = 262, 360, 263, 330, 343
    KEY_IC, KEY_PPAGE, KEY_NPAGE = 331, 339, 338
    A_REVERSE = A_BOLD = A_NORMAL = A_DIM = 0

    class error(Exception):
        pass

    @staticmethod
    def curs_set(_n):
        pass

    @staticmethod
    def color_pair(_n):
        return 0


class _FakeScr:
    """미리 정한 키 시퀀스를 돌려주는 가짜 화면."""

    def __init__(self, keys, h=24, w=80):
        self.keys = list(keys)
        self.h, self.w = h, w
        self._nodelay = False

    def getmaxyx(self):
        return self.h, self.w

    def clear(self):
        pass

    def refresh(self):
        pass

    def move(self, *_a):
        pass

    def addstr(self, *_a, **_k):
        pass

    def nodelay(self, flag):
        self._nodelay = flag

    def get_wch(self):
        if not self.keys:
            # 논블로킹 peek 중 입력 없음 → 단독 Esc로 판정되게 함
            raise _FakeCurses.error("no input")
        return self.keys.pop(0)


def _seq(s):
    """문자열을 개별 키(문자) 시퀀스로 펼친다."""
    return list(s)


class InputOverlayTest(unittest.TestCase):
    """입력 오버레이: 개행·초안 보존·단어 이동 (터미널 없이 직접 구동)."""

    def _run(self, qtype, keys, initial=""):
        q = {"type": qtype, "q": "질문?", "accept": ["x"], "reference": "r"}
        scr = _FakeScr(keys)
        return exam._input_overlay(scr, _FakeCurses, q, initial)

    def test_essay_shift_enter_inserts_newline(self):
        # AAA + Shift+Enter(ESC[13;2u) + BBB + Enter
        keys = _seq("AAA") + _seq("\x1b[13;2u") + _seq("BBB") + ["\n"]
        text, submitted = self._run("essay", keys)
        self.assertTrue(submitted)
        self.assertEqual(text, "AAA\nBBB")

    def test_essay_option_enter_inserts_newline(self):
        # Option+Enter는 ESC + CR
        keys = _seq("AAA") + ["\x1b", "\r"] + _seq("BBB") + ["\n"]
        text, submitted = self._run("essay", keys)
        self.assertTrue(submitted)
        self.assertEqual(text, "AAA\nBBB")

    def test_short_ignores_newline_keys(self):
        # 단답은 개행이 들어가지 않아야 한다
        keys = _seq("XX") + _seq("\x1b[13;2u") + ["\x1b", "\r"] + _seq("YY") + ["\n"]
        text, submitted = self._run("short", keys)
        self.assertTrue(submitted)
        self.assertEqual(text, "XXYY")

    def test_lone_esc_closes_but_keeps_draft(self):
        # Esc 뒤에 입력이 없으면 단독 Esc → 닫기(미제출), 내용은 보존
        text, submitted = self._run("short", _seq("draft") + ["\x1b"])
        self.assertFalse(submitted)
        self.assertEqual(text, "draft")

    def test_option_arrow_does_not_close_and_moves_by_word(self):
        # "grant select" 상태에서 Option+←(ESC ESC[D) 후 '!' 삽입 →
        # 커서가 단어 앞으로 이동했으므로 'select' 앞에 삽입된다.
        keys = _seq("grant select") + ["\x1b", "\x1b", "["] + ["D"] \
            + _seq("!") + ["\n"]
        text, submitted = self._run("short", keys)
        self.assertTrue(submitted)
        self.assertEqual(text, "grant !select")

    def test_essay_up_down_moves_between_lines(self):
        # 두 줄 작성 후 ↑ 로 첫 줄 끝으로 이동해 '!' 삽입
        keys = _seq("AB") + _seq("\x1b[13;2u") + _seq("CD") \
            + [_FakeCurses.KEY_UP] + _seq("!") + ["\n"]
        text, submitted = self._run("essay", keys)
        self.assertTrue(submitted)
        self.assertEqual(text, "AB!\nCD")


class SpecialKeySequenceTest(unittest.TestCase):
    """ESC 시퀀스 분류: 특수키(Home/End)와 Alt·Ctrl 수식을 구분해야 한다."""

    def _run(self, qtype, keys, initial=""):
        q = {"type": qtype, "q": "질문?", "accept": ["x"], "reference": "r"}
        return exam._input_overlay(_FakeScr(keys), _FakeCurses, q, initial)

    def _home_moves_to_start(self, seq):
        # "abc" 입력 후 Home → 맨 앞에 '!' 삽입되면 Home이 살아있는 것
        keys = _seq("abc") + _seq(seq) + _seq("!") + ["\n"]
        text, submitted = self._run("short", keys)
        self.assertTrue(submitted)
        self.assertEqual(text, "!abc", f"{seq!r} 가 Home으로 동작하지 않음")

    def test_home_csi_variants(self):
        self._home_moves_to_start("\x1b[H")     # CSI H
        self._home_moves_to_start("\x1bOH")     # SS3 H (application mode)
        self._home_moves_to_start("\x1b[1~")    # vt220 스타일
        self._home_moves_to_start("\x1b[7~")    # rxvt 스타일

    def _end_moves_to_end(self, seq):
        # 커서를 Home으로 보낸 뒤 End → 맨 뒤에 '!' 삽입
        keys = _seq("abc") + _seq("\x1b[H") + _seq(seq) + _seq("!") + ["\n"]
        text, _ = self._run("short", keys)
        self.assertEqual(text, "abc!", f"{seq!r} 가 End로 동작하지 않음")

    def test_end_csi_variants(self):
        self._end_moves_to_end("\x1b[F")
        self._end_moves_to_end("\x1bOF")
        self._end_moves_to_end("\x1b[4~")
        self._end_moves_to_end("\x1b[8~")

    def test_plain_arrow_is_char_move_not_alt(self):
        # 수식자 없는 ESC[D는 '한 글자 왼쪽'이어야 한다(alt로 오분류 금지)
        keys = _seq("ab") + _seq("\x1b[D") + _seq("!") + ["\n"]
        text, _ = self._run("short", keys)
        self.assertEqual(text, "a!b")

    def test_alt_arrow_csi_modifier_is_word_move(self):
        # ESC[1;3D → Alt+← → 단어 단위
        keys = _seq("grant select") + _seq("\x1b[1;3D") + _seq("!") + ["\n"]
        text, _ = self._run("short", keys)
        self.assertEqual(text, "grant !select")

    def test_ctrl_arrow_is_word_move(self):
        # ESC[1;5C → Ctrl+→ → 단어 단위(항상 통하는 대체 경로)
        keys = _seq("\x1b[H") + _seq("\x1b[1;5C") + _seq("!") + ["\n"]
        text, _ = self._run("short", keys, initial="grant select")
        self.assertEqual(text, "grant! select")

    def test_meta_b_f_word_move(self):
        # macOS Option-as-Meta: ESC b / ESC f
        keys = _seq("grant select") + ["\x1b", "b"] + _seq("!") + ["\n"]
        text, _ = self._run("short", keys)
        self.assertEqual(text, "grant !select")
        keys = _seq("\x1b[H") + ["\x1b", "f"] + _seq("!") + ["\n"]
        text, _ = self._run("short", keys, initial="grant select")
        self.assertEqual(text, "grant! select")

    def test_esc_esc_arrow_word_move(self):
        # ESC ESC[D 형태(Alt 접두 + 화살표 시퀀스)
        keys = _seq("grant select") + ["\x1b"] + _seq("\x1b[D") + _seq("!") + ["\n"]
        text, _ = self._run("short", keys)
        self.assertEqual(text, "grant !select")

    def test_ctrl_a_e_always_work(self):
        # 터미널과 무관하게 통하는 대체키: Ctrl-A(1) / Ctrl-E(5)
        keys = _seq("abc") + [1] + _seq("!") + [5] + _seq("?") + ["\n"]
        text, _ = self._run("short", keys)
        self.assertEqual(text, "!abc?")

    def test_delete_key_sequence(self):
        # ESC[3~ → Delete(커서 위 문자 삭제)
        keys = _seq("abc") + _seq("\x1b[H") + _seq("\x1b[3~") + ["\n"]
        text, _ = self._run("short", keys)
        self.assertEqual(text, "bc")


class ExtendedKeyTest(unittest.TestCase):
    """ncurses 확장 키(kLFT3=Alt+←, kRIT5=Ctrl+→)를 수식키로 해석해야 한다.

    실제 터미널에서 Option+←는 ESC 시퀀스가 아니라 단일 정수 키코드로
    도착한다(--keydebug로 확인). 이 경로가 빠지면 단어 이동이 먹통이 된다.
    """

    class _CursesWithNames(_FakeCurses):
        _NAMES = {545: b"kLFT3", 546: b"kRIT3", 560: b"kLFT5", 562: b"kRIT5",
                  536: b"kHOM3", 999: b"KEY_WEIRD"}

        @classmethod
        def keyname(cls, code):
            return cls._NAMES.get(code, b"UNKNOWN")

    def test_decode_alt_and_ctrl_arrows(self):
        c = self._CursesWithNames
        self.assertEqual(exam._decode_named_key(c, 545), ("alt", c.KEY_LEFT))
        self.assertEqual(exam._decode_named_key(c, 546), ("alt", c.KEY_RIGHT))
        self.assertEqual(exam._decode_named_key(c, 560), ("ctrl", c.KEY_LEFT))
        self.assertEqual(exam._decode_named_key(c, 562), ("ctrl", c.KEY_RIGHT))
        self.assertEqual(exam._decode_named_key(c, 536), ("alt", c.KEY_HOME))

    def test_unknown_names_pass_through(self):
        c = self._CursesWithNames
        self.assertIsNone(exam._decode_named_key(c, 999))     # 패턴 불일치
        self.assertIsNone(exam._decode_named_key(_FakeCurses, 545))  # keyname 없음

    def test_extended_alt_left_moves_by_word_in_overlay(self):
        c = self._CursesWithNames
        q = {"type": "short", "q": "질문?", "accept": ["x"], "reference": "r"}
        keys = _seq("grant select") + [545] + _seq("!") + ["\n"]
        text, submitted = exam._input_overlay(_FakeScr(keys), c, q, "")
        self.assertTrue(submitted)
        self.assertEqual(text, "grant !select")

    def test_extended_ctrl_right_moves_by_word_in_overlay(self):
        c = self._CursesWithNames
        q = {"type": "short", "q": "질문?", "accept": ["x"], "reference": "r"}
        keys = [c.KEY_HOME, 562] + _seq("!") + ["\n"]
        text, _ = exam._input_overlay(_FakeScr(keys), c, q, "grant select")
        self.assertEqual(text, "grant! select")


class LockAndIdempotencyTest(unittest.TestCase):
    """제출 확정(잠금)과 스트릭 집계 멱등성 — 콤보 어뷰징 회귀 방지."""

    def test_is_locked_states(self):
        self.assertFalse(exam.is_locked({"answered": False, "correct": None}))
        self.assertTrue(exam.is_locked({"answered": True, "correct": True}))
        self.assertTrue(exam.is_locked({"answered": True, "correct": False}))
        # 서술형 건너뜀은 채점된 답이 아니므로 잠기지 않는다(다시 풀 수 있음)
        self.assertFalse(exam.is_locked({"answered": True, "correct": None}))

    def test_record_streak_is_idempotent(self):
        session = {"streak": 0, "best": 0}
        self.assertTrue(exam.record_streak(session, True, False))
        self.assertEqual(session["streak"], 1)
        # 같은 제출을 다시 집계하려 해도 스트릭 불변(Enter 연타 어뷰징 차단)
        for _ in range(5):
            self.assertFalse(exam.record_streak(session, True, True))
        self.assertEqual(session["streak"], 1)
        self.assertEqual(session["best"], 1)

    def test_record_streak_accumulates_and_resets(self):
        session = {"streak": 0, "best": 0}
        exam.record_streak(session, True, False)
        exam.record_streak(session, True, False)
        self.assertEqual(session["streak"], 2)
        self.assertEqual(session["best"], 2)
        exam.record_streak(session, False, False)   # 오답 → 리셋
        self.assertEqual(session["streak"], 0)
        self.assertEqual(session["best"], 2)        # 최고 기록은 유지

    def test_record_streak_skips_none(self):
        session = {"streak": 3, "best": 3}
        self.assertFalse(exam.record_streak(session, None, False))
        self.assertEqual(session["streak"], 3)      # 건너뜀은 불변

    def test_init_states_has_counted_flag(self):
        qs = [{"type": "short", "q": "?", "accept": ["x"]}]
        st = exam._init_states(qs, __import__("random").Random(0))[0]
        self.assertFalse(st["counted"])


class ShuffleChoicesTest(unittest.TestCase):
    def test_answer_mapping_preserved(self):
        import random
        choices = ["a", "b", "c", "d"]
        for seed in range(50):
            rng = random.Random(seed)
            new_choices, new_answer, order = exam.shuffle_choices(choices, 2, rng)
            # 정답 텍스트가 새 인덱스에서 그대로 유지
            self.assertEqual(new_choices[new_answer], choices[2])
            # 순열은 원본 집합을 보존
            self.assertEqual(sorted(order), [0, 1, 2, 3])
            self.assertEqual([choices[i] for i in order], new_choices)

    def test_grading_after_shuffle(self):
        import random
        rng = random.Random(1)
        choices = ["격리성", "무결성", "독립성", "색인"]
        new_choices, new_answer, _ = exam.shuffle_choices(choices, 0, rng)
        # 셔플 후에도 정답 인덱스 채점이 정확
        self.assertTrue(exam.grade_mcq(new_answer, new_answer))
        self.assertEqual(new_choices[new_answer], "격리성")


class AnswerLeakLintTest(unittest.TestCase):
    """실제 문제은행에서 질문이 정답을 노출하지 않는지 검사(회귀 방지)."""

    def _banks(self):
        return list((REPO_ROOT / "exams").glob("**/*.json"))

    def test_short_question_does_not_contain_answer(self):
        banks = self._banks()
        if not banks:
            self.skipTest("아직 문제은행 없음")
        for path in banks:
            data = json.loads(path.read_text(encoding="utf-8"))
            for q in data["questions"]:
                if q["type"] != "short":
                    continue
                qn = exam.normalize_answer(q.get("q", ""))
                for a in q.get("accept", []):
                    an = exam.normalize_answer(a)
                    with self.subTest(bank=path.name, qid=q.get("id")):
                        self.assertFalse(
                            an and an in qn,
                            f"{path.name}:{q.get('id')} 질문이 정답 '{a}'을 노출")

    def test_all_questions_have_hint(self):
        banks = self._banks()
        if not banks:
            self.skipTest("아직 문제은행 없음")
        for path in banks:
            data = json.loads(path.read_text(encoding="utf-8"))
            for q in data["questions"]:
                with self.subTest(bank=path.name, qid=q.get("id")):
                    self.assertTrue(q.get("hint"),
                                    f"{path.name}:{q.get('id')} hint 없음")

    # ------------------------------------------------------------------ #
    # 보기 길이 단서 (래칫)
    #
    # 보기 순서는 출제할 때 섞으므로 위치는 정답을 알려주지 않는다. 그런데 **길이**
    # 는 알려준다 — 처음 측정했을 때 85문항 중 64개(75%)에서 정답이 다른 어떤 보기
    # 보다 길었고, 정답 평균 37자 대 오답 21자였다. "항상 가장 긴 것을 찍는다"만으로
    # 통과 기준(70%)을 넘겨, 한 문제도 읽지 않고 객관식을 통과할 수 있었다.
    #
    # **동점은 세지 않는다.** 정답과 같은 길이의 오답이 있으면 "가장 긴 것"을 고를
    # 수 없으므로 단서가 성립하지 않는다. 오히려 "A는 X, B는 Y"의 정답에 "A는 Y,
    # B는 X"를 짝지으면 길이가 저절로 같아지는데, 그건 가장 좋은 오답 작성법이라
    # 벌하면 안 된다(그래서 `>` 로 센다. 동점까지 세면 처음 수치는 67/85였고,
    # 어느 기준으로 보든 우연 기대치 25%를 크게 넘었다).
    #
    # 원인은 출제 습관이다 — 정답은 정확하게 쓰려다 길어지고 오답은 짧게 던진다.
    # 216문항을 한 번에 손볼 수는 없으므로 남은 빚을 아래에 고정한다.
    # **줄어드는 방향으로만** 바꾼다 — 은행을 고쳤으면 그 줄의 숫자도 함께 낮춰라.
    # ------------------------------------------------------------------ #
    LONGEST_ANSWER_BASELINE = {
        "01-rdbms-fundamentals.json": 1,
        "02-sql-basics.json": 1,
        "03-installation-and-access.json": 0,
        "04-user-and-privilege-management.json": 2,
        "05-backup-basics.json": 3,
        "06-basic-monitoring.json": 1,
        "01-transaction-and-locking.json": 3,
        "02-indexing-and-query-tuning.json": 3,
        "03-performance-monitoring.json": 4,
        "04-backup-recovery-strategies.json": 3,
        "05-replication-basics.json": 0,
        "06-schema-change-management.json": 3,
        "07-cloud-db-infra-and-connection.json": 0,
        "08-cloud-managed-db-basics.json": 0,
        "01-advanced-performance-tuning.json": 0,
        "02-high-availability-and-failover.json": 0,
        "03-disaster-recovery.json": 0,
        "04-scaling-and-sharding.json": 0,
        "05-security-and-compliance.json": 0,
        "06-automation-and-iac.json": 2,
        "07-cloud-managed-db-advanced.json": 0,
        "08-kubernetes-db-operators.json": 0,
        "09-incident-response-and-postmortem.json": 0,
    }
    # 정답 평균 길이 ÷ 오답 평균 길이. 처음 1.7571 → 03-advanced 1.3929 → 02-intermediate 1.2357.
    LENGTH_RATIO_BASELINE = 1.24

    @staticmethod
    def _longest_is_answer(q):
        others = [len(c) for i, c in enumerate(q["choices"]) if i != q["answer"]]
        return len(q["choices"][q["answer"]]) > max(others)

    def _mcq(self, path):
        return [q for q in json.loads(path.read_text(encoding="utf-8"))
                ["questions"] if q["type"] == "mcq"]

    def test_no_bank_leaks_more_answers_by_length(self):
        for path in self._banks():
            mcq = self._mcq(path)
            if not mcq:
                continue
            hit = sum(1 for q in mcq if self._longest_is_answer(q))
            cap = self.LONGEST_ANSWER_BASELINE.get(path.name)
            if cap is None:
                # 새로 만든 은행은 기존 빚을 물려받을 이유가 없다. 무작위 기대치는
                # 4지선다에서 25%이므로 절반을 상한으로 둔다.
                cap = len(mcq) // 2
                why = f"새 은행은 {len(mcq)}문항 중 {cap}개까지만 허용"
            else:
                why = f"기준선 {cap} (줄이는 방향으로만 바꾼다)"
            with self.subTest(bank=path.name):
                self.assertLessEqual(
                    hit, cap,
                    f"{path.name}: 정답이 가장 긴 보기인 문항 {hit}/{len(mcq)} — {why}")

    def test_the_correct_choice_does_not_grow_longer_overall(self):
        """'가장 길다'를 피해도 정답만 계속 길어지면 단서는 남는다."""
        correct, wrong = [], []
        for path in self._banks():
            for q in self._mcq(path):
                for i, c in enumerate(q["choices"]):
                    (correct if i == q["answer"] else wrong).append(len(c))
        if not correct or not wrong:
            self.skipTest("아직 객관식 없음")
        ratio = (sum(correct) / len(correct)) / (sum(wrong) / len(wrong))
        self.assertLessEqual(
            round(ratio, 2), self.LENGTH_RATIO_BASELINE,
            f"정답/오답 평균 길이 비율 {ratio:.2f} "
            f"(기준선 {self.LENGTH_RATIO_BASELINE}) — 오답을 정답만큼 "
            f"구체적으로 쓰거나 정답을 줄여라")


class SeedParseTest(unittest.TestCase):
    def test_checklist_and_dbms_tagging(self):
        md = (
            "# 샘플 챕터\n"
            "## 3. 실습 예제\n"
            "```sql\n"
            "SELECT 1;  -- 결과: 1\n"
            "```\n"
            "## 4. 체크리스트\n"
            "- [ ] 무언가를 할 수 있다.\n"
            "<!-- dbms:postgresql -->\n"
            "- [ ] PostgreSQL 전용 항목을 안다.\n"
            "<!-- /dbms:postgresql -->\n"
        )
        events = seed_exam.parse_chapter(md.splitlines(keepends=True))
        kinds = [e[0] for e in events]
        self.assertIn("checklist", kinds)
        self.assertIn("practice", kinds)
        # dbms 태깅: 마커 안의 체크리스트 항목은 postgresql
        pg = [e for e in events if e[0] == "checklist" and e[1] == "postgresql"]
        self.assertEqual(len(pg), 1)

    def test_marker_inside_fence_is_ignored(self):
        # 코드펜스 안의 마커는 예시 텍스트로 간주(filter_dbms 규칙 상속)
        md = (
            "# t\n"
            "## 실습 예제\n"
            "```\n"
            "<!-- dbms:mysql -->\n"
            "echo hi  -- 결과: hi\n"
            "```\n"
        )
        events = seed_exam.parse_chapter(md.splitlines(keepends=True))
        practice = [e for e in events if e[0] == "practice"]
        self.assertEqual(len(practice), 1)
        self.assertEqual(practice[0][1], "neutral")  # 펜스 내 마커 무시 → neutral

    def test_seed_chapter_builds_draft(self):
        bank = seed_exam.seed_chapter(
            REPO_ROOT / "01-beginner" / "01-rdbms-fundamentals.md")
        self.assertTrue(bank["questions"])
        self.assertTrue(all(q.get("_draft") for q in bank["questions"]))


class RealBanksTest(unittest.TestCase):
    """실제 exams/ 문제은행이 스키마를 통과하는지(존재할 때만)."""

    def test_all_banks_valid(self):
        banks = list((REPO_ROOT / "exams").glob("**/*.json"))
        if not banks:
            self.skipTest("아직 문제은행 없음")
        for path in banks:
            with self.subTest(bank=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(exam.validate_bank(data), [],
                                 f"{path} 검증 실패")


if __name__ == "__main__":
    unittest.main()
