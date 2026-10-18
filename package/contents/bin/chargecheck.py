#!/usr/bin/env python3
"""Collect peripheral battery levels for the ChargeCheck widget. Read-only.

Sources:
- kernel power_supply devices with scope "Device" (wireless mice, keyboards
  and controllers whose driver registers a battery)
- BlueZ Battery1 of connected Bluetooth devices (earbuds, headphones, ...)
- Arctis Sound Manager's D-Bus status, when it is running
- HeadsetControl, when installed
- 2.4 GHz keyboard receivers that need a vendor battery query, see receiver.py
"""
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time

import receiver

ICONS = {
    "mouse": "input-mouse", "keyboard": "input-keyboard", "headset": "audio-headset",
    "headphones": "audio-headphones", "gamepad": "input-gamepad", "phone": "phone-symbolic",
    "speaker": "audio-speakers-symbolic", "tablet": "input-tablet-symbolic", "other": "battery",
}
STATES = {"Charging": "charging", "Discharging": "discharging", "Full": "full", "Not charging": "idle"}
EV_KEY, EV_REL, EV_ABS, EV_REP = 1 << 1, 1 << 2, 1 << 3, 1 << 20
HEADSETCONTROL_INTERVAL = 60
RUNTIME = receiver.RUNTIME


def percent(value):
    try:
        number = float(value)
        return round(number) if math.isfinite(number) and 0 <= number <= 100 else None
    except (TypeError, ValueError):
        return None


def row(key, name, kind, level=None, state="unknown", detail="", stale=False):
    return dict(id=key, name=name, icon=ICONS.get(kind, ICONS["other"]), level=percent(level),
                state=state, detail=detail, stale=stale)


def mac(text):
    found = re.search(r"([0-9a-f]{2}[:_]){5}[0-9a-f]{2}", str(text).lower())
    return found.group(0).replace("_", ":") if found else None


def clean_name(name):
    return re.sub(r"\s+(\d(\.\d)?G\s+)?(Wireless\s+)?(Dongle|Receiver)$", "", name.strip(), flags=re.I) or name


def kind_from_name(name):
    name = name.lower()
    for words, kind in ((("mouse",), "mouse"), (("keyboard", "keeb"), "keyboard"),
                        (("controller", "gamepad", "joy-con", "dualsense", "dualshock", "xbox"), "gamepad"),
                        (("headset",), "headset"), (("buds", "headphone", "airpods", "earphone"), "headphones")):
        if any(word in name for word in words):
            return kind
    return None


def input_kind(hid):
    """Guess the device type from the input nodes of a HID device.

    The battery often hangs off a secondary interface of a receiver, so the
    input nodes of every interface on the same USB device are considered.
    """
    nodes = sorted((hid / "input").glob("input*"))
    interface = hid.resolve().parent
    if (interface / "bInterfaceNumber").exists():  # USB receiver, not Bluetooth
        nodes += sorted(interface.parent.glob("*/*/input/input*"))
    found = set()
    for node in nodes:
        try:
            caps = node / "capabilities"
            ev, rel = int((caps / "ev").read_text(), 16), int((caps / "rel").read_text().split()[-1], 16)
        except (OSError, ValueError, IndexError):
            continue
        if ev & EV_ABS and not ev & EV_REL:
            found.add("gamepad")
        elif ev & EV_REL and rel & 0b11:
            found.add("mouse")
        elif ev & EV_KEY and ev & EV_REP:
            found.add("keyboard")
    # Mouse receivers also expose a keyboard node for macro keys.
    return next((kind for kind in ("mouse", "gamepad", "keyboard") if kind in found), "other")


def uevent(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def power_supply_rows(root=Path("/sys/class/power_supply")):
    result = []
    for supply in sorted(root.glob("*")):
        try:
            props = uevent(supply / "uevent")
            scope = props.get("POWER_SUPPLY_SCOPE") or (supply / "scope").read_text().strip()
        except OSError:
            continue
        if scope != "Device" or props.get("POWER_SUPPLY_PRESENT", "1") != "1":
            continue
        device = supply / "device"
        name = props.get("POWER_SUPPLY_MODEL_NAME", "")
        try:
            hid = uevent(device / "uevent")
            name = name or hid.get("HID_NAME", "")
            address = mac(hid.get("HID_UNIQ", "")) or mac(supply.name)
        except OSError:
            address = mac(supply.name)
        name = clean_name(name or supply.name)
        kind = kind_from_name(name) or input_kind(device)
        item = row(address or supply.name, name, kind, props.get("POWER_SUPPLY_CAPACITY"),
                   STATES.get(props.get("POWER_SUPPLY_STATUS"), "unknown"))
        if props.get("POWER_SUPPLY_ONLINE", "1") != "1":
            item.update(level=None, state="offline", detail="Asleep or off")
        result.append(item)
    return result


def bluez_rows(objects):
    """Connected Bluetooth devices only; BlueZ keeps batteries of disconnected ones."""
    result = []
    for path, interfaces in objects.items():
        device = interfaces.get("org.bluez.Device1", {})
        battery = interfaces.get("org.bluez.Battery1")
        if not device.get("Connected", False) or battery is None:
            continue
        name = str(device.get("Alias", device.get("Name", "Bluetooth device")))
        icon = str(device.get("Icon", ""))
        kind = kind_from_name(name) or {"input-mouse": "mouse", "input-keyboard": "keyboard", "input-gaming": "gamepad",
                                        "audio-headset": "headset", "audio-headphones": "headphones", "phone": "phone",
                                        "audio-card": "speaker", "input-tablet": "tablet"}.get(icon, "other")
        result.append(row(mac(device.get("Address", path)) or str(path), name, kind, battery.get("Percentage")))
    return result


def asm_row(status):
    """Arctis Sound Manager status; a powered-off headset never shows its retained level."""
    values = {key: value.get("value") for group in status.values() if isinstance(group, dict)
              for key, value in group.items() if isinstance(value, dict)}
    power = values.get("headset_power_status")
    charge = values.get("cable_charging")
    if power in ("off", "offline") and charge != "on":
        return row("arctis", "Headset", "headset", state="offline", detail="Off or out of range")
    state = {"on": "charging", "off": "discharging", "plugged_not_charging": "idle"}.get(charge, "unknown")
    return row("arctis", "Headset", "headset", values.get("headset_battery_charge"), state)


def headset_row(device):
    battery = device.get("battery", {})
    status = battery.get("status", "")
    error = device.get("errors", {}).get("battery", "").lower()
    state = {"BATTERY_CHARGING": "charging", "BATTERY_AVAILABLE": "discharging"}.get(status, "unknown")
    level = percent(battery.get("level")) if state != "unknown" else None
    detail = ""
    if "open device" in error:
        detail = "No permission, install HeadsetControl's udev rules"
    elif "offline" in error or "not connected" in error or status == "BATTERY_UNAVAILABLE":
        state, detail = "offline", "Off or out of range"
    name = device.get("device") or "Headset"
    return row("hc-" + str(device.get("id_vendor")) + str(device.get("id_product")), name, "headset", level, state, detail)


def headsetcontrol_rows(now=None):
    """HeadsetControl talks to the headset directly, so its answer is cached for a minute."""
    binary = shutil.which("headsetcontrol")
    if not binary:
        return []
    now = time.time() if now is None else now
    cache = RUNTIME / "chargecheck-headsetcontrol.json"
    try:
        cached = json.loads(cache.read_text())
        if now - cached["time"] < HEADSETCONTROL_INTERVAL:
            return cached["rows"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        process = subprocess.run([binary, "-b", "-o", "json"], capture_output=True, text=True, timeout=8)
        rows = [headset_row(device) for device in json.loads(process.stdout).get("devices", [])
                if "CAP_BATTERY_STATUS" in device.get("capabilities", [])]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    try:
        cache.write_text(json.dumps({"time": now, "rows": rows}))
    except OSError:
        pass
    return rows


def dbus_rows(errors):
    try:
        import dbus
    except ImportError:
        errors.append("python-dbus not installed")
        return [], False
    rows, asm = [], False
    try:
        bus = dbus.SystemBus()
        manager = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        rows += bluez_rows(manager.GetManagedObjects(timeout=4))
    except dbus.DBusException:
        pass  # no Bluetooth
    try:
        session = dbus.SessionBus()
        name = "name.giacomofurlan.ArctisManager.Next"
        if session.name_has_owner(name):
            asm = True
            proxy = session.get_object(name, "/name/giacomofurlan/ArctisManager/Next/Status")
            rows.append(asm_row(json.loads(str(dbus.Interface(proxy, name + ".Status").GetStatus(timeout=3)))))
    except (dbus.DBusException, ValueError):
        if asm:
            rows.append(row("arctis", "Headset", "headset", detail="Could not read status"))
    return rows, asm


def collect():
    errors = []
    devices = power_supply_rows()
    for dev, name, result in receiver.keyboards():
        devices.append(row(dev, name, "keyboard", result.get("level"), result.get("state", "unknown"),
                           result.get("detail", ""), result.get("stale", False)))
    extra, asm = dbus_rows(errors)
    # Arctis Sound Manager owns the headset's HID interface; don't compete with it.
    devices += extra + ([] if asm else headsetcontrol_rows())
    seen, unique = set(), []
    for item in devices:  # Bluetooth HID devices appear both in power_supply and BlueZ
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    return dict(devices=unique, errors=errors, updated=int(time.time()))


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
