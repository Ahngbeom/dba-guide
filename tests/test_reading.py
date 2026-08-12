#!/usr/bin/env python3
"""reading.py(챕터 읽기 모드) 단위 테스트.

실행:
    python3 -m unittest discover -s tests
"""
import contextlib
import io
import sys
import types
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
        """`main` 위에서만 성립하는 검사다 — 벤더 브랜치에서는 건너뛴다.

        `postgresql`/`mysql`/`oracle` 브랜치는 `main` 을 이미 필터해 둔 파생
        뷰이고, 스크립트가 `*.md` 만 거르므로 이 스위트는 거기서도 그대로
        돈다. 이미 필터된 트리에서 한 번 더 거르면 무동작이라 아래 엄격
        부등호가 깨진다 — 필터가 고장 나서가 아니라 걷어낼 것이 없어서다.
        마커 유무로 어느 쪽 트리인지 가른다.
        """
        full = reading.chapter_text(self.CHAPTER)
        if "<!-- dbms:" not in full:
            self.skipTest("벤더 브랜치 — 이미 필터된 뷰라 걷어낼 마커가 없다")
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
    """본문은 렌더를 거쳐 `$PAGER` 로 간다 — curses 안에 뷰어를 만들지 않는다."""

    CHAPTER = "01-beginner/03-installation-and-access.md"

    @contextlib.contextmanager
    def _capture(self, printed=False):
        """`page_text` 를 가로챈다.

        `page_text` 는 이제 `(returncode, printed_inline)` 튜플을 돌려주므로
        대역도 같은 모양이어야 한다 — 문자열을 돌려주면 호출부의 언패킹이
        깨진다.
        """
        seen = {}
        real = reading.page_text

        def fake(text):
            seen["text"] = text
            return 0, printed

        reading.page_text = fake
        try:
            yield seen
        finally:
            reading.page_text = real

    def test_it_hands_the_filtered_text_to_the_pager(self):
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        self.assertIn("설치", seen["text"])
        self.assertNotIn("<!-- dbms:", seen["text"])

    def test_the_text_is_rendered_not_raw(self):
        """마크업 기호가 그대로 가면 렌더를 안 거친 것이다."""
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        self.assertNotIn("## ", seen["text"])
        self.assertIn("◆", seen["text"])

    def test_rendering_happens_after_the_vendor_filter(self):
        """순서가 뒤집히면 필터가 마커를 못 찾아 다른 벤더 본문이 남는다."""
        raw = reading.chapter_text(self.CHAPTER)
        if "<!-- dbms:" not in raw:
            self.skipTest("벤더 브랜치 — 이미 필터된 뷰라 걷어낼 마커가 없다")
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        rendered_full = reading.markdown_render.render(
            raw, width=reading.text_width(),
            color=reading.pager_supports_color())
        self.assertLess(len(seen["text"]), len(rendered_full))

    def test_a_pipe_gets_no_escape_sequences(self):
        """테스트는 tty 가 아니다 — 색이 꺼져야 한다."""
        with self._capture() as seen:
            reading.read_chapter(self.CHAPTER, dbms="postgresql")
        self.assertNotIn("\x1b", seen["text"])

    def test_it_reports_whether_it_printed_inline(self):
        """`page_text` 의 `printed_inline` 을 그대로 넘겨야 한다.

        호출부(`main`)가 이 값 하나로 pause 여부를 정한다. 여기서 삼키면
        페이저가 없는 환경에서 챕터 본문이 다음 curses 프레임에 지워진다.
        """
        with self._capture(printed=True):
            self.assertTrue(reading.read_chapter(self.CHAPTER))
        with self._capture(printed=False):
            self.assertFalse(reading.read_chapter(self.CHAPTER))


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


class ChapterPauseTest(unittest.TestCase):
    """이슈 #95 — 챕터를 읽을 때마다 뜻 없는 'Enter'를 요구하지 않는다.

    `pause_after_output()`은 "다음 curses 프레임의 `erase()`가 방금 찍힌 평문을
    한 프레임도 못 읽히고 지우는 것"을 막으려고 있다. `less`가 본문을 삼켰다면
    지킬 평문이 애초에 없다 — 그런데도 무조건 불러서, 챕터를 하나 읽을 때마다
    Enter를 한 번씩 눌러야 했다.
    """

    @contextlib.contextmanager
    def _flow(self, picks, printed=False, took_exam=False):
        """선택 → 읽기 → (시험) 한 바퀴를 돌리고 pause 호출을 센다."""
        seq = iter(list(picks) + [None] * 6)
        paused = []
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "offer_exam",
                          "pause_after_output")}
        real_exam_main = reading.exam.main
        reading.choose = lambda title, labels: next(seq)
        reading.read_chapter = lambda rel, dbms=None: printed
        reading.offer_exam = lambda rel, bank, ask=input: took_exam
        reading.pause_after_output = lambda: paused.append(1)
        reading.exam.main = lambda argv: 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield paused
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)
            reading.exam.main = real_exam_main

    def test_the_pager_swallowed_it_so_we_do_not_pause(self):
        """평상시 경로다 — `less`가 있으면 여기로 온다."""
        with self._flow([0, 0, 0], printed=False) as paused:
            reading.main([])
        self.assertEqual(paused, [], "페이저가 삼켰는데도 멈췄다")

    def test_a_plain_text_fallback_still_pauses(self):
        """`$PAGER`도 `less`도 없으면 본문이 그대로 찍힌다 — 지켜야 한다."""
        with self._flow([0, 0, 0], printed=True) as paused:
            reading.main([])
        self.assertEqual(paused, [1])

    def test_taking_the_exam_pauses_even_when_the_pager_swallowed_it(self):
        """`exam.main`이 평문을 남겼을 수 있다 — 안전한 쪽으로 떨어진다.

        1=PostgreSQL, 1=02-intermediate, 1=그 티어의 두 번째 챕터(은행 있음).
        `offer_exam`이 '예'라고 답하도록 고정한다.
        """
        # 픽 인덱스 [1, 1, 1]이 실제로 가리키는 챕터를 `discover_chapters`로
        # 다시 구해 은행이 있는지 미리 확인한다 — `offer_exam`을 `True`로
        # 고정해 뒀는데 `exam.exam_bank_for`는 스텁하지 않았으므로, 파일
        # 목록이 바뀌어 이 슬롯에 은행 없는 챕터가 들어오면
        # `args = [str(exam.REPO_ROOT / bank)]`에서 `bank`가 `None`이 되어
        # 멈춤 여부와 무관한 자리에서 뜻 모를 `TypeError`가 난다. 여기서
        # 미리 걸리면 실패 메시지가 "은행이 없다"는 진짜 원인을 바로 보여준다.
        chapter = reading.discover_chapters("02-intermediate")[1]
        self.assertIsNotNone(
            reading.exam.exam_bank_for(chapter),
            f"{chapter}에 문제은행이 없다 — 픽 인덱스 [1, 1, 1]이 가리키는 "
            "챕터가 바뀌었다면 이 테스트의 인덱스를 은행 있는 챕터로 옮겨야 한다")
        with self._flow([1, 1, 1], printed=False, took_exam=True) as paused:
            reading.main([])
        self.assertEqual(paused, [1])


class ReadingQuitKeyTest(unittest.TestCase):
    """선택 화면이 종료 키를 안내해야 한다 — 없는 것처럼 보이면 없는 것이다."""

    def test_the_footer_offers_both_back_and_quit(self):
        seen = {}
        fake_curses = types.SimpleNamespace(
            curs_set=lambda _n: None,
            wrapper=lambda driver: driver(object()))

        def fake_pick(_stdscr, _curses, _title, _labels, footer=None, **_kw):
            seen["footer"] = footer
            return 0

        real_pick = reading.pick
        real_curses = sys.modules.get("curses")
        real_in, real_out = sys.stdin.isatty, sys.stdout.isatty
        reading.pick = fake_pick
        sys.modules["curses"] = fake_curses
        sys.stdin.isatty = lambda: True
        sys.stdout.isatty = lambda: True
        try:
            reading.choose("제목", ["a", "b"])
        finally:
            reading.pick = real_pick
            if real_curses is None:
                del sys.modules["curses"]
            else:
                sys.modules["curses"] = real_curses
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out

        self.assertIn("Esc/q 뒤로", seen["footer"], seen)
        self.assertIn("Q 종료", seen["footer"], seen)

    def test_running_it_standalone_survives_a_quit(self):
        """`__main__` 가드가 없으면 `Q` 한 번에 트레이스백이 뜬다.

        여기만 소스를 읽는다 — 파이프로 실행하면 비-tty라 `pick_line` 경로로
        떨어지고, 그 경로에는 `Q`가 없어서(설계상 범위 밖) 동작으로는 이
        가드에 도달할 방법이 없다. `test_the_launcher_points_at_the_script`가
        런처 내용을 읽는 것과 같은 종류의 검사다.
        """
        body = (REPO_ROOT / "scripts" / "reading.py").read_text(
            encoding="utf-8")
        self.assertIn("except QuitApp", body)

    def test_running_it_standalone_survives_ctrl_c(self):
        """`shooting.py`의 `__main__` 블록은 `KeyboardInterrupt`도 잡는데
        여기는 빠져 있었다 — `python3 scripts/reading.py`를 직접 돌리다 Ctrl-C를
        누르면 트레이스백이 떴다. 위 테스트와 같은 이유로 소스만 읽는다.
        """
        body = (REPO_ROOT / "scripts" / "reading.py").read_text(
            encoding="utf-8")
        self.assertIn("except KeyboardInterrupt", body)
