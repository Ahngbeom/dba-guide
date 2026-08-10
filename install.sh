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

# 이 디렉터리가 우리 설치본인가 — git 저장소의 루트인 것만으로는 부족하다.
# 남의 저장소(또는 벤더 브랜치 클론)가 설치 경로에 앉아 있으면 fetch·checkout
# 이 그 저장소를 대상으로 돌고, --purge 는 남의 자료를 지운다. 두 자리 모두
# 이 함수를 통과해야만 손을 댄다.
is_our_install() {
  is_repo_root "$1" || return 1
  [ -f "$1/scripts/guide.py" ]
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
#
# 대상이 아예 없는 끊어진 링크는 우리 것으로 본다. 기여자가 자기 클론에서
# 설치한 뒤 그 클론을 옮기거나 지우면 우리가 만든 링크 세 개가 그대로 끊긴
# 채 남는데, 이를 충돌로 보면 재설치가 영영 막히고 "남의 프로그램이 그
# 이름을 쓰고 있다"는 거짓말을 하게 된다. 실체가 없는 링크는 아무것도
# 가리지 않으므로 덮어써도 잃을 것이 없다.
is_our_link() {
  [ -L "$1" ] || return 1
  target="$(readlink "$1")"
  [ "$(basename "$target")" = "$2" ] || return 1
  [ -e "$target" ] || return 0
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
  # 디렉터리가 아닌 것(일반 파일·끊어진 링크)이 자리를 차지한 경우.
  # 그대로 두면 [ ! -d ] 가 참이라 클론 분기로 흘러 git이 영어 fatal 을
  # 뱉고 128로 죽는다. 같은 상황이니 같은 한국어 안내로 멈춘다.
  if { [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ]; } && [ ! -d "$INSTALL_DIR" ]; then
    die "$INSTALL_DIR 에 디렉터리가 아닌 것이 있습니다." \
        "  이 스크립트는 자기가 만들지 않은 것을 지우지 않습니다." \
        "  직접 확인하고 치운 뒤 다시 실행하세요."
  fi

  if [ ! -d "$INSTALL_DIR" ] || [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    info "저장소를 내려받습니다 → $INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
  elif ! is_our_install "$INSTALL_DIR"; then
    die "$INSTALL_DIR 이 이 학습서의 설치본이 아닙니다." \
        "  git 저장소가 아니거나, 다른 저장소가 그 자리에 있습니다." \
        "  이 스크립트는 자기가 만들지 않은 디렉터리를 지우거나 옮기지 않습니다." \
        "  직접 확인하고 치운 뒤 다시 실행하세요."
  else
    git -C "$INSTALL_DIR" fetch --quiet --tags origin
  fi

  tag="$(latest_release_tag "$INSTALL_DIR")"
  if [ -z "$tag" ]; then
    die "정식 릴리스 태그를 찾지 못했습니다 — $REPO_URL" \
        "  vX.Y.Z 형식의 태그가 하나도 없습니다."
  fi

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
  link_launchers "$INSTALL_DIR"
  report "$INSTALL_DIR" "$tag"
}

# 스크립트가 dba-guide 작업 트리 안에 놓여 있으면 그 최상위를 출력한다.
# git 저장소인 것만으로는 부족하다 — 런처와 scripts/guide.py 를 함께 갖고
# 있어야 우리 저장소로 본다. 이름만 같은 남의 저장소를 설치본으로 삼는
# 사고를 막기 위해서다. 파이프 실행 시 BASH_SOURCE는 실재 경로가 아니라
# 자연히 managed 로 떨어진다.
#
# 최상위 경로는 `git rev-parse --show-toplevel` 이 아니라 `is_repo_root` 로
# 판별한 뒤 원래의 `$dir` 을 그대로 출력한다 — show-toplevel 은 항상 실제
# 경로(symlink 해제)를 돌려주는데, macOS에서는 `$TMPDIR` 자체가 /private 아래
# 심볼릭 링크라 논리 경로와 어긋난다. `is_repo_root` 는 양쪽을 `pwd -P` 로
# 정규화해 비교만 할 뿐 값을 새로 만들어 내지 않으므로 이 문제가 없다.
detect_inplace() {
  src="${BASH_SOURCE[0]:-}"
  [ -n "$src" ] && [ -f "$src" ] || return 1
  dir="$(cd "$(dirname "$src")" && pwd)"
  is_repo_root "$dir" || return 1
  [ -f "$dir/guide" ] || return 1
  [ -f "$dir/scripts/guide.py" ] || return 1
  printf '%s\n' "$dir"
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
       "$INSTALL_DIR 을 지우면" \
       "  시험 결과 ${n_exam}건 · 정리 노트 ${n_notes}건이 함께 사라집니다." \
       "  이 기록은 git에 올라가지 않아 다른 사본이 없습니다."

  if [ ! -t 0 ]; then
    die "" \
        "비대화형 실행이라 확인을 받을 수 없어 멈췄습니다." \
        "  정말 지우려면 직접 실행하세요: rm -rf \"$INSTALL_DIR\""
  fi

  printf '정말 지울까요? [y/N] '
  # EOF(Ctrl-D)면 read가 실패한다. set -e 아래에서 그냥 두면 아무 말 없이
  # 1로 죽으므로, 취소와 똑같이 다룬다.
  if ! read -r answer; then
    info "" "취소했습니다."
    return 0
  fi
  case "$answer" in
    y|Y|yes|YES) rm -rf "$INSTALL_DIR"; info "삭제했습니다." ;;
    *) info "취소했습니다." ;;
  esac
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

  if [ "$mode" = "uninstall" ]; then
    uninstall "$purge"
    exit 0
  fi

  check_prereqs
  if top="$(detect_inplace)"; then
    install_inplace "$top"
  else
    install_managed
  fi
}

main "$@"
