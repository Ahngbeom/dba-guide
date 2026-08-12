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
import tui  # noqa: E402


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


class ChapterLabelTest(unittest.TestCase):
    """목록만 보고 어느 챕터에 시험이 있고 지난 기록이 어떤지 알 수 있어야 한다.

    조용한 쪽이 기본이다. 은행이 있는 챕터가 23개로 다수라 거기 전부
    `[시험 있음]`을 붙이면 그게 잡음이 된다 — 소수인 '없음'과 실제 기록만
    표시한다.
    """

    WITH_BANK = "02-intermediate/01-transaction-and-locking.md"
    NO_BANK = "01-beginner/00-overview.md"

    def setUp(self):
        # 고정값이 틀리면 아래 단언들이 엉뚱한 것을 증명한다.
        self.assertIsNotNone(reading.exam.exam_bank_for(self.WITH_BANK),
                             f"{self.WITH_BANK} 에 은행이 없다")
        self.assertIsNone(reading.exam.exam_bank_for(self.NO_BANK),
                          f"{self.NO_BANK} 에 은행이 생겼다")

    def test_a_chapter_without_a_bank_says_so(self):
        self.assertEqual(reading.chapter_labels([self.NO_BANK], []),
                         ["00-overview.md   [시험 없음]"])

    def test_a_banked_chapter_with_no_record_stays_quiet(self):
        self.assertEqual(reading.chapter_labels([self.WITH_BANK], []),
                         ["01-transaction-and-locking.md"])

    def test_a_record_is_appended_in_the_exam_modules_wording(self):
        """같은 정보가 두 화면에서 다르게 보이면 안 된다.

        `exam._chapter_labels`가 쓰는 서식 그대로다.
        """
        records = [{"chapter": self.WITH_BANK, "auto_total": 10,
                    "score": 0.92, "grade": "A"}]
        self.assertEqual(
            reading.chapter_labels([self.WITH_BANK], records),
            ["01-transaction-and-locking.md   [지난 최고 A·92%]"])

    def test_a_record_for_another_chapter_does_not_leak(self):
        records = [{"chapter": "01-beginner/99-없는챕터.md", "auto_total": 10,
                    "score": 0.92, "grade": "A"}]
        self.assertEqual(reading.chapter_labels([self.WITH_BANK], records),
                         ["01-transaction-and-locking.md"])

    def test_it_keeps_the_given_order(self):
        got = reading.chapter_labels([self.NO_BANK, self.WITH_BANK], [])
        self.assertEqual(got[0], "00-overview.md   [시험 없음]")
        self.assertEqual(got[1], "01-transaction-and-locking.md")


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
    def _flow(self, picks, action=None, printed=False):
        """DBMS → 티어 → 챕터 한 바퀴를 돌린다.

        `action`은 챕터 화면에서 누를 동작 키(`"x"` 또는 `None`). 가짜 `choose`는
        진짜와 같은 계약을 지킨다 — `actions`를 받았을 때만 `Picked`를 돌려준다.
        """
        seq = iter(list(picks) + [None] * 6)
        read, exams, paused = [], [], []
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "run_exam",
                          "pause_after_output")}

        def fake_choose(title, labels, actions=""):
            idx = next(seq)
            if not actions or idx is None:
                return idx
            return reading.Picked(idx, action)

        reading.choose = fake_choose
        reading.read_chapter = (
            lambda rel, dbms=None: read.append((rel, dbms)) or printed)
        reading.run_exam = (
            lambda rel, bank, dbms: exams.append((rel, bank, dbms)))
        reading.pause_after_output = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield read, exams, paused
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)

    def test_dbms_then_tier_then_chapter_reaches_the_reader(self):
        # 0=전체, 0=01-beginner, 0=그 티어의 첫 챕터
        with self._flow([0, 0, 0]) as (read, exams, _):
            self.assertEqual(reading.main([]), 0)
        self.assertEqual(len(read), 1)
        self.assertTrue(read[0][0].startswith("01-beginner/"))
        self.assertIsNone(read[0][1])          # '전체'는 필터 없음
        self.assertEqual(exams, [], "Enter는 읽기다 — 시험이 아니다")

    def test_choosing_a_vendor_passes_it_through(self):
        with self._flow([1, 0, 0]) as (read, _, _):
            reading.main([])
        self.assertEqual(read[0][1], "postgresql")

    def test_quitting_at_the_first_screen_reads_nothing(self):
        with self._flow([]) as (read, exams, _):
            self.assertEqual(reading.main([]), 0)
        self.assertEqual(read, [])
        self.assertEqual(exams, [])

    def test_the_action_key_runs_that_chapters_exam_instead_of_reading(self):
        """1=PostgreSQL, 1=02-intermediate, 1=은행이 있는 챕터.

        `discover_chapters`는 파일명 순이라 인덱스 1은 `00-overview.md` 다음,
        즉 `01-transaction-and-locking.md`다. 고정값이 흔들리면 아래
        `assertIsNotNone`이 먼저 알려 준다.
        """
        rel = reading.discover_chapters("02-intermediate")[1]
        self.assertIsNotNone(reading.exam.exam_bank_for(rel),
                             f"고정값이 틀렸다 — {rel} 에 은행이 없다")
        with self._flow([1, 1, 1], action="x") as (read, exams, paused):
            reading.main([])
        self.assertEqual(read, [], "시험을 골랐는데 챕터를 읽었다")
        self.assertEqual(len(exams), 1)
        self.assertEqual(exams[0][0], rel)
        self.assertIsNotNone(exams[0][1])
        self.assertEqual(exams[0][2], "postgresql", "고른 벤더를 흘렸다")
        self.assertEqual(paused, [1], "시험 뒤에는 평문이 남을 수 있다")

    def test_the_action_key_does_nothing_on_a_chapter_without_a_bank(self):
        """0=전체, 0=01-beginner, 0=`00-overview.md`(은행 없음).

        그 행이 이미 `[시험 없음]`이라 화면이 이유를 적고 있다.
        """
        rel = reading.discover_chapters("01-beginner")[0]
        self.assertIsNone(reading.exam.exam_bank_for(rel),
                          f"고정값이 틀렸다 — {rel} 에 은행이 생겼다")
        with self._flow([0, 0, 0], action="x") as (read, exams, paused):
            reading.main([])
        self.assertEqual(exams, [], "은행이 없는데 시험을 열었다")
        self.assertEqual(read, [], "시험 키를 눌렀는데 챕터를 읽었다")
        self.assertEqual(paused, [], "아무 일도 안 했는데 멈췄다")

    def test_the_action_key_comes_back_to_the_chapter_list(self):
        """시험을 보고 나면 목록으로 돌아와 다음 챕터를 고를 수 있어야 한다."""
        with self._flow([1, 1, 1, 1], action="x") as (_, exams, _p):
            reading.main([])
        self.assertEqual(len(exams), 2, "한 번 보고 목록을 떠났다")


class ChapterPauseTest(unittest.TestCase):
    """이슈 #95 — 챕터를 읽을 때마다 뜻 없는 'Enter'를 요구하지 않는다.

    `pause_after_output()`은 "다음 curses 프레임의 `erase()`가 방금 찍힌 평문을
    한 프레임도 못 읽히고 지우는 것"을 막으려고 있다. `less`가 본문을 삼켰다면
    지킬 평문이 애초에 없다 — 그런데도 무조건 불러서, 챕터를 하나 읽을 때마다
    Enter를 한 번씩 눌러야 했다.
    """

    @contextlib.contextmanager
    def _flow(self, picks, printed=False):
        """선택 → 읽기 한 바퀴를 돌리고 pause 호출을 센다."""
        seq = iter(list(picks) + [None] * 6)
        paused = []
        real = {n: getattr(reading, n)
                for n in ("choose", "read_chapter", "pause_after_output")}

        def fake_choose(title, labels, actions=""):
            idx = next(seq)
            if not actions or idx is None:
                return idx
            return reading.Picked(idx, None)

        reading.choose = fake_choose
        reading.read_chapter = lambda rel, dbms=None: printed
        reading.pause_after_output = lambda: paused.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield paused
        finally:
            for n, fn in real.items():
                setattr(reading, n, fn)

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


class RunExamTest(unittest.TestCase):
    """핸드오프 인자 계약. 전에는 `main` 안에 인라인으로 있었다.

    `exam.main`은 대상을 cwd 기준 상대경로로 받는다(CLI 계약). `./guide`를
    저장소 밖 cwd에서 띄운 경우에도 은행을 찾으려면 절대경로여야 하고, 고른
    벤더를 흘리면 PostgreSQL 챕터를 읽고도 MySQL·Oracle 문항이 다 나온다.
    """

    CHAPTER = "02-intermediate/01-transaction-and-locking.md"

    @contextlib.contextmanager
    def _capture(self):
        captured = {}
        real = reading.exam.main
        reading.exam.main = lambda argv: captured.setdefault("argv", argv) or 0
        try:
            yield captured
        finally:
            reading.exam.main = real

    def test_it_passes_an_absolute_bank_path(self):
        bank = reading.exam.exam_bank_for(self.CHAPTER)
        with self._capture() as captured:
            reading.run_exam(self.CHAPTER, bank, None)
        argv = captured["argv"]
        self.assertTrue(Path(argv[0]).is_absolute(), argv)
        self.assertTrue(argv[0].startswith(str(REPO_ROOT)), argv)
        self.assertTrue(argv[0].endswith(".json"), argv)

    def test_it_forwards_the_chosen_vendor(self):
        bank = reading.exam.exam_bank_for(self.CHAPTER)
        with self._capture() as captured:
            reading.run_exam(self.CHAPTER, bank, "postgresql")
        self.assertEqual(captured["argv"][1:], ["--dbms", "postgresql"])

    def test_the_whole_choice_omits_the_flag(self):
        """`dbms`가 `None`('전체')이면 `exam`이 스스로 묻게 둔다."""
        bank = reading.exam.exam_bank_for(self.CHAPTER)
        with self._capture() as captured:
            reading.run_exam(self.CHAPTER, bank, None)
        self.assertEqual(captured["argv"][1:], [])


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

    def _footer(self, **kw):
        """`choose`가 `pick`에 실제로 넘긴 footer를 가로챈다."""
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
            reading.choose("제목", ["a", "b"], **kw)
        finally:
            reading.pick = real_pick
            if real_curses is None:
                del sys.modules["curses"]
            else:
                sys.modules["curses"] = real_curses
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out
        return seen["footer"]

    def test_the_chapter_footer_offers_the_exam_action(self):
        footer = self._footer(actions="x")
        self.assertIn("Enter 읽기", footer)
        self.assertIn("x 시험", footer)
        self.assertIn("Esc/q 뒤로", footer)
        self.assertIn("Q 종료", footer)

    def test_a_screen_without_actions_says_nothing_about_the_exam(self):
        """없는 키를 안내하면 안내가 거짓말이 된다."""
        footer = self._footer()
        self.assertIn("Enter 선택", footer)
        self.assertNotIn("시험", footer)

    def test_the_chapter_footer_fits_an_eighty_column_terminal(self):
        """`tui.bar`가 잘라내면 안내가 조용히 사라진다.

        `bar`는 폭 `w-1`로 자르므로 80칸 터미널에서 쓸 수 있는 것은 79칸이다.
        """
        self.assertLessEqual(tui.cwidth(self._footer(actions="x")), 79)

    def test_the_line_fallback_wraps_its_answer_in_picked(self):
        """평문 선택기는 동작 키를 모른다 — `choose`가 계약만 맞춰 준다."""
        real_pick_line = reading.pick_line
        real_in, real_out = sys.stdin.isatty, sys.stdout.isatty
        reading.pick_line = lambda title, labels: 1
        sys.stdin.isatty = lambda: False
        sys.stdout.isatty = lambda: False
        try:
            self.assertEqual(reading.choose("제목", ["a", "b"], actions="x"),
                             reading.Picked(1, None))
            reading.pick_line = lambda title, labels: None
            self.assertIsNone(reading.choose("제목", ["a", "b"], actions="x"))
        finally:
            reading.pick_line = real_pick_line
            sys.stdin.isatty, sys.stdout.isatty = real_in, real_out

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

    def test_the_docs_explain_the_exam_action(self):
        """안내 없는 단축키는 없는 것과 같다.

        `README.md` 의 "한 번에 시작하기" 절이 `./guide` 흐름을 산문으로
        설명하는 유일한 자리다.
        """
        body = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`x`", body, "README에 시험 단축키 안내가 없다")
        self.assertNotIn("시험을 볼지 물은", body,
                         "없어진 [Y/n] 프롬프트를 README가 아직 설명한다")
