#target aftereffects
#targetengine "codex_ae_bridge"

(function codexAEBridgePanel(thisObject) {
    function engineScript() {
        var versionFolder = app.version.split(".")[0] + "." + app.version.split(".")[1];
        return new File(Folder.userData.fsName + "/Adobe/After Effects/" + versionFolder + "/Scripts/CodexAEBridge.jsx");
    }

    function ensureBridge() {
        if (!$.global.CodexAEBridge) {
            var script = engineScript();
            if (!script.exists) throw new Error("CodexAEBridge.jsx is not installed in the After Effects Scripts folder.");
            $.evalFile(script);
        }
        return $.global.CodexAEBridge;
    }

    var panel = thisObject instanceof Panel ? thisObject : new Window("palette", "Codex AE Bridge", undefined, { resizeable: true });
    panel.orientation = "column";
    panel.alignChildren = ["fill", "top"];
    panel.margins = 12;

    var statusText = panel.add("statictext", undefined, "Checking bridge...");
    var rootText = panel.add("statictext", undefined, "", { multiline: true });
    rootText.preferredSize.height = 42;

    var buttons = panel.add("group");
    buttons.orientation = "row";
    var startButton = buttons.add("button", undefined, "Start");
    var stopButton = buttons.add("button", undefined, "Stop");
    var refreshButton = buttons.add("button", undefined, "Refresh");

    function refresh() {
        try {
            var bridge = ensureBridge();
            var status = bridge.status();
            statusText.text = status.active ? "ONLINE  v" + status.version : "OFFLINE  v" + status.version;
            rootText.text = "Queue: " + status.root;
        } catch (error) {
            statusText.text = "ERROR";
            rootText.text = String(error.message || error);
        }
        panel.layout.layout(true);
    }

    startButton.onClick = function () {
        ensureBridge().start();
        refresh();
    };
    stopButton.onClick = function () {
        ensureBridge().stop();
        refresh();
    };
    refreshButton.onClick = refresh;
    panel.onResizing = panel.onResize = function () { this.layout.resize(); };

    refresh();
    if (panel instanceof Window) {
        panel.center();
        panel.show();
    }
    return panel;
}(this));
