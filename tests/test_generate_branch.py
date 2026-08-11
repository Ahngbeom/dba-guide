#!/usr/bin/env python3
"""generate-branch.sh 통합 테스트.

임시 디렉터리에 만든 저장소를 대상으로 진짜 스크립트를 실행한다. 네트워크도
`main` 저장소의 상태도 건드리지 않는다.

`install.sh`와 달리 스크립트를 **복사하지 않는다.** 이 스크립트는 두 경로를
따로 구한다 — `script_dir`은 `${BASH_SOURCE[0]}`에서(옆에 있는
`filter_dbms.py`를 부르려고), `repo_root`는 cwd의 `git rev-parse
--show-toplevel`에서. 그래서 진짜 스크립트를 임시 저장소를 cwd로 실행하면
필터는 제대로 찾으면서 대상 저장소만 임시본이 된다. 복사하면 반대로
`filter_dbms.py`를 잃는다.

실행:
    python3 -m unittest discover -s tests
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATE_SH = REPO_ROOT / "scripts" / "generate-branch.sh"

CHAPTER = """\
# 1장

공통 문장이다.

<!-- dbms:postgresql -->
PostgreSQL 전용 문단.
<!-- /dbms:postgresql -->

<!-- dbms:mysql -->
MySQL 전용 문단.
<!-- /dbms:mysql -->

<!-- dbms:oracle -->
Oracle 전용 문단.
<!-- /dbms:oracle -->
"""

# 비교 표는 일부러 마킹하지 않는다 — 모든 브랜치에서 그대로 지나가야 한다.
UNMARKED = """\
# 용어집

| 용어 | PostgreSQL | MySQL | Oracle |
|---|---|---|---|
| 락 | ROW SHARE | InnoDB row lock | TX lock |
"""


def git(cwd, *args):
    """저장소 준비용 git 호출. 실패하면 즉시 드러나도록 check=True."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


class GenerateBranchTestCase(unittest.TestCase):
    """`main` 브랜치와 마킹된 챕터를 갖춘 최소 저장소.

    `resolve()`로 실제 경로를 쓴다. 스크립트가 워크트리 자리를 `cd .. && pwd`
    로 계산한 뒤 `git worktree list`의 기록과 문자열로 대조하기 때문에,
    macOS의 `/var` → `/private/var` 같은 심볼릭 링크가 섞이면 재실행 시
    묵은 워크트리를 못 알아본다.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dba-guide-branch-")).resolve()
        self.addCleanup(self._cleanup)
        self.repo = self.tmp / "guide"
        self._make_repo()

    def _cleanup(self):
        # 워크트리를 남긴 채 지우면 임시 저장소가 사라져도 등록만 남는다.
        # 임시 저장소 자체를 통째로 버리므로 prune 은 필요 없지만, 실패한
        # 테스트가 남긴 워크트리 디렉터리까지 확실히 치운다.
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_repo(self):
        (self.repo / "01-beginner").mkdir(parents=True)
        (self.repo / "appendix").mkdir()
        (self.repo / "01-beginner" / "01-intro.md").write_text(
            CHAPTER, encoding="utf-8")
        (self.repo / "appendix" / "glossary.md").write_text(
            UNMARKED, encoding="utf-8")
        (self.repo / "run.sh").write_text("echo hi\n", encoding="utf-8")
        git(self.repo, "init", "--quiet")
        git(self.repo, "config", "user.email", "test@example.com")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "initial")
        # 기본 브랜치 이름은 git 설정에 따라 갈린다. 스크립트는 `main`을
        # 이름으로 참조하므로 여기서 못박는다.
        git(self.repo, "branch", "-M", "main")

    def run_script(self, *args, cwd=None):
        return subprocess.run(
            ["bash", str(GENERATE_SH), *args],
            cwd=str(cwd or self.repo), capture_output=True, text=True,
        )

    def worktree(self, dbms):
        return self.tmp / f"guide-{dbms}"

    def chapter_in(self, dbms):
        return (self.worktree(dbms) / "01-beginner" / "01-intro.md").read_text(
            encoding="utf-8")


class ArgumentTest(GenerateBranchTestCase):
    """대상 DBMS를 잘못 주면 아무것도 만들지 않고 멈춘다."""

    def test_no_argument_prints_usage(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("usage:", r.stderr)

    def test_unknown_dbms_prints_usage(self):
        r = self.run_script("mariadb")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("usage:", r.stderr)

    def test_rejected_argument_creates_no_worktree(self):
        self.run_script("mariadb")
        self.assertFalse(self.worktree("mariadb").exists())
        self.assertNotIn("mariadb", git(self.repo, "branch", "--list"))


class DirtyTreeTest(GenerateBranchTestCase):
    """더티 트리에서는 시작하지 않는다.

    워크트리를 `main`으로 리셋해 만드는 스크립트라, 커밋되지 않은 작업이
    있는 채로 돌면 그 작업이 결과에 반영되지 않은 채 브랜치가 만들어진다.
    """

    def test_refuses_when_tree_is_dirty(self):
        (self.repo / "01-beginner" / "01-intro.md").write_text(
            CHAPTER + "\n작업 중\n", encoding="utf-8")
        r = self.run_script("mysql")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("working tree is not clean", r.stderr)
        self.assertFalse(self.worktree("mysql").exists())

    def test_untracked_file_also_counts_as_dirty(self):
        (self.repo / "메모.md").write_text("임시\n", encoding="utf-8")
        r = self.run_script("mysql")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertFalse(self.worktree("mysql").exists())


class FilterTest(GenerateBranchTestCase):
    """생성된 뷰의 내용."""

    def setUp(self):
        super().setUp()
        self.result = self.run_script("mysql")
        self.assertEqual(self.result.returncode, 0,
                         self.result.stdout + self.result.stderr)

    def test_keeps_only_the_target_vendor(self):
        body = self.chapter_in("mysql")
        self.assertIn("MySQL 전용 문단.", body)
        self.assertNotIn("PostgreSQL 전용 문단.", body)
        self.assertNotIn("Oracle 전용 문단.", body)

    def test_strips_the_marker_comments_themselves(self):
        self.assertNotIn("<!-- dbms:", self.chapter_in("mysql"))

    def test_keeps_unmarked_prose(self):
        self.assertIn("공통 문장이다.", self.chapter_in("mysql"))

    def test_unmarked_file_is_byte_identical(self):
        """비교 표와 치트시트는 의도적으로 마킹하지 않는다 — 손대면 안 된다."""
        after = (self.worktree("mysql") / "appendix" / "glossary.md").read_bytes()
        self.assertEqual(after, UNMARKED.encode("utf-8"))

    def test_non_markdown_files_pass_through(self):
        after = (self.worktree("mysql") / "run.sh").read_text(encoding="utf-8")
        self.assertEqual(after, "echo hi\n")

    def test_commits_the_result_on_the_vendor_branch(self):
        head = git(self.worktree("mysql"), "rev-parse", "--abbrev-ref", "HEAD")
        self.assertEqual(head.strip(), "mysql")
        message = git(self.worktree("mysql"), "log", "-1", "--pretty=%s")
        main_sha = git(self.repo, "rev-parse", "main").strip()[:12]
        self.assertEqual(message.strip(),
                         f"Regenerate mysql view from main@{main_sha}")

    def test_leaves_no_uncommitted_change_in_the_worktree(self):
        self.assertEqual(git(self.worktree("mysql"), "status", "--short"), "")


class SourceRepoTest(GenerateBranchTestCase):
    """원본 저장소는 건드리지 않는다."""

    def test_does_not_move_head(self):
        """기여자의 브랜치를 옮겨 놓으면 안 된다.

        `install.sh`가 in-place 모드에서 HEAD를 건드리지 않는 것과 같은 이유다
        — 릴리스 절차 한가운데서 남의 작업 위치가 바뀌면 알아차리기 어렵다.
        """
        before = git(self.repo, "rev-parse", "HEAD").strip()
        branch_before = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.run_script("oracle")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), before)
        self.assertEqual(
            git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip(),
            branch_before)

    def test_leaves_the_source_tree_clean(self):
        self.run_script("oracle")
        self.assertEqual(git(self.repo, "status", "--short"), "")

    def test_does_not_push(self):
        """스크립트 주석이 'Does NOT push'라고 약속한다.

        원격이 붙어 있어도 벤더 브랜치가 저절로 올라가면 안 된다 — 사람이
        결과 워크트리를 확인한 뒤 직접 올리는 것이 규약이다.
        """
        bare = self.tmp / "origin.git"
        subprocess.run(["git", "init", "--quiet", "--bare", str(bare)],
                       check=True, capture_output=True)
        git(self.repo, "remote", "add", "origin", str(bare))
        git(self.repo, "push", "--quiet", "origin", "main")

        self.run_script("mysql")

        refs = subprocess.run(["git", "-C", str(bare), "show-ref"],
                              capture_output=True, text=True).stdout
        self.assertIn("refs/heads/main", refs)
        self.assertNotIn("refs/heads/mysql", refs)


class WorkingDirectoryTest(GenerateBranchTestCase):
    """저장소 안 어디서 실행해도 결과가 같아야 한다.

    스크립트는 `git rev-parse --show-toplevel`로 루트를 찾은 뒤 거기로
    `cd` 한다. 루트에서만 돌리는 테스트는 cwd가 숨은 입력이라는 사실을
    영영 못 본다 — 같은 저장소에서 `exam`의 경로 해석이 그렇게 깨져 있었다.
    """

    def test_runs_from_a_subdirectory(self):
        r = self.run_script("mysql", cwd=self.repo / "01-beginner")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MySQL 전용 문단.", self.chapter_in("mysql"))
        self.assertNotIn("Oracle 전용 문단.", self.chapter_in("mysql"))


class RegenerateTest(GenerateBranchTestCase):
    """두 번째 실행 — 릴리스마다 반복되는 경로다."""

    def test_stale_worktree_is_replaced(self):
        first = self.run_script("mysql")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_script("mysql")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("removing stale worktree", second.stdout)
        self.assertIn("MySQL 전용 문단.", self.chapter_in("mysql"))

    def test_does_not_accumulate_commits(self):
        """재생성해도 벤더 브랜치는 항상 `main` + 커밋 하나다.

        스크립트가 `-B`로 브랜치를 매번 `main`으로 리셋하기 때문이다. 이
        성질이 깨지면 파생 뷰에 히스토리가 쌓이고, 릴리스 문서가 안내하는
        `push --force-with-lease`의 전제도 함께 무너진다.
        """
        for _ in range(3):
            r = self.run_script("mysql")
            self.assertEqual(r.returncode, 0, r.stderr)
            ahead = git(self.repo, "rev-list", "--count", "main..mysql").strip()
            self.assertEqual(ahead, "1")

    def test_no_commit_when_there_is_nothing_to_filter(self):
        """마커가 하나도 없으면 커밋할 것이 없다.

        아직 마킹되지 않은 챕터는 세 DBMS를 모두 보여주는 것이 의도된
        안전한 기본값이므로, 그 상태를 '변경 없음'으로 지나가야 한다.
        """
        (self.repo / "01-beginner" / "01-intro.md").write_text(
            "# 1장\n\n마커가 없는 본문.\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "unmarked chapter")

        r = self.run_script("mysql")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no changes after filtering", r.stdout)
        self.assertEqual(
            git(self.repo, "rev-parse", "mysql").strip(),
            git(self.repo, "rev-parse", "main").strip())

    def test_picks_up_a_new_main_commit(self):
        self.run_script("mysql")
        (self.repo / "01-beginner" / "02-next.md").write_text(
            "# 2장\n\n<!-- dbms:mysql -->\n새 MySQL 문단.\n"
            "<!-- /dbms:mysql -->\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "add chapter 2")

        r = self.run_script("mysql")
        self.assertEqual(r.returncode, 0, r.stderr)
        added = (self.worktree("mysql") / "01-beginner" / "02-next.md").read_text(
            encoding="utf-8")
        self.assertIn("새 MySQL 문단.", added)
        self.assertNotIn("<!-- dbms:", added)


class BrokenMarkerTest(GenerateBranchTestCase):
    """마커가 깨졌으면 조용히 반쪽짜리 뷰를 만들지 말고 멈춰야 한다.

    `filter_dbms.py`는 불균형 마커에 1을 돌려준다. 스크립트의 `set -e`가
    그것을 받아 중단시킨다 — 이 연결이 끊기면 잘려 나간 챕터가 그대로
    커밋된다.
    """

    def _commit_broken_chapter(self):
        (self.repo / "01-beginner" / "01-intro.md").write_text(
            "# 1장\n\n<!-- dbms:mysql -->\n닫히지 않았다.\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "broken marker")

    def test_aborts_on_unbalanced_marker(self):
        self._commit_broken_chapter()
        r = self.run_script("mysql")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("Unclosed dbms marker", r.stderr)

    def test_does_not_commit_the_broken_view(self):
        self._commit_broken_chapter()
        self.run_script("mysql")
        # 워크트리는 만들어진 뒤 중단되므로 남아 있을 수 있다. 중요한 것은
        # 잘린 내용이 벤더 브랜치의 커밋으로 굳지 않는 것이다.
        if self.worktree("mysql").exists():
            message = git(self.worktree("mysql"), "log", "-1", "--pretty=%s")
            self.assertNotIn("Regenerate", message)


if __name__ == "__main__":
    unittest.main()
