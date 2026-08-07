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


class ModeTableTest(unittest.TestCase):
    """메뉴는 모드 목록 데이터다 — 후속의 '챕터 읽기'가 한 줄로 붙어야 한다."""

    def test_both_runners_are_offered(self):
        self.assertEqual([m.key for m in guide.MODES], ["exam", "shoot"])

    def test_scale_reports_the_real_counts(self):
        import glob
        n = sum(len(json.load(open(p))["questions"])
                for p in glob.glob(str(REPO_ROOT / "exams" / "*" / "*.json")))
        self.assertIn(f"{n}문항", guide.exam_scale())
        stages = len(list((REPO_ROOT / "shooting" / "stages").glob("*.json")))
        self.assertIn(f"{stages}스테이지", guide.shoot_scale())

    def test_labels_carry_title_and_scale(self):
        labels = guide.menu_labels()
        self.assertEqual(len(labels), len(guide.MODES))
        self.assertIn("학습 점검", labels[0])
        self.assertIn("문항", labels[0])
        self.assertIn("장애 대응", labels[1])
        self.assertIn("스테이지", labels[1])

    def test_a_broken_bank_does_not_blank_the_menu(self):
        """은행 하나가 깨졌다고 메뉴가 비면 고칠 방법도 사라진다."""
        real = guide.exam.load_bank
        guide.exam.load_bank = lambda p: (_ for _ in ()).throw(ValueError("깨짐"))
        try:
            self.assertIn("문항", guide.exam_scale())
        finally:
            guide.exam.load_bank = real


class ModeIsolationTest(unittest.TestCase):
    """모드가 끝나는 것이 런처를 죽이면 안 된다.

    두 `main()`은 끝나는 방식이 다르다(실측):
      exam.main      SystemExit 을 5곳에서 올린다. KeyboardInterrupt 는 스스로
                     잡아 130 을 반환한다.
      shooting.main  KeyboardInterrupt 를 잡지 않는다 — 지금까지는 모듈의
                     __main__ 블록이 마지막 방어선이었다.

    런처가 그 자리를 대신하지 않으면 `--dbms` 조합이 비어 SystemExit 이 오르는
    것만으로 메뉴로 돌아오지 못한다.
    """

    def _mode(self, boom):
        return guide.Mode("t", "테스트", lambda: "0개", boom)

    def _run(self, boom):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            guide.run_mode(self._mode(boom))
        return buf.getvalue()

    def test_a_normal_return_comes_back(self):
        self.assertEqual(self._run(lambda: 0), "")

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
