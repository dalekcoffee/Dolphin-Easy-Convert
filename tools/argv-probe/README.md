# argv probe — one throwaway test

Determines whether Dolphin passes a multi-file selection to a service menu as
**one process with many arguments** (`%F`) or **many processes with one argument
each**. That single fact decides whether Easy Convert shows one progress window
for a batch or one per file.

Nothing here is part of the final package. It is installed by hand, used once,
and deleted.

## What it does

`argv-probe.sh` appends its own arguments to `/tmp/dolphin-argv-probe.log` and
pops up a dialog with the count. It never opens, modifies, moves or converts the
files you select — it only prints their paths.

## Install (user-level, no sudo)

```bash
mkdir -p ~/.local/bin ~/.local/share/kio/servicemenus
cp tools/argv-probe/argv-probe.sh ~/.local/bin/
chmod +x ~/.local/bin/argv-probe.sh

# write the menu file with your real home path baked into Exec=
sed "s|@BIN@|$HOME/.local/bin/argv-probe.sh|" \
    tools/argv-probe/argv-probe.desktop.in \
    > ~/.local/share/kio/servicemenus/zz-argv-probe.desktop
chmod +x ~/.local/share/kio/servicemenus/zz-argv-probe.desktop
```

The `chmod +x` on the `.desktop` is required in `~/.local/share/...` (KDE treats
that location as unauthorized without it). System files under `/usr/share` do not
need it — which is why the final RPM ships mode `0644`.

## Run the test

1. Open Dolphin, select **exactly 3 files** (any type — try images).
2. Right-click → **ARGV PROBE (%F)**.
3. Note what the dialog says, then repeat with **ARGV PROBE (%f)**.

If a new menu entry does not appear, log out and back in — Dolphin caches
service menus. `kbuildsycoca6 --noincremental` may also refresh it.

## Report back

```bash
cat /tmp/dolphin-argv-probe.log
```

## Uninstall — removes every trace

```bash
rm -f ~/.local/share/kio/servicemenus/zz-argv-probe.desktop \
      ~/.local/bin/argv-probe.sh \
      /tmp/dolphin-argv-probe.log
```
