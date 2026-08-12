#!/usr/bin/env python3
"""tui.py 표시 폭·줄바꿈 프리미티브 단위 테스트 (표준 라이브러리 unittest).

exam.py에서 추출하기 전까지 이 함수들은 테스트가 없었다. 전각 문자가 섞인
한국어 콘텐츠를 다루는 저장소라 폭 계산이 틀리면 화면이 조용히 깨진다.

실행:
    python3 -m unittest discover -s tests
"""
import contextlib
import io
import os
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

    def test_affirmative_both_representations(self):
        for k in (ord("y"), "y", ord("Y"), "Y"):
            self.assertTrue(tui.is_affirmative(k), k)

    def test_affirmative_defaults_to_no(self):
        """y 외에는 전부 '아니오'다 — 되돌릴 수 없는 동작의 기본값."""
        for k in (ord("n"), "n", ord("q"), "q", 10, "\n", 27, -1, None):
            self.assertFalse(tui.is_affirmative(k), k)


class _PickCurses:
    """pick()을 돌리기 위한 최소 curses 대역."""
    error = type("error", (Exception,), {})
    A_REVERSE = A_BOLD = A_DIM = A_NORMAL = 0
    # 실제 ncurses 값과 같게 둔다 — 화면 코드가 정수 상수로 먼저 판별한다.
    KEY_UP, KEY_DOWN = 259, 258

    @staticmethod
    def color_pair(_n):
        return 0


class _PickScreen:
    """키를 순서대로 돌려주는 화면 대역. 그린 텍스트를 모아둔다."""

    def __init__(self, keys, size=(24, 80)):
        self._keys = list(keys)
        self._size = size
        self.drawn = []

    def erase(self):
        self.drawn.append("\f")      # 프레임 경계

    def clear(self):
        self.erase()

    def refresh(self):
        pass

    def timeout(self, _ms):
        pass

    def nodelay(self, _flag):
        pass

    def getmaxyx(self):
        return self._size

    def addstr(self, _y, _x, text, _attr=0):
        self.drawn.append(text)

    def getch(self):
        # 키가 떨어지면 계속 마지막 키를 준다 — 처리 못 하면 테스트가 멈춰
        # 누락이 곧 드러난다.
        return self._keys.pop(0) if self._keys else ord("q")

    @property
    def frames(self):
        return "".join(self.drawn).split("\f")


class PickTest(unittest.TestCase):
    """세로 목록 선택기 — exam.py와 shooting.py에 흩어져 있던 것을 모은 것."""

    LABELS = ["primary", "replica", "arbiter"]

    def _pick(self, keys, labels=None, **kw):
        screen = _PickScreen(keys)
        idx = tui.pick(screen, _PickCurses(), "고르세요",
                       labels if labels is not None else self.LABELS, **kw)
        return idx, screen

    def test_enter_takes_the_highlighted_row(self):
        self.assertEqual(self._pick([10])[0], 0)

    def test_arrows_move(self):
        self.assertEqual(self._pick([_PickCurses.KEY_DOWN, 10])[0], 1)
        self.assertEqual(
            self._pick([_PickCurses.KEY_DOWN, _PickCurses.KEY_DOWN, 10])[0], 2)

    def test_vi_keys_move(self):
        self.assertEqual(self._pick([ord("j"), 10])[0], 1)
        self.assertEqual(self._pick([ord("j"), ord("k"), 10])[0], 0)

    def test_movement_wraps_around(self):
        # 첫 줄에서 위로 가면 마지막 줄로.
        self.assertEqual(self._pick([ord("k"), 10])[0], len(self.LABELS) - 1)

    def test_number_keys_select_directly(self):
        self.assertEqual(self._pick([ord("2")])[0], 1)
        self.assertEqual(self._pick([ord("3")])[0], 2)

    def test_out_of_range_number_is_ignored(self):
        # 항목이 3개인데 9를 눌러도 아무 일도 없어야 한다.
        self.assertEqual(self._pick([ord("9"), 10])[0], 0)

    def test_cancel_keys(self):
        self.assertIsNone(self._pick([ord("q")])[0])
        self.assertIsNone(self._pick([27])[0])

    def test_cancel_can_be_disabled(self):
        # 취소를 막으면 q는 무시되고 계속 고르게 된다.
        idx, _ = self._pick([ord("q"), 10], allow_cancel=False)
        self.assertEqual(idx, 0)

    def test_single_key_representations_agree(self):
        # read_key(wide=False)는 정수를 준다. 문자열 표현으로 와도 같아야 한다.
        self.assertEqual(self._pick(["j", "\n"])[0], 1)

    def test_labels_are_drawn(self):
        _, screen = self._pick([10])
        self.assertTrue(any("primary" in f for f in screen.frames))
        self.assertTrue(any("replica" in f for f in screen.frames))

    def test_long_list_scrolls_instead_of_overflowing(self):
        # 화면보다 긴 목록에서 마지막 항목을 고를 수 있어야 한다.
        labels = [f"항목 {i}" for i in range(40)]
        screen = _PickScreen([_PickCurses.KEY_UP, 10], size=(10, 80))
        idx = tui.pick(screen, _PickCurses(), "고르세요", labels)
        self.assertEqual(idx, len(labels) - 1)
        # 마지막 프레임에 그 항목이 실제로 그려져 있어야 한다(잘리면 안 된다).
        self.assertIn("항목 39", screen.frames[-1])

    def test_empty_list_returns_none(self):
        self.assertIsNone(tui.pick(_PickScreen([]), _PickCurses(), "제목", []))


class PageTextCharacterizationTest(unittest.TestCase):
    """옮기기 전 현재 동작을 고정한다.

    `page_text` 에는 테스트가 하나도 없었다. 테스트 없이 옮기면 전체 스위트가
    통과해도 이 함수에 대해서는 아무것도 증명하지 못한다.
    (`shooting.py` 에서 옮겨 온 뒤에도 같은 동작이어야 한다.)
    """

    def setUp(self):
        import tui
        self.mod = tui

    @contextlib.contextmanager
    def _env(self, pager=None, which=None):
        real_env = dict(os.environ)
        real_which = self.mod.shutil.which
        if pager is None:
            os.environ.pop("PAGER", None)
        else:
            os.environ["PAGER"] = pager
        self.mod.shutil.which = lambda n, *a, **k: which
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(real_env)
            self.mod.shutil.which = real_which

    def test_hands_the_text_to_the_pager(self):
        seen = {}

        class FakeProc:
            returncode = 7

            def communicate(self, text):
                seen["text"] = text

        real = self.mod.subprocess.Popen
        self.mod.subprocess.Popen = lambda *a, **k: seen.setdefault(
            "cmd", a[0]) and None or FakeProc()
        try:
            with self._env(pager="less -R"):
                rc = self.mod.page_text("본문")
        finally:
            self.mod.subprocess.Popen = real
        self.assertEqual(seen["cmd"], ["less", "-R"])
        self.assertEqual(seen["text"], "본문")
        self.assertEqual(rc, 7)

    def test_prints_plainly_when_there_is_no_pager(self):
        buf = io.StringIO()
        with self._env(pager=None, which=None):
            with contextlib.redirect_stdout(buf):
                rc = self.mod.page_text("본문")
        self.assertEqual(buf.getvalue().strip(), "본문")
        self.assertEqual(rc, 0)

    def test_a_failing_pager_falls_back_to_printing(self):
        real = self.mod.subprocess.Popen

        def boom(*a, **k):
            raise OSError("페이저 실행 실패")

        self.mod.subprocess.Popen = boom
        buf = io.StringIO()
        try:
            with self._env(pager="없는페이저"):
                with contextlib.redirect_stdout(buf):
                    rc = self.mod.page_text("본문")
        finally:
            self.mod.subprocess.Popen = real
        self.assertEqual(buf.getvalue().strip(), "본문")
        self.assertEqual(rc, 0)


class PagerColorTest(unittest.TestCase):
    """ANSI 를 넣어도 안전한 페이저인지 판정한다.

    지금보다 나빠지는 유일한 경우가 여기다 — `PAGER=more` 에 이스케이프를
    보내면 `ESC[1m` 이 글자로 찍힌다.
    """

    class _TTY(io.StringIO):
        def isatty(self):
            return True

    class _Pipe(io.StringIO):
        def isatty(self):
            return False

    @contextlib.contextmanager
    def _env(self, **kv):
        real = dict(os.environ)
        os.environ.pop("NO_COLOR", None)
        os.environ.pop("PAGER", None)
        os.environ["TERM"] = "xterm-256color"
        for k, v in kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(real)

    def test_no_pager_set_means_we_will_use_less_dash_r(self):
        with self._env():
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_less_is_fine(self):
        with self._env(PAGER="less"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_less_with_flags_is_fine(self):
        with self._env(PAGER="less -FRX"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_an_absolute_path_to_less_is_fine(self):
        with self._env(PAGER="/usr/bin/less"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_more_is_not(self):
        with self._env(PAGER="more"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))

    def test_formatters_are_not_treated_as_colour_pagers(self):
        # bat과 delta는 포맷터다. 입력을 재해석·재장식하므로 ANSI를 그대로
        # 통과시킨다는 보장이 없다. 모르는 페이저로 분류되면 무색으로 안전하게 떨어진다.
        with self._env(PAGER="bat"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))
        with self._env(PAGER="delta"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))

    def test_moar_and_ov_are_colour_pagers(self):
        # moar와 ov는 순수 페이저다.
        with self._env(PAGER="moar"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))
        with self._env(PAGER="ov"):
            self.assertTrue(tui.pager_supports_color(self._TTY()))

    def test_no_color_env_wins_over_everything(self):
        with self._env(PAGER="less", NO_COLOR="1"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))

    def test_a_pipe_gets_no_colour(self):
        with self._env(PAGER="less"):
            self.assertFalse(tui.pager_supports_color(self._Pipe()))

    def test_a_dumb_terminal_gets_no_colour(self):
        with self._env(PAGER="less", TERM="dumb"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))

    def test_an_unparseable_pager_gets_no_colour(self):
        with self._env(PAGER="less 'unclosed"):
            self.assertFalse(tui.pager_supports_color(self._TTY()))


class TextWidthTest(unittest.TestCase):
    """가로 200칸에서 한글 문단이 한 줄로 늘어지면 오히려 못 읽는다."""

    @contextlib.contextmanager
    def _cols(self, n):
        real = tui.shutil.get_terminal_size
        tui.shutil.get_terminal_size = lambda fallback=(80, 24): os.terminal_size(
            (n, 24))
        try:
            yield
        finally:
            tui.shutil.get_terminal_size = real

    def test_a_narrow_terminal_gets_the_floor(self):
        with self._cols(30):
            self.assertEqual(tui.text_width(), 40)

    def test_a_wide_terminal_gets_the_ceiling(self):
        with self._cols(200):
            self.assertEqual(tui.text_width(), 100)

    def test_a_normal_terminal_gets_two_columns_of_margin(self):
        with self._cols(90):
            self.assertEqual(tui.text_width(), 88)


class LessRawFlagTest(unittest.TestCase):
    """`less` 에 `-R` 이 없으면 ANSI 가 글자로 찍힌다. 없을 때만 붙인다."""

    def _cmd(self, pager):
        seen = {}

        class FakeProc:
            returncode = 0

            def communicate(self, text):
                pass

        real_popen = tui.subprocess.Popen
        real_env = dict(os.environ)
        tui.subprocess.Popen = lambda *a, **k: (seen.setdefault("cmd", a[0])
                                                and None or FakeProc())
        os.environ["PAGER"] = pager
        try:
            tui.page_text("본문")
        finally:
            tui.subprocess.Popen = real_popen
            os.environ.clear()
            os.environ.update(real_env)
        return seen["cmd"]

    def test_bare_less_gets_dash_r(self):
        self.assertEqual(self._cmd("less"), ["less", "-R"])

    def test_existing_dash_r_is_not_duplicated(self):
        self.assertEqual(self._cmd("less -R"), ["less", "-R"])

    def test_a_bundled_flag_counts(self):
        self.assertEqual(self._cmd("less -FRX"), ["less", "-FRX"])

    def test_the_long_form_counts(self):
        self.assertEqual(self._cmd("less --RAW-CONTROL-CHARS"),
                         ["less", "--RAW-CONTROL-CHARS"])

    def test_other_pagers_are_left_alone(self):
        self.assertEqual(self._cmd("more"), ["more"])

    def test_short_option_with_prompt_argument(self):
        # -P는 인자를 받는 옵션. -Pcurrent에서 current는 인자이지 옵션글자가 아니다.
        # 이전에는 current에 r이 있어서 오탐했다.
        self.assertEqual(self._cmd("less -Pcurrent"), ["less", "-Pcurrent", "-R"])

    def test_short_option_with_tab_argument(self):
        # -x는 인자를 받는 옵션.
        self.assertEqual(self._cmd("less -x4"), ["less", "-x4", "-R"])

    def test_short_option_with_file_argument(self):
        # -o는 인자를 받는 옵션.
        self.assertEqual(self._cmd("less -ofile.log"), ["less", "-ofile.log", "-R"])


class PickLineTest(unittest.TestCase):
    """`pick()`의 평문 짝. 파이프로 돌릴 때도 고를 수 있어야 한다."""

    def _choose(self, typed):
        it = iter(typed)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = tui.pick_line("무엇을 할까요", ["가", "나"],
                                ask=lambda _: next(it))
        return got, buf.getvalue()

    def test_a_number_selects(self):
        self.assertEqual(self._choose(["2"])[0], 1)

    def test_q_cancels(self):
        self.assertIsNone(self._choose(["q"])[0])

    def test_a_bad_entry_asks_again(self):
        got, out = self._choose(["9", "x", "1"])
        self.assertEqual(got, 0)
        self.assertIn("잘못된 입력", out)

    def test_closed_input_cancels(self):
        def eof(_):
            raise EOFError
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(tui.pick_line("제목", ["가"], ask=eof))


if __name__ == "__main__":
    unittest.main()
