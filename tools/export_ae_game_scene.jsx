/* Export GAME_SCENE and its nested compositions without modifying the AEP. */
(function () {
    var PROJECT_PATH = "F:/Projects/CnC Pinball GUI V3 Python/okletek/Guitar heroe/gitihiri.aep";
    var OUTPUT_PATH = "F:/Projects/CnC Pinball GUI V3 Python/okletek/Guitar heroe/game_scene_export.json";
    var ROOT_COMP_NAME = "GAME_SCENE";

    function quote(value) {
        return '"' + String(value)
            .replace(/\\/g, "\\\\")
            .replace(/"/g, '\\"')
            .replace(/\r/g, "\\r")
            .replace(/\n/g, "\\n")
            .replace(/\t/g, "\\t") + '"';
    }

    function stringify(value) {
        var kind, parts, key, index;
        if (value === null || value === undefined) {
            return "null";
        }
        kind = typeof value;
        if (kind === "number") {
            if (!isFinite(value)) {
                return "null";
            }
            // ExtendScript follows the OS locale, so Hungarian installs turn
            // 44.2 into "44,2", which would corrupt a JSON numeric array.
            return String(value).replace(",", ".");
        }
        if (kind === "boolean") {
            return value ? "true" : "false";
        }
        if (kind === "string") {
            return quote(value);
        }
        if (value instanceof Array) {
            parts = [];
            for (index = 0; index < value.length; index += 1) {
                parts.push(stringify(value[index]));
            }
            return "[" + parts.join(",") + "]";
        }
        parts = [];
        for (key in value) {
            if (value.hasOwnProperty(key) && typeof value[key] !== "function") {
                parts.push(quote(key) + ":" + stringify(value[key]));
            }
        }
        return "{" + parts.join(",") + "}";
    }

    function plainValue(value) {
        var result, index;
        if (value instanceof Array) {
            result = [];
            for (index = 0; index < value.length; index += 1) {
                result.push(plainValue(value[index]));
            }
            return result;
        }
        if (typeof value === "number" || typeof value === "boolean" || typeof value === "string") {
            return value;
        }
        return String(value);
    }

    function enumName(value) {
        try {
            return String(value);
        } catch (error) {
            return null;
        }
    }

    function exportProperty(property, compTime) {
        var output, keyframes, keyIndex, key;
        if (!property) {
            return null;
        }
        output = {
            name: property.name,
            matchName: property.matchName,
            value: plainValue(property.valueAtTime(compTime, false)),
            expressionEnabled: Boolean(property.canSetExpression && property.expressionEnabled),
            expression: property.canSetExpression ? property.expression : "",
            dimensionsSeparated: Boolean(property.dimensionsSeparated),
            keys: []
        };
        keyframes = [];
        for (keyIndex = 1; keyIndex <= property.numKeys; keyIndex += 1) {
            key = {
                time: property.keyTime(keyIndex),
                value: plainValue(property.keyValue(keyIndex))
            };
            try { key.inInterpolation = enumName(property.keyInInterpolationType(keyIndex)); } catch (ignore1) {}
            try { key.outInterpolation = enumName(property.keyOutInterpolationType(keyIndex)); } catch (ignore2) {}
            keyframes.push(key);
        }
        output.keys = keyframes;
        return output;
    }

    function exportTransforms(layer, compTime) {
        var transform = layer.property("ADBE Transform Group");
        var position = transform ? transform.property("ADBE Position") : null;
        var result = {
            anchorPoint: exportProperty(transform && transform.property("ADBE Anchor Point"), compTime),
            position: exportProperty(position, compTime),
            scale: exportProperty(transform && transform.property("ADBE Scale"), compTime),
            rotation: exportProperty(transform && transform.property("ADBE Rotate Z"), compTime),
            opacity: exportProperty(transform && transform.property("ADBE Opacity"), compTime)
        };
        if (layer.threeDLayer) {
            result.orientation = exportProperty(transform.property("ADBE Orientation"), compTime);
            result.xRotation = exportProperty(transform.property("ADBE Rotate X"), compTime);
            result.yRotation = exportProperty(transform.property("ADBE Rotate Y"), compTime);
        }
        if (position && position.dimensionsSeparated) {
            result.positionX = exportProperty(transform.property("ADBE Position_0"), compTime);
            result.positionY = exportProperty(transform.property("ADBE Position_1"), compTime);
            if (layer.threeDLayer) {
                result.positionZ = exportProperty(transform.property("ADBE Position_2"), compTime);
            }
        }
        return result;
    }

    function exportSource(source) {
        var output, sourceFile;
        if (!source) {
            return null;
        }
        output = {
            name: source.name,
            id: source.id,
            type: source instanceof CompItem ? "comp" : (source instanceof FootageItem ? "footage" : "other"),
            width: source.width,
            height: source.height,
            pixelAspect: source.pixelAspect,
            duration: source.duration,
            frameRate: source.frameRate
        };
        if (source instanceof FootageItem) {
            sourceFile = null;
            try { sourceFile = source.file; } catch (ignore1) {}
            if (!sourceFile) {
                try { sourceFile = source.mainSource.file; } catch (ignore2) {}
            }
            output.file = sourceFile ? sourceFile.fsName : null;
            try { output.isStill = Boolean(source.mainSource.isStill); } catch (ignore3) { output.isStill = false; }
            try { output.nativeFrameRate = source.mainSource.nativeFrameRate; } catch (ignore4) { output.nativeFrameRate = null; }
        }
        return output;
    }

    function exportLayer(layer, compTime) {
        var output = {
            index: layer.index,
            name: layer.name,
            enabled: Boolean(layer.enabled),
            activeAtZero: Boolean(layer.activeAtTime(compTime)),
            parentIndex: layer.parent ? layer.parent.index : null,
            parentName: layer.parent ? layer.parent.name : null,
            startTime: layer.startTime,
            inPoint: layer.inPoint,
            outPoint: layer.outPoint,
            stretch: layer.stretch,
            label: layer.label,
            hasVideo: Boolean(layer.hasVideo),
            hasAudio: Boolean(layer.hasAudio),
            audioEnabled: Boolean(layer.hasAudio && layer.audioEnabled),
            threeDLayer: Boolean(layer.threeDLayer),
            adjustmentLayer: Boolean(layer.adjustmentLayer),
            guideLayer: Boolean(layer.guideLayer),
            motionBlur: Boolean(layer.motionBlur),
            shy: Boolean(layer.shy),
            solo: Boolean(layer.solo),
            locked: Boolean(layer.locked),
            blendingMode: enumName(layer.blendingMode),
            source: exportSource(layer.source),
            transform: exportTransforms(layer, compTime)
        };
        try { output.collapseTransformation = Boolean(layer.collapseTransformation); } catch (ignore1) {}
        try { output.preserveTransparency = Boolean(layer.preserveTransparency); } catch (ignore2) {}
        try {
            output.timeRemapEnabled = Boolean(layer.timeRemapEnabled);
            output.timeRemap = layer.timeRemapEnabled ? exportProperty(layer.property("ADBE Time Remapping"), compTime) : null;
        } catch (ignore3) {
            output.timeRemapEnabled = false;
            output.timeRemap = null;
        }
        return output;
    }

    function exportComp(comp, exported) {
        var layers, index, childSource;
        if (exported[comp.id]) {
            return;
        }
        layers = [];
        for (index = 1; index <= comp.numLayers; index += 1) {
            layers.push(exportLayer(comp.layer(index), comp.displayStartTime));
        }
        exported[comp.id] = {
            id: comp.id,
            name: comp.name,
            width: comp.width,
            height: comp.height,
            pixelAspect: comp.pixelAspect,
            duration: comp.duration,
            frameRate: comp.frameRate,
            frameDuration: comp.frameDuration,
            displayStartTime: comp.displayStartTime,
            workAreaStart: comp.workAreaStart,
            workAreaDuration: comp.workAreaDuration,
            bgColor: plainValue(comp.bgColor),
            layers: layers
        };
        for (index = 1; index <= comp.numLayers; index += 1) {
            childSource = comp.layer(index).source;
            if (childSource && childSource instanceof CompItem) {
                exportComp(childSource, exported);
            }
        }
    }

    function findComp(name) {
        var index, item;
        for (index = 1; index <= app.project.numItems; index += 1) {
            item = app.project.item(index);
            if (item instanceof CompItem && item.name === name) {
                return item;
            }
        }
        return null;
    }

    app.beginSuppressDialogs();
    try {
        var projectFile = new File(PROJECT_PATH);
        if (!projectFile.exists) {
            throw new Error("Project not found: " + PROJECT_PATH);
        }
        if (!app.project || !app.project.file || app.project.file.fsName !== projectFile.fsName) {
            app.open(projectFile);
        }
        var rootComp = findComp(ROOT_COMP_NAME);
        if (!rootComp) {
            throw new Error("Composition not found: " + ROOT_COMP_NAME);
        }
        var exported = {};
        exportComp(rootComp, exported);
        var result = {
            exporterVersion: 1,
            project: projectFile.fsName,
            rootComp: ROOT_COMP_NAME,
            exportedAt: (new Date()).toUTCString(),
            comps: exported
        };
        var outputFile = new File(OUTPUT_PATH);
        outputFile.encoding = "UTF-8";
        if (!outputFile.open("w")) {
            throw new Error("Could not open output: " + OUTPUT_PATH);
        }
        outputFile.write(stringify(result));
        outputFile.close();
    } catch (error) {
        var errorFile = new File(OUTPUT_PATH + ".error.txt");
        errorFile.encoding = "UTF-8";
        if (errorFile.open("w")) {
            errorFile.write(error.toString() + "\n" + (error.line || ""));
            errorFile.close();
        }
    } finally {
        app.endSuppressDialogs(false);
    }
}());
