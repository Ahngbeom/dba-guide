#!/usr/bin/env python3
"""tui.py 표시 폭·줄바꿈 프리미티브 단위 테스트 (표준 라이브러리 unittest).

exam.py에서 추출하기 전까지 이 함수들은 테스트가 없었다. 전각 문자가 섞인
한국어 콘텐츠를 다루는 저장소라 폭 계산이 틀리면 화면이 조용히 깨진다.

실행:
    python3 -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import tui  # noqa: E402


class CwidthTest(unittest.TestCase):
    def test_ascii_is_one_per_char(self):
        self.assertEqual(tui.cwidth("abc"), 3)

    def test_hangul_is_two_per_char(self):
        self.assertEqual(tui.cwidth("복제"), 4)

    def test_mixed(self):
        self.assertEqual(tui.cwidth("DB 복제"), 3 + 4)

    def test_empty(self):
        self.assertEqual(tui.cwidth(""), 0)

    def test_combining_and_narrow_symbols(self):
        # 박스 드로잉·화살표는 East Asian Width가 A(ambiguous)라 폭 1로 센다.
        self.assertEqual(tui.cwidth("→"), 1)


class FitTest(unittest.TestCase):
    def test_no_truncation_when_it_fits(self):
        self.assertEqual(tui.fit("abc", 10), "abc")

    def test_truncates_ascii(self):
        self.assertEqual(tui.fit("abcdef", 3), "abc")

    def test_never_splits_a_wide_char_in_half(self):
        # 폭 3에 전각 2칸짜리 두 글자 → 한 글자만 들어가야 한다(폭 2 사용).
        got = tui.fit("복제", 3)
        self.assertEqual(got, "복")
        self.assertLessEqual(tui.cwidth(got), 3)

    def test_zero_cols_yields_empty(self):
        self.assertEqual(tui.fit("복제", 0), "")

    def test_result_width_never_exceeds_cols(self):
        text = "DB 복제 지연 troubleshooting"
        for cols in range(0, 40):
            self.assertLessEqual(tui.cwidth(tui.fit(text, cols)), cols)


class WrapTest(unittest.TestCase):
    def test_always_returns_at_least_one_line(self):
        self.assertEqual(tui.wrap("", 10), [""])

    def test_wraps_on_spaces(self):
        self.assertEqual(tui.wrap("aaa bbb ccc", 7), ["aaa bbb", "ccc"])

    def test_preserves_explicit_newlines(self):
        self.assertEqual(tui.wrap("aaa\nbbb", 10), ["aaa", "bbb"])

    def test_force_splits_a_word_longer_than_cols(self):
        lines = tui.wrap("abcdefghij", 4)
        self.assertEqual(lines, ["abcd", "efgh", "ij"])

    def test_no_line_exceeds_cols_with_wide_chars(self):
        text = ("복제 지연이 발생하면 단일 쓰레드 적용이 거대 트랜잭션에 "
                "막혔는지 먼저 확인한다")
        for cols in (8, 12, 20, 33):
            for line in tui.wrap(text, cols):
                self.assertLessEqual(tui.cwidth(line), cols)

    def test_cols_is_floored_at_four(self):
        # cols=1이어도 무한 루프 없이 끝나야 한다(내부적으로 최소 4로 올림).
        lines = tui.wrap("복제지연", 1)
        self.assertTrue(lines)
        for line in lines:
            self.assertLessEqual(tui.cwidth(line), 4)


class DecodeNamedKeyTest(unittest.TestCase):
    """확장 키코드 해석. curses 모듈은 최소 스텁으로 대체한다."""

    class _FakeCurses:
        KEY_LEFT, KEY_RIGHT, KEY_HOME = 260, 261, 262

        def __init__(self, table):
            self._table = table

        def keyname(self, code):
            return self._table.get(code, b"unknown")

    def test_alt_and_ctrl_modifiers(self):
        c = self._FakeCurses({545: b"kLFT3", 546: b"kLFT5", 547: b"kLFT2"})
        self.assertEqual(tui.decode_named_key(c, 545), ("alt", c.KEY_LEFT))
        self.assertEqual(tui.decode_named_key(c, 546), ("ctrl", c.KEY_LEFT))
        # 2 → mod=1 → Shift만. alt/ctrl 아님.
        self.assertEqual(tui.decode_named_key(c, 547), ("key", c.KEY_LEFT))

    def test_unmatched_name_returns_none(self):
        c = self._FakeCurses({999: b"kSOMETHINGELSE"})
        self.assertIsNone(tui.decode_named_key(c, 999))

    def test_non_int_and_negative_return_none(self):
        c = self._FakeCurses({})
        self.assertIsNone(tui.decode_named_key(c, "a"))
        self.assertIsNone(tui.decode_named_key(c, -1))


class CursorHelpersMovedTest(unittest.TestCase):
    """exam.py에서 옮겨온 커서 함수가 tui.py에서도 동일하게 동작하는지.

    (원본 동작은 tests/test_exam.py가 exam.word_left 등으로 계속 검증한다 —
    그쪽이 이동의 안전망이라 수정하지 않는다.)
    """

    def test_available_from_tui(self):
        chars = list("grant select on emp")
        self.assertEqual(tui.word_left(chars, len(chars)),
                         len("grant select on "))
        self.assertEqual(tui.word_right(chars, 0), len("grant"))
        self.assertEqual(tui.line_start(chars, 5), 0)
        self.assertEqual(tui.line_end(chars, 0), len(chars))
        self.assertEqual(tui.move_line(chars, 3, -1), 3)   # 한 줄뿐이면 제자리


class KeyNormalizationTest(unittest.TestCase):
    """read_key는 wide 여부에 따라 정수/문자열을 섞어 돌려준다.

    이 정규화가 없으면 한쪽만 처리하다 키가 조용히 죽는다(실제로 그랬다).
    """

    def test_int_and_str_normalize_to_same_char(self):
        for ch in ("q", "r", "h", "b", "2"):
            self.assertEqual(tui.key_char(ord(ch)), ch)
            self.assertEqual(tui.key_char(ch), ch)
            self.assertEqual(tui.key_char(ord(ch)), tui.key_char(ch))

    def test_uppercase_is_distinct(self):
        self.assertEqual(tui.key_char(ord("Q")), "Q")
        self.assertNotEqual(tui.key_char(ord("Q")), tui.key_char(ord("q")))

    def test_digits(self):
        self.assertEqual(tui.key_char(ord("1")), "1")
        self.assertTrue(tui.key_char(ord("3")).isdigit())

    def test_non_character_values(self):
        self.assertIsNone(tui.key_char(None))
        self.assertIsNone(tui.key_char(-1))       # 타임아웃 반환값
        self.assertIsNone(tui.key_char("여러글자"))
        self.assertIsNone(tui.key_char(True))     # bool은 int 서브클래스라 방어

    def test_multibyte_char_passes_through(self):
        self.assertEqual(tui.key_char("가"), "가")
        self.assertEqual(tui.key_char(ord("가")), "가")

    def test_enter_both_representations(self):
        for k in (10, 13, "\n", "\r", 343):
            self.assertTrue(tui.is_enter(k), k)
        for k in (ord("q"), "q", 27, -1, None):
            self.assertFalse(tui.is_enter(k), k)

    def test_backspace_both_representations(self):
        for k in (127, 263, "\x7f", "\b"):
            self.assertTrue(tui.is_backspace(k), k)
        for k in (ord("b"), "b", 10, -1, None):
            self.assertFalse(tui.is_backspace(k), k)

    def test_is_idle(self):
        self.assertTrue(tui.is_idle("key", -1))
        self.assertTrue(tui.is_idle("key", None))
        self.assertFalse(tui.is_idle("key", ord("q")))
        self.assertFalse(tui.is_idle("esc", None))   # Esc는 유휴가 아니다


if __name__ == "__main__":
    unittest.main()
