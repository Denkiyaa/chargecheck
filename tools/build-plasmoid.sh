#!/usr/bin/env bash
# Builds chargecheck.plasmoid for a GitHub release.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../package"
rm -f ../chargecheck.plasmoid
zip -q -r -X ../chargecheck.plasmoid metadata.json contents -x '*__pycache__*' '*.pyc'
echo "$(cd .. && pwd)/chargecheck.plasmoid"
