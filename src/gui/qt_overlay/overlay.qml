import QtQuick
import QtQuick.Window
import QtQuick.Layouts
import QtQuick.Effects
import QtQuick.Shapes

Window {
    id: win
    flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
    color: "transparent"
    // Shown/hidden imperatively from Python via showFullScreen()/hide().

    // --- data pushed from Python --------------------------------------
    property string mode: "grid"      // "grid" | "arrows" | "ring"
    property int rows: 3
    property int cols: 3
    property var cells: []            // [{title, icon, app}]
    property int sel: 0               // selected linear index (grid)
    property string accent: "#4CC2FF" // Win11 accent
    property color cellColor: "#202124"  // shared: tile ("mount") base
    property color hlColor: "#2A3A22"    // shared: app-cell highlight
    property color arrowColor: "#F6F4F0"    // gaze-move: the 8 arrows
    property color centerColor: "#F6F4F0"   // gaze-move: centre ring
    property color pointerColor: "#F6F4F0"  // gaze-move: cursor-follow ring
    property string arrowDir: ""      // focused compass dir ("" = centre)
    property real ringX: 0            // cursor-ring centre (ring mode)
    property real ringY: 0
    property real jumpX: 0            // gaze-jump landing point (logical px)
    property real jumpY: 0
    property int jumpTick: 0          // bump to (re)play the landing ripple
    // Landing colours; a jump that snapped onto a clickable thing comes in
    // orange (development aid, see mouse_controller.SNAP_DEBUG_COLOUR).
    property color jumpRingColor: "#FFFFFF"
    property color jumpDotColor: "#80868B"
    // Content fade (the acrylic backdrop is a native effect and appears
    // instantly; the foreground fades in/out over it). Driven from Python.
    property real contentOpacity: 0

    signal escPressed()

    // Card geometry derived from the screen + grid so the layout always
    // fills the screen with comfortable gutters.
    readonly property real gutter: Math.round(Math.min(width, height) * 0.022)
    readonly property real marginX: Math.round(width * 0.06)
    readonly property real marginY: Math.round(height * 0.08)
    readonly property real cellW: (width - marginX * 2 - gutter * (cols - 1)) / cols
    readonly property real cellH: (height - marginY * 2 - gutter * (rows - 1)) / rows

    function cellX(i) { return marginX + (i % cols) * (cellW + gutter) }
    function cellY(i) { return marginY + Math.floor(i / cols) * (cellH + gutter) }

    readonly property var dirs: [
        {name: "N",  dx: 0,  dy: -1}, {name: "NE", dx: 1,  dy: -1},
        {name: "E",  dx: 1,  dy: 0},  {name: "SE", dx: 1,  dy: 1},
        {name: "S",  dx: 0,  dy: 1},  {name: "SW", dx: -1, dy: 1},
        {name: "W",  dx: -1, dy: 0},  {name: "NW", dx: -1, dy: -1}
    ]

    Item {
        id: content
        anchors.fill: parent
        focus: true
        opacity: win.contentOpacity
        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        Keys.onEscapePressed: win.escPressed()

        // ============================== GRID =========================== //
        Item {
            anchors.fill: parent
            visible: win.mode === "grid"
            Repeater {
                model: win.mode === "grid" ? win.cells : []
                delegate: Rectangle {
                    id: tile
                    required property int index
                    required property var modelData
                    readonly property bool isSel: index === win.sel

                    x: win.cellX(index); y: win.cellY(index)
                    width: win.cellW; height: win.cellH
                    radius: Math.round(Math.min(width, height) * 0.10)
                    // Shared colours: app-specific cells use the highlight
                    // colour, the rest the cell ("mount") colour; selection
                    // is a brighter alpha of the same base.
                    color: {
                        var base = modelData.app ? win.hlColor : win.cellColor
                        return Qt.rgba(base.r, base.g, base.b,
                                       isSel ? 0.95 : 0.72)
                    }
                    Behavior on color { ColorAnimation { duration: 160 } }

                    transformOrigin: Item.Center
                    scale: isSel ? 1.085 : 1.0
                    z: isSel ? 2 : 0
                    Behavior on scale { NumberAnimation { duration: 190; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }

                    layer.enabled: true
                    layer.effect: MultiEffect {
                        shadowEnabled: true
                        shadowColor: tile.isSel ? "#73000000" : "#5A000000"
                        shadowBlur: tile.isSel ? 0.95 : 0.5
                        shadowVerticalOffset: tile.isSel ? 16 : 8
                        Behavior on shadowBlur { NumberAnimation { duration: 190 } }
                        Behavior on shadowVerticalOffset { NumberAnimation { duration: 190 } }
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Math.round(parent.height * 0.10)
                        spacing: Math.round(parent.height * 0.04)
                        Image {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.fillHeight: true
                            Layout.fillWidth: true
                            source: modelData.icon ? "file:///" + modelData.icon : ""
                            visible: modelData.icon ? true : false
                            fillMode: Image.PreserveAspectFit
                            smooth: true; mipmap: true
                            sourceSize.width: 256; sourceSize.height: 256
                        }
                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.fillWidth: true
                            text: modelData.title || ""
                            color: "#F3F3F3"
                            font.family: "Segoe UI Variable Display"
                            font.pixelSize: Math.max(14, Math.round(parent.height * (modelData.icon ? 0.11 : 0.22)))
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            maximumLineCount: 2
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }

        // ============================ ARROWS =========================== //
        // Gaze move: 8 compass arrows + a centre ring target. Drawn in QML
        // (NOT PNG images) so the off-white fill (#F6F4F0) is applied
        // directly — the colorized PNGs were rendering grey. The arrow you
        // look toward is enlarged + fully opaque; the rest sit dimmer.
        Item {
            anchors.fill: parent
            visible: win.mode === "arrows"

            readonly property real cx: width / 2
            readonly property real cy: height / 2
            readonly property real ex: width * 0.40
            // Top row (N/NE/NW) sits lower and bottom row (S/SE/SW) higher
            // than the horizontal ones — vertical reach trimmed 0.40 -> 0.36
            // (E/W have dy=0 so are unaffected).
            readonly property real ey: height * 0.36
            readonly property real baseSz: Math.round(Math.min(width, height) * 0.144)

            Repeater {
                model: win.mode === "arrows" ? win.dirs : []
                delegate: Item {
                    id: arrow
                    required property var modelData
                    readonly property bool isSel: win.arrowDir === modelData.name
                    width: parent.baseSz; height: parent.baseSz
                    x: parent.cx + modelData.dx * parent.ex - width / 2
                    y: parent.cy + modelData.dy * parent.ey - height / 2
                    transformOrigin: Item.Center
                    // NOTE: the Item is NOT rotated — only the Shape inside
                    // is. That keeps the drop shadow (applied to this Item's
                    // layer) pointing straight DOWN like the centre ring's,
                    // instead of rotating with each arrow.
                    scale: isSel ? 2.1 : 1.0
                    opacity: isSel ? 1.0 : 0.55
                    Behavior on scale { NumberAnimation { duration: 180; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
                    Behavior on opacity { NumberAnimation { duration: 180 } }
                    layer.enabled: true
                    layer.effect: MultiEffect {
                        shadowEnabled: true
                        // Lighter drop shadow (was #88000000).
                        shadowColor: "#5A000000"
                        shadowBlur: 0.7
                        // Same light position as the centre ring.
                        shadowVerticalOffset: 10
                    }
                    // Solid arrow (triangle head + thicker stem) pointing
                    // up; the Shape carries the per-direction rotation.
                    Shape {
                        anchors.fill: parent
                        transformOrigin: Item.Center
                        rotation: Math.atan2(modelData.dx, -modelData.dy) * 180 / Math.PI
                        ShapePath {
                            fillColor: win.arrowColor
                            strokeWidth: 0
                            startX: arrow.width * 0.50; startY: arrow.height * 0.06
                            PathLine { x: arrow.width * 0.84; y: arrow.height * 0.48 }
                            PathLine { x: arrow.width * 0.645; y: arrow.height * 0.48 }
                            PathLine { x: arrow.width * 0.645; y: arrow.height * 0.94 }
                            PathLine { x: arrow.width * 0.355; y: arrow.height * 0.94 }
                            PathLine { x: arrow.width * 0.355; y: arrow.height * 0.48 }
                            PathLine { x: arrow.width * 0.16; y: arrow.height * 0.48 }
                            PathLine { x: arrow.width * 0.50; y: arrow.height * 0.06 }
                        }
                    }
                }
            }

            // Centre target — a ring, highlighted when gaze is central.
            Rectangle {
                id: centerDot
                readonly property bool isSel: win.arrowDir === ""
                width: parent.baseSz * 0.72; height: width; radius: width / 2
                x: parent.cx - width / 2
                y: parent.cy - height / 2
                color: "transparent"
                border.width: Math.max(4, width * 0.16)
                border.color: win.centerColor
                antialiasing: true
                transformOrigin: Item.Center
                scale: centerDot.isSel ? 2.1 : 1.0
                opacity: centerDot.isSel ? 1.0 : 0.55
                Behavior on scale { NumberAnimation { duration: 180; easing.type: Easing.OutBack } }
                Behavior on opacity { NumberAnimation { duration: 180 } }
                layer.enabled: true
                layer.effect: MultiEffect {
                    shadowEnabled: true
                    // Lighter drop shadow (was #88000000).
                    shadowColor: "#5A000000"
                    shadowBlur: 0.7
                    // Light from higher overhead: longer downward drop.
                    shadowVerticalOffset: 10
                }
            }
        }
    }

    // ============================== JUMP =============================== //
    // Gaze-jump landing flourish: a sonar ripple + a quick centre flash at
    // the snap point. Outside the fading content (and shown over a fully
    // transparent, un-frosted window). Replays whenever jumpTick changes.
    Item {
        id: jumpLayer
        anchors.fill: parent
        visible: win.mode === "jump"
        // Single 0->1 progress drives the whole "target lock" flourish.
        property real a: 1
        NumberAnimation on a {
            id: jumpAnim
            running: false
            from: 0; to: 1; duration: 620; easing.type: Easing.OutCubic
        }
        Connections {
            target: win
            function onJumpTickChanged() { jumpAnim.restart() }
        }

        // Two expanding rings (the 2nd lags for a sonar feel), each with a
        // soft drop shadow.
        Rectangle {
            readonly property real p: jumpLayer.a
            width: 70 + p * 300; height: width; radius: width / 2
            x: win.jumpX - width / 2; y: win.jumpY - height / 2
            color: "transparent"
            border.width: Math.max(2, 10 * (1 - p))
            border.color: win.jumpRingColor
            antialiasing: true
            opacity: 1 - p
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: "#80000000"
                shadowBlur: 0.8
                shadowVerticalOffset: 4
            }
        }
        Rectangle {
            readonly property real p: Math.max(0, jumpLayer.a - 0.18) / 0.82
            width: 70 + p * 220; height: width; radius: width / 2
            x: win.jumpX - width / 2; y: win.jumpY - height / 2
            color: "transparent"
            border.width: Math.max(1, 7 * (1 - p))
            border.color: win.jumpRingColor
            antialiasing: true
            opacity: (jumpLayer.a > 0.18 ? 1 : 0) * (1 - p) * 0.8
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: "#80000000"
                shadowBlur: 0.8
                shadowVerticalOffset: 4
            }
        }

        // Centre dot — pops then fades. Greyscale fill with a white rim.
        Rectangle {
            width: 30; height: 30; radius: 15
            x: win.jumpX - width / 2; y: win.jumpY - height / 2
            color: win.jumpDotColor
            border.width: 3
            border.color: "#FFFFFF"
            antialiasing: true
            transformOrigin: Item.Center
            scale: 0.6 + jumpLayer.a * 1.0
            opacity: 1 - jumpLayer.a
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: "#80000000"
                shadowBlur: 0.9
                shadowVerticalOffset: 5
            }
        }
    }

    // ============================== RING =============================== //
    // Cursor-follow ring during the glide (outside the fading content so it
    // stays crisp while the move runs). FacePilot palette: same off-white
    // (#F6F4F0) as the arrows / centre target.
    Item {
        anchors.fill: parent
        visible: win.mode === "ring"
        Rectangle {
            width: 60; height: 60; radius: 30
            x: win.ringX - 30; y: win.ringY - 30
            color: "transparent"
            border.width: 7
            border.color: win.pointerColor
            antialiasing: true
        }
    }
}
