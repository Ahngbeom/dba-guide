# `install.sh` 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 저장소를 손에 넣고 실행 가능한 상태로 만드는 과정을 한 줄로 만드는 `install.sh`를 추가한다.

**Architecture:** 저장소 루트에 단일 bash 스크립트를 두고, 설치·업데이트·제거를 한 진입점이 담당한다. 저장소는 `${XDG_DATA_HOME:-~/.local/share}/dba-guide`에 클론해 **최신 정식 릴리스 태그**로 detached 체크아웃하고, 런처 세 개(`guide`·`exam`·`shoot`)를 `~/.local/bin`에 심볼릭 링크한다. 테스트는 임시 디렉터리에 만든 로컬 저장소를 `file://` 원본으로 삼아 스크립트를 실제로 실행하므로 네트워크가 필요 없다.

**Tech Stack:** Bash(3.2 호환), git, Python 3 표준 라이브러리 `unittest` + `subprocess`.

**설계 문서:** `docs/superpowers/specs/2026-08-10-installer-design.md`

## Global Constraints

- **bash 3.2 호환 필수.** macOS 기본 `/bin/bash`는 3.2.57이며 `curl | bash`는 이 셸로 실행된다. 연관 배열(`declare -A`), `readarray`/`mapfile`, `${var,,}`/`${var^^}`, `&>>`, `;;&` 금지. 배열 대신 공백/개행 구분 문자열을 쓴다.
- 스크립트 첫 줄은 `#!/usr/bin/env bash`, 그다음 `set -euo pipefail`.
- **사용자 대면 출력은 전부 한국어.** 저장소 전체 관례다.
- **커밋 메시지는 영어 명령형.** 저장소 관례(`Write down how releases get cut`, `Teach the engine to speak PostgreSQL`).
- 파이썬 코드는 **표준 라이브러리만**. `pip` 설치 금지(`CLAUDE.md` 규약).
- 테스트는 **네트워크를 쓰지 않는다.**
- Python 최소 버전 하한은 **3.9** (`scripts/shooting.py:2495`가 유일한 근거).
- 설치 경로: `${XDG_DATA_HOME:-$HOME/.local/share}/dba-guide` · 링크 경로: `$HOME/.local/bin`
- 런처 이름 세 개: `guide` `exam` `shoot` (접두어 없음)
- 태그 패턴: `^v[0-9]+\.[0-9]+\.[0-9]+$` (프리릴리스 `-rc` 제외)
- 환경 변수 이음매: `DBA_GUIDE_REPO_URL` (기본 `https://github.com/Ahngbeom/dba-guide.git`)
- 전체 테스트 실행: `python3 -m unittest discover -s tests`

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `install.sh` (신규, 실행 권한) | 설치·업데이트·제거 전부. 함수 단위로 나누되 파일은 하나 — 원격에서 한 번에 내려받아 실행하는 것이 전제라 분할할 수 없다 |
| `tests/test_install.py` (신규) | `install.sh`를 실제 실행하는 통합 테스트. 임시 `HOME` + `file://` 원본 |
| `README.md` (수정) | `## 한 번에 시작하기`(94행) 앞에 `## 설치` 절 신설 |
| `docs/release-policy.md` (수정) | 릴리스 전 점검 항목 추가(38-46행 목록) + 순서 의존성 문단 |
| `CLAUDE.md` (수정) | 도구 목록(10-13행)에 `install.sh` 한 줄 |

### `install.sh` 함수 구성 (최종 형태)

| 함수 | 책임 |
|---|---|
| `info` / `die` / `have` | 출력·중단·명령 존재 확인 |
| `usage` | 도움말 |
| `check_prereqs` | git·python3(≥3.9)·docker 확인 |
| `latest_release_tag <dir>` | 정식 태그 중 최신 하나를 표준출력으로 |
| `is_our_link <link> <name>` | 그 링크가 우리가 만든 것인지 |
| `link_launchers <src>` | 런처 세 개 링크. 충돌은 일괄 보고 후 중단 |
| `report <dir> <ver>` | 마무리 안내 + PATH 점검 |
| `detect_inplace` | in-place 모드면 저장소 최상위를 표준출력으로, 아니면 1 반환 |
| `install_inplace <top>` | 링크만. **HEAD를 옮기지 않는다** |
| `install_managed` | 클론/업데이트 + 태그 체크아웃 + 링크 |
| `uninstall <purge>` | 링크 제거, 선택적으로 디렉터리 삭제 |
| `main` | 인자 파싱 후 분기 |

---

## Task 1: 테스트 하네스와 인자 파싱

**Files:**
- Create: `install.sh`
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: 없음
- Produces: `install.sh`의 `info()` `die()` `have()` `usage()` `main()`. 테스트 측 `InstallerTestCase` 기반 클래스가 `self.script`(임시 디렉터리로 복사된 `install.sh` 경로), `self.origin`(file:// 원본 저장소), `self.home`, `self.install_dir`, `self.bin_dir`, `self.run_installer(*args, stdin=None)`, `self.tag(name)`, `self.commit(message)`를 제공한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_install.py`를 새로 만든다.

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_install -v`
Expected: FAIL — `install.sh`가 없어 `shutil.copy2`에서 `FileNotFoundError`.

- [ ] **Step 3: `install.sh` 골격을 쓴다**

```bash
#!/usr/bin/env bash
#
# DBA 학습서 설치 스크립트.
#
#   설치·업데이트: curl -fsSL https://raw.githubusercontent.com/Ahngbeom/dba-guide/main/install.sh | bash
#   제거:         curl -fsSL https://raw.githubusercontent.com/Ahngbeom/dba-guide/main/install.sh | bash -s -- --uninstall
#
# 저장소는 ${XDG_DATA_HOME:-~/.local/share}/dba-guide 에 두고, 런처 세 개를
# ~/.local/bin 에 심볼릭 링크한다. 설치 지점은 항상 최신 정식 릴리스 태그다 —
# 이 저장소에는 CI가 없어 main HEAD는 아무도 점검하지 않은 상태이기 때문이다.
#
# macOS 기본 bash는 3.2다. 연관 배열·mapfile·${var,,} 를 쓰지 않는다.
set -euo pipefail

REPO_URL="${DBA_GUIDE_REPO_URL:-https://github.com/Ahngbeom/dba-guide.git}"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dba-guide"
BIN_DIR="$HOME/.local/bin"
LAUNCHERS="guide exam shoot"

info() { printf '%s\n' "$@"; }
die() { printf '%s\n' "$@" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
  cat <<'EOF'
사용법: install.sh [옵션]

옵션 없이 실행하면 설치하고, 이미 설치돼 있으면 업데이트한다(멱등).

  --uninstall          런처 링크만 제거한다. 저장소와 학습 기록은 남는다.
  --uninstall --purge  저장소 디렉터리까지 지운다. 확인을 받는다.
  --help               이 도움말.

환경 변수:
  XDG_DATA_HOME       설치 경로의 부모 (기본: ~/.local/share)
  DBA_GUIDE_REPO_URL  클론 원본 (기본: GitHub)
EOF
}

main() {
  mode="install"
  purge="no"
  while [ $# -gt 0 ]; do
    case "$1" in
      --uninstall) mode="uninstall" ;;
      --purge) purge="yes" ;;
      --help|-h) usage; exit 0 ;;
      *) die "알 수 없는 옵션: $1" "" "사용법은 --help 로 볼 수 있습니다." ;;
    esac
    shift
  done

  if [ "$purge" = "yes" ] && [ "$mode" != "uninstall" ]; then
    die "--purge 는 --uninstall 과 함께만 쓸 수 있습니다." \
        "  제거하려면: install.sh --uninstall --purge"
  fi

  die "아직 구현되지 않았습니다."
}

main "$@"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `chmod +x install.sh && python3 -m unittest tests.test_install -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add install.sh tests/test_install.py
git commit -m "Sketch the installer entry point and its test harness

The harness copies install.sh into a temp dir before running it. Executing
the in-repo copy would make BASH_SOURCE point at the real dba-guide tree,
silently routing every test through the in-place branch."
```

---

## Task 2: 최초 설치 — 클론, 최신 정식 태그, 링크

**Files:**
- Modify: `install.sh` (`main`의 `die "아직 구현되지 않았습니다."` 교체)
- Modify: `tests/test_install.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 1의 `info` `die` `have` `main`, 테스트의 `InstallerTestCase`
- Produces: `check_prereqs()`, `latest_release_tag <dir>` (표준출력에 태그 한 줄, 없으면 빈 출력), `is_our_link <link> <name>` (0/1), `link_launchers <src>`, `report <dir> <ver>`, `install_managed()`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_install.py` 끝에 붙인다.

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_install -v`
Expected: FAIL — 4건 모두. `"아직 구현되지 않았습니다."`로 종료 코드 1.

- [ ] **Step 3: 구현한다**

`install.sh`의 `usage()`와 `main()` **사이**에 함수들을 넣는다.

```bash
check_prereqs() {
  have git || die "git이 필요합니다." \
                  "  macOS:         xcode-select --install" \
                  "  Debian/Ubuntu: sudo apt install git"
  have python3 || die "python3가 필요합니다 (3.9 이상)."
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    die "python3 3.9 이상이 필요합니다. 현재: $(python3 -V 2>&1)"
  fi
  if ! have docker; then
    info "경고: docker가 없습니다." \
         "      챕터 읽기와 학습 점검은 그대로 쓸 수 있지만," \
         "      장애 대응(shoot)은 로컬 Docker 랩이 필요합니다." \
         ""
  fi
}

# 정식 릴리스 태그 중 최신 하나. 없으면 아무것도 출력하지 않는다.
# --sort=-v:refname 만으로는 프리릴리스가 위로 올 수 있어 패턴 필터가 필수다.
latest_release_tag() {
  git -C "$1" tag -l --sort=-v:refname \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    | head -n 1 || true
}

# 이 링크가 우리가 만든 것인가 — 대상 파일명이 런처 이름이고,
# 그 옆에 scripts/guide.py가 있으면 우리 설치본이다.
is_our_link() {
  [ -L "$1" ] || return 1
  target="$(readlink "$1")"
  [ "$(basename "$target")" = "$2" ] || return 1
  [ -f "$(dirname "$target")/scripts/guide.py" ]
}

link_launchers() {
  src="$1"
  conflicts=""
  for name in $LAUNCHERS; do
    link="$BIN_DIR/$name"
    if [ -e "$link" ] || [ -L "$link" ]; then
      if ! is_our_link "$link" "$name"; then
        conflicts="${conflicts}  ${link}
"
      fi
    fi
  done
  if [ -n "$conflicts" ]; then
    die "다음 이름이 이미 쓰이고 있어 링크를 만들지 못했습니다:" \
        "$conflicts" \
        "치우거나 이름을 바꾼 뒤 다시 실행하세요."
  fi
  mkdir -p "$BIN_DIR"
  for name in $LAUNCHERS; do
    ln -sfn "$src/$name" "$BIN_DIR/$name"
  done
}

report() {
  info "" \
       "설치 완료 — $2" \
       "  저장소: $1" \
       "  명령:   guide · exam · shoot"
  case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) info "" \
            "${BIN_DIR} 이 PATH에 없습니다. 셸 설정에 다음 한 줄을 더하세요:" \
            '  export PATH="$HOME/.local/bin:$PATH"' ;;
  esac
  info "" \
       "시작하려면:            guide" \
       "장애 대응 전 사전점검:  shoot doctor"
}

install_managed() {
  if [ ! -d "$INSTALL_DIR" ] || [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    info "저장소를 내려받습니다 → $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  else
    git -C "$INSTALL_DIR" fetch --quiet --tags origin
  fi

  tag="$(latest_release_tag "$INSTALL_DIR")"
  if [ -z "$tag" ]; then
    die "정식 릴리스 태그를 찾지 못했습니다 — $REPO_URL" \
        "  vX.Y.Z 형식의 태그가 하나도 없습니다."
  fi

  git -C "$INSTALL_DIR" checkout --quiet --detach "$tag"
  link_launchers "$INSTALL_DIR"
  report "$INSTALL_DIR" "$tag"
}
```

`main()`의 마지막 줄을 바꾼다.

```bash
  # 교체 전: die "아직 구현되지 않았습니다."
  check_prereqs
  install_managed
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_install -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add install.sh tests/test_install.py
git commit -m "Install the latest release tag, not whatever main happens to be

This repository has no CI: the suite, shoot doctor, and the vendor-branch
regeneration are all hand-run before a tag is cut. main HEAD is the one
state nobody has checked, so learners should never land on it.

Sorting by -v:refname alone is not enough — it can float v9.9.9-rc.1 above
v1.0.0, so the tag pattern filter carries the actual guarantee."
```

---

## Task 3: 업데이트 — 멱등, 태그 이동, 로컬 수정 시 중단

**Files:**
- Modify: `install.sh` (`install_managed` 안)
- Modify: `tests/test_install.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 2의 `install_managed`, `latest_release_tag`, `link_launchers`, `report`
- Produces: 없음 (`install_managed` 내부 동작만 확장)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_install.UpdateTest -v`
Expected: FAIL — `test_rerun_is_idempotent`은 "최신"이 출력에 없어 실패, `test_local_modification_stops_the_update`는 `checkout`이 그냥 진행돼 실패.

- [ ] **Step 3: 구현한다**

`install_managed()`에서 태그 확인 이후, `checkout` 이전에 두 블록을 끼운다.

```bash
  # 교체 전:
  #   git -C "$INSTALL_DIR" checkout --quiet --detach "$tag"

  current="$(git -C "$INSTALL_DIR" describe --tags --exact-match 2>/dev/null || true)"
  if [ "$current" = "$tag" ]; then
    info "이미 최신입니다 — $tag"
    link_launchers "$INSTALL_DIR"
    report "$INSTALL_DIR" "$tag"
    return 0
  fi

  # 추적 파일의 수정만 본다. 미추적 파일(메모·학습 기록)은 업데이트를 막지 않는다.
  dirty="$(git -C "$INSTALL_DIR" status --short --untracked-files=no)"
  if [ -n "$dirty" ]; then
    die "설치본에 로컬 수정이 있어 업데이트를 멈췄습니다 — $INSTALL_DIR" \
        "$dirty" \
        "  되돌리고 업데이트하려면: git -C \"$INSTALL_DIR\" restore ." \
        "  그대로 두려면 업데이트하지 않아도 됩니다."
  fi

  git -C "$INSTALL_DIR" checkout --quiet --detach "$tag"
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_install -v`
Expected: PASS (11 tests)

- [ ] **Step 5: 커밋**

```bash
git add install.sh tests/test_install.py
git commit -m "Stop the update instead of overwriting a modified install

Checked with --untracked-files=no on purpose. Tracked edits mean somebody
changed the install and should decide what happens to it; untracked files
are memos and the gitignored learning records, and blocking on those would
make every update fail for anyone who has played a stage."
```

---

## Task 4: 방어 — 링크 충돌과 비-git 디렉터리

**Files:**
- Modify: `install.sh` (`install_managed` 앞부분)
- Modify: `tests/test_install.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 2의 `link_launchers`, `is_our_link`, `install_managed`
- Produces: 없음

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_install.GuardTest -v`
Expected: 앞의 두 건은 PASS(Task 2의 `link_launchers`가 이미 처리), 세 번째는 FAIL — `git fetch`가 저장소가 아닌 디렉터리에서 실패해 종료 코드가 128이거나 메시지가 영어다.

> 앞의 두 건이 이미 통과하는 것은 정상이다. Task 2에서 충돌 처리를 함께 넣었기 때문이며, 이 태스크는 그 동작에 회귀 테스트를 채우고 남은 구멍 하나를 막는다.

- [ ] **Step 3: 구현한다**

`install_managed()`의 분기를 세 갈래로 늘린다.

```bash
  # 교체 전:
  #   if [ ! -d "$INSTALL_DIR" ] || [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  #     ... git clone ...
  #   else
  #     git -C "$INSTALL_DIR" fetch --quiet --tags origin
  #   fi

  if [ ! -d "$INSTALL_DIR" ] || [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    info "저장소를 내려받습니다 → $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  elif ! is_repo_root "$INSTALL_DIR"; then
    die "$INSTALL_DIR 에 git 저장소가 아닌 내용이 있습니다." \
        "  이 스크립트는 자기가 만들지 않은 디렉터리를 지우지 않습니다." \
        "  직접 확인하고 치운 뒤 다시 실행하세요."
  else
    git -C "$INSTALL_DIR" fetch --quiet --tags origin
  fi
```

`latest_release_tag()` **앞**에 헬퍼를 넣는다.

```bash
# 이 디렉터리 자체가 git 저장소의 루트인가.
#
# `rev-parse --is-inside-work-tree` 를 쓰면 안 된다 — 상위로 올라가며 `.git` 을
# 찾으므로, 홈을 git 으로 관리하는 사람에게는 남의 저장소 안의 평범한
# 디렉터리까지 참으로 답한다. 그대로 통과시키면 이어지는 fetch·checkout 이
# 우리가 만들지도 않은 **바깥 저장소**를 대상으로 돈다.
#
# 심볼릭 링크(macOS의 /tmp → /private/tmp 등) 때문에 양쪽을 모두 `pwd -P` 로
# 정규화해 비교한다.
is_repo_root() {
  root="$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)" || return 1
  [ -n "$root" ] || return 1
  [ "$(cd "$root" && pwd -P)" = "$(cd "$1" && pwd -P)" ]
}
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_install -v`
Expected: PASS (15 tests — 14 from the brief plus the outer-repo regression added in this task's fix round)

- [ ] **Step 5: 커밋**

```bash
git add install.sh tests/test_install.py
git commit -m "Refuse to touch anything the installer did not create

Three cases, one rule: a plain file named guide, a foreign symlink, and a
non-git directory sitting at the install path all stop the run with the
path printed. Conflicts are collected and reported together so the user
does not hit the same wall three times in a row."
```

---

## Task 5: in-place 모드

**Files:**
- Modify: `install.sh` (`detect_inplace`, `install_inplace` 추가 + `main` 분기)
- Modify: `tests/test_install.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 2의 `link_launchers`, `report`
- Produces: `detect_inplace()` (성공 시 저장소 최상위 경로를 표준출력, 아니면 1 반환), `install_inplace <top>`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_install.InPlaceTest -v`
Expected: FAIL — 첫 번째는 클론이 아니라 `install_dir`에 새로 설치돼 실패.

- [ ] **Step 3: 구현한다**

`install_managed()` 아래에 추가한다.

```bash
# 스크립트가 dba-guide 작업 트리 안에 놓여 있으면 그 최상위를 출력한다.
# git 저장소인 것만으로는 부족하다 — 런처와 scripts/guide.py 를 함께 갖고
# 있어야 우리 저장소로 본다. 이름만 같은 남의 저장소를 설치본으로 삼는
# 사고를 막기 위해서다. 파이프 실행 시 BASH_SOURCE는 실재 경로가 아니라
# 자연히 managed 로 떨어진다.
detect_inplace() {
  src="${BASH_SOURCE[0]:-}"
  [ -n "$src" ] && [ -f "$src" ] || return 1
  dir="$(cd "$(dirname "$src")" && pwd)"
  git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 1
  top="$(git -C "$dir" rev-parse --show-toplevel)"
  [ -f "$top/guide" ] || return 1
  [ -f "$top/scripts/guide.py" ] || return 1
  printf '%s\n' "$top"
}

install_inplace() {
  info "저장소 안에서 실행됐습니다 — 이 자리를 그대로 씁니다." \
       "  $1" \
       "  버전은 git으로 직접 관리하세요. 이 스크립트는 HEAD를 옮기지 않습니다." \
       ""
  link_launchers "$1"
  ver="$(git -C "$1" describe --tags --always 2>/dev/null || echo '(버전 정보 없음)')"
  report "$1" "$ver"
}
```

`main()`의 마지막 두 줄을 바꾼다.

```bash
  # 교체 전:
  #   check_prereqs
  #   install_managed

  check_prereqs
  if top="$(detect_inplace)"; then
    install_inplace "$top"
  else
    install_managed
  fi
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_install -v`
Expected: PASS (17 tests)

- [ ] **Step 5: 커밋**

```bash
git add install.sh tests/test_install.py
git commit -m "Link in place when run from inside a clone, and leave HEAD alone

A contributor running ./install.sh in their working clone should not find
their branch detached onto a release tag. Detection requires both the git
work tree and the launchers next to scripts/guide.py, so a same-named
stranger repository does not get adopted as the install."
```

---

## Task 6: 제거 — `--uninstall`과 `--purge`

**Files:**
- Modify: `install.sh` (`uninstall` 추가 + `main` 분기)
- Modify: `tests/test_install.py` (클래스 추가)

**Interfaces:**
- Consumes: Task 2의 `is_our_link`, Task 1의 `main` 인자 파싱
- Produces: `uninstall <purge>` (`purge`는 `"yes"`/`"no"`)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
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
        self.assertIn("시험 결과 2건", r.stdout)
        self.assertIn("정리 노트 1건", r.stdout)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python3 -m unittest tests.test_install.UninstallTest -v`
Expected: FAIL — 4건 모두. `--uninstall`이 `install_managed`로 흘러간다.

- [ ] **Step 3: 구현한다**

`install_inplace()` 아래에 추가한다.

```bash
uninstall() {
  removed=0
  for name in $LAUNCHERS; do
    link="$BIN_DIR/$name"
    if is_our_link "$link" "$name"; then
      rm -f "$link"
      removed=$((removed + 1))
      info "링크 제거 — $link"
    elif [ -e "$link" ] || [ -L "$link" ]; then
      info "건너뜀 (우리가 만든 것이 아닙니다) — $link"
    fi
  done
  if [ "$removed" -eq 0 ]; then
    info "제거할 링크가 없습니다."
  fi

  if [ "$1" != "yes" ]; then
    info "" \
         "저장소는 남겨 뒀습니다 — $INSTALL_DIR" \
         "  학습 기록(시험 결과·정리 노트)이 그 안에 있습니다." \
         "  전부 지우려면: install.sh --uninstall --purge"
    return 0
  fi

  if [ ! -d "$INSTALL_DIR" ]; then
    info "지울 저장소가 없습니다."
    return 0
  fi

  n_exam=0
  if [ -f "$INSTALL_DIR/.exam-results/results.jsonl" ]; then
    n_exam="$(wc -l < "$INSTALL_DIR/.exam-results/results.jsonl" | tr -d ' ')"
  fi
  n_notes=0
  if [ -d "$INSTALL_DIR/.shooting-progress/notes" ]; then
    n_notes="$(find "$INSTALL_DIR/.shooting-progress/notes" -type f -name '*.md' | wc -l | tr -d ' ')"
  fi
  info "" \
       "$INSTALL_DIR 을 지웁니다." \
       "  시험 결과 ${n_exam}건 · 정리 노트 ${n_notes}건이 함께 사라집니다." \
       "  이 기록은 git에 올라가지 않아 다른 사본이 없습니다."

  if [ ! -t 0 ]; then
    die "" \
        "비대화형 실행이라 확인을 받을 수 없어 멈췄습니다." \
        "  정말 지우려면 직접 실행하세요: rm -rf \"$INSTALL_DIR\""
  fi

  printf '정말 지울까요? [y/N] '
  read -r answer
  case "$answer" in
    y|Y|yes|YES) rm -rf "$INSTALL_DIR"; info "삭제했습니다." ;;
    *) info "취소했습니다." ;;
  esac
}
```

`main()`에서 `check_prereqs` **앞에** 분기를 넣는다.

```bash
  if [ "$mode" = "uninstall" ]; then
    uninstall "$purge"
    exit 0
  fi

  check_prereqs
```

- [ ] **Step 4: 통과를 확인한다**

Run: `python3 -m unittest tests.test_install -v`
Expected: PASS (21 tests)

- [ ] **Step 5: 커밋**

```bash
git add install.sh tests/test_install.py
git commit -m "Keep the learning records unless --purge is spelled out

.exam-results and .shooting-progress are gitignored, so the postmortem
notes under them exist nowhere else — CLAUDE.md calls filling those in the
exercise itself. Plain --uninstall drops the symlinks and stops. --purge
counts what would be lost first, and refuses outright without a tty,
because a piped run cannot answer the confirmation."
```

---

## Task 7: 문서

**Files:**
- Modify: `README.md` (94행 `## 한 번에 시작하기` 앞)
- Modify: `docs/release-policy.md` (38-46행 점검 목록, `## 절차` 앞)
- Modify: `CLAUDE.md` (10-13행 도구 목록)

**Interfaces:**
- Consumes: Task 1-6이 확정한 CLI (`install.sh`, `--uninstall`, `--purge`, `--help`)
- Produces: 없음

- [ ] **Step 1: `README.md`에 `## 설치` 절을 넣는다**

`## 한 번에 시작하기`(94행) **바로 앞**에 삽입한다.

```markdown
## 설치

한 줄로 끝난다. 저장소를 `~/.local/share/dba-guide`에 두고 `guide`·`exam`·`shoot`
세 명령을 `~/.local/bin`에 걸어 준다.

```bash
curl -fsSL https://raw.githubusercontent.com/Ahngbeom/dba-guide/main/install.sh | bash
```

같은 명령을 다시 실행하면 최신 릴리스로 **업데이트**된다. 제거는
`… | bash -s -- --uninstall`이며, 이때 학습 기록(시험 결과·정리 노트)은 남는다.
기록까지 지우려면 `--purge`를 덧붙인다.

스크립트를 먼저 읽고 실행하고 싶다면 두 단계로 나눠도 된다.

```bash
curl -fsSL -o install.sh https://raw.githubusercontent.com/Ahngbeom/dba-guide/main/install.sh
less install.sh && bash install.sh
```

**설치하지 않아도 된다.** 클론만 해도 전부 그대로 동작한다.

```bash
git clone https://github.com/Ahngbeom/dba-guide.git
cd dba-guide && ./guide
```

필요한 것은 `git`과 `python3`(3.9 이상)뿐이다. 장애 대응 게임을 하려면 Docker가
추가로 필요하다(`./shoot doctor`가 점검해 준다). Windows에서는 WSL 안에서 쓴다.
```

- [ ] **Step 2: `docs/release-policy.md`에 점검 항목과 순서 의존성을 적는다**

「릴리스 전 점검」 목록(46행 `- [ ] 벤더 브랜치 3개를 …` 다음)에 한 줄 추가한다.

```markdown
- [ ] 릴리스 발행 **후** `curl … | bash`를 한 번 실제로 돌려 새 태그를 집는지 확인했다
```

그리고 `## 절차` 절 끝(현재 `--draft=false` 코드 블록 다음)에 문단을 더한다.

```markdown
### 인스톨러와 태그의 순서

`install.sh`는 **최신 정식 태그**를 설치한다. 따라서 인스톨러 관련 변경을
`main`에 머지해도 태그를 발행하기 전까지는 아무도 그 변경을 설치하지 못한다.
스크립트 자체는 `raw.githubusercontent.com/.../main/install.sh`에서 받으므로
버그 수정은 즉시 반영되지만, **설치되는 내용은 태그를 기다린다.**

그래서 발행 직후 한 번은 실제로 돌려 봐야 한다. CI가 없으므로 이것도 사람 몫이다.
```

- [ ] **Step 3: `CLAUDE.md` 도구 목록에 한 줄 넣는다**

`- \`scripts/generate-branch.sh\` …`(10행) **앞**에 삽입한다.

```markdown
- `install.sh` (repo root) — one-line installer: clones into `${XDG_DATA_HOME:-~/.local/share}/dba-guide`, checks out the **latest release tag** (never `main` HEAD — there is no CI here, so only tagged commits have been hand-checked), and symlinks `guide`/`exam`/`shoot` into `~/.local/bin`. Same script handles update (re-run, idempotent) and removal (`--uninstall`, plus `--purge` to delete the tree). Three rules it must never break: (1) it refuses to touch anything it did not create — a foreign file at a launcher name, a non-git directory at the install path, and tracked local modifications each stop the run; (2) `--uninstall` keeps the directory, because `.exam-results/` and `.shooting-progress/notes/` are gitignored and have no other copy; (3) run from inside a clone it links in place and **never moves HEAD**, so a contributor's branch does not get detached onto a tag. Must stay **bash 3.2 compatible** — macOS ships 3.2.57 and `curl | bash` uses it. Tests: `tests/test_install.py` runs the real script against a `file://` origin, copying it to a temp dir first (running the in-repo copy would make `BASH_SOURCE` trigger in-place mode and void every managed-mode test).
```

- [ ] **Step 4: 전체 스위트와 문서 링크를 확인한다**

Run: `python3 -m unittest discover -s tests`
Expected: 이전 557건 + 신규 21건 = **578 tests, OK**

Run: `./shoot doctor`
Expected: 통과 (문서 변경이 스테이지 파싱을 깨지 않았는지)

- [ ] **Step 5: 커밋**

```bash
git add README.md docs/release-policy.md CLAUDE.md
git commit -m "Document how to get the guide in the first place

README opened on ./guide and never said where the tree comes from — the
acquisition step was missing from every document in the repository. The
new section leads with the installer but keeps plain git clone next to it,
since cloning alone has always been enough to run everything.

The release policy gains the ordering rule that bites once: install.sh is
fetched from main but installs the latest tag, so a merged change reaches
nobody until a release is published."
```

---

## 완료 후 확인 (사람이 직접)

자동화하지 않는 항목이다. `docs/release-policy.md`의 점검 목록으로 넘어간다.

- [ ] `v1.2.0` 발행 후 실제 GitHub에서 `curl -fsSL … | bash` 1회
- [ ] macOS(bash 3.2)에서 1회
- [ ] Linux에서 1회
- [ ] 설치 후 `guide`가 실제로 메뉴를 띄우는지, `shoot doctor`가 도는지
