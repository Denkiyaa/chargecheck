// Renders the widget's DeviceCard with sample data to docs/*.png for the README.
// Run: tools/screenshots.sh
import QtQuick
import QtQuick.Window
import "../package/contents/ui"

Window {
    id: win
    width: 760; height: 360
    visible: true
    color: "transparent"
    readonly property string out: Qt.application.arguments[Qt.application.arguments.length - 1]
    readonly property var scenes: ({
        hero: [
            {id: "m", name: "Wireless Mouse", icon: "input-mouse", level: 64, state: "discharging", detail: "", stale: false},
            {id: "k", name: "Wireless Keyboard", icon: "input-keyboard", level: 92, state: "charging", detail: "", stale: false},
            {id: "h", name: "Wireless Headset", icon: "audio-headset", level: 76, state: "discharging", detail: "", stale: false},
            {id: "b", name: "Bluetooth Earbuds", icon: "audio-headphones", level: 100, state: "full", detail: "", stale: false},
            {id: "g", name: "Game Controller", icon: "input-gamepad", level: 12, state: "discharging", detail: "", stale: false}
        ],
        states: [
            {id: "k", name: "Wireless Keyboard", icon: "input-keyboard", level: 81, state: "unknown", detail: "Idle / asleep · last read 09:42", stale: true},
            {id: "m", name: "Wireless Mouse", icon: "input-mouse", level: null, state: "offline", detail: "Asleep or off", stale: false},
            {id: "h", name: "Wireless Headset", icon: "audio-headset", level: 18, state: "charging", detail: "", stale: false}
        ]
    })
    readonly property string scene: out.indexOf("states") >= 0 ? "states" : "hero"
    readonly property var devices: scenes[scene]

    Rectangle {
        id: shot
        width: box.width + 80; height: win.scene === "states" ? 200 : 168
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#2b3a55" }
            GradientStop { position: 1; color: "#3d2b4f" }
        }
        Rectangle {
            id: box
            x: 40; y: 40
            width: win.devices.length * 68 + 20; height: 88
            color: "#e619202c"; radius: 18; border.color: "#44556477"
            Row {
                anchors.centerIn: parent
                spacing: 6
                Repeater {
                    model: win.devices
                    delegate: DeviceCard {}
                }
            }
        }
        // Static copy of the hover tooltip so the screenshot shows what it says.
        Rectangle {
            visible: win.scene === "states"
            x: box.x + 4; y: box.y + box.height + 8
            width: tip.implicitWidth + 20; height: tip.implicitHeight + 12
            radius: 6; color: "#f2232a36"; border.color: "#44556477"
            Text {
                id: tip
                anchors.centerIn: parent
                text: "Wireless Keyboard\nIdle / asleep · last read 09:42"
                color: "#e6ebf2"; font.pixelSize: 12; lineHeight: 1.15
            }
        }
    }
    Timer {
        interval: 800; running: true
        onTriggered: shot.grabToImage(result => { result.saveToFile(win.out); Qt.quit(); })
    }
}
