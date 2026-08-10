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
