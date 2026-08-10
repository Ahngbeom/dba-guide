#!/usr/bin/env python3
"""reading.py(챕터 읽기 모드) 단위 테스트.

실행:
    python3 -m unittest discover -s tests
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import reading  # noqa: E402


class ChapterDiscoveryTest(unittest.TestCase):
    """개요·치트시트·부록도 읽을거리다 — 전부 내놓는다."""

    def test_tiers_are_the_four_content_directories(self):
        self.assertEqual(
            reading.TIERS,
            ("01-beginner", "02-intermediate", "03-advanced", "appendix"))

    def test_each_tier_finds_its_markdown(self):
        for tier in reading.TIERS:
            found = reading.discover_chapters(tier)
            on_disk = sorted(p.name for p in (REPO_ROOT / tier).glob("*.md"))
            self.assertEqual([Path(r).name for r in found], on_disk, tier)
            self.assertTrue(all(r.startswith(tier + "/") for r in found), tier)

    def test_scale_counts_every_chapter(self):
        total = sum(len(list((REPO_ROOT / t).glob("*.md")))
                    for t in reading.TIERS)
        self.assertEqual(reading.chapter_count(), total)
        self.assertEqual(reading.read_scale(), f"{total}챕터")


class ChapterTextTest(unittest.TestCase):
    """벤더를 고르면 그 벤더의 본문만 읽는다.

    `filter_dbms.filter_lines` 를 쓴다 — `generate-branch.sh` 가 단일 벤더
    브랜치를 만들 때 쓰는 바로 그 함수다. 새로 만들면 브랜치 뷰와 갈라진다.
    """

    CHAPTER = "01-beginner/03-installation-and-access.md"

    def test_no_filter_returns_the_file_as_is(self):
        raw = (REPO_ROOT / self.CHAPTER).read_text(encoding="utf-8")
        self.assertEqual(reading.chapter_text(self.CHAPTER), raw)

    def test_a_vendor_drops_the_other_vendors_blocks(self):
        full = reading.chapter_text(self.CHAPTER)
        pg = reading.chapter_text(self.CHAPTER, dbms="postgresql")
        self.assertLess(len(pg), len(full), "필터가 아무것도 걷어내지 않았다")
        self.assertNotIn("<!-- dbms:", pg, "마커가 본문에 남았다")

    def test_a_missing_chapter_says_so_instead_of_crashing(self):
        text = reading.chapter_text("01-beginner/99-없는챕터.md")
        self.assertIn("읽을 수 없습니다", text)

    def test_a_malformed_chapter_says_so_instead_of_crashing(self):
        """`filter_lines`는 OSError가 아니라 ValueError를 올린다(예: 닫히지
        않은 코드 펜스) — `chapter_text`가 OSError만 잡으면 이 경로가 그대로
        새어 나가 메뉴로 돌아갈 수 없다."""
        # 실제 티어 디렉터리 아래에 둬야 한다 — chapter_text는 rel을 REPO_ROOT
        # 기준 상대경로로 읽는다.
        rel = "01-beginner/_test-unclosed-code-fence.md"
        path = REPO_ROOT / rel
        self.assertFalse(path.exists(), "테스트용 파일이 이미 존재한다")
        path.write_text("```sql\nSELECT 1;\n", encoding="utf-8")
        try:
            text = reading.chapter_text(rel, dbms="postgresql")
        finally:
            path.unlink()
        self.assertIn("읽을 수 없습니다", text)


class _TTYStringIO(io.StringIO):
    """`redirect_stdout` 이 붙인 스트림이 tty 로 보여야 `offer_exam` 이 실제로
    `ask()` 까지 간다."""

    def isatty(self):
        return True


class ExamOfferTest(unittest.TestCase):
    """은행이 있는 챕터에서만 묻는다.

    개요·치트시트·부록에는 문제은행이 없다. 없는데 묻고 '예'를 받으면 갈 곳이
    없다. 비-tty 에서도 묻지 않는다 — `input()` 이 다음 입력 줄을 삼켜 파이프
    실행이 깨진다(`./guide` 에서 같은 함정을 이미 밟았다).
    """

    WITH_BANK = "02-intermediate/01-transaction-and-locking.md"
    NO_BANK = "01-beginner/07-commands-cheatsheet.md"

    def setUp(self):
        # `offer_exam` 은 이제 은행 경로를 스스로 구하지 않고 호출부가 넘긴
        # `bank` 를 그대로 쓴다(핸드오프에서 같은 조회를 두 번 하지 않도록
        # `reading.main` 이 한 번만 구해 재사용한다) — 테스트도 같은 계약을
        # 따라 실제 매핑을 미리 구해 둔다.
        self.with_bank = reading.exam.exam_bank_for(self.WITH_BANK)
        self.no_bank = reading.exam.exam_bank_for(self.NO_BANK)
        self.assertIsNotNone(self.with_bank, "고정값 자체가 은행 없는 챕터다")
        self.assertIsNone(self.no_bank, "고정값 자체가 은행 있는 챕터다")

    @contextlib.contextmanager
    def _stdin_as_tty(self):
        """`sys.stdin.isatty()` 가 True 를 돌려주게 한다.

        `offer_exam` 은 `sys.stdin.isatty()` 도 함께 본다. 파이프로 돌리는
        자동화 실행 하네스에서는 프로세스 자체가 tty 에 붙어 있지 않아 이게
        원래 False 라, `ask` 가 불리기도 전에 짧게 끝나 버려서 이 테스트들이
        검증하려는 y/Enter/n/EOF 분기를 타지 못한다. 하네스와 무관하게
        고정해야 이 테스트가 실제 코드 경로를 결정적으로 검증한다.
        """
        real = sys.stdin.isatty
        sys.stdin.isatty = lambda: True
        try:
            yield
        finally:
            sys.stdin.isatty = real

    def test_it_does_not_ask_when_there_is_no_bank(self):
        """은행 부재가 묻지 않는 **유일한** 이유임을 증명해야 한다 — tty
        가드가 먼저 걸리면 이 테스트는 `offer_exam`에서 `not bank or` 를
        지워도 계속 통과해 버린다(실측: 이전 버전이 그랬다). 그래서 형제
        테스트들과 같은 `_stdin_as_tty()`/`_TTYStringIO()` 로 tty처럼 보이게
        고정한 채로, 그래도 묻지 않는지를 본다.
        """
        asked = []
        with self._stdin_as_tty(), contextlib.redirect_stdout(_TTYStringIO()):
            got = reading.offer_exam(self.NO_BANK, self.no_bank,
                                     ask=lambda p: asked.append(p) or "y")
        self.assertFalse(got)
        self.assertEqual(asked, [])

    def test_yes_accepts(self):
        with self._stdin_as_tty(), contextlib.redirect_stdout(_TTYStringIO()):
            self.assertTrue(reading.offer_exam(self.WITH_BANK, self.with_bank,
                                               ask=lambda _: "y"))

    def test_enter_accepts_because_the_default_is_yes(self):
        with self._stdin_as_tty(), contextlib.redirect_stdout(_TTYStringIO()):
            self.assertTrue(reading.offer_exam(self.WITH_BANK, self.with_bank,
                                               ask=lambda _: ""))

    def test_n_declines(self):
        with self._stdin_as_tty(), contextlib.redirect_stdout(_TTYStringIO()):
            self.assertFalse(reading.offer_exam(self.WITH_BANK, self.with_bank,
                                                ask=lambda _: "n"))

    def test_closed_input_declines(self):
        def eof(_):
            raise EOFError
        with self._stdin_as_tty(), contextlib.redirect_stdout(_TTYStringIO()):
            self.assertFalse(reading.offer_exam(self.WITH_BANK, self.with_bank,
                                                ask=eof))


class ReadChapterTest(unittest.TestCase):
    """본문은 `$PAGER` 로 간다 — curses 안에 뷰어를 만들지 않는다."""

    def test_it_hands_the_filtered_text_to_the_pager(self):
        seen = {}
        real = reading.page_text
        reading.page_text = lambda text: seen.setdefault("text", text)
        try:
            reading.read_chapter("01-beginner/03-installation-and-access.md",
                                 dbms="postgresql")
        finally:
            reading.page_text = real
        self.assertIn("설치", seen["text"])
        self.assertNotIn("<!-- dbms:", seen["text"])


class ReadingMainTest(unittest.TestCase):
    """고른 챕터가 실제로 읽히고, 끝나면 티어 선택으로 돌아온다.

    순수 함수만 맞고 호출부가 안 쓰는 누락을 이 저장소에서 네 번 겪었다.
    """

    @contextlib.contextmanager
    def _flow(self, picks):
        """고를 인덱스를 순서대로 돌려준다. 목록이 끝나면 None(뒤로/종료)."""
        seq = iter(list(picks) + [None] * 6)
        read = []
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "offer_exam")}
        reading.choose = lambda title, labels: next(seq)
        reading.read_chapter = lambda rel, dbms=None: read.append((rel, dbms))
        reading.offer_exam = lambda rel, bank, ask=input: False
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield read
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)

    def test_dbms_then_tier_then_chapter_reaches_the_reader(self):
        # 0=전체, 0=01-beginner, 0=그 티어의 첫 챕터
        with self._flow([0, 0, 0]) as read:
            self.assertEqual(reading.main([]), 0)
        self.assertEqual(len(read), 1)
        self.assertTrue(read[0][0].startswith("01-beginner/"))
        self.assertIsNone(read[0][1])          # '전체'는 필터 없음

    def test_choosing_a_vendor_passes_it_through(self):
        # 1=PostgreSQL
        with self._flow([1, 0, 0]) as read:
            reading.main([])
        self.assertEqual(read[0][1], "postgresql")

    def test_the_exam_handoff_uses_an_absolute_bank_path_and_forwards_dbms(self):
        """Important 1 + 2 회귀.

        전에는 `exam.main([exam.exam_bank_for(rel)])`처럼 cwd 기준 상대경로만
        넘기고 고른 DBMS는 흘렸다 — `./guide`를 저장소 밖 cwd에서 띄우면 은행을
        못 찾고("대상을 찾을 수 없습니다: exams/...") PostgreSQL 챕터를 읽고도
        MySQL·Oracle 문항까지 다 나왔다. 이제 절대경로 + `--dbms`를 넘겨야
        한다.
        """
        captured = {}
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "offer_exam")}
        real_exam_main = reading.exam.main
        # 1=PostgreSQL, 1=02-intermediate, 1=그 티어의 두 번째 챕터
        # (00-overview.md 다음, 정렬 순서상 01-transaction-and-locking.md =
        # ExamOfferTest.WITH_BANK) → offer_exam이 "예"라고 답한다.
        seq = iter([1, 1, 1] + [None] * 6)
        reading.choose = lambda title, labels: next(seq)
        reading.read_chapter = lambda rel, dbms=None: None
        reading.offer_exam = lambda rel, bank, ask=input: True
        reading.exam.main = lambda argv: captured.setdefault("argv", argv) or 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                reading.main([])
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)
            reading.exam.main = real_exam_main

        argv = captured["argv"]
        self.assertTrue(Path(argv[0]).is_absolute(), argv)
        self.assertTrue(argv[0].startswith(str(REPO_ROOT)), argv)
        self.assertTrue(argv[0].endswith(".json"), argv)
        self.assertEqual(argv[1:], ["--dbms", "postgresql"], argv)

    def test_quitting_at_the_first_screen_reads_nothing(self):
        with self._flow([]) as read:
            self.assertEqual(reading.main([]), 0)
        self.assertEqual(read, [])
