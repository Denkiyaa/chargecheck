from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package/contents/bin"))
import chargecheck
import receiver


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class PowerSupply(unittest.TestCase):
    def supply(self, root, name, uevent, inputs=()):
        write(root / name / "uevent", uevent)
        for i, (ev, rel) in enumerate(inputs):
            write(root / name / f"device/input/input{i}/capabilities/ev", ev)
            write(root / name / f"device/input/input{i}/capabilities/rel", rel)

    def test_detects_type_from_input_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = "POWER_SUPPLY_SCOPE=Device\nPOWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_CAPACITY=35\n"
            self.supply(root, "hid-a", base + "POWER_SUPPLY_MODEL_NAME=Acme 2.4G Dongle\n", [("1f", "1040"), ("17", "1943")])
            self.supply(root, "hid-b", base + "POWER_SUPPLY_MODEL_NAME=Acme Board\n", [("120013", "0")])
            self.supply(root, "BAT0", "POWER_SUPPLY_SCOPE=System\nPOWER_SUPPLY_CAPACITY=80\n")
            rows = chargecheck.power_supply_rows(root)
        self.assertEqual([(r["name"], r["icon"], r["level"]) for r in rows],
                         [("Acme", "input-mouse", 35), ("Acme Board", "input-keyboard", 35)])

    def test_offline_device_hides_retained_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.supply(root, "m", "POWER_SUPPLY_SCOPE=Device\nPOWER_SUPPLY_ONLINE=0\nPOWER_SUPPLY_CAPACITY=50\n"
                                   "POWER_SUPPLY_MODEL_NAME=Some Mouse\n")
            [item] = chargecheck.power_supply_rows(root)
        self.assertEqual((item["level"], item["state"]), (None, "offline"))

    def test_sleeping_device_reporting_zero_is_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.supply(root, "m", "POWER_SUPPLY_SCOPE=Device\nPOWER_SUPPLY_ONLINE=1\nPOWER_SUPPLY_CAPACITY=0\n"
                                   "POWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_MODEL_NAME=Some Mouse\n")
            [item] = chargecheck.power_supply_rows(root)
        self.assertEqual((item["level"], item["state"]), (None, "offline"))


class Bluetooth(unittest.TestCase):
    def test_disconnected_devices_are_ignored(self):
        objects = {"/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {
            "org.bluez.Device1": {"Connected": False, "Alias": "Wireless Earbuds", "Address": "AA:BB:CC:DD:EE:FF"},
            "org.bluez.Battery1": {"Percentage": 100}}}
        self.assertEqual(chargecheck.bluez_rows(objects), [])
        objects["/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"]["org.bluez.Device1"]["Connected"] = True
        [item] = chargecheck.bluez_rows(objects)
        self.assertEqual((item["id"], item["icon"], item["level"]), ("aa:bb:cc:dd:ee:ff", "audio-headphones", 100))


class Names(unittest.TestCase):
    def test_meaningless_names_become_generic(self):
        for name, kind, expected in (("2.4G RF Keyboard & Mouse", "keyboard", "Wireless Keyboard"),
                                     ("USB Receiver", "mouse", "Wireless Mouse"),
                                     ("Headset", "headset", "Wireless Headset"),
                                     ("Acme M1 Pro", "mouse", "Acme M1 Pro"),
                                     ("Arctis Nova 7", "headset", "Arctis Nova 7")):
            self.assertEqual(chargecheck.row("x", name, kind)["name"], expected)


class Dedupe(unittest.TestCase):
    def test_bluetooth_and_receiver_of_one_keyboard_show_once(self):
        silent = chargecheck.row("320f5088", "Acme K87", "keyboard", detail="Keyboard did not answer")
        bluetooth = chargecheck.row("d1:02:38:55:18:2d", "Acme K87 BT1", "keyboard", 82)
        self.assertEqual(chargecheck.dedupe([silent, bluetooth]), [bluetooth])
        stale = dict(chargecheck.row("320f5088", "Acme K87", "keyboard", 95), stale=True)
        self.assertEqual(chargecheck.dedupe([bluetooth, stale]), [bluetooth])

    def test_cable_and_idle_receiver_of_one_mouse_show_once(self):
        receiver_row = dict(chargecheck.row("r", "Acme Mouse 1K", "mouse", state="offline"), vendor="3554")
        cable = dict(chargecheck.row("c", "Acme M1 Pro", "mouse", 65, "charging"), vendor="3554")
        self.assertEqual(chargecheck.dedupe([receiver_row, cable]), [cable])
        other_brand = dict(cable, vendor="046d")
        self.assertEqual(len(chargecheck.dedupe([receiver_row, other_brand])), 2)

    def test_distinct_devices_stay_separate(self):
        pads = [chargecheck.row(str(i), "Wireless Controller", "gamepad", 50 + i) for i in range(2)]
        self.assertEqual(len(chargecheck.dedupe(pads)), 2)
        mouse = chargecheck.row("m", "Wireless Mouse", "mouse")
        keyboard = chargecheck.row("k", "Wireless Keyboard", "keyboard")
        self.assertEqual(len(chargecheck.dedupe([mouse, keyboard])), 2)


class Headsets(unittest.TestCase):
    def test_asm_ignores_retained_battery_while_off(self):
        status = {"headset": {"headset_power_status": {"value": "off"}, "headset_battery_charge": {"value": 7}}}
        self.assertIsNone(chargecheck.asm_row(status)["level"])
        status["headset"]["headset_power_status"]["value"] = "on"
        status["headset"]["cable_charging"] = {"value": "off"}
        self.assertEqual(chargecheck.asm_row(status)["state"], "discharging")

    def test_headset_name_comes_from_its_usb_audio_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for dev, product, cls in (("1-3", "Some Mouse", "03"), ("1-5.4", "Nova Headset", "01")):
                write(root / dev / "idVendor", "1038\n")
                write(root / dev / "product", product + "\n")
                write(root / dev / f"{dev}:1.0" / "bInterfaceClass", cls + "\n")
            self.assertEqual(chargecheck.usb_audio_name("1038", root), "Nova Headset")
            self.assertIsNone(chargecheck.usb_audio_name("046d", root))

    def test_headsetcontrol_states(self):
        for status, expected in [("BATTERY_CHARGING", "charging"), ("BATTERY_AVAILABLE", "discharging")]:
            result = chargecheck.headset_row({"device": "Arctis Nova 7", "battery": {"status": status, "level": 52}})
            self.assertEqual((result["level"], result["state"]), (52, expected))
        result = chargecheck.headset_row({"battery": {"status": "BATTERY_UNAVAILABLE", "level": -1}})
        self.assertEqual((result["level"], result["state"]), (None, "offline"))

    def test_invalid_percentages(self):
        for value in (None, -1, 101, "NaN", "unknown"):
            self.assertIsNone(chargecheck.percent(value))
        self.assertEqual(chargecheck.percent(0), 0)


class Receiver(unittest.TestCase):
    def test_real_report(self):
        report = bytes.fromhex("0400001a060000006401") + bytes(54)
        self.assertEqual(receiver.parse_report(report), {"level": 100, "state": "charging"})
        self.assertIsNone(receiver.parse_report(bytes(64)))

    def test_queries_only_while_typing(self):
        read = mock.Mock(return_value={"level": 80, "state": "discharging"})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(receiver, "RUNTIME", Path(tmp)):
            battery = lambda now, activity: receiver.battery("kb", "/dev/null", now=now, activity=activity, read=read)
            self.assertIsNone(battery(10_000, 0)["level"])
            self.assertEqual(battery(10_000, 9_990)["level"], 80)
            battery(10_100, 10_090)
            self.assertEqual(read.call_count, 1)
            asleep = battery(30_000, 10_090)
            self.assertEqual((asleep["level"], asleep["stale"], read.call_count), (80, True, 1))

    def test_keeps_reading_while_charging(self):
        read = mock.Mock(return_value={"level": 70, "state": "charging"})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(receiver, "RUNTIME", Path(tmp)):
            battery = lambda now: receiver.battery("kb", "/dev/null", now=now, activity=9_990, read=read)
            battery(10_000)
            idle = battery(20_000)
            self.assertEqual((idle["state"], idle.get("stale"), read.call_count), ("charging", None, 2))
            read.return_value = {"level": None, "state": "unknown"}
            self.assertTrue(battery(30_000)["stale"])
            battery(40_000)
            self.assertEqual(read.call_count, 3)


if __name__ == "__main__":
    unittest.main()
