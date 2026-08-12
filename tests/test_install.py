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

    # 런처와 그것이 부르는 모듈. `shoot`만 이름이 어긋난다(scripts/shooting.py).
    LAUNCHERS = (("guide", "guide"), ("exam", "exam"), ("shoot", "shooting"))

    def _make_origin(self):
        """런처 세 개와 그 대상 모듈을 갖춘 최소 저장소.

        런처는 **저장소의 진짜 파일을 복사한다.** `echo` 스텁으로 대신하면
        링크 경유로 불렸을 때 자기 위치를 어떻게 푸는지가 검사 대상에서 빠지고,
        바로 그 자리에 v1.2.0의 결함이 있었다 — `${BASH_SOURCE[0]}`는 심볼릭
        링크를 따라가지 않으므로 세 명령 모두 설치 후 동작하지 않았다.
        """
        (self.origin / "scripts").mkdir(parents=True)
        for launcher, module in self.LAUNCHERS:
            shutil.copy2(REPO_ROOT / launcher, self.origin / launcher)
            (self.origin / "scripts" / f"{module}.py").write_text(
                "print('%s 실행됨')\n" % launcher, encoding="utf-8")
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

    def env(self, xdg="default"):
        """실행 환경. `xdg`로 `XDG_DATA_HOME`을 갈아끼운다.

        기본값은 임시 HOME 아래의 표준 경로다. 다른 경로를 주면 설치 위치가
        따라 움직이고, `None`을 주면 변수 자체를 지운다 — 커스텀 경로로
        설치한 뒤 그 값을 빠뜨리고 다시 실행하는 상황을 재현하기 위해서다.
        """
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["DBA_GUIDE_REPO_URL"] = self.origin.as_uri()
        if xdg == "default":
            env["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        elif xdg is None:
            env.pop("XDG_DATA_HOME", None)
        else:
            env["XDG_DATA_HOME"] = str(xdg)
        return env

    def run_installer(self, *args, stdin="", xdg="default"):
        return subprocess.run(
            ["bash", str(self.script), *args],
            env=self.env(xdg), cwd=str(self.tmp),
            input=stdin, capture_output=True, text=True,
        )

    def run_installer_on_a_tty(self, *args, answer="", xdg="default"):
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
                env=self.env(xdg), cwd=str(self.tmp), stdin=slave,
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

    @property
    def state_file(self):
        """우리가 만든 설치본의 위치 기록. in-place 설치는 남기지 않는다."""
        return self.home / ".local" / "state" / "dba-guide" / "install-path"

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

    def test_a_fresh_install_does_not_claim_to_be_already_up_to_date(self):
        """첫 설치에 "이미 최신입니다"는 어리둥절하다.

        릴리스 직후에는 최신 태그가 `main`의 tip에 있어, 갓 클론한 HEAD가
        곧 그 태그다. 조기 반환 분기를 그대로 타면 처음 설치하는 사람이
        "이미"라는 말을 먼저 읽는다.
        """
        self.tag("v1.0.0")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("이미 최신", r.stdout)
        self.assertIn("설치 완료", r.stdout)

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

    def test_a_live_foreign_symlink_with_a_relative_target_blocks_and_survives(self):
        """상대 경로를 가리키는, 멀쩡히 살아 있는 남의 링크.

        `readlink`가 돌려준 상대 경로를 `[ -e ]`로 그대로 검사하면 설치기의
        cwd를 기준으로 풀린다 — 링크 자신이 있는 디렉터리 기준이 아니다.
        그러면 사용자 셸에서는 멀쩡히 열리는 링크가 "끊긴 링크"로 오판되어
        `is_our_link`이 참을 돌려주고, 남의 링크가 우리 것으로 입양되어
        덮어써진다. 우리가 만드는 링크는 항상 절대 경로이므로, 상대 경로
        대상은 애초에 입양 대상이 아니어야 한다.
        """
        self.tag("v1.0.0")
        elsewhere = self.home / ".local" / "elsewhere"
        elsewhere.mkdir(parents=True)
        (elsewhere / "guide").write_text("#!/bin/sh\necho 남의 guide\n")
        self.bin_dir.mkdir(parents=True)
        relative_target = "../elsewhere/guide"
        (self.bin_dir / "guide").symlink_to(relative_target)
        # 링크가 있는 자리(bin_dir) 기준으로는 살아 있다 — 설치기의 cwd
        # 기준으로 잘못 풀면 끊긴 것처럼 보일 뿐이다.
        self.assertTrue((self.bin_dir / "guide").exists())
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(self.bin_dir / "guide"), r.stderr)
        self.assertEqual(os.readlink(self.bin_dir / "guide"), relative_target)

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

    def test_a_plain_uninstall_points_at_the_purge_command(self):
        """지우는 방법을 알려주되, 기록이 남아 그 명령이 그대로 통해야 한다."""
        self._install_with_records()
        r = self.run_installer("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("install.sh --uninstall --purge", r.stdout)
        self.assertTrue(self.state_file.is_file())

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


class LinkedCommandTest(InstallerTestCase):
    """링크를 만드는 것으로 끝이 아니다 — 그 링크로 실제로 실행돼야 한다.

    v1.2.0은 링크가 가리키는 **문자열**만 검사했다. 세 런처는 모두
    `dirname "${BASH_SOURCE[0]}"`로 자기 위치를 찾는데, 그 값은 심볼릭
    링크를 따라가지 않고 링크 자신의 경로다. 그래서 설치된 명령은
    `~/.local/bin/scripts/exam.py`를 찾다 전부 실패했다.
    """

    def _run_installed(self, name):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        # cwd를 설치 경로 바깥으로 둔다 — 링크 경유 해석이 시험 대상이다.
        return subprocess.run([str(self.bin_dir / name)], env=env,
                              cwd=str(self.tmp), capture_output=True, text=True)

    def test_every_installed_command_runs_through_its_symlink(self):
        self.tag("v1.0.0")
        self.assertEqual(self.run_installer().returncode, 0)
        for name, _ in self.LAUNCHERS:
            with self.subTest(launcher=name):
                r = self._run_installed(name)
                self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")
                self.assertIn(f"{name} 실행됨", r.stdout)

    def test_arguments_survive_the_symlink(self):
        """인자를 그대로 넘기는 것이 런처의 유일한 일이다."""
        self.tag("v1.0.0")
        self.assertEqual(self.run_installer().returncode, 0)
        (self.install_dir / "scripts" / "exam.py").write_text(
            "import sys\nprint('args=' + ' '.join(sys.argv[1:]))\n", encoding="utf-8")
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        r = subprocess.run([str(self.bin_dir / "exam"), "--dbms", "postgresql"],
                           env=env, cwd=str(self.tmp), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("args=--dbms postgresql", r.stdout)


class SelfLocatingTest(InstallerTestCase):
    """설치 위치는 **기록**이 기억한다 — 링크가 아니다.

    `INSTALL_DIR`는 매 실행마다 `XDG_DATA_HOME`으로 계산될 뿐이라, 커스텀
    경로로 설치한 뒤 그 값을 빠뜨리면 스크립트가 지난 설치본을 못 찾는다.
    실측된 결과는 조용한 두 번째 설치본과 그리로 옮겨간 링크였고, 원래
    설치본과 학습 기록은 고아가 됐다.

    근거를 링크로 삼으면 안 된다. 링크는 in-place 설치에서도 걸리는데 그
    대상은 기여자의 작업 클론이라, 나중의 managed 실행이 그 클론을 설치본으로
    오인해 HEAD를 태그로 떨어뜨린다(실측). `install_managed`만 남기는 기록이
    유일하게 안전한 근거다. `InPlaceIsNeverAdoptedTest`가 그 경계를 지킨다.

    픽스처는 태그 **뒤에** 커밋을 하나 둔다. 그래야 클론의 HEAD가 최신 태그와
    달라 실제 설치가 타는 `checkout --detach` 분기를 지나간다 — 태그가 tip에
    있으면 조기 반환 분기만 검사하게 되고, 진짜 설치 경로의 기록이 통째로
    테스트 밖에 남는다.
    """

    def setUp(self):
        super().setUp()
        self.custom = self.tmp / "tools"
        self.custom_install = self.custom / "dba-guide"
        self.tag("v1.0.0")
        self.commit("after the release")
        r = self.run_installer(xdg=self.custom)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.custom_install.is_dir())

    def test_rerunning_without_the_variable_reuses_the_existing_install(self):
        r = self.run_installer(xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.install_dir.exists(),
                         "기본 경로에 두 번째 설치본이 생겼다")
        self.assertIn(str(self.custom_install), r.stdout)

    def test_the_links_keep_pointing_at_the_original_install(self):
        self.run_installer(xdg=None)
        for name, _ in self.LAUNCHERS:
            with self.subTest(launcher=name):
                self.assertEqual(os.readlink(self.bin_dir / name),
                                 str(self.custom_install / name))

    def test_it_says_which_install_it_adopted(self):
        """조용히 고르면 안 된다 — 계산된 기본 경로와 다르다는 사실을 알린다."""
        r = self.run_installer(xdg=None)
        self.assertIn("기존 설치본", r.stdout)

    def test_uninstall_without_the_variable_reports_the_real_directory(self):
        r = self.run_installer("--uninstall", xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.custom_install), r.stdout)
        self.assertTrue(self.custom_install.is_dir())

    def test_purge_without_the_variable_deletes_the_real_directory(self):
        """가장 나쁜 경우 — 엉뚱한 트리를 지우고 진짜는 남기는 것."""
        r = self.run_installer_on_a_tty("--uninstall", "--purge",
                                        answer="y\n", xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.custom_install.exists())

    def test_a_plain_uninstall_keeps_the_record_for_a_later_purge(self):
        """링크를 지워도 위치 기록은 남아야 한다.

        `--uninstall`은 런처 링크를 지운다. 위치를 링크에서만 알아냈다면 그
        순간 단서가 사라져, 나중의 `--uninstall --purge`가 기본 경로를 보고
        진짜 설치본과 학습 기록을 남긴 채 끝난다.
        """
        r = self.run_installer("--uninstall", xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.state_file.is_file(), "기록이 지워졌다")
        self.assertEqual(self.state_file.read_text().strip(),
                         str(self.custom_install))

    def test_purge_after_a_plain_uninstall_still_finds_the_real_tree(self):
        self.run_installer("--uninstall", xdg=None)
        self.assertTrue(self.custom_install.is_dir(), "아직 남아 있어야 한다")
        r = self.run_installer_on_a_tty("--uninstall", "--purge",
                                        answer="y\n", xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.custom_install.exists())
        self.assertFalse(self.state_file.exists(), "지운 뒤에도 기록이 남았다")

    def test_the_record_names_the_managed_install(self):
        self.assertTrue(self.state_file.is_file())
        self.assertEqual(self.state_file.read_text().strip(),
                         str(self.custom_install))

    def test_no_record_means_the_computed_path_is_used(self):
        """기록이 없으면 예전대로 계산된 경로를 쓴다."""
        self.state_file.unlink()
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.install_dir.is_dir())

    def test_deleting_the_links_relinks_the_recorded_install(self):
        """링크만 날아간 경우는 수리해야 한다 — 두 번째 설치본을 만들지 않는다."""
        for name, _ in self.LAUNCHERS:
            (self.bin_dir / name).unlink()
        r = self.run_installer(xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.install_dir.exists())
        self.assertEqual(os.readlink(self.bin_dir / "guide"),
                         str(self.custom_install / "guide"))


class InPlaceIsNeverAdoptedTest(InstallerTestCase):
    """기여자의 작업 클론을 설치본으로 오인하면 안 된다.

    in-place 설치는 사용자의 클론에 링크만 건다. 그 뒤 managed 의도로
    실행했을 때 그 클론을 설치본으로 채택하면, `fetch`·`checkout --detach`가
    남의 저장소를 대상으로 돌아 **작업 브랜치가 릴리스 태그로 detached 되고
    작업 파일이 워킹 트리에서 사라진다** — `install_inplace`가 지키기로 한
    "HEAD를 옮기지 않는다"를 정면으로 깬다. `--purge`는 그 클론을 통째로
    지운다. 그래서 위치 기록은 **우리가 만든 설치본에만** 남긴다.
    """

    def setUp(self):
        super().setUp()
        self.tag("v1.0.0")
        self.work = self.tmp / "work"
        subprocess.run(["git", "clone", "--quiet", self.origin.as_uri(), str(self.work)],
                       check=True, capture_output=True, text=True)
        shutil.copy2(INSTALL_SH, self.work / "install.sh")
        git(self.work, "config", "user.email", "t@example.com")
        git(self.work, "config", "user.name", "t")
        git(self.work, "switch", "--quiet", "-c", "my-feature")
        (self.work / "MY-WORK.md").write_text("작업 중\n", encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "--quiet", "-m", "my feature work")
        # in-place 설치 — 링크가 작업 클론을 가리키게 된다.
        r = subprocess.run(["bash", str(self.work / "install.sh")],
                           env=self.env(), cwd=str(self.work),
                           input="", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.readlink(self.bin_dir / "guide"), str(self.work / "guide"))

    def _head(self):
        return git(self.work, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def test_in_place_leaves_no_record(self):
        self.assertFalse(self.state_file.exists(),
                         "in-place 설치가 위치 기록을 남겼다")

    def test_a_later_managed_run_does_not_touch_the_clone(self):
        before = git(self.work, "rev-parse", "HEAD").strip()
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._head(), "my-feature", "작업 브랜치가 detached 됐다")
        self.assertEqual(git(self.work, "rev-parse", "HEAD").strip(), before)
        self.assertTrue((self.work / "MY-WORK.md").is_file(), "작업 파일이 사라졌다")

    def test_a_later_purge_does_not_delete_the_clone(self):
        r = self.run_installer_on_a_tty("--uninstall", "--purge", answer="y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.work.is_dir(), "사용자의 클론이 지워졌다")
        self.assertTrue((self.work / "MY-WORK.md").is_file())


class RecordIsNotBlindlyTrustedTest(InstallerTestCase):
    """기록은 새로 생긴 **신뢰 입력**이다 — 낡거나 엉뚱하면 물러서야 한다."""

    def setUp(self):
        super().setUp()
        self.tag("v1.0.0")
        self.commit("after the release")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def _record(self, text):
        self.state_file.write_text(text, encoding="utf-8")

    def _install_lands_at_default(self):
        r = self.run_installer(xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.install_dir.is_dir())

    def test_a_record_naming_a_deleted_path_is_ignored(self):
        self._record(str(self.tmp / "gone" / "dba-guide"))
        self._install_lands_at_default()

    def test_a_record_naming_a_foreign_repository_is_ignored(self):
        stranger = self.tmp / "stranger"
        stranger.mkdir()
        git(stranger, "init", "--quiet")
        self._record(str(stranger))
        self._install_lands_at_default()

    def test_a_record_naming_a_plain_file_is_ignored(self):
        f = self.tmp / "not-a-dir"
        f.write_text("x\n")
        self._record(str(f))
        self._install_lands_at_default()

    def test_a_zero_byte_record_is_ignored(self):
        """`[ -n "$recorded" ]` 가드를 실제로 밟는 유일한 입력이다."""
        self._record("")
        self._install_lands_at_default()

    def test_a_whitespace_record_is_ignored(self):
        """공백은 비어 있지 않다 — 여기서 걸러내는 것은 is_our_install 이다."""
        self._record("   \n")
        self._install_lands_at_default()


class ExplicitPathWinsTest(InstallerTestCase):
    """목적지를 지정했으면 그대로 따른다.

    기록이 명시적 지정을 이기면 이동할 방법이 없어진다 — 평범한
    `--uninstall`은 기록을 남기므로 재설치가 옛 경로로 끌려가고, 남는
    탈출구는 `--purge`, 즉 **백업 없는 학습 기록을 지우는 것**뿐이다.
    """

    def setUp(self):
        super().setUp()
        self.tag("v1.0.0")
        self.commit("after the release")
        self.first = self.tmp / "tools"
        self.second = self.tmp / "elsewhere"
        r = self.run_installer(xdg=self.first)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_new_explicit_path_relocates_the_install(self):
        r = self.run_installer(xdg=self.second)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.second / "dba-guide").is_dir(), "지정한 곳에 설치되지 않았다")
        self.assertEqual(os.readlink(self.bin_dir / "guide"),
                         str(self.second / "dba-guide" / "guide"))
        self.assertEqual(self.state_file.read_text().strip(),
                         str(self.second / "dba-guide"))

    def test_relocating_does_not_require_deleting_the_records(self):
        """옛 설치본은 그대로 둔다 — 학습 기록은 사용자가 옮기거나 지운다."""
        self.run_installer(xdg=self.second)
        self.assertTrue((self.first / "dba-guide").is_dir())

    def test_omitting_the_variable_still_follows_the_record(self):
        r = self.run_installer(xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.install_dir.exists())


class RecordingIsBookkeepingTest(InstallerTestCase):
    """기록은 부기다 — 실패해도 설치를 죽이면 안 되고, 설치보다 늦으면 안 된다."""

    def setUp(self):
        super().setUp()
        self.tag("v1.0.0")
        self.commit("after the release")

    def test_an_unwritable_record_does_not_fail_the_install(self):
        # 기록 디렉터리 자리에 일반 파일을 둔다 — mkdir -p 가 실패한다.
        state_root = self.home / ".local" / "state"
        state_root.mkdir(parents=True)
        (state_root / "dba-guide").write_text("in the way\n")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("설치 완료", r.stdout)

    def test_a_link_conflict_still_leaves_the_install_recorded(self):
        """링크 단계에서 죽어도 트리는 이미 우리 것이다 — 기록이 없으면
        다음 실행이 두 번째 설치본을 만든다."""
        self.bin_dir.mkdir(parents=True)
        (self.bin_dir / "exam").write_text("남의 것\n")
        r = self.run_installer(xdg=self.tmp / "tools")
        self.assertEqual(r.returncode, 1)
        self.assertTrue((self.tmp / "tools" / "dba-guide").is_dir())
        self.assertTrue(self.state_file.is_file(), "트리는 만들고 기록은 안 했다")


class WarnsAboutAnotherInstallTest(InstallerTestCase):
    """기록이 없는데 링크가 다른 곳을 가리키는 상태 — 지난 라운드가 추가하고
    한 번도 실행되지 않았던 경로다.

    모든 호출 경로가 `XDG_DATA_HOME` 미설정을 요구하는데 테스트 헬퍼의
    기본값은 그 변수를 **항상 설정**했다. 기본값이 안전한 쪽으로 치우쳐
    있으면 위험한 분기는 영원히 테스트 밖에 남는다.
    """

    def setUp(self):
        super().setUp()
        self.tag("v1.0.0")
        self.commit("after the release")
        self.work = self.tmp / "work"
        subprocess.run(["git", "clone", "--quiet", self.origin.as_uri(), str(self.work)],
                       check=True, capture_output=True, text=True)
        shutil.copy2(INSTALL_SH, self.work / "install.sh")
        git(self.work, "config", "user.email", "t@example.com")
        git(self.work, "config", "user.name", "t")
        git(self.work, "switch", "--quiet", "-c", "my-feature")
        (self.work / "MY-WORK.md").write_text("작업 중\n", encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "--quiet", "-m", "my feature work")
        r = subprocess.run(["bash", str(self.work / "install.sh")],
                           env=self.env(), cwd=str(self.work),
                           input="", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_it_notices_the_other_install(self):
        r = self.run_installer(xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.work), r.stdout)

    def test_it_never_advises_pointing_the_installer_at_a_working_clone(self):
        """치명적이었던 자리 — 그 조언을 따르면 남의 브랜치가 태그로 떨어진다."""
        r = self.run_installer(xdg=None)
        notice = r.stdout[:r.stdout.find("저장소를 내려받습니다")] or r.stdout
        self.assertNotIn("XDG_DATA_HOME", notice)

    def test_the_clone_survives_that_run(self):
        before = git(self.work, "rev-parse", "HEAD").strip()
        self.run_installer(xdg=None)
        self.assertEqual(git(self.work, "rev-parse", "--abbrev-ref", "HEAD").strip(),
                         "my-feature")
        self.assertEqual(git(self.work, "rev-parse", "HEAD").strip(), before)
        self.assertTrue((self.work / "MY-WORK.md").is_file())

    def test_uninstall_does_not_print_the_install_notice(self):
        """제거 중에 '이 실행은 …에 설치합니다'는 거짓말이다."""
        r = self.run_installer("--uninstall", xdg=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("설치합니다", r.stdout)


class AmbientXdgTest(InstallerTestCase):
    """`+x`와 `:-`가 어긋나면 안 된다.

    `INSTALL_DIR`은 `:-`로 계산되므로 빈 값은 기본 경로를 뜻한다. 그런데
    채택을 `+x`로 건너뛰면 빈 값이 '명시'가 되어, 기록이 있는데도 무시하고
    기본 경로에 두 번째 설치본을 만든 뒤 **기록까지 덮어쓴다**.
    """

    def setUp(self):
        super().setUp()
        self.tag("v1.0.0")
        self.commit("after the release")
        self.custom = self.tmp / "tools"
        r = self.run_installer(xdg=self.custom)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_empty_variable_is_not_an_explicit_destination(self):
        r = self.run_installer(xdg="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.install_dir.exists(), "두 번째 설치본이 생겼다")
        self.assertEqual(self.state_file.read_text().strip(),
                         str(self.custom / "dba-guide"))

    def test_an_explicit_destination_that_disagrees_says_so(self):
        """조용히 무시하면 사용자는 기록이 있다는 사실조차 모른다."""
        r = self.run_installer(xdg=self.tmp / "elsewhere")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.custom / "dba-guide"), r.stdout)


class NoRecordNeverLocksAnyoneOutTest(InstallerTestCase):
    """기록이 없다고 해서 설치본을 남의 것으로 몰면 안 된다.

    한 라운드에서 "HEAD가 detached면 우리가 만든 것"이라는 가드를 뒀다가,
    스크립트가 **자기가 만든 설치본을 거부**했다. 원인은 `install_managed`의
    조기 반환 분기다 — 릴리스 직후에는 최신 태그가 `main`의 tip이라 갓 클론한
    HEAD가 이미 그 태그이고, `checkout --detach`가 아예 실행되지 않아 설치본이
    **브랜치 위에** 남는다. 기록이 없던 v1.3.0 이전 사용자는 영구히 갇혔다.

    소유권은 git 상태에서 추론할 수 없다. 기록이 없으면 그냥 기록이 없는
    것이고, 그때의 올바른 동작은 계산된 경로를 쓰는 기존 동작이다.
    """

    def _install_then_forget_the_record(self):
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.state_file.unlink()

    def test_an_install_left_on_a_branch_still_updates(self):
        """태그가 tip이면 설치본은 브랜치 위에 남는다 — 정상 상태다."""
        self.tag("v1.0.0")
        self._install_then_forget_the_record()
        self.assertTrue(git(self.install_dir, "symbolic-ref", "-q", "HEAD").strip(),
                        "이 픽스처는 브랜치 위 설치본을 만들어야 한다")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("설치 완료", r.stdout)

    def test_a_detached_install_still_updates(self):
        self.tag("v1.0.0")
        self.commit("after the release")
        self._install_then_forget_the_record()
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("설치 완료", r.stdout)

    def test_a_rerun_after_a_failed_first_attempt_recovers(self):
        """태그를 못 찾아 죽으면 클론만 남는다 — 다음 실행이 이어받아야 한다."""
        r = self.run_installer()
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.install_dir.is_dir(), "클론은 남아 있다")
        self.tag("v1.0.0")
        self.commit("after the release")
        r = self.run_installer()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head_tag(), "v1.0.0")


class PurgingOneInstallSparesTheOtherRecordTest(InstallerTestCase):
    """두 벌이 있을 때, 한쪽을 지우면서 다른 쪽의 기록까지 지우면 안 된다."""

    def setUp(self):
        super().setUp()
        self.tag("v1.0.0")
        self.commit("after the release")
        self.old = self.tmp / "tools"
        self.assertEqual(self.run_installer(xdg=self.old).returncode, 0)
        self.assertEqual(self.run_installer(xdg=self.home / ".local" / "share").returncode, 0)
        self.assertEqual(self.state_file.read_text().strip(), str(self.install_dir))

    def test_purging_the_old_one_keeps_the_record_of_the_live_one(self):
        r = self.run_installer_on_a_tty("--uninstall", "--purge",
                                        answer="y\n", xdg=self.old)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.old / "dba-guide").exists(), "옛 설치본이 남았다")
        self.assertTrue(self.install_dir.is_dir(), "살아 있는 설치본이 지워졌다")
        self.assertTrue(self.state_file.is_file(), "다른 쪽 기록까지 지웠다")
        self.assertEqual(self.state_file.read_text().strip(), str(self.install_dir))
