"""Battery query for 2.4 GHz keyboard receivers that don't report a battery to the kernel.

Some receivers only answer a vendor command: 0x1a on HID interface 1, report 4,
padded to the full report size. Only that read-only query is sent; no
configuration, pairing, reset or firmware commands.

Each query is relayed over the air by the receiver and wakes the keyboard, so
it is only sent while the keyboard is being typed on, or while it charges and
runs on cable power. A small watcher process records the time of the last key
event (key contents are discarded); when the keyboard is idle the last reading
is returned marked as stale.
"""
import json
import os
import re
from pathlib import Path
import select
import subprocess
import sys
import time

# USB (vendor, product) IDs of receivers known to answer the query. Keyboards
# on the same firmware likely work too; add them here and to
# udev/70-chargecheck-receiver.rules.
KNOWN = {(0x320F, 0x5088)}

QUERY_INTERVAL = 300      # while typing, at most one query per 5 minutes
ACTIVE_WINDOW = 60        # "in use" = a key event within the last minute
FALLBACK_INTERVAL = 900   # without access to key events, query every 15 minutes
CHARGING_INTERVAL = 60    # on the cable a query costs no battery
EV_REP = 1 << 20          # autorepeat capability: marks keyboard event nodes

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
WATCHER_PID = RUNTIME / "chargecheck-watcher.pid"


def key(vendor, product):
    return f"{vendor:04x}{product:04x}"


def cache_file(dev):
    return RUNTIME / f"chargecheck-{dev}.json"


def activity_file(dev):
    return RUNTIME / f"chargecheck-{dev}-activity"


def parse_report(data):
    if len(data) < 10 or data[:4] != bytes([4, 0, 0, 0x1a]):
        return None
    level, charging = data[8], data[9]
    if level > 100:
        return None
    return {"level": level, "state": {0: "discharging", 1: "charging"}.get(charging, "unknown")}


def product_name(hidraw):
    try:
        name = (hidraw / "device/../../product").read_text().strip()
    except OSError:
        return "Wireless keyboard"
    return re.sub(r"\s+(\d(\.\d)?G\s+)?(Wireless\s+)?(Dongle|Receiver)$", "", name, flags=re.I) or name


def find_receivers(root=Path("/sys/class/hidraw")):
    """Yield (dev key, name, hidraw node) for connected known receivers."""
    for entry in sorted(root.glob("*")):
        try:
            hid_id = dict(line.split("=", 1) for line in (entry / "device/uevent").read_text().splitlines() if "=" in line)["HID_ID"]
            _, vendor, product = (int(part, 16) for part in hid_id.split(":"))
            if (vendor, product) not in KNOWN:
                continue
            if (entry / "device/../bInterfaceNumber").read_text().strip() != "01":
                continue
        except (OSError, KeyError, ValueError):
            continue
        yield key(vendor, product), product_name(entry), "/dev/" + entry.name


def query(node):
    try:
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError:
        return {"level": None, "state": "unknown", "detail": "No permission, run setup-keyboard-access.sh"}
    except OSError:
        return {"level": None, "state": "unknown", "detail": "Receiver not available"}
    try:
        os.write(fd, bytes([4, 0, 0, 0x1a, 6, 0, 0, 0]).ljust(64, b"\0"))
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            result = parse_report(os.read(fd, 64))
            if result:
                return result
    except OSError:
        pass
    finally:
        os.close(fd)
    return {"level": None, "state": "unknown", "detail": "Keyboard did not answer (asleep or off)"}


def keyboard_events(root=Path("/sys/class/input")):
    """Yield (dev key, event node) for key event nodes of known receivers."""
    for event in sorted(root.glob("event*")):
        try:
            ids = event / "device/id"
            vendor, product = int((ids / "vendor").read_text(), 16), int((ids / "product").read_text(), 16)
            if (vendor, product) in KNOWN and int((event / "device/capabilities/ev").read_text(), 16) & EV_REP:
                yield key(vendor, product), "/dev/input/" + event.name
        except (OSError, ValueError):
            continue


def watch_activity():
    """Record the time of the last key event per keyboard; key contents are discarded."""
    WATCHER_PID.write_text(str(os.getpid()))
    while True:
        fds, readable = {}, set()
        for dev, node in keyboard_events():
            try:
                fds[os.open(node, os.O_RDONLY | os.O_NONBLOCK)] = dev
                readable.add(dev)
            except OSError:
                continue
        for dev in {dev for dev, _ in keyboard_events()} - readable:
            activity_file(dev).unlink(missing_ok=True)
        for dev in readable:
            if not activity_file(dev).exists():
                activity_file(dev).write_text("0")
        written = {}
        deadline = time.monotonic() + 30  # rescan for replugged receivers
        try:
            while fds and time.monotonic() < deadline:
                ready, _, _ = select.select(list(fds), [], [], max(0, deadline - time.monotonic()))
                for fd in ready:
                    os.read(fd, 4096)
                    now = time.time()
                    if now - written.get(fds[fd], 0) >= 5:
                        written[fds[fd]] = now
                        activity_file(fds[fd]).write_text(str(int(now)))
        except OSError:
            pass
        for fd in fds:
            os.close(fd)
        if not fds:
            time.sleep(30)


def ensure_watcher():
    try:
        pid = int(WATCHER_PID.read_text())
        if "receiver.py" in Path(f"/proc/{pid}/cmdline").read_text():
            return
    except (OSError, ValueError):
        pass
    subprocess.Popen([sys.executable, __file__, "--watch"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def last_activity(dev):
    """Epoch seconds of the last key event, or None when key events are not readable."""
    try:
        return int(activity_file(dev).read_text())
    except (OSError, ValueError):
        return None


def stale(cached, detail):
    result = dict(cached["result"], state="unknown", stale=True)
    result["detail"] = detail + " · last read " + time.strftime("%H:%M", time.localtime(cached["time"]))
    return result


def battery(dev, node, now=None, activity=None, read=query):
    """Query only while the keyboard is in use or charging; otherwise return the last reading as stale."""
    now = time.time() if now is None else now
    try:
        cached = json.loads(cache_file(dev).read_text())
        cached["time"], cached["result"]["level"]
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    charging = bool(cached) and cached["result"].get("state") == "charging"
    if activity is not None and now - activity > ACTIVE_WINDOW and not charging:
        if cached:
            return stale(cached, "Idle / asleep")
        return {"level": None, "state": "unknown", "detail": "Idle, press a key to read"}
    interval = CHARGING_INTERVAL if charging else QUERY_INTERVAL if activity is not None else FALLBACK_INTERVAL
    if cached and now - cached["time"] < interval:
        return cached["result"]
    result = read(node)
    if result.get("level") is None:
        if not cached:
            return result
        if charging:  # unplugged and asleep: stop querying until it is typed on again
            cached["result"]["state"] = "unknown"
            try:
                cache_file(dev).write_text(json.dumps(cached))
            except OSError:
                pass
        return stale(cached, "No answer")
    try:
        cache_file(dev).write_text(json.dumps({"time": now, "result": result}))
    except OSError:
        pass
    return result


def keyboards():
    """Yield (dev key, name, result) for every connected known keyboard."""
    receivers = list(find_receivers())
    if receivers:
        ensure_watcher()
    for dev, name, node in receivers:
        yield dev, name, battery(dev, node, activity=last_activity(dev))


if __name__ == "__main__":
    if sys.argv[1:] == ["--watch"]:
        watch_activity()
    else:
        print(json.dumps([{"id": dev, "name": name, **result} for dev, name, result in keyboards()]))
