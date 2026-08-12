#!/usr/bin/env python3
"""guide.py(통합 런처) 단위 테스트.

실행:
    python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import guide  # noqa: E402
import tui  # noqa: E402


class ModeTableTest(unittest.TestCase):
    """메뉴는 모드 목록 데이터다 — 후속의 '챕터 읽기'가 한 줄로 붙어야 한다."""

    def test_modes_are_offered_in_learning_order(self):
        """읽기 → 확인 → 겪기. 메뉴 순서가 곧 학습 순서다.

        `README.md`가 "챕터 읽기 · 학습 점검 · 장애 대응"이라고 적고 있으니
        메뉴도 그 순서여야 한다. 처음 온 사람에게 첫 줄은 권하는 출발점이다.
        """
        self.assertEqual([m.key for m in guide.MODES],
                         ["read", "exam", "shoot"])

    def test_only_the_read_mode_skips_the_launcher_pause(self):
        """`reading`은 페이저 사용 여부를 보고 스스로 판단한다 (이슈 #95).

        `exam`·`shoot`은 평문을 남기므로 런처가 멈춰 줘야 한다 — `shoot`은
        등급표·후일담을, `exam`은 라인 모드 진행을 평문으로 찍는다.
        """
        self.assertEqual({m.key: m.pause for m in guide.MODES},
                         {"read": False, "exam": True, "shoot": True})

    def test_scale_reports_the_real_counts(self):
        """`exam.discover_banks()`와 같은 재귀 글롭으로 세어야 한다.

        `exams/*/*.json`(비재귀)은 지금은 은행이 모두 한 단계 깊이라 우연히
        같은 수를 내지만, 구현(`exam.discover_banks`)은
        `exams/**/*.json`(recursive=True)이라 은행이 더 깊어지면 갈라진다.
        """
        import glob
        n = 0
        for p in glob.glob(str(REPO_ROOT / "exams" / "**" / "*.json"),
                            recursive=True):
            with open(p, encoding="utf-8") as f:
                n += len(json.load(f)["questions"])
        self.assertEqual(f"{n}문항", guide.exam_scale())
        stages = len(list((REPO_ROOT / "shooting" / "stages").glob("*.json")))
        self.assertEqual(f"{stages}스테이지", guide.shoot_scale())

    def test_labels_carry_title_and_scale(self):
        labels = guide.menu_labels()
        self.assertEqual(len(labels), len(guide.MODES))
        self.assertIn("챕터 읽기", labels[0])
        self.assertIn("챕터", labels[0])
        self.assertIn("학습 점검", labels[1])
        self.assertIn("문항", labels[1])
        self.assertIn("장애 대응", labels[2])
        self.assertIn("스테이지", labels[2])

    def test_labels_align_by_display_width_not_char_count(self):
        """`str.ljust`는 글자 수로 세는데 한글은 화면에서 두 칸이다.

        제목 길이가 우연히 같은 두 모드만으로는 이 버그가 드러나지 않는다.
        가상의 세 번째 모드('챕터 읽기')를 붙여, 표시 폭이 다른 제목들이
        섞여도 규모 열의 시작 위치가 모두 같은지 확인한다.
        """
        from tui import cwidth
        fake_modes = (
            guide.Mode("a", "학습 점검 (퀴즈/시험)", lambda: "0문항",
                       lambda: 0, True),
            guide.Mode("b", "장애 대응 (실전 훈련)", lambda: "0스테이지",
                       lambda: 0, True),
            guide.Mode("c", "챕터 읽기", lambda: "0챕터", lambda: 0, False),
        )
        real = guide.MODES
        guide.MODES = fake_modes
        try:
            labels = guide.menu_labels()
        finally:
            guide.MODES = real
        # 규모가 시작하는 화면 열 = 제목의 표시 폭 + 뒤따르는 공백 수(각 1칸).
        # "   "로 나누면 패딩 자체가 공백 3개 이상을 포함할 때 잘못 잘린다.
        columns = []
        for mode, label in zip(fake_modes, labels):
            rest = label[len(mode.title):]
            gap = len(rest) - len(rest.lstrip(" "))
            columns.append(cwidth(mode.title) + gap)
        self.assertEqual(len(set(columns)), 1, labels)

    def test_a_broken_bank_does_not_blank_the_menu(self):
        """은행 하나가 깨졌다고 메뉴가 비면 고칠 방법도 사라진다.

        기대값은 정확히 "0문항"이어야 한다 — "문항"만 있어도 통과하는
        느슨한 단언은 예외가 새는 경우까지 가려낼 수 없다.
        """
        real = guide.exam.load_bank
        guide.exam.load_bank = lambda p: (_ for _ in ()).throw(ValueError("깨짐"))
        try:
            self.assertEqual("0문항", guide.exam_scale())
        finally:
            guide.exam.load_bank = real


class ModeIsolationTest(unittest.TestCase):
    """모드가 끝나는 것이 런처를 죽이면 안 된다.

    두 `main()`은 끝나는 방식이 다르다(실측):
      exam.main      SystemExit 을 6곳(341, 1102, 1124, 1144, 1224, 1265)에서
                     올리지만, `main([])`로 실제 도달 가능한 것은 4곳뿐이다
                     (1124, 1144, 1224, 1265) — 341 은 target 인자를 줄 때만,
                     1102 는 --keydebug 일 때만 거친다. KeyboardInterrupt 는
                     스스로 잡아 130 을 반환한다.
      shooting.main  KeyboardInterrupt 를 잡지 않는다 — 지금까지는 모듈의
                     __main__ 블록이 마지막 방어선이었다.

    런처가 그 자리를 대신하지 않으면 `--dbms` 조합이 비어 SystemExit 이 오르는
    것만으로 메뉴로 돌아오지 못한다.
    """

    def _mode(self, boom):
        return guide.Mode("t", "테스트", lambda: "0개", boom, False)

    def _run(self, boom):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            guide.run_mode(self._mode(boom))
        return buf.getvalue()

    def test_a_normal_return_comes_back(self):
        self.assertEqual(self._run(lambda: 0), "")

    def test_a_normal_return_reports_nothing_was_printed(self):
        """`main`이 이 값으로 멈출지 정한다 — 안 찍었으면 멈출 이유가 없다."""
        self.assertFalse(guide.run_mode(self._mode(lambda: 0)))

    def test_a_reported_failure_says_it_printed(self):
        def boom():
            raise SystemExit("출제할 문항이 없습니다(필터 조건 확인).")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(guide.run_mode(self._mode(boom)))

    def test_an_interrupt_says_it_printed(self):
        def boom():
            raise KeyboardInterrupt
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(guide.run_mode(self._mode(boom)))

    def test_a_silent_system_exit_says_nothing_was_printed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(guide.run_mode(
                self._mode(lambda: (_ for _ in ()).throw(SystemExit(0)))))

    def test_system_exit_does_not_escape(self):
        def boom():
            raise SystemExit("출제할 문항이 없습니다(필터 조건 확인).")
        self.assertIn("출제할 문항이 없습니다", self._run(boom))

    def test_a_silent_system_exit_prints_nothing(self):
        """`SystemExit(0)`·`SystemExit()`는 메시지가 아니라 종료 코드다.

        `str(SystemExit(0))` 은 `"0"`, `str(SystemExit(None))` 은 `"None"` 이라
        문자열로 판단하면 둘 다 화면에 찍힌다. `e.code` 로 봐야 한다.
        """
        for silent in (SystemExit(0), SystemExit(), SystemExit(None)):
            def boom(exc=silent):
                raise exc
            self.assertEqual(self._run(boom), "", repr(silent.code))

    def test_a_numeric_failure_code_is_reported(self):
        """`SystemExit(1)` 은 메시지가 없다 — 무슨 일인지 알려 줘야 한다."""
        def boom():
            raise SystemExit(1)
        self.assertIn("1", self._run(boom))

    def test_keyboard_interrupt_does_not_escape(self):
        def boom():
            raise KeyboardInterrupt
        self.assertIn("중단했습니다", self._run(boom))

    def test_an_unexpected_error_still_escapes(self):
        """예상 못 한 버그까지 삼키면 트레이스백이 사라져 고칠 수 없다."""
        def boom():
            raise ValueError("엉뚱한 버그")
        with self.assertRaises(ValueError):
            with contextlib.redirect_stdout(io.StringIO()):
                guide.run_mode(self._mode(boom))


class PauseAfterModeTest(unittest.TestCase):
    """모드가 남긴 평문 출력을 메뉴 리프레시가 곧바로 지워선 안 된다.

    tty면 다음 프레임에 `choose_menu`→curses가 화면을 지운다. 그 직전에
    `run_mode`가 찍은 것(예: `SystemExit`이 물고 온 사유, shoot의 등급표·
    후일담)이 한 프레임도 못 읽히고 사라지므로, tty에서만 한 번 멈춰야 한다.
    비-tty(파이프)에서 멈추면 `input()`이 다음 입력 줄을 삼켜 파이프 실행이
    깨지므로, 거기서는 멈추면 안 된다.

    실제 멈춤 로직은 `tui.pause_after_output()`으로 옮겨졌다(`reading`도 같은
    함정을 겪어서 공용이 됐다) — `guide.pause_after_mode()`는 그 얇은 래퍼다.
    그래서 여기서 patch하는 대상도 `guide.input`이 아니라 `tui.input`이다:
    실제 `input()` 호출이 이제 `tui` 모듈 안에서 일어나기 때문이다. tty
    판정은 여전히 전역 `sys.stdin`/`sys.stdout`을 보므로 `_patch_tty`는 그대로
    유효하다.
    """

    def _patch_tty(self, is_tty):
        real_in, real_out = sys.stdin.isatty, sys.stdout.isatty
        sys.stdin.isatty = lambda: is_tty
        sys.stdout.isatty = lambda: True

        def _restore():
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out
        return _restore

    def test_it_does_not_pause_when_not_a_tty(self):
        restore = self._patch_tty(False)
        called = []
        # `pause_after_output`이 이제 반환값을 `.strip()` 한다 —
        # `list.append`가 돌려주는 `None`으로는 그 경로를 지날 수 없다.
        tui.input = lambda *a, **k: called.append(1) or ""
        try:
            guide.pause_after_mode()
        finally:
            del tui.input
            restore()
        self.assertEqual(called, [])

    def test_it_pauses_when_a_tty(self):
        restore = self._patch_tty(True)
        called = []
        # `pause_after_output`이 이제 반환값을 `.strip()` 한다 —
        # `list.append`가 돌려주는 `None`으로는 그 경로를 지날 수 없다.
        tui.input = lambda *a, **k: called.append(1) or ""
        try:
            guide.pause_after_mode()
        finally:
            del tui.input
            restore()
        self.assertEqual(called, [1])

    def test_it_swallows_eof_and_keyboard_interrupt(self):
        restore = self._patch_tty(True)
        try:
            for exc in (EOFError, KeyboardInterrupt):
                def boom(*a, exc=exc, **k):
                    raise exc
                tui.input = boom
                guide.pause_after_mode()  # 예외 없이 돌아와야 한다
        finally:
            del tui.input
            restore()

    def test_main_pauses_after_a_pausing_mode_but_not_after_quitting(self):
        """배선 테스트: `main()`이 `run_mode` 뒤에 `pause_after_mode`를 부르는가.

        마지막에 메뉴에서 바로 종료(`None`)할 때는 `run_mode`가 안 불리므로
        `pause_after_mode`도 불리면 안 된다.

        모드를 **키로** 찾는다 — 인덱스를 박아 두면 메뉴 순서를 바꿀 때마다
        이 배선 테스트가 함께 깨진다(`MainLoopTest._index_of`와 같은 이유).
        """
        keys = [m.key for m in guide.MODES]
        picks = [keys.index("exam"), keys.index("shoot")]
        real_choose, real_run, real_pause = (
            guide.choose_menu, guide.run_mode, guide.pause_after_mode)
        seq = iter(picks + [None])
        paused = []
        guide.choose_menu = lambda labels: next(seq)
        guide.run_mode = lambda mode: False      # 아무것도 찍지 않았다
        guide.pause_after_mode = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                guide.main([])
        finally:
            guide.choose_menu, guide.run_mode, guide.pause_after_mode = (
                real_choose, real_run, real_pause)
        self.assertEqual(paused, [1, 1])

    def test_the_read_mode_does_not_pause_because_reading_decides_itself(self):
        """`reading`이 페이저 사용 여부를 보고 더 정확하게 판단한다 (이슈 #95).

        여기서 또 멈추면 그 판단이 무의미해지고, 챕터 하나 읽고 나갈 때 Enter
        프롬프트를 두 번 통과해야 한다.
        """
        keys = [m.key for m in guide.MODES]
        real_choose, real_run, real_pause = (
            guide.choose_menu, guide.run_mode, guide.pause_after_mode)
        seq = iter([keys.index("read"), None])
        paused = []
        guide.choose_menu = lambda labels: next(seq)
        guide.run_mode = lambda mode: False
        guide.pause_after_mode = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                guide.main([])
        finally:
            guide.choose_menu, guide.run_mode, guide.pause_after_mode = (
                real_choose, real_run, real_pause)
        self.assertEqual(paused, [])

    def test_a_mode_that_printed_pauses_even_if_it_does_not_normally(self):
        """`run_mode`가 사유를 찍었다면 `pause` 값과 무관하게 멈춰야 한다.

        `read` 모드도 여기 해당한다 — `reading.main`이 `exam.main`의
        `SystemExit`을 그대로 흘려보낸다.
        """
        keys = [m.key for m in guide.MODES]
        real_choose, real_run, real_pause = (
            guide.choose_menu, guide.run_mode, guide.pause_after_mode)
        seq = iter([keys.index("read"), None])
        paused = []
        guide.choose_menu = lambda labels: next(seq)
        guide.run_mode = lambda mode: True       # 뭔가 찍었다
        guide.pause_after_mode = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                guide.main([])
        finally:
            guide.choose_menu, guide.run_mode, guide.pause_after_mode = (
                real_choose, real_run, real_pause)
        self.assertEqual(paused, [1])


class MainLoopTest(unittest.TestCase):
    """고른 모드가 실제로 불려야 한다.

    순수 함수만 맞고 호출부가 안 쓰는 누락을 이 저장소에서 세 번 겪었다
    (진단 문항 셔플, play_footer, database_exists). 그래서 배선을 따로 잡는다.
    """

    @contextlib.contextmanager
    def _menu(self, picks):
        """고를 인덱스를 순서대로 돌려주고, 마지막에 None(종료)을 준다."""
        seq = iter(list(picks) + [None])
        ran = []
        real_choose, real_run = guide.choose_menu, guide.run_mode
        guide.choose_menu = lambda labels: next(seq)
        guide.run_mode = lambda mode: ran.append(mode.key)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield ran
        finally:
            guide.choose_menu, guide.run_mode = real_choose, real_run

    @staticmethod
    def _index_of(key):
        """모드를 **키로** 찾는다.

        인덱스를 박아 두면 메뉴 순서를 바꿀 때마다 배선 테스트가 함께 깨진다.
        여기서 검사하려는 것은 "고른 항목의 러너가 돈다"이지 그 항목이 몇
        번째냐가 아니다 — 순서는 `ModeTableTest`가 따로 고정한다.
        """
        return [m.key for m in guide.MODES].index(key)

    def test_picking_a_mode_runs_that_runner(self):
        with self._menu([self._index_of("shoot")]) as ran:
            self.assertEqual(guide.main([]), 0)
        self.assertEqual(ran, ["shoot"])

    def test_the_menu_comes_back_until_you_quit(self):
        picks = [self._index_of(k) for k in ("exam", "shoot", "exam")]
        with self._menu(picks) as ran:
            guide.main([])
        self.assertEqual(ran, ["exam", "shoot", "exam"])

    def test_quitting_at_the_menu_ends_the_program(self):
        with self._menu([]) as ran:
            self.assertEqual(guide.main([]), 0)
        self.assertEqual(ran, [])


class LauncherTest(unittest.TestCase):
    """런처가 없으면 `./guide` 는 존재하지 않는 것과 같다."""

    def test_the_launcher_exists_and_is_executable(self):
        import os
        p = REPO_ROOT / "guide"
        self.assertTrue(p.is_file(), "저장소 루트에 guide 런처가 없다")
        self.assertTrue(os.access(p, os.X_OK), "guide 에 실행 권한이 없다")

    def test_the_launcher_points_at_the_script(self):
        body = (REPO_ROOT / "guide").read_text(encoding="utf-8")
        self.assertIn("scripts/guide.py", body)
        self.assertIn("#!/usr/bin/env bash", body)

    def test_it_runs_and_offers_all_three_modes(self):
        """실제로 실행해 메뉴가 뜨는지 본다 — 파이프라 평문 폴백으로 돈다."""
        import subprocess
        p = subprocess.run([str(REPO_ROOT / "guide")], input="q\n",
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("학습 점검", p.stdout)
        self.assertIn("장애 대응", p.stdout)
        self.assertIn("챕터 읽기", p.stdout)

    def test_the_docs_mention_it(self):
        for name in ("README.md", "CLAUDE.md"):
            body = (REPO_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("./guide", body, f"{name} 에 ./guide 안내가 없다")


class ReadingModeWiringTest(unittest.TestCase):
    """세 번째 모드를 고르면 읽기 러너가 실제로 불려야 한다."""

    def test_the_read_mode_runs_the_reading_runner(self):
        ran = []
        real = guide.reading.main
        guide.reading.main = lambda argv: ran.append(argv) or 0
        try:
            mode = next(m for m in guide.MODES if m.key == "read")
            mode.run()
        finally:
            guide.reading.main = real
        self.assertEqual(ran, [[]])

    def test_its_scale_counts_chapters(self):
        mode = next(m for m in guide.MODES if m.key == "read")
        self.assertIn("챕터", mode.scale())


class GlobalQuitTest(unittest.TestCase):
    """이슈 #95 — 어느 화면에서든 `Q` 한 타로 앱이 끝나야 한다.

    `run_mode`는 `QuitApp`을 **잡지 않는다**. 지금도 `SystemExit`·
    `KeyboardInterrupt`만 잡으므로 그대로 통과하는데, 나중에
    `except Exception`으로 넓히는 사람을 막으려면 그 계약을 테스트로 못 박아야
    한다.
    """

    def test_run_mode_does_not_swallow_a_quit(self):
        def boom():
            raise tui.QuitApp
        mode = guide.Mode("t", "테스트", lambda: "0개", boom, False)
        with self.assertRaises(tui.QuitApp):
            with contextlib.redirect_stdout(io.StringIO()):
                guide.run_mode(mode)

    def test_main_ends_cleanly_when_a_mode_quits(self):
        real_choose, real_run = guide.choose_menu, guide.run_mode

        def boom(mode):
            raise tui.QuitApp

        guide.choose_menu = lambda labels: 0
        guide.run_mode = boom
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(guide.main([]), 0)
        finally:
            guide.choose_menu, guide.run_mode = real_choose, real_run

    def test_main_ends_cleanly_when_the_menu_quits(self):
        """최상위 메뉴에서 `Q`를 눌러도 같은 경로로 끝나야 한다."""
        real_choose = guide.choose_menu

        def boom(labels):
            raise tui.QuitApp

        guide.choose_menu = boom
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(guide.main([]), 0)
        finally:
            guide.choose_menu = real_choose
