#!/usr/bin/env python3
"""install.sh 통합 테스트.

임시 디렉터리에 만든 저장소를 `file://` 원본으로 삼아 install.sh를 실제로
실행한다. 네트워크를 쓰지 않는다.

실행:
    python3 -m unittest discover -s tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


def git(cwd, *args):
    """저장소 준비용 git 호출. 실패하면 즉시 드러나도록 check=True."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


class InstallerTestCase(unittest.TestCase):
    """임시 HOME과 file:// 원본을 갖춘 격리 환경.

    install.sh를 **복사해서** 실행하는 것이 핵심이다. 저장소 안의 원본을
    그대로 실행하면 `${BASH_SOURCE[0]}`가 진짜 dba-guide 저장소를 가리켜
    in-place 모드로 빠지고, managed 모드 테스트가 전부 무의미해진다.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dba-guide-install-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.script = self.tmp / "install.sh"
        shutil.copy2(INSTALL_SH, self.script)
        self.origin = self.tmp / "origin"
        self._make_origin()

    def _make_origin(self):
        """런처 세 개와 scripts/guide.py를 갖춘 최소 저장소."""
        (self.origin / "scripts").mkdir(parents=True)
        (self.origin / "scripts" / "guide.py").write_text("# stub\n")
        for name in ("guide", "exam", "shoot"):
            p = self.origin / name
            p.write_text("#!/usr/bin/env bash\necho %s\n" % name)
            p.chmod(0o755)
        git(self.origin, "init", "--quiet")
        git(self.origin, "config", "user.email", "test@example.com")
        git(self.origin, "config", "user.name", "test")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "--quiet", "-m", "initial")

    def tag(self, name):
        git(self.origin, "tag", name)

    def commit(self, message="more"):
        (self.origin / "NOTES.md").write_text(message + "\n")
        git(self.origin, "add", "-A")
        git(self.origin, "commit", "--quiet", "-m", message)

    def run_installer(self, *args, stdin=""):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        env["DBA_GUIDE_REPO_URL"] = self.origin.as_uri()
        return subprocess.run(
            ["bash", str(self.script), *args],
            env=env, cwd=str(self.tmp),
            input=stdin, capture_output=True, text=True,
        )

    @property
    def install_dir(self):
        return self.home / ".local" / "share" / "dba-guide"

    @property
    def bin_dir(self):
        return self.home / ".local" / "bin"

    def head_tag(self):
        return git(self.install_dir, "describe", "--tags", "--exact-match").strip()


class ArgumentTest(InstallerTestCase):
    """인자 파싱은 오용을 조용히 통과시키지 않아야 한다."""

    def test_help_exits_zero_and_explains_the_three_modes(self):
        r = self.run_installer("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--uninstall", r.stdout)
        self.assertIn("--purge", r.stdout)

    def test_unknown_option_fails(self):
        r = self.run_installer("--nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--nope", r.stderr)

    def test_purge_without_uninstall_is_refused(self):
        """--purge 단독은 오용이다. 조용히 설치로 흘러가면 안 된다."""
        r = self.run_installer("--purge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--uninstall", r.stderr)
        self.assertFalse(self.install_dir.exists())


class FreshInstallTest(InstallerTestCase):
    """빈 상태에서의 첫 설치."""

    def test_installs_latest_release_tag_and_links_three_launchers(self):
        self.tag("v1.0.0")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head_tag(), "v1.0.0")
        for name in ("guide", "exam", "shoot"):
            link = self.bin_dir / name
            self.assertTrue(link.is_symlink(), f"{name} 링크가 없다")
            self.assertEqual(os.readlink(link), str(self.install_dir / name))

    def test_prerelease_tags_are_ignored(self):
        """정렬만으로는 v9.9.9-rc.1이 위로 올 수 있다 — 패턴 필터가 있어야 한다."""
        self.tag("v1.0.0")
        self.commit("prerelease work")
        self.tag("v9.9.9-rc.1")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head_tag(), "v1.0.0")

    def test_no_release_tag_is_a_hard_stop(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn("태그", r.stderr)

    def test_reports_path_guidance_when_bin_dir_is_not_on_path(self):
        self.tag("v1.0.0")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PATH", r.stdout)
        self.assertIn(str(self.bin_dir), r.stdout)


class UpdateTest(InstallerTestCase):
    """두 번째 실행부터의 동작."""

    def test_rerun_is_idempotent(self):
        self.tag("v1.0.0")
        self.assertEqual(self.run_installer().returncode, 0)
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("최신", r.stdout)
        self.assertEqual(self.head_tag(), "v1.0.0")

    def test_moves_to_a_newer_tag(self):
        self.tag("v1.0.0")
        self.assertEqual(self.run_installer().returncode, 0)
        self.commit("next release")
        self.tag("v1.1.0")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head_tag(), "v1.1.0")

    def test_local_modification_stops_the_update(self):
        """설치본을 고쳐 둔 사람의 작업을 말없이 덮지 않는다."""
        self.tag("v1.0.0")
        self.assertEqual(self.run_installer().returncode, 0)
        (self.install_dir / "guide").write_text("#!/usr/bin/env bash\necho mine\n")
        self.commit("next release")
        self.tag("v1.1.0")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn("로컬 수정", r.stderr)
        self.assertEqual(self.head_tag(), "v1.0.0")
        self.assertIn("mine", (self.install_dir / "guide").read_text())

    def test_untracked_files_do_not_block_the_update(self):
        """학습 기록은 gitignore 대상이지만, 그 외 미추적 파일도 막지 않는다."""
        self.tag("v1.0.0")
        self.assertEqual(self.run_installer().returncode, 0)
        (self.install_dir / "MEMO.txt").write_text("메모\n")
        self.commit("next release")
        self.tag("v1.1.0")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head_tag(), "v1.1.0")
        self.assertTrue((self.install_dir / "MEMO.txt").exists())


class GuardTest(InstallerTestCase):
    """자기가 만들지 않은 것을 지우거나 덮지 않는다."""

    def test_existing_plain_file_named_guide_blocks_and_survives(self):
        self.tag("v1.0.0")
        self.bin_dir.mkdir(parents=True)
        victim = self.bin_dir / "guide"
        victim.write_text("남의 스크립트\n")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(victim), r.stderr)
        self.assertFalse(victim.is_symlink())
        self.assertEqual(victim.read_text(), "남의 스크립트\n")

    def test_all_conflicts_are_reported_at_once(self):
        """하나씩 죽으면 사용자가 같은 실패를 세 번 겪는다."""
        self.tag("v1.0.0")
        self.bin_dir.mkdir(parents=True)
        for name in ("guide", "exam", "shoot"):
            (self.bin_dir / name).write_text("남의 것\n")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        for name in ("guide", "exam", "shoot"):
            self.assertIn(str(self.bin_dir / name), r.stderr)

    def test_non_git_directory_at_install_path_is_a_hard_stop(self):
        self.tag("v1.0.0")
        self.install_dir.mkdir(parents=True)
        (self.install_dir / "중요.txt").write_text("남의 자료\n")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.install_dir), r.stderr)
        self.assertTrue((self.install_dir / "중요.txt").exists())

    def test_directory_inside_an_outer_git_repo_is_still_a_hard_stop(self):
        """홈을 git으로 관리하는 사람 — 설치 경로가 남의 저장소 안에 들어앉는다.

        `rev-parse --is-inside-work-tree`는 상위로 올라가며 `.git`을 찾으므로
        이 경우 참을 돌려준다. 그대로 통과시키면 이어지는 fetch·checkout이
        **바깥 저장소**를 대상으로 돈다.
        """
        self.tag("v1.0.0")
        git(self.home, "init", "--quiet")
        self.install_dir.mkdir(parents=True)
        (self.install_dir / "중요.txt").write_text("남의 자료\n")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.install_dir), r.stderr)
        self.assertTrue((self.install_dir / "중요.txt").exists())


class InPlaceTest(InstallerTestCase):
    """이미 클론한 저장소 안에서 실행한 경우."""

    def _clone(self):
        clone = self.tmp / "work"
        subprocess.run(["git", "clone", "--quiet", self.origin.as_uri(), str(clone)],
                       check=True, capture_output=True, text=True)
        shutil.copy2(INSTALL_SH, clone / "install.sh")
        return clone

    def _run_in(self, clone):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        env["DBA_GUIDE_REPO_URL"] = self.origin.as_uri()
        return subprocess.run(["bash", str(clone / "install.sh")],
                              env=env, cwd=str(clone),
                              input="", capture_output=True, text=True)

    def test_links_to_the_clone_and_does_not_move_head(self):
        self.tag("v1.0.0")
        self.commit("unreleased work")
        clone = self._clone()
        before = git(clone, "rev-parse", "HEAD").strip()
        r = self._run_in(clone)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(clone, "rev-parse", "HEAD").strip(), before)
        self.assertEqual(os.readlink(self.bin_dir / "guide"), str(clone / "guide"))
        self.assertFalse(self.install_dir.exists())

    def test_a_git_repo_without_the_launchers_is_not_treated_as_in_place(self):
        """이름만 같은 남의 저장소를 설치본으로 오인하면 안 된다."""
        self.tag("v1.0.0")
        stranger = self.tmp / "stranger"
        stranger.mkdir()
        git(stranger, "init", "--quiet")
        shutil.copy2(INSTALL_SH, stranger / "install.sh")
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        env["DBA_GUIDE_REPO_URL"] = self.origin.as_uri()
        r = subprocess.run(["bash", str(stranger / "install.sh")],
                           env=env, cwd=str(stranger),
                           input="", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.install_dir.exists())
        self.assertEqual(os.readlink(self.bin_dir / "guide"),
                         str(self.install_dir / "guide"))
