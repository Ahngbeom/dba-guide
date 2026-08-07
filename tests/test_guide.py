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
