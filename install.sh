#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
id=io.github.denkiyaa.chargecheck
if kpackagetool6 --type Plasma/Applet --show "$id" >/dev/null 2>&1; then
    kpackagetool6 --type Plasma/Applet --upgrade "$PWD/package"
else
    kpackagetool6 --type Plasma/Applet --install "$PWD/package"
fi
# Restart the keyboard activity watcher so it runs the new version.
pkill -f "chargecheck/contents/bin/receiver.py --watch" || true
