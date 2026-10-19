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


GENERIC_NAMES = {
    "mouse": "Wireless Mouse", "keyboard": "Wireless Keyboard", "headset": "Wireless Headset",
    "headphones": "Wireless Earbuds", "gamepad": "Game Controller", "phone": "Phone",
    "speaker": "Speaker", "tablet": "Tablet", "other": "Wireless Device",
}
# Words that say nothing about the model, as in "2.4G RF Keyboard & Mouse" or "USB Receiver".
FILLER_WORDS = {"rf", "hid", "device", "keyboard", "mouse", "and", "gaming", "composite", "generic",
                "optical", "headset", "headphones", "controller", "combo", "mini", "pro", "2.4", "5.8g"}


def display_name(name, kind):
    """The device's own name, or a generic one when it names no model at all."""
    if name_tokens(name) - FILLER_WORDS:
        return name
    return GENERIC_NAMES.get(kind, GENERIC_NAMES["other"])


def row(key, name, kind, level=None, state="unknown", detail="", stale=False):
    return dict(id=key, name=display_name(name, kind), icon=ICONS.get(kind, ICONS["other"]),
                level=percent(level), state=state, detail=detail, stale=stale)


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
        vendor = ""
        try:
            hid = uevent(device / "uevent")
            name = name or hid.get("HID_NAME", "")
            address = mac(hid.get("HID_UNIQ", "")) or mac(supply.name)
            bus, vendor_id, _ = hid.get("HID_ID", "::").split(":")
            vendor = vendor_id[-4:].lower() if bus == "0003" else ""  # USB only
        except (OSError, ValueError):
            address = mac(supply.name)
        name = clean_name(name or supply.name)
        kind = kind_from_name(name) or input_kind(device)
        item = row(address or supply.name, name, kind, props.get("POWER_SUPPLY_CAPACITY"),
                   STATES.get(props.get("POWER_SUPPLY_STATUS"), "unknown"))
        item["vendor"] = vendor
        # A sleeping device's receiver can still answer, with a level of 0.
        asleep = item["level"] == 0 and item["state"] not in ("charging", "full")
        if props.get("POWER_SUPPLY_ONLINE", "1") != "1" or asleep:
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


ASM_VENDOR = "1038"  # the only USB vendor whose headsets Arctis Sound Manager drives


def usb_audio_name(vendor, root=Path("/sys/bus/usb/devices")):
    """Product name of the first USB audio device from this vendor, skipping its mice and keyboards."""
    for device in sorted(root.glob("*")):
        try:
            if (device / "idVendor").read_text().strip() != vendor:
                continue
            if any((interface / "bInterfaceClass").read_text().strip() == "01"
                   for interface in device.glob(device.name + ":*")):
                return (device / "product").read_text().strip()
        except OSError:
            continue
    return None


def asm_row(status, name="Headset"):
    """Arctis Sound Manager status; a powered-off headset never shows its retained level."""
    values = {key: value.get("value") for group in status.values() if isinstance(group, dict)
              for key, value in group.items() if isinstance(value, dict)}
    power = values.get("headset_power_status")
    charge = values.get("cable_charging")
    if power in ("off", "offline") and charge != "on":
        return row("arctis", name, "headset", state="offline", detail="Off or out of range")
    state = {"on": "charging", "off": "discharging", "plugged_not_charging": "idle"}.get(charge, "unknown")
    return row("arctis", name, "headset", values.get("headset_battery_charge"), state)


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
            status = json.loads(str(dbus.Interface(proxy, name + ".Status").GetStatus(timeout=3)))
            rows.append(asm_row(status, usb_audio_name(ASM_VENDOR) or "Headset"))
    except (dbus.DBusException, ValueError):
        if asm:
            rows.append(row("arctis", usb_audio_name(ASM_VENDOR) or "Headset", "headset", detail="Could not read status"))
    return rows, asm


CONNECTION_WORDS = {"bt", "ble", "2.4g", "2.4ghz", "dongle", "receiver", "wireless", "usb"}


def name_tokens(name):
    """Model words of a device name, without connection words like "BT1" or "2.4G Dongle"."""
    return {word for word in re.findall(r"[\w.]+", name.lower())
            if word not in CONNECTION_WORDS and not re.fullmatch(r"(bt|ble)\d", word)}


def live(item):
    return item["level"] is not None and not item["stale"]


def same_device(a, b):
    """Same id, or the same device seen over another connection.

    Only checked when one side has no live reading, so two identical
    controllers in use at once both stay visible. A mouse charging over its
    cable and its idle receiver share the USB vendor ID and type. A keyboard on
    Bluetooth shares no ID with its receiver, only its model name.
    """
    if a["id"] == b["id"]:
        return True
    if live(a) and live(b):
        return False
    if a.get("vendor") and a.get("vendor") == b.get("vendor") and a["icon"] == b["icon"]:
        return True
    small, large = sorted((name_tokens(a["name"]), name_tokens(b["name"])), key=len)
    return len(small) >= 2 and small <= large


def quality(item):
    return (live(item), item["level"] is not None, item["state"] != "offline")


def dedupe(devices):
    unique = []
    for item in devices:
        match = next((i for i, kept in enumerate(unique) if same_device(kept, item)), None)
        if match is None:
            unique.append(item)
        elif quality(item) > quality(unique[match]):
            unique[match] = item
    return unique


def collect():
    errors = []
    devices = power_supply_rows()
    for dev, name, result in receiver.keyboards():
        item = row(dev, name, "keyboard", result.get("level"), result.get("state", "unknown"),
                   result.get("detail", ""), result.get("stale", False))
        item["vendor"] = dev[:4]
        devices.append(item)
    extra, asm = dbus_rows(errors)
    # Arctis Sound Manager owns the headset's HID interface; don't compete with it.
    devices += extra + ([] if asm else headsetcontrol_rows())
    return dict(devices=dedupe(devices), errors=errors, updated=int(time.time()))


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False))
