#!/usr/bin/env bash
# Build the dolphin-easy-convert RPM from this checkout.
#
# Needs: rpm-build, libappstream-glib   (sudo dnf install rpm-build libappstream-glib)
# Produces: dist/dolphin-easy-convert-<version>-<release>.noarch.rpm
#
# This script only builds. It never installs anything and never calls sudo.

set -euo pipefail

cd "$(dirname "$0")"

NAME=dolphin-easy-convert
SPEC=packaging/${NAME}.spec
VERSION=$(awk '/^Version:/ {print $2; exit}' "$SPEC")

if ! command -v rpmbuild >/dev/null 2>&1; then
  echo "rpmbuild not found. Install it with:" >&2
  echo "    sudo dnf install rpm-build libappstream-glib" >&2
  exit 1
fi

TOP=$(mktemp -d)
trap 'rm -rf "$TOP"' EXIT
mkdir -p "$TOP"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

echo "==> packing ${NAME}-${VERSION}.tar.gz"
# %autosetup expects the tarball to unpack into <name>-<version>/
if git rev-parse --git-dir >/dev/null 2>&1; then
  git archive --format=tar.gz --prefix="${NAME}-${VERSION}/" \
      -o "$TOP/SOURCES/${NAME}-${VERSION}.tar.gz" HEAD
else
  tar czf "$TOP/SOURCES/${NAME}-${VERSION}.tar.gz" \
      --transform "s,^\.,${NAME}-${VERSION}," \
      --exclude=.git --exclude=dist .
fi

echo "==> rpmbuild"
rpmbuild --define "_topdir $TOP" -bb "$SPEC"

mkdir -p dist
find "$TOP/RPMS" -name '*.rpm' -exec cp -v {} dist/ \;

echo
echo "Built:"
ls -1 dist/*.rpm
echo
echo "Install with:"
echo "    sudo dnf install ./dist/${NAME}-${VERSION}-*.noarch.rpm"
echo
echo "Remove with:"
echo "    sudo dnf remove ${NAME}"
