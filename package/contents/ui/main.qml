pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as Support

PlasmoidItem {
    id: root
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    preferredRepresentation: fullRepresentation
    readonly property int refreshMs: 15000
    readonly property int lowBattery: 15
    property var devices: []
    property string errorText: ""
    property bool busy: false
    property var warned: ({})
    readonly property string script: Qt.resolvedUrl("../bin/chargecheck.py").toString().replace(/^file:\/\//, "")
    readonly property string command: "/usr/bin/python3 '" + decodeURIComponent(script).replace(/'/g, "'\\''") + "'"

    function refresh() {
        if (!busy) {
            busy = true;
            reader.connectSource(command);
        }
    }
    function shellQuote(text) {
        return "'" + String(text).replace(/'/g, "'\\''") + "'";
    }
    Support.DataSource {
        id: reader
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            disconnectSource(sourceName);
            root.busy = false;
            try {
                if (data["exit code"] !== 0) throw new Error("reader");
                const result = JSON.parse(data.stdout);
                root.devices = result.devices;
                root.errorText = result.errors.join(" · ");
                for (const device of result.devices) {
                    if (device.level === null || device.state === "offline" || device.stale) continue;
                    if (device.level > root.lowBattery + 5 || device.state === "charging") {
                        delete root.warned[device.id];
                    } else if (device.level <= root.lowBattery && !root.warned[device.id]) {
                        root.warned[device.id] = true;
                        notifier.connectSource("notify-send -a ChargeCheck -i battery-caution 'Low battery' "
                            + root.shellQuote(device.name + ": " + device.level + "%"));
                    }
                }
            } catch (e) {
                root.devices = [];
                root.errorText = "Could not read batteries";
            }
        }
    }
    Support.DataSource {
        id: notifier
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => disconnectSource(sourceName)
    }
    Timer { interval: root.refreshMs; running: true; repeat: true; onTriggered: root.refresh() }
    Component.onCompleted: refresh()

    // The box hugs the right edge and grows with the device count.
    fullRepresentation: Item {
        readonly property int boxWidth: Math.max(160, root.devices.length * 68 + 20)
        Layout.minimumWidth: boxWidth
        Layout.preferredWidth: boxWidth
        Layout.minimumHeight: 88
        Layout.preferredHeight: 88
        Rectangle {
            anchors { top: parent.top; right: parent.right; bottom: parent.bottom }
            width: parent.boxWidth
            color: "#e619202c"
            radius: 18
            border.color: "#44556477"
            Row {
                anchors.centerIn: parent
                spacing: 6
                Repeater {
                    model: root.devices
                    delegate: DeviceCard {}
                }
            }
            Text {
                anchors.centerIn: parent
                width: parent.width - 24
                visible: root.devices.length === 0
                text: root.errorText || "No battery devices found"
                color: "#8390a3"; font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
            }
        }
    }
}
