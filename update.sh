#!/usr/bin/env bash
# update.sh — update Easy Convert from this git checkout and reinstall it.
#
# Usage:
#   ./update.sh               update the branch you are on, then reinstall
#   ./update.sh BRANCH        switch to BRANCH (from GitHub) and update that
#   ./update.sh --force       rebuild and reinstall even if nothing changed
#   ./update.sh --yes         don't stop at dnf's "Is this ok?" question
#
# What it does, in order (read it before running it):
#   1. refuses to run as root — only the install step uses sudo
#   2. stops if this checkout has uncommitted changes to tracked files
#   3. git fetch + fast-forward only (never merges, rebases or discards)
#   4. shows what changed
#   5. runs the project's tests (standard library, offline)
#   6. builds the RPM with ./build-rpm.sh (offline)
#   7. installs that exact RPM: dnf install (upgrade) or dnf reinstall (same
#      version), via sudo
#   8. refreshes KDE's menu cache and runs dolphin-easy-convert --check
#
# Network: step 3 only — git talking to this repository's own "origin".
# Nothing is downloaded or installed from anywhere else.

set -euo pipefail

# Everything lives in main(), called on the last line, so bash has read the
# whole script before step 3 can replace this very file with a newer one.
main() {
cd "$(dirname "$0")"

NAME=dolphin-easy-convert
SPEC=packaging/${NAME}.spec
FORCE=0
YES=()
BRANCH=""

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --yes|-y) YES=(-y) ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Unknown option: $arg (see ./update.sh --help)" >&2; exit 2 ;;
    *) BRANCH="$arg" ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mStopped:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. not as root ------------------------------------------------------------
if [[ ${EUID} -eq 0 ]]; then
  fail "run this as yourself, not with sudo — it asks for sudo only to install."
fi

# 2. clean checkout ---------------------------------------------------------
git rev-parse --git-dir >/dev/null 2>&1 \
  || fail "this folder is not a git checkout of Dolphin-Easy-Convert."
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  git status --short --untracked-files=no
  fail "you have local changes (above). Commit or stash them first:  git stash"
fi

current=$(git rev-parse --abbrev-ref HEAD)
if [[ -z "$BRANCH" ]]; then
  [[ "$current" != "HEAD" ]] \
    || fail "not on a branch. Name one, e.g.:  ./update.sh main"
  BRANCH=$current
fi
old=$(git rev-parse HEAD)

# 3. fetch + fast-forward ---------------------------------------------------
say "Fetching ${BRANCH} from GitHub"
git fetch --quiet origin "$BRANCH" \
  || fail "could not fetch '${BRANCH}' (offline, or no such branch?)."

if [[ "$BRANCH" != "$current" ]]; then
  if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    git checkout --quiet "$BRANCH"
  else
    git checkout --quiet -b "$BRANCH" --track "origin/${BRANCH}"
  fi
fi
git merge --ff-only --quiet "origin/${BRANCH}" \
  || fail "your '${BRANCH}' has commits GitHub doesn't, so it can't be
         fast-forwarded. Nothing was changed. To see them:
           git log --oneline origin/${BRANCH}..${BRANCH}"
new=$(git rev-parse HEAD)

# 4. what changed -----------------------------------------------------------
VERSION=$(awk '/^Version:/ {print $2; exit}' "$SPEC")
installed=$(rpm -q --qf '%{VERSION}' "$NAME" 2>/dev/null || true)
[[ "$installed" == *"not installed"* ]] && installed=""

if [[ "$old" == "$new" && "$installed" == "$VERSION" && $FORCE -eq 0 ]]; then
  say "Already up to date — version ${VERSION} is installed."
  echo "    (./update.sh --force rebuilds and reinstalls anyway)"
  exit 0
fi

say "Version: ${installed:-not installed} → ${VERSION}"
if [[ "$old" != "$new" ]]; then
  echo "New changes:"
  git log --no-merges --format='  • %s' "${old}..${new}"
fi

# 5. tests ------------------------------------------------------------------
say "Running tests"
python3 -m unittest discover -s tests -q \
  || fail "tests failed, so nothing was installed. Your current version is
         untouched."

# 6. build ------------------------------------------------------------------
say "Building the package"
build_log=$(mktemp)
if ! ./build-rpm.sh >"$build_log" 2>&1; then
  echo "Last lines of the build log ($build_log):"
  tail -n 25 "$build_log"
  fail "the build failed (above). Nothing was installed."
fi
rm -f "$build_log"
shopt -s nullglob
rpms=(dist/"${NAME}-${VERSION}"-*.noarch.rpm)
(( ${#rpms[@]} == 1 )) || fail "expected one built RPM in dist/, found ${#rpms[@]}."
rpm_file=${rpms[0]}

# 7. install ----------------------------------------------------------------
built=$(rpm -qp --qf '%{VERSION}-%{RELEASE}' "$rpm_file")
have=$(rpm -q --qf '%{VERSION}-%{RELEASE}' "$NAME" 2>/dev/null || true)
if [[ "$have" == "$built" ]]; then
  say "Reinstalling ${built} (sudo)"
  sudo dnf reinstall "${YES[@]}" "./${rpm_file}"
else
  say "Installing ${built} (sudo)"
  sudo dnf install "${YES[@]}" "./${rpm_file}"
fi

# 8. refresh + check --------------------------------------------------------
if command -v kbuildsycoca6 >/dev/null 2>&1; then
  kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
fi
say "Checking the installed version"
dolphin-easy-convert --check || true

cat <<'EOF'

Done. Close every Dolphin window and reopen it to see new menu entries.
A conversion window that was already open keeps the old version until it
is closed.
EOF
}

main "$@"
exit
