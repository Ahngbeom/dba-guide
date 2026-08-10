#!/usr/bin/env python3
"""install.sh 통합 테스트.

임시 디렉터리에 만든 저장소를 `file://` 원본으로 삼아 install.sh를 실제로
실행한다. 네트워크를 쓰지 않는다.

실행:
    python3 -m unittest discover -s tests
"""
import os
import pty
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

    def env(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        env["DBA_GUIDE_REPO_URL"] = self.origin.as_uri()
        return env

    def run_installer(self, *args, stdin=""):
        return subprocess.run(
            ["bash", str(self.script), *args],
            env=self.env(), cwd=str(self.tmp),
            input=stdin, capture_output=True, text=True,
        )

    def run_installer_on_a_tty(self, *args, answer=""):
        """진짜 tty를 stdin에 붙여 실행한다.

        `install.sh`의 확인 프롬프트는 `[ -t 0 ]` 뒤에 있어, 파이프로는
        영영 닿지 않는다 — 삭제 분기와 취소 분기를 실제로 밟으려면 pty가
        필요하다. `pty`는 표준 라이브러리이고 네트워크를 쓰지 않는다.
        답은 미리 써 둔다. 라인 디시플린이 자식이 읽을 때까지 들고 있다.
        """
        master, slave = pty.openpty()
        try:
            if answer:
                os.write(master, answer.encode())
            return subprocess.run(
                ["bash", str(self.script), *args],
                env=self.env(), cwd=str(self.tmp), stdin=slave,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        finally:
            os.close(slave)
            os.close(master)

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

    def test_a_foreign_symlink_named_guide_blocks_and_survives(self):
        """남의 심볼릭 링크도 일반 파일과 똑같이 충돌이다.

        `is_our_link`의 마지막 줄(대상 옆의 `scripts/guide.py` 확인)이 유일한
        판별자가 되도록, 대상 파일명은 런처 이름과 일부러 같게 둔다. 그 줄이
        사라지면 이 링크가 우리 것으로 입양돼 `ln -sfn`에 덮인다.
        """
        self.tag("v1.0.0")
        stranger = self.tmp / "stranger-bin"
        stranger.mkdir()
        (stranger / "guide").write_text("#!/bin/sh\necho 남의 guide\n")
        self.bin_dir.mkdir(parents=True)
        (self.bin_dir / "guide").symlink_to(stranger / "guide")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.bin_dir / "guide"), r.stderr)
        self.assertEqual(os.readlink(self.bin_dir / "guide"), str(stranger / "guide"))

    def test_a_link_to_another_name_in_our_tree_blocks_and_survives(self):
        """대상 파일명 확인이 유일한 판별자인 경우.

        대상 옆에는 `scripts/guide.py`가 있으므로 마지막 줄은 통과한다.
        파일명 확인이 사라지면 `guide`가 엉뚱한 실행 파일을 가리키게 된다.
        """
        self.tag("v1.0.0")
        other = self.tmp / "other-tree"
        (other / "scripts").mkdir(parents=True)
        (other / "scripts" / "guide.py").write_text("# stub\n")
        (other / "notguide").write_text("#!/bin/sh\necho 다른 것\n")
        self.bin_dir.mkdir(parents=True)
        (self.bin_dir / "guide").symlink_to(other / "notguide")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.bin_dir / "guide"), r.stderr)
        self.assertEqual(os.readlink(self.bin_dir / "guide"), str(other / "notguide"))

    def test_a_dangling_launcher_link_does_not_block_the_install(self):
        """기여자가 설치에 쓴 클론을 지우면 우리 링크 세 개가 끊긴 채 남는다.

        이를 충돌로 보면 재설치가 영영 막히고, 남이 그 이름을 쓰고 있다는
        거짓 안내가 나간다.
        """
        self.tag("v1.0.0")
        gone = self.home / "gone-clone"
        self.bin_dir.mkdir(parents=True)
        for name in ("guide", "exam", "shoot"):
            (self.bin_dir / name).symlink_to(gone / name)
            self.assertFalse((self.bin_dir / name).exists())
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("guide", "exam", "shoot"):
            self.assertEqual(os.readlink(self.bin_dir / name),
                             str(self.install_dir / name))

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

    def test_a_foreign_git_repo_at_the_install_path_is_a_hard_stop(self):
        """설치 경로에 앉은 남의 저장소를 fetch·checkout 하지 않는다.

        벤더 브랜치 클론을 이 경로에 둔 사람이 대표적이다. git 저장소인 것만
        보고 통과시키면 `checkout --detach`가 말없이 그 사람을 브랜치 밖으로
        옮긴다.
        """
        self.tag("v1.0.0")
        self.install_dir.mkdir(parents=True)
        (self.install_dir / "중요.txt").write_text("남의 저장소\n")
        git(self.install_dir, "init", "--quiet")
        git(self.install_dir, "config", "user.email", "test@example.com")
        git(self.install_dir, "config", "user.name", "test")
        git(self.install_dir, "add", "-A")
        git(self.install_dir, "commit", "--quiet", "-m", "남의 커밋")
        before = git(self.install_dir, "rev-parse", "HEAD").strip()
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.install_dir), r.stderr)
        self.assertEqual(git(self.install_dir, "rev-parse", "HEAD").strip(), before)
        self.assertTrue((self.install_dir / "중요.txt").exists())

    def test_a_regular_file_at_the_install_path_is_a_hard_stop(self):
        """디렉터리가 아니면 `[ ! -d ]`가 참이라 클론 분기로 흐른다.

        그대로 두면 git이 영어 fatal을 뱉고 128로 죽는다. 한국어 안내로
        멈추고 그 파일은 그대로 남아야 한다.
        """
        self.tag("v1.0.0")
        self.install_dir.parent.mkdir(parents=True, exist_ok=True)
        self.install_dir.write_text("남의 파일\n")
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.install_dir), r.stderr)
        self.assertEqual(self.install_dir.read_text(), "남의 파일\n")


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


class UninstallTest(InstallerTestCase):
    """제거는 학습 기록을 지우지 않는다 — 어디에도 백업이 없는 유일본이다."""

    def _install_with_records(self):
        self.tag("v1.0.0")
        self.assertEqual(self.run_installer().returncode, 0)
        results = self.install_dir / ".exam-results"
        results.mkdir()
        (results / "results.jsonl").write_text('{"score": 1}\n{"score": 2}\n')
        notes = self.install_dir / ".shooting-progress" / "notes" / "1-1"
        notes.mkdir(parents=True)
        (notes / "20260810-S.md").write_text("# 정리 노트\n")

    def test_uninstall_removes_links_and_keeps_the_records(self):
        self._install_with_records()
        r = self.run_installer("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("guide", "exam", "shoot"):
            self.assertFalse((self.bin_dir / name).exists())
            self.assertFalse((self.bin_dir / name).is_symlink())
        self.assertTrue((self.install_dir / ".exam-results" / "results.jsonl").exists())

    def test_uninstall_leaves_a_foreign_file_alone(self):
        self._install_with_records()
        (self.bin_dir / "exam").unlink()
        (self.bin_dir / "exam").write_text("남의 것\n")
        r = self.run_installer("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.bin_dir / "exam").read_text(), "남의 것\n")

    def test_uninstall_removes_a_dangling_link_it_created(self):
        """대상 트리가 사라진 링크도 우리 것이다. 남의 것이라 우기면 안 된다."""
        self._install_with_records()
        (self.bin_dir / "exam").unlink()
        (self.bin_dir / "exam").symlink_to(self.home / "gone-clone" / "exam")
        r = self.run_installer("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("건너뜀", r.stdout)
        self.assertFalse((self.bin_dir / "exam").is_symlink())

    def test_uninstall_does_not_name_an_install_dir_that_was_never_created(self):
        """in-place 설치였다면 이 경로는 만들어진 적이 없다."""
        self.assertFalse(self.install_dir.exists())
        r = self.run_installer("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("그 안에 있습니다", r.stdout)
        self.assertIn("설치 디렉터리가 없습니다", r.stdout)

    def test_purge_without_a_tty_refuses_to_delete(self):
        """파이프 실행에서는 확인을 받을 수 없다. 그럴 땐 지우지 않는다."""
        self._install_with_records()
        r = self.run_installer("--uninstall", "--purge")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.install_dir.exists())
        self.assertIn("rm -rf", r.stderr)

    def test_purge_counts_the_records_before_asking(self):
        """무엇이 사라지는지 숫자로 보여준 뒤에 물어야 한다."""
        self._install_with_records()
        r = self.run_installer("--uninstall", "--purge")
        self.assertEqual(r.returncode, 1)
        self.assertIn("시험 결과 2건", r.stdout)
        self.assertIn("정리 노트 1건", r.stdout)

    def test_purge_refuses_a_directory_it_did_not_create(self):
        """설치한 적 없는 사람의 자료가 그 경로에 있는 경우.

        개수를 세기 전에 막아야 한다. "시험 결과 0건"은 지워도 잃을 게 없다는
        뜻으로 읽히는데, 가드가 필요한 상황이 바로 그 경우다.
        """
        self.tag("v1.0.0")
        self.install_dir.mkdir(parents=True)
        (self.install_dir / "중요.txt").write_text("남의 자료\n")
        r = self.run_installer_on_a_tty("--uninstall", "--purge", answer="y\n")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("시험 결과", r.stdout)
        self.assertTrue((self.install_dir / "중요.txt").exists())

    def test_purge_answered_no_on_a_tty_keeps_everything(self):
        """확인 분기는 tty가 있어야 밟힌다 — 취소 쪽."""
        self._install_with_records()
        r = self.run_installer_on_a_tty("--uninstall", "--purge", answer="n\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("취소했습니다", r.stdout)
        self.assertTrue(self.install_dir.exists())
        self.assertTrue((self.install_dir / ".exam-results" / "results.jsonl").exists())

    def test_purge_answered_yes_on_a_tty_deletes_the_tree(self):
        """확인 분기는 tty가 있어야 밟힌다 — 삭제 쪽."""
        self._install_with_records()
        r = self.run_installer_on_a_tty("--uninstall", "--purge", answer="y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("삭제했습니다", r.stdout)
        self.assertFalse(self.install_dir.exists())

    def test_purge_treats_eof_as_a_cancel(self):
        """Ctrl-D에 `read`가 실패한다. set -e 아래에서 말없이 죽으면 안 된다."""
        self._install_with_records()
        r = self.run_installer_on_a_tty("--uninstall", "--purge", answer="\x04")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("취소했습니다", r.stdout)
        self.assertTrue(self.install_dir.exists())
