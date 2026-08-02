#!/usr/bin/env bash
# argv-probe.sh — answers one question: when you select several files in Dolphin
# and click a service menu entry, does KDE run this ONCE with every path, or
# once PER file?
#
# All it does is append its own arguments to a log (see LOG= below) and show a
# dialog.
# It does not read, write, move or convert any of the files you select — it
# only prints their names.
#
# Installed by hand into ~/.local/share/kio/servicemenus/ for one test, then
# deleted. See tools/argv-probe/README.md.

# Logged under XDG_RUNTIME_DIR (/run/user/$UID, mode 0700, yours alone) rather
# than /tmp, so a predictable name in a world-writable dir can't be pre-created
# as a symlink and followed by the append below.
LOG="${XDG_RUNTIME_DIR:-$HOME}/dolphin-argv-probe.log"

{
  echo "--- invocation at $(date -Is) ---"
  echo "field code : ${1:-<none>}"
  shift
  echo "argc       : $#"
  i=1
  for a in "$@"; do
    echo "  arg[$i]  : $a"
    i=$((i + 1))
  done
} >> "$LOG"

# Show the result immediately so you don't have to go read the log.
if command -v kdialog >/dev/null 2>&1; then
  kdialog --title "argv probe" \
          --msgbox "This process received $# path(s).

If you selected 3 files and this says 3, %F batches correctly.
If it says 1 (and you get 3 popups), it forks per file.

Full log: $LOG"
fi
