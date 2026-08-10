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
