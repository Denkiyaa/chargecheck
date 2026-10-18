#!/usr/bin/env bash
# Regenerates the README images from tools/preview.qml.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for scene in hero states; do
    QT_SCALE_FACTOR=2 qml6 tools/preview.qml -- "$PWD/docs/$scene.png"
done
