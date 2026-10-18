#!/usr/bin/env bash
# Installs the udev rule for 2.4 GHz keyboard receivers. Run with sudo or pkexec.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
install -m644 udev/70-chargecheck-receiver.rules /etc/udev/rules.d/70-chargecheck-receiver.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw --action=add
udevadm settle
