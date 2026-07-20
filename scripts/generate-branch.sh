#!/usr/bin/env bash
# Regenerate a single-vendor branch (postgresql/mysql/oracle) from main.
#
# The branch is a derived view, not hand-edited content: this script resets
# it to main's tree, then strips every `<!-- dbms:X -->` block that doesn't
# match the target DBMS. Files without markers pass through unchanged, so
# chapters that haven't been marked up yet still show all three DBMS (safe
# fallback) instead of silently losing content.
#
# Does NOT push. Review the generated worktree, then push yourself if it
# looks right.
set -euo pipefail

VALID_DBMS=(postgresql mysql oracle)
target="${1:-}"

usage() {
  echo "usage: $0 <postgresql|mysql|oracle>" >&2
  exit 2
}

[[ -n "$target" ]] || usage
valid=false
for d in "${VALID_DBMS[@]}"; do
  [[ "$target" == "$d" ]] && valid=true
done
[[ "$valid" == true ]] || usage

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -n "$(git status --short)" ]]; then
  echo "error: working tree is not clean; commit or stash before regenerating a branch" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
worktree_dir="$(cd .. && pwd)/$(basename "$repo_root")-${target}"
main_sha="$(git rev-parse main)"

if git worktree list --porcelain | grep -qx "worktree ${worktree_dir}"; then
  echo "removing stale worktree at ${worktree_dir}"
  git worktree remove --force "${worktree_dir}"
fi

echo "creating worktree for '${target}' at ${worktree_dir} (reset to main@${main_sha:0:12})"
git worktree add "${worktree_dir}" -B "${target}" main

while IFS= read -r -d '' md_file; do
  python3 "${script_dir}/filter_dbms.py" "${target}" "${md_file}"
done < <(find "${worktree_dir}" -name '*.md' -not -path '*/.git/*' -print0)

pushd "${worktree_dir}" >/dev/null
if [[ -n "$(git status --short)" ]]; then
  git add -A
  git commit -m "Regenerate ${target} view from main@${main_sha:0:12}" >/dev/null
  echo "committed ${target} view"
else
  echo "no changes after filtering (branch already matches this main revision)"
fi
popd >/dev/null

cat <<EOF

Done. Review the result:
  cd "${worktree_dir}"
  git log -1 --stat

Push when you're happy with it (force-with-lease is expected here: this
branch is a derived view that gets reset to a fresh commit rooted at
main's current tip on every regeneration, so it's almost never a
fast-forward of what's already on origin after the first push):
  git -C "${worktree_dir}" push --force-with-lease origin ${target}

Clean up the worktree when done:
  git worktree remove "${worktree_dir}"
EOF
