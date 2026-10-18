import QtQuick
import QtQuick.Controls as Controls
import org.kde.kirigami as Kirigami

// One device: battery ring with the device icon, percentage below, details on hover.
Item {
    id: card
    required property var modelData
    property bool showTip: hover.containsMouse
    width: 62; height: 70
    // Stale = last reading of an idle/sleeping device, not live data.
    opacity: modelData.stale ? 0.5 : 1
    readonly property color accent: modelData.state === "offline" || modelData.level === null ? "#8390a3"
        : modelData.state === "charging" ? "#85e9b5" : modelData.level <= 20 ? "#ffaf73" : "#adc4ff"

    function stateText() {
        if (modelData.detail) return modelData.detail;
        switch (modelData.state) {
        case "charging": return "Charging";
        case "discharging": return "On battery";
        case "full": return "Fully charged";
        case "idle": return "Not charging";
        case "offline": return "Disconnected";
        default: return "Charge state unknown";
        }
    }

    Canvas {
        id: ring
        anchors.horizontalCenter: parent.horizontalCenter
        y: 2; width: 46; height: 46
        property real level: card.modelData.level || 0
        property color tint: card.accent
        onLevelChanged: requestPaint()
        onTintChanged: requestPaint()
        onPaint: {
            const ctx = getContext("2d");
            ctx.reset(); ctx.lineWidth = 3;
            ctx.strokeStyle = "#354152";
            ctx.beginPath(); ctx.arc(23, 23, 20, 0, Math.PI * 2); ctx.stroke();
            ctx.strokeStyle = tint; ctx.lineCap = "round";
            ctx.beginPath(); ctx.arc(23, 23, 20, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * level / 100); ctx.stroke();
        }
        Kirigami.Icon {
            anchors.centerIn: parent
            width: 22; height: 22
            source: card.modelData.icon
            color: card.accent
        }
        Rectangle {
            anchors { right: parent.right; bottom: parent.bottom }
            visible: card.modelData.state === "charging"
            width: 15; height: 15; radius: 7.5; color: "#85e9b5"
            Text { anchors.centerIn: parent; text: "ϟ"; color: "#162b24"; font.pixelSize: 14; font.bold: true }
            SequentialAnimation on opacity {
                running: card.modelData.state === "charging"; loops: Animation.Infinite
                NumberAnimation { from: 1; to: 0.4; duration: 1100 }
                NumberAnimation { from: 0.4; to: 1; duration: 1100 }
            }
        }
    }
    Text {
        anchors.horizontalCenter: parent.horizontalCenter; y: 51
        text: card.modelData.level === null ? "—" : (card.modelData.stale ? "~" : "") + card.modelData.level + "%"
        color: card.accent; font.pixelSize: 12; font.weight: Font.DemiBold
    }
    MouseArea {
        id: hover
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
    }
    Controls.ToolTip.visible: card.showTip
    Controls.ToolTip.delay: 300
    Controls.ToolTip.text: card.modelData.name + "\n" + card.stateText()
}
