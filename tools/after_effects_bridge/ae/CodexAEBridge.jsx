#target aftereffects
#targetengine "codex_ae_bridge"

(function () {
    var BRIDGE_VERSION = "0.1.41";
    var POLL_MS = 200;
    var HEARTBEAT_MS = 750;

    function pad(value, length) {
        var text = String(value);
        while (text.length < length) text = "0" + text;
        return text;
    }

    function isoNow() {
        var d = new Date();
        return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1, 2) + "-" + pad(d.getUTCDate(), 2)
            + "T" + pad(d.getUTCHours(), 2) + ":" + pad(d.getUTCMinutes(), 2) + ":" + pad(d.getUTCSeconds(), 2)
            + "." + pad(d.getUTCMilliseconds(), 3) + "Z";
    }

    function escapeJson(text) {
        return String(text)
            .replace(/\\/g, "\\\\")
            .replace(/\"/g, "\\\"")
            .replace(/\r/g, "\\r")
            .replace(/\n/g, "\\n")
            .replace(/\t/g, "\\t")
            .replace(/[\u0000-\u001f]/g, function (character) {
                return "\\u" + pad(character.charCodeAt(0).toString(16), 4);
            });
    }

    function jsonStringify(value) {
        if (value === null || value === undefined) return "null";
        if (typeof value === "number") return isFinite(value) ? String(value) : "null";
        if (typeof value === "boolean") return value ? "true" : "false";
        if (typeof value === "string") return "\"" + escapeJson(value) + "\"";
        if (value instanceof Array) {
            var arrayParts = [];
            for (var i = 0; i < value.length; i++) arrayParts.push(jsonStringify(value[i]));
            return "[" + arrayParts.join(",") + "]";
        }
        var parts = [];
        for (var key in value) {
            if (value.hasOwnProperty(key) && typeof value[key] !== "function") {
                parts.push(jsonStringify(key) + ":" + jsonStringify(value[key]));
            }
        }
        return "{" + parts.join(",") + "}";
    }

    function parseJson(text) {
        return eval("(" + text + ")");
    }

    function ensureFolder(folder) {
        if (folder.exists) return true;
        if (folder.parent && !folder.parent.exists) ensureFolder(folder.parent);
        return folder.create();
    }

    function readText(file) {
        if (!file.open("r")) throw new Error("Cannot open file for reading: " + file.fsName);
        file.encoding = "UTF-8";
        var text = file.read();
        file.close();
        return text;
    }

    function writeTextAtomic(file, text) {
        ensureFolder(file.parent);
        var temp = new File(file.parent.fsName + "/" + file.name + ".tmp");
        if (temp.exists) temp.remove();
        if (!temp.open("w")) throw new Error("Cannot open file for writing: " + temp.fsName);
        temp.encoding = "UTF-8";
        temp.write(text);
        temp.close();
        if (file.exists) file.remove();
        if (!temp.rename(file.name)) throw new Error("Cannot finalize file: " + file.fsName);
    }

    function safeError(error) {
        return {
            message: error && error.message ? String(error.message) : String(error),
            file: error && error.fileName ? String(error.fileName) : null,
            line: error && error.line ? Number(error.line) : null,
            stack: error && error.stack ? String(error.stack) : null
        };
    }

    function requireProject() {
        if (!app.project) throw new Error("No After Effects project is open.");
        return app.project;
    }

    function itemKind(item) {
        if (item instanceof CompItem) return "comp";
        if (item instanceof FolderItem) return "folder";
        if (item instanceof FootageItem) return "footage";
        return "item";
    }

    function findProjectItem(reference, expectedKind) {
        var project = requireProject();
        var matches = [];
        var i;
        if (typeof reference === "number") {
            for (i = 1; i <= project.numItems; i++) {
                if (project.item(i).id === reference) return project.item(i);
            }
            throw new Error("Project item ID not found: " + reference);
        }
        var wanted = String(reference);
        for (i = 1; i <= project.numItems; i++) {
            var candidate = project.item(i);
            if (candidate.name === wanted && (!expectedKind || itemKind(candidate) === expectedKind)) matches.push(candidate);
        }
        if (matches.length === 0 && /^\d+$/.test(wanted)) return findProjectItem(Number(wanted), expectedKind);
        if (matches.length === 0) throw new Error("Project item not found: " + wanted);
        if (matches.length > 1) throw new Error("Project item name is ambiguous; use its numeric ID: " + wanted);
        return matches[0];
    }

    function findComp(reference) {
        var item = findProjectItem(reference, "comp");
        if (!(item instanceof CompItem)) throw new Error("Item is not a composition: " + reference);
        return item;
    }

    function findLayer(comp, reference) {
        if (typeof reference === "number") {
            if (reference < 1 || reference > comp.numLayers) throw new Error("Layer index out of range: " + reference);
            return comp.layer(reference);
        }
        var wanted = String(reference);
        var matches = [];
        for (var i = 1; i <= comp.numLayers; i++) {
            if (comp.layer(i).name === wanted) matches.push(comp.layer(i));
        }
        if (matches.length === 0 && /^\d+$/.test(wanted)) return findLayer(comp, Number(wanted));
        if (matches.length === 0) throw new Error("Layer not found: " + wanted);
        if (matches.length > 1) throw new Error("Layer name is ambiguous; use its numeric index: " + wanted);
        return matches[0];
    }

    function safeValue(property) {
        try {
            var value = property.value;
            if (value instanceof Array) {
                var copied = [];
                for (var i = 0; i < value.length; i++) copied.push(value[i]);
                return copied;
            }
            if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") return value;
        } catch (ignored) {}
        return null;
    }

    function transformSnapshot(layer) {
        var transform = layer.property("ADBE Transform Group");
        if (!transform) return null;
        var snapshot = {};
        var mappings = {
            anchor: "ADBE Anchor Point",
            position: "ADBE Position",
            scale: "ADBE Scale",
            rotation: "ADBE Rotate Z",
            opacity: "ADBE Opacity"
        };
        for (var key in mappings) {
            if (mappings.hasOwnProperty(key)) {
                var property = transform.property(mappings[key]);
                if (property) snapshot[key] = safeValue(property);
            }
        }
        return snapshot;
    }

    function serializeItem(item) {
        var output = {
            id: item.id,
            name: item.name,
            type: itemKind(item),
            parent_folder: item.parentFolder ? item.parentFolder.name : null
        };
        if (item instanceof CompItem) {
            output.width = item.width;
            output.height = item.height;
            output.duration = item.duration;
            output.fps = item.frameRate;
            output.layers = item.numLayers;
        } else if (item instanceof FootageItem) {
            output.width = item.width;
            output.height = item.height;
            output.duration = item.duration;
            try { output.path = item.file ? item.file.fsName : null; } catch (ignored) { output.path = null; }
            try {
                if (item.mainSource instanceof SolidSource) {
                    output.is_solid = true;
                    output.solid_color = item.mainSource.color;
                }
            } catch (ignoredSolid) {}
        }
        return output;
    }

    function enumText(value) {
        try { return String(value); } catch (ignored) { return null; }
    }

    function propertyAnimationSnapshot(property, maxKeys) {
        var output = {
            name: property.name,
            match_name: property.matchName,
            value: safeValue(property),
            key_count: property.numKeys || 0
        };
        try {
            output.expression_enabled = property.expressionEnabled === true;
            if (output.expression_enabled) output.expression = property.expression;
        } catch (ignoredExpression) {}
        if (output.key_count > 0) {
            output.keys = [];
            var limit = Math.min(output.key_count, maxKeys || 20);
            for (var i = 1; i <= limit; i++) {
                var key = { time: property.keyTime(i) };
                try { key.value = property.keyValue(i); } catch (ignoredValue) { key.value = null; }
                try { key.in_type = enumText(property.keyInInterpolationType(i)); } catch (ignoredIn) {}
                try { key.out_type = enumText(property.keyOutInterpolationType(i)); } catch (ignoredOut) {}
                output.keys.push(key);
            }
            output.keys_truncated = output.key_count > limit;
        }
        return output;
    }

    function transformAnimationSnapshot(layer) {
        var transform = layer.property("ADBE Transform Group");
        if (!transform) return [];
        var matchNames = ["ADBE Anchor Point", "ADBE Position", "ADBE Scale", "ADBE Rotate Z", "ADBE Opacity"];
        var animated = [];
        for (var i = 0; i < matchNames.length; i++) {
            var property = transform.property(matchNames[i]);
            if (property && (property.numKeys > 0 || property.expressionEnabled === true)) {
                animated.push(propertyAnimationSnapshot(property, 30));
            }
        }
        return animated;
    }

    function effectSnapshots(layer) {
        var parade = layer.property("ADBE Effect Parade");
        var effects = [];
        if (!parade) return effects;
        var effectLimit = Math.min(parade.numProperties, 40);
        for (var i = 1; i <= effectLimit; i++) {
            var effect = parade.property(i);
            var effectData = {
                index: i,
                name: effect.name,
                match_name: effect.matchName,
                enabled: effect.enabled,
                properties: []
            };
            var propertyLimit = Math.min(effect.numProperties, 40);
            for (var j = 1; j <= propertyLimit; j++) {
                var property = effect.property(j);
                if (property.propertyType === PropertyType.PROPERTY) {
                    effectData.properties.push(propertyAnimationSnapshot(property, 20));
                }
            }
            effectData.properties_truncated = effect.numProperties > propertyLimit;
            effects.push(effectData);
        }
        return effects;
    }

    function maskSnapshots(layer) {
        var masks = layer.property("ADBE Mask Parade");
        var output = [];
        if (!masks) return output;
        for (var i = 1; i <= masks.numProperties; i++) {
            var mask = masks.property(i);
            output.push({
                index: i,
                name: mask.name,
                mode: enumText(mask.maskMode),
                inverted: mask.inverted,
                opacity: safeValue(mask.property("ADBE Mask Opacity")),
                feather: safeValue(mask.property("ADBE Mask Feather")),
                expansion: safeValue(mask.property("ADBE Mask Offset"))
            });
        }
        return output;
    }

    function textSnapshot(layer) {
        try {
            var property = layer.property("ADBE Text Properties").property("ADBE Text Document");
            if (!property) return null;
            var document = property.value;
            return {
                text: document.text,
                font: document.font,
                font_size: document.fontSize,
                fill_color: document.fillColor,
                stroke_color: document.strokeColor,
                apply_fill: document.applyFill,
                apply_stroke: document.applyStroke,
                stroke_width: document.strokeWidth,
                animation: propertyAnimationSnapshot(property, 20)
            };
        } catch (ignored) {
            return null;
        }
    }

    function serializeLayer(layer) {
        var source = null;
        try {
            source = layer.source ? { id: layer.source.id, name: layer.source.name, type: itemKind(layer.source) } : null;
        } catch (ignored) {}
        var output = {
            index: layer.index,
            name: layer.name,
            type: layer instanceof TextLayer ? "text" : (layer instanceof ShapeLayer ? "shape" : "av"),
            enabled: layer.enabled,
            locked: layer.locked,
            shy: layer.shy,
            three_d: layer.threeDLayer,
            adjustment: layer.adjustmentLayer,
            guide: layer.guideLayer,
            motion_blur: layer.motionBlur,
            blending_mode: enumText(layer.blendingMode),
            track_matte_type: enumText(layer.trackMatteType),
            start_time: layer.startTime,
            in_point: layer.inPoint,
            out_point: layer.outPoint,
            source: source,
            transform: transformSnapshot(layer),
            transform_animation: transformAnimationSnapshot(layer),
            masks: maskSnapshots(layer),
            effects: effectSnapshots(layer)
        };
        var text = textSnapshot(layer);
        if (text) output.text = text;
        return output;
    }

    function compSnapshot(comp, maxLayers) {
        var layers = [];
        var limit = Math.min(comp.numLayers, maxLayers || 250);
        for (var i = 1; i <= limit; i++) layers.push(serializeLayer(comp.layer(i)));
        return {
            id: comp.id,
            name: comp.name,
            width: comp.width,
            height: comp.height,
            pixel_aspect: comp.pixelAspect,
            duration: comp.duration,
            fps: comp.frameRate,
            time: comp.time,
            work_area_start: comp.workAreaStart,
            work_area_duration: comp.workAreaDuration,
            background_color: comp.bgColor,
            layer_count: comp.numLayers,
            layers_truncated: comp.numLayers > limit,
            layers: layers
        };
    }

    function withUndo(name, callback) {
        app.beginUndoGroup(name);
        try {
            return callback();
        } finally {
            app.endUndoGroup();
        }
    }

    function uniqueCompName(baseName) {
        var project = requireProject();
        for (var version = 1; version < 1000; version++) {
            var candidate = baseName + "_codex_v" + pad(version, 2);
            var exists = false;
            for (var i = 1; i <= project.numItems; i++) {
                if (project.item(i).name === candidate) {
                    exists = true;
                    break;
                }
            }
            if (!exists) return candidate;
        }
        throw new Error("Could not create a unique Codex composition name.");
    }

    function normalizedVector(property, value) {
        if (!(value instanceof Array)) return value;
        var current = property.value;
        if (!(current instanceof Array) || value.length === current.length) return value;
        var normalized = [];
        for (var i = 0; i < current.length; i++) normalized.push(i < value.length ? value[i] : current[i]);
        return normalized;
    }

    function setTransformProperty(layer, matchName, value, time) {
        if (value === undefined || value === null) return;
        var transform = layer.property("ADBE Transform Group");
        var property = transform ? transform.property(matchName) : null;
        if (!property) throw new Error("Layer does not expose transform property: " + matchName);
        var normalized = normalizedVector(property, value);
        if (time !== undefined && time !== null) property.setValueAtTime(time, normalized);
        else property.setValue(normalized);
    }

    function applyTransform(layer, args) {
        setTransformProperty(layer, "ADBE Anchor Point", args.anchor, args.time);
        setTransformProperty(layer, "ADBE Position", args.position, args.time);
        setTransformProperty(layer, "ADBE Scale", args.scale, args.time);
        setTransformProperty(layer, "ADBE Rotate Z", args.rotation, args.time);
        setTransformProperty(layer, "ADBE Opacity", args.opacity, args.time);
    }

    function commandPing() {
        var project = requireProject();
        var active = project.activeItem;
        return {
            bridge_version: BRIDGE_VERSION,
            app_name: app.name || "Adobe After Effects",
            app_version: app.version,
            project_name: project.file ? project.file.name : "Untitled Project",
            project_path: project.file ? project.file.fsName : null,
            active_item: active ? serializeItem(active) : null,
            item_count: project.numItems
        };
    }

    function commandGetProject(args) {
        var project = requireProject();
        var maxItems = args.max_items || 500;
        var items = [];
        var limit = Math.min(project.numItems, maxItems);
        for (var i = 1; i <= limit; i++) items.push(serializeItem(project.item(i)));
        var selected = [];
        for (i = 0; i < project.selection.length; i++) selected.push(serializeItem(project.selection[i]));
        return {
            name: project.file ? project.file.name : "Untitled Project",
            path: project.file ? project.file.fsName : null,
            item_count: project.numItems,
            items_truncated: project.numItems > limit,
            active_item: project.activeItem ? serializeItem(project.activeItem) : null,
            selected_items: selected,
            items: items
        };
    }

    function commandCreateComp(args) {
        return withUndo("Codex: Create composition", function () {
            var comp = requireProject().items.addComp(
                args.name,
                args.width || 640,
                args.height || 480,
                args.pixel_aspect || 1,
                args.duration || 5,
                args.fps || 30
            );
            comp.openInViewer();
            return compSnapshot(comp, 25);
        });
    }

    function commandDuplicateComp(args) {
        return withUndo("Codex: Duplicate composition", function () {
            var source = findComp(args.comp);
            var copy = source.duplicate();
            copy.name = args.new_name || uniqueCompName(source.name);
            if (args.open !== false) copy.openInViewer();
            return { source: serializeItem(source), duplicate: compSnapshot(copy, 250) };
        });
    }

    function scaleLayerAnimation(layer, factor) {
        var scale = layer.property("ADBE Transform Group").property("ADBE Scale");
        if (!scale) return;
        if (scale.numKeys > 0) {
            for (var i = 1; i <= scale.numKeys; i++) {
                var keyed = scale.keyValue(i);
                scale.setValueAtKey(i, [keyed[0] * factor, keyed[1] * factor, keyed.length > 2 ? keyed[2] : 100]);
            }
        } else {
            var current = scale.value;
            scale.setValue([current[0] * factor, current[1] * factor, current.length > 2 ? current[2] : 100]);
        }
    }

    function setScoreLayerText(layer, text) {
        var sourceText = layer.property("ADBE Text Properties").property("ADBE Text Document");
        if (!sourceText || sourceText.expressionEnabled === true) return false;
        var document = sourceText.value;
        document.text = text;
        sourceText.setValue(document);
        return true;
    }

    function recenterTextAnchor(layer, time) {
        var rect = layer.sourceRectAtTime(time, false);
        layer.property("ADBE Transform Group").property("ADBE Anchor Point")
            .setValue([rect.left + rect.width / 2, rect.top + rect.height / 2, 0]);
        return rect;
    }

    function commandDuplicateScoreVariants(args) {
        return withUndo("Codex: Duplicate score variants", function () {
            var source = findComp(args.template_comp);
            var heroName = args.hero_layer || "SCORE | Editable gold face";
            var sourceHero = findLayer(source, heroName);
            if (!(sourceHero instanceof TextLayer)) throw new Error("Editable score layer is not a text layer: " + heroName);
            var sampleTime = args.sample_time === undefined ? Math.min(2.5, source.duration - source.frameDuration) : Number(args.sample_time);
            var sourceRect = sourceHero.sourceRectAtTime(sampleTime, false);
            var maxWidth = args.max_text_width || sourceRect.width;
            var variants = args.variants || [];
            if (!variants.length) throw new Error("At least one score variant is required.");
            var output = [];

            for (var variantIndex = 0; variantIndex < variants.length; variantIndex++) {
                var spec = variants[variantIndex];
                var text = String(spec.text);
                var name = spec.name || (text + "_codex_v10");
                if (projectItemNameExists(name)) throw new Error("Target composition already exists: " + name);
                var copy = source.duplicate();
                copy.name = name;
                try {
                    var hero = findLayer(copy, heroName);
                    if (!(hero instanceof TextLayer)) throw new Error("Duplicated score layer is missing: " + heroName);
                    if (!setScoreLayerText(hero, text)) throw new Error("Editable score layer unexpectedly has an expression: " + heroName);

                    for (var i = 1; i <= copy.numLayers; i++) {
                        var layer = copy.layer(i);
                        if (layer instanceof TextLayer && layer.name.indexOf("SCORE |") === 0 && layer.name !== heroName) {
                            setScoreLayerText(layer, text);
                        }
                    }

                    var newRect = hero.sourceRectAtTime(sampleTime, false);
                    var factor = Math.min(1.0, maxWidth / Math.max(1, newRect.width));
                    for (i = 1; i <= copy.numLayers; i++) {
                        layer = copy.layer(i);
                        if (layer instanceof TextLayer && layer.name.indexOf("SCORE |") === 0) {
                            recenterTextAnchor(layer, sampleTime);
                            if (factor < 0.99999) scaleLayerAnimation(layer, factor);
                        }
                    }
                    copy.time = sampleTime;
                    output.push({
                        text: text,
                        scale_factor: factor,
                        template_width: sourceRect.width,
                        source_text_width: newRect.width,
                        comp: serializeItem(copy)
                    });
                } catch (error) {
                    try { copy.remove(); } catch (ignoredRemove) {}
                    throw error;
                }
            }
            return { template: serializeItem(source), variants: output };
        });
    }

    function projectItemNameExists(name) {
        var project = requireProject();
        for (var i = 1; i <= project.numItems; i++) {
            if (project.item(i).name === name) return true;
        }
        return false;
    }

    function findOrCreateProjectFolder(name) {
        var project = requireProject();
        for (var i = 1; i <= project.numItems; i++) {
            if (project.item(i) instanceof FolderItem && project.item(i).name === name) return project.item(i);
        }
        return project.items.addFolder(name);
    }

    function importStill(pathValue, targetFolder) {
        var project = requireProject();
        var file = new File(pathValue);
        if (!file.exists) throw new Error("Background asset does not exist: " + file.fsName);
        for (var i = 1; i <= project.numItems; i++) {
            var item = project.item(i);
            if (item instanceof FootageItem) {
                try {
                    if (item.file && item.file.fsName.toLowerCase() === file.fsName.toLowerCase()) return item;
                } catch (ignored) {}
            }
        }
        var imported = project.importFile(new ImportOptions(file));
        if (targetFolder) imported.parentFolder = targetFolder;
        return imported;
    }

    function setKeyframes(property, keys) {
        for (var i = 0; i < keys.length; i++) property.setValueAtTime(keys[i][0], keys[i][1]);
        for (i = 1; i <= property.numKeys; i++) {
            try {
                property.setInterpolationTypeAtKey(i, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
                property.setTemporalAutoBezierAtKey(i, true);
                property.setTemporalContinuousAtKey(i, true);
            } catch (ignoredEase) {}
        }
    }

    function replaceKeyframes(property, keys) {
        while (property.numKeys > 0) property.removeKey(1);
        setKeyframes(property, keys);
    }

    function replaceHoldKeyframes(property, keys) {
        while (property.numKeys > 0) property.removeKey(1);
        for (var i = 0; i < keys.length; i++) property.setValueAtTime(keys[i][0], keys[i][1]);
        for (i = 1; i <= property.numKeys; i++) {
            try {
                property.setInterpolationTypeAtKey(i, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
            } catch (ignoredHold) {}
        }
    }

    function addShapeGroup(layer, kind, size, offset, color, opacity) {
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        var shape;
        if (kind === "rect") {
            shape = vectors.addProperty("ADBE Vector Shape - Rect");
            shape.property("ADBE Vector Rect Size").setValue(size);
            shape.property("ADBE Vector Rect Position").setValue(offset || [0, 0]);
        } else {
            shape = vectors.addProperty("ADBE Vector Shape - Ellipse");
            shape.property("ADBE Vector Ellipse Size").setValue(size);
            shape.property("ADBE Vector Ellipse Position").setValue(offset || [0, 0]);
        }
        var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
        fill.property("ADBE Vector Fill Color").setValue(color);
        fill.property("ADBE Vector Fill Opacity").setValue(opacity === undefined ? 100 : opacity);
        return group;
    }

    function addFullFrameLayer(comp, name, color, opacity) {
        var layer = comp.layers.addShape();
        layer.name = name;
        addShapeGroup(layer, "rect", [comp.width + 8, comp.height + 8], [0, 0], color, 100);
        layer.property("ADBE Transform Group").property("ADBE Position").setValue([comp.width / 2, comp.height / 2]);
        layer.property("ADBE Transform Group").property("ADBE Opacity").setValue(opacity);
        return layer;
    }

    function addCloudLayer(comp, name, side, color, front) {
        var layer = comp.layers.addShape();
        layer.name = name;
        var sign = side === "left" ? -1 : 1;
        var bubbles = front ? [
            [190, 175, 45], [150, 145, 125], [220, 190, 210], [165, 155, 300],
            [185, 170, 385], [140, 130, 455], [120, 110, 520]
        ] : [
            [230, 205, 35], [185, 175, 140], [245, 215, 250], [190, 180, 355], [150, 145, 455]
        ];
        for (var i = 0; i < bubbles.length; i++) {
            var x = sign * (bubbles[i][2] - 245);
            var y = ((i % 3) - 1) * 58 + (front ? 10 : -12);
            var shade = i % 2 === 0 ? color : [color[0] * 0.82, color[1] * 0.9, color[2] * 0.82];
            addShapeGroup(layer, "ellipse", [bubbles[i][0], bubbles[i][1]], [x, y], shade, front ? 92 : 70);
        }
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([0, 0]);
        var basePosition = [comp.width / 2, comp.height / 2];
        var targetPosition = [comp.width / 2 + sign * (front ? 255 : 180), comp.height / 2 + (front ? 22 : -18)];
        setKeyframes(transform.property("ADBE Position"), [[0, basePosition], [front ? 1.05 : 1.35, targetPosition]]);
        setKeyframes(transform.property("ADBE Scale"), [[0, front ? [125, 125] : [112, 112]], [front ? 1.05 : 1.35, front ? [172, 172] : [145, 145]]]);
        setKeyframes(transform.property("ADBE Opacity"), [[0, front ? 100 : 74], [front ? 0.42 : 0.55, front ? 100 : 70], [front ? 1.05 : 1.35, 0]]);
        try {
            var blur = layer.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
            blur.property(1).setValue(front ? 10 : 17);
        } catch (ignoredBlur) {}
        try {
            var turbulence = layer.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
            turbulence.property(1).setValue(front ? 34 : 24);
            turbulence.property(2).setValue(front ? 78 : 110);
        } catch (ignoredTurbulence) {}
        layer.blendingMode = front ? BlendingMode.NORMAL : BlendingMode.SCREEN;
        layer.motionBlur = true;
        return layer;
    }

    function styleTextLayer(layer, template, fillColor, strokeColor, strokeWidth, position) {
        var sourceText = layer.property("ADBE Text Properties").property("ADBE Text Document");
        var document = sourceText.value;
        document.text = template.text;
        document.font = template.font;
        document.fontSize = template.fontSize;
        document.tracking = template.tracking;
        document.applyFill = true;
        document.fillColor = fillColor;
        document.applyStroke = true;
        document.strokeColor = strokeColor;
        document.strokeWidth = strokeWidth;
        document.strokeOverFill = false;
        try { document.justification = ParagraphJustification.CENTER_JUSTIFY; } catch (ignoredJustification) {}
        sourceText.setValue(document);
        var rect = layer.sourceRectAtTime(0, false);
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([rect.left + rect.width / 2, rect.top + rect.height / 2, 0]);
        transform.property("ADBE Position").setValue(position);
    }

    function animateRewardText(layer, basePosition, outroRotation) {
        var transform = layer.property("ADBE Transform Group");
        setKeyframes(transform.property("ADBE Position"), [
            [0, [basePosition[0], basePosition[1] + 48, 0]],
            [0.62, [basePosition[0], basePosition[1] - 12, 0]],
            [0.84, [basePosition[0], basePosition[1] + 8, 0]],
            [1.18, [basePosition[0], basePosition[1], 0]],
            [4.35, [basePosition[0], basePosition[1], 0]],
            [4.92, [basePosition[0], basePosition[1] - 12, 0]]
        ]);
        setKeyframes(transform.property("ADBE Scale"), [
            [0, [34, 34, 100]], [0.38, [34, 34, 100]], [0.62, [128, 128, 100]],
            [0.82, [91, 91, 100]], [1.02, [106, 106, 100]], [1.18, [100, 100, 100]],
            [3.46, [100, 100, 100]], [3.62, [104, 104, 100]], [3.80, [100, 100, 100]],
            [4.35, [100, 100, 100]], [4.92, [116, 116, 100]]
        ]);
        setKeyframes(transform.property("ADBE Rotate Z"), [
            [0, -8], [0.62, 3.5], [0.82, -2], [1.18, 0], [4.35, 0], [4.92, outroRotation || 3]
        ]);
        setKeyframes(transform.property("ADBE Opacity"), [[0, 0], [0.36, 0], [0.60, 100], [4.35, 100], [4.92, 0]]);
        layer.motionBlur = true;
    }

    function addParticle(comp, index, color, delayed) {
        var layer = comp.layers.addShape();
        layer.name = "Spark " + pad(index + 1, 2);
        var size = 5 + (index % 4) * 3;
        addShapeGroup(layer, "ellipse", [size, size], [0, 0], color, 100);
        var transform = layer.property("ADBE Transform Group");
        var angle = (index * 47 + 18) * Math.PI / 180;
        var radius = 105 + (index % 5) * 31;
        var start = delayed ? 3.42 : 0.54;
        var peak = start + 0.22;
        var end = start + (delayed ? 0.85 : 1.35);
        var center = [comp.width / 2, comp.height / 2 + 6];
        var target = [center[0] + Math.cos(angle) * radius, center[1] + Math.sin(angle) * radius * 0.72];
        var drift = [target[0] + Math.cos(angle) * 22, target[1] + 20];
        setKeyframes(transform.property("ADBE Position"), [[start, center], [peak + 0.25, target], [end, drift]]);
        setKeyframes(transform.property("ADBE Scale"), [[start, [0, 0]], [peak, [125, 125]], [end, [35, 35]]]);
        setKeyframes(transform.property("ADBE Opacity"), [[start, 0], [peak, 100], [end, 0]]);
        layer.blendingMode = BlendingMode.ADD;
        layer.motionBlur = true;
        return layer;
    }

    function addOutlinedEllipseGroup(layer, size, offset, fillColor, strokeColor, strokeWidth, opacity) {
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        var shape = vectors.addProperty("ADBE Vector Shape - Ellipse");
        shape.property("ADBE Vector Ellipse Size").setValue(size);
        shape.property("ADBE Vector Ellipse Position").setValue(offset || [0, 0]);
        if (fillColor) {
            var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
            fill.property("ADBE Vector Fill Color").setValue(fillColor);
            fill.property("ADBE Vector Fill Opacity").setValue(opacity === undefined ? 100 : opacity);
        }
        if (strokeColor && strokeWidth > 0) {
            var stroke = vectors.addProperty("ADBE Vector Graphic - Stroke");
            stroke.property("ADBE Vector Stroke Color").setValue(strokeColor);
            stroke.property("ADBE Vector Stroke Width").setValue(strokeWidth);
            stroke.property("ADBE Vector Stroke Opacity").setValue(opacity === undefined ? 100 : opacity);
        }
        return group;
    }

    function addPsychedelicMote(comp, index, color, start, duration, radius, angleDegrees, anchorLayer) {
        var layer = comp.layers.addShape();
        layer.name = "Cel mote " + pad(index + 1, 2);
        var width = 7 + (index % 4) * 3;
        var height = 5 + ((index + 2) % 3) * 3;
        addOutlinedEllipseGroup(layer, [width, height], [0, 0], color, [0.025, 0.08, 0.075], 1.8, 100);
        if (index % 4 === 0) {
            addOutlinedEllipseGroup(layer, [Math.max(3, width * 0.34), Math.max(3, height * 0.34)], [-width * 0.13, -height * 0.12], [1.0, 0.88, 0.42], null, 0, 100);
        }
        var transform = layer.property("ADBE Transform Group");
        var angle = angleDegrees * Math.PI / 180;
        var center = [comp.width / 2, comp.height / 2 + 6];
        var tangent = [-Math.sin(angle), Math.cos(angle)];
        var midRadius = radius * 0.48;
        var bend = (index % 2 === 0 ? 1 : -1) * (22 + (index % 5) * 6);
        var p1 = [center[0], center[1], 0];
        var p2 = [center[0] + Math.cos(angle) * midRadius + tangent[0] * bend,
            center[1] + Math.sin(angle) * midRadius * 0.73 + tangent[1] * bend, 0];
        var p3 = [center[0] + Math.cos(angle) * radius,
            center[1] + Math.sin(angle) * radius * 0.73, 0];
        var p4 = [p3[0] + tangent[0] * 18, p3[1] + tangent[1] * 18 + 10, 0];
        replaceKeyframes(transform.property("ADBE Position"), [
            [start, p1], [start + duration * 0.34, p2], [start + duration * 0.76, p3], [start + duration, p4]
        ]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.12, [128, 128, 100]],
            [start + duration * 0.72, [84, 84, 100]], [start + duration, [0, 0, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[start, index * 19], [start + duration, index % 2 === 0 ? 210 : -170]]);
        replaceKeyframes(transform.property("ADBE Opacity"), [
            [start, 0], [start + 0.09, 92], [start + duration * 0.75, 76], [start + duration, 0]
        ]);
        layer.motionBlur = true;
        if (anchorLayer) layer.moveAfter(anchorLayer);
        return layer;
    }

    function addPsychedelicPulse(comp, index, color, start, anchorLayer) {
        var layer = comp.layers.addShape();
        layer.name = "Psychedelic pulse " + pad(index + 1, 2);
        addOutlinedEllipseGroup(layer, [116 + index * 12, 78 + (index % 2) * 16], [0, 0], null, color, 6 - (index % 2), 82);
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Position").setValue([comp.width / 2, comp.height / 2 + 6, 0]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.15, [46, 46, 100]], [start + 0.82, [270, 270, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[start, -8 + index * 11], [start + 0.82, 13 - index * 7]]);
        replaceKeyframes(transform.property("ADBE Opacity"), [[start, 0], [start + 0.12, 72], [start + 0.82, 0]]);
        try {
            var turbulence = layer.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
            turbulence.property(1).setValue(16 + index * 2);
            turbulence.property(2).setValue(58 + index * 7);
        } catch (ignoredPulseTurbulence) {}
        layer.blendingMode = BlendingMode.SCREEN;
        layer.motionBlur = true;
        if (anchorLayer) layer.moveAfter(anchorLayer);
        return layer;
    }

    function addSmokeAssetLayer(comp, smokeItem, name, front) {
        var layer = comp.layers.add(smokeItem);
        layer.name = name;
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([smokeItem.width / 2, smokeItem.height / 2, 0]);
        setKeyframes(transform.property("ADBE Position"), front ? [
            [0, [comp.width / 2, comp.height / 2 + 8, 0]],
            [1.08, [comp.width / 2, comp.height / 2 - 4, 0]]
        ] : [
            [0, [comp.width / 2, comp.height / 2 + 12, 0]],
            [1.42, [comp.width / 2, comp.height / 2 - 10, 0]]
        ]);
        setKeyframes(transform.property("ADBE Scale"), front ? [
            [0, [0, 0, 100]], [0.16, [18, 18, 100]], [0.28, [47, 47, 100]], [0.82, [108, 108, 100]], [1.18, [124, 124, 100]]
        ] : [
            [0, [0, 0, 100]], [0.18, [12, 12, 100]], [0.34, [38, 38, 100]], [1.02, [117, 117, 100]], [1.52, [134, 134, 100]]
        ]);
        setKeyframes(transform.property("ADBE Rotate Z"), front ? [[0, -4], [1.18, 3]] : [[0, 7], [1.52, -5]]);
        setKeyframes(transform.property("ADBE Opacity"), front ? [
            [0, 88], [0.30, 92], [0.82, 48], [1.18, 0]
        ] : [
            [0, 55], [0.38, 70], [1.02, 32], [1.52, 0]
        ]);
        layer.blendingMode = front ? BlendingMode.NORMAL : BlendingMode.SCREEN;
        layer.motionBlur = true;
        return layer;
    }

    function coverScaleFor(comp, item, multiplier) {
        var fit = Math.max(comp.width / item.width, comp.height / item.height) * 100;
        return fit * (multiplier === undefined ? 1 : multiplier);
    }

    function addModularAssetLayer(comp, item, name, blendMode, opacity, scaleMultiplier) {
        var layer = comp.layers.add(item);
        layer.name = name;
        layer.blendingMode = blendMode || BlendingMode.NORMAL;
        layer.motionBlur = true;
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([item.width / 2, item.height / 2, 0]);
        transform.property("ADBE Position").setValue([comp.width / 2, comp.height / 2, 0]);
        var scale = coverScaleFor(comp, item, scaleMultiplier || 1);
        transform.property("ADBE Scale").setValue([scale, scale, 100]);
        transform.property("ADBE Opacity").setValue(opacity === undefined ? 100 : opacity);
        return layer;
    }

    function addAnimatedTurbulence(layer, amount, size, evolutionTurns) {
        try {
            var turbulence = layer.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
            var amountProperty = turbulence.property("ADBE Turbulent Displace-0002") || turbulence.property(1);
            var sizeProperty = turbulence.property("ADBE Turbulent Displace-0003") || turbulence.property(2);
            var evolutionProperty = turbulence.property("ADBE Turbulent Displace-0006") || turbulence.property(6);
            if (amountProperty) amountProperty.setValue(amount);
            if (sizeProperty) sizeProperty.setValue(size);
            if (evolutionProperty) setKeyframes(evolutionProperty, [[0, 0], [5.0, 360 * (evolutionTurns || 1)]]);
            return turbulence;
        } catch (ignoredTurbulence) {
            return null;
        }
    }

    function styleDynamicRewardText(layer, text, template, fillColor, strokeColor, strokeWidth, position, maxWidth, maxHeight) {
        var sourceText = layer.property("ADBE Text Properties").property("ADBE Text Document");
        var document = sourceText.value;
        document.text = text;
        if (template) {
            try { document.font = template.font; } catch (ignoredTemplateFont) {}
        }
        document.fontSize = 210;
        document.tracking = -10;
        document.applyFill = true;
        document.fillColor = fillColor;
        document.applyStroke = true;
        document.strokeColor = strokeColor;
        document.strokeWidth = strokeWidth;
        document.strokeOverFill = false;
        try { document.justification = ParagraphJustification.CENTER_JUSTIFY; } catch (ignoredJustification) {}
        sourceText.setValue(document);
        var rect = layer.sourceRectAtTime(0, false);
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([rect.left + rect.width / 2, rect.top + rect.height / 2, 0]);
        transform.property("ADBE Position").setValue(position);
        var fit = Math.min(100, maxWidth / Math.max(1, rect.width) * 100, maxHeight / Math.max(1, rect.height) * 100);
        transform.property("ADBE Scale").setValue([fit, fit, 100]);
        return fit;
    }

    function animateDynamicRewardText(layer, basePosition, fitScale, delay, outroRotation) {
        var transform = layer.property("ADBE Transform Group");
        var start = delay || 0;
        replaceKeyframes(transform.property("ADBE Position"), [
            [start, [basePosition[0], basePosition[1] + 38, 0]],
            [start + 0.54, [basePosition[0], basePosition[1] + 38, 0]],
            [start + 0.74, [basePosition[0], basePosition[1] - 13, 0]],
            [start + 0.90, [basePosition[0], basePosition[1] + 7, 0]],
            [start + 1.12, [basePosition[0], basePosition[1], 0]],
            [4.30, [basePosition[0], basePosition[1], 0]],
            [4.86, [basePosition[0], basePosition[1] - 13, 0]]
        ]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.48, [0, 0, 100]],
            [start + 0.70, [fitScale * 1.24, fitScale * 1.24, 100]],
            [start + 0.86, [fitScale * 0.90, fitScale * 0.90, 100]],
            [start + 1.00, [fitScale * 1.07, fitScale * 1.07, 100]],
            [start + 1.12, [fitScale, fitScale, 100]],
            [2.40, [fitScale, fitScale, 100]], [2.54, [fitScale * 1.025, fitScale * 1.025, 100]],
            [2.70, [fitScale, fitScale, 100]], [3.72, [fitScale, fitScale, 100]],
            [3.86, [fitScale * 1.035, fitScale * 1.035, 100]], [4.02, [fitScale, fitScale, 100]],
            [4.30, [fitScale, fitScale, 100]], [4.86, [fitScale * 1.13, fitScale * 1.13, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [
            [start, -7], [start + 0.70, 3.2], [start + 0.88, -1.6],
            [start + 1.12, 0], [4.30, 0], [4.86, outroRotation || 3]
        ]);
        replaceKeyframes(transform.property("ADBE Opacity"), [
            [start, 0], [start + 0.48, 0], [start + 0.66, 100], [4.30, 100], [4.86, 0]
        ]);
        layer.motionBlur = true;
    }

    function addStarGlint(comp, index, color, start, x, y, anchorLayer) {
        var layer = comp.layers.addShape();
        layer.name = "FX | Star glint " + pad(index + 1, 2);
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        var star = vectors.addProperty("ADBE Vector Shape - Star");
        star.property("ADBE Vector Star Type").setValue(1);
        star.property("ADBE Vector Star Points").setValue(4);
        star.property("ADBE Vector Star Inner Radius").setValue(1.8 + (index % 3));
        star.property("ADBE Vector Star Outer Radius").setValue(8 + (index % 4) * 3);
        star.property("ADBE Vector Star Rotation").setValue(45);
        var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
        fill.property("ADBE Vector Fill Color").setValue(color);
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Position").setValue([x, y, 0]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.12, [135, 135, 100]],
            [start + 0.26, [76, 76, 100]], [start + 0.44, [0, 0, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Opacity"), [
            [start, 0], [start + 0.08, 100], [start + 0.30, 82], [start + 0.44, 0]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[start, -22], [start + 0.44, 30]]);
        layer.blendingMode = BlendingMode.ADD;
        layer.motionBlur = true;
        if (anchorLayer) layer.moveAfter(anchorLayer);
        return layer;
    }

    function setNamedEffectValue(effect, names, value) {
        for (var i = 0; i < names.length; i++) {
            try {
                var property = effect.property(names[i]);
                if (property) {
                    property.setValue(value);
                    return property;
                }
            } catch (ignored) {}
        }
        return null;
    }

    function addCandySpiral(comp, index, position, scale, colors, start, anchorLayer) {
        var layer = comp.layers.addShape();
        layer.name = "CANDY | Animated spiral " + pad(index + 1, 2);
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        var pathProperty = vectors.addProperty("ADBE Vector Shape - Group").property("ADBE Vector Shape");
        var shape = new Shape();
        shape.closed = false;
        var vertices = [];
        var inTangents = [];
        var outTangents = [];
        for (var pointIndex = 0; pointIndex < 46; pointIndex++) {
            var angle = pointIndex * 0.43;
            var radius = 1.65 * pointIndex;
            vertices.push([Math.cos(angle) * radius, Math.sin(angle) * radius]);
            inTangents.push([0, 0]);
            outTangents.push([0, 0]);
        }
        shape.vertices = vertices;
        shape.inTangents = inTangents;
        shape.outTangents = outTangents;
        pathProperty.setValue(shape);
        var outerStroke = vectors.addProperty("ADBE Vector Graphic - Stroke");
        outerStroke.property("ADBE Vector Stroke Color").setValue(colors[0]);
        outerStroke.property("ADBE Vector Stroke Width").setValue(12);
        try { outerStroke.property("ADBE Vector Stroke Line Cap").setValue(2); } catch (ignoredOuterCap) {}
        var innerStroke = vectors.addProperty("ADBE Vector Graphic - Stroke");
        innerStroke.property("ADBE Vector Stroke Color").setValue(colors[1]);
        innerStroke.property("ADBE Vector Stroke Width").setValue(5.5);
        try { innerStroke.property("ADBE Vector Stroke Line Cap").setValue(2); } catch (ignoredInnerCap) {}
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Position").setValue(position);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.24, [scale * 1.12, scale * 1.12, 100]],
            [start + 0.46, [scale, scale, 100]], [2.75, [scale * 1.06, scale * 1.06, 100]],
            [4.45, [scale, scale, 100]], [5.0, [scale * 0.86, scale * 0.86, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[start, -28 - index * 7], [2.5, 18 + index * 11], [5.0, 54 + index * 16]]);
        replaceKeyframes(transform.property("ADBE Opacity"), [[start, 0], [start + 0.18, 78], [4.45, 68], [5.0, 0]]);
        layer.blendingMode = BlendingMode.SCREEN;
        layer.motionBlur = true;
        if (anchorLayer) layer.moveAfter(anchorLayer);
        return layer;
    }

    function addCandyConfetti(comp, index, position, color, start, anchorLayer) {
        var layer = comp.layers.addShape();
        layer.name = "CONFETTI | Floating candy " + pad(index + 1, 2);
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        if (index % 3 === 0) {
            var ellipse = vectors.addProperty("ADBE Vector Shape - Ellipse");
            ellipse.property("ADBE Vector Ellipse Size").setValue([7 + index % 5, 7 + index % 5]);
        } else {
            var rect = vectors.addProperty("ADBE Vector Shape - Rect");
            rect.property("ADBE Vector Rect Size").setValue([5 + index % 4, 12 + index % 7]);
            rect.property("ADBE Vector Rect Roundness").setValue(2);
        }
        var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
        fill.property("ADBE Vector Fill Color").setValue(color);
        var transform = layer.property("ADBE Transform Group");
        replaceKeyframes(transform.property("ADBE Position"), [
            [start, [position[0] - 5, position[1] + 12, 0]],
            [2.5, [position[0] + 7, position[1] - 8, 0]],
            [5.0, [position[0] - 2, position[1] - 27, 0]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[start, index * 19], [2.5, index * 19 + 145], [5.0, index * 19 + 310]]);
        replaceKeyframes(transform.property("Opacity"), [[start, 0], [start + 0.20, 88], [4.48, 76], [5.0, 0]]);
        layer.motionBlur = true;
        if (anchorLayer) layer.moveAfter(anchorLayer);
        return layer;
    }

    function addCandyLeaf(comp, item, index, position, scale, rotation, start, anchorLayer) {
        var layer = comp.layers.add(item);
        layer.name = "LEAF | Independent candy drift " + pad(index + 1, 2);
        var transform = layer.property("ADBE Transform Group");
        replaceKeyframes(transform.property("ADBE Position"), [
            [0, [position[0] - 4, position[1] + 5, 0]],
            [2.5, [position[0] + 5 + index % 3, position[1] - 6, 0]],
            [5.0, [position[0] - 3, position[1] + 2, 0]]
        ]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [0, [scale * 0.82, scale * 0.82, 100]], [start, [scale * 0.82, scale * 0.82, 100]],
            [start + 0.30, [scale * 1.08, scale * 1.08, 100]], [start + 0.52, [scale, scale, 100]],
            [2.6, [scale * 1.06, scale * 1.06, 100]], [5.0, [scale * 0.94, scale * 0.94, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[0, rotation - 8], [2.5, rotation + 7], [5.0, rotation - 4]]);
        replaceKeyframes(transform.property("ADBE Opacity"), [[0, 0], [start, 0], [start + 0.24, 92], [4.45, 86], [5.0, 0]]);
        layer.motionBlur = true;
        if (anchorLayer) layer.moveAfter(anchorLayer);
        return layer;
    }

    function commandBuildCandyRewardVariant(args) {
        return withUndo("Codex: Build candy psychedelic reward", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                comp.motionBlur = true;
                comp.shutterAngle = 220;
                comp.shutterPhase = -110;

                var i;
                for (i = 1; i <= comp.numLayers; i++) {
                    var existingLayer = comp.layer(i);
                    if (existingLayer.name === "FRAME | RGBA purple smoke portal"
                        || existingLayer.name === "LEAVES | RGBA slow parallax"
                        || existingLayer.name === "BG | Rotating radial core"
                        || existingLayer.name.indexOf("Adjustment Layer") === 0) {
                        existingLayer.enabled = false;
                    }
                    if (existingLayer.name.indexOf("SMOKE |") === 0) {
                        try {
                            existingLayer.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.72";
                            var tint = existingLayer.property("ADBE Effect Parade").addProperty("ADBE Tint");
                            setNamedEffectValue(tint, ["Map Black To"], [0.92, 0.18, 0.62]);
                            setNamedEffectValue(tint, ["Map White To"], [1.0, 0.97, 0.72]);
                            setNamedEffectValue(tint, ["Amount to Tint"], 82);
                        } catch (ignoredSmokeTint) {}
                    }
                }

                var assetFolder = findOrCreateProjectFolder("Codex Assets");
                var backgroundItem = importStill(args.background_path, assetFolder);
                var leafItem = importStill(args.leaf_path, assetFolder);
                var bottom = findLayer(comp, "BG | Emerald safety");

                var candyBackground = addModularAssetLayer(comp, backgroundItem, "BG CANDY | Main rotating ribbons", BlendingMode.NORMAL, 100, 1.18);
                var backgroundScale = coverScaleFor(comp, backgroundItem, 1.18);
                var candyBackgroundT = candyBackground.property("ADBE Transform Group");
                replaceKeyframes(candyBackgroundT.property("ADBE Scale"), [
                    [0, [backgroundScale, backgroundScale, 100]],
                    [2.5, [backgroundScale * 1.035, backgroundScale * 1.035, 100]],
                    [5.0, [backgroundScale, backgroundScale, 100]]
                ]);
                replaceKeyframes(candyBackgroundT.property("ADBE Rotate Z"), [[0, -6], [2.5, 2], [5.0, 10]]);
                candyBackground.moveBefore(bottom);

                var candyEcho = addModularAssetLayer(comp, backgroundItem, "BG CANDY | Counter rotating echo", BlendingMode.SCREEN, 18, 1.32);
                var echoScale = coverScaleFor(comp, backgroundItem, 1.32);
                var candyEchoT = candyEcho.property("ADBE Transform Group");
                replaceKeyframes(candyEchoT.property("ADBE Scale"), [
                    [0, [echoScale, echoScale, 100]], [2.5, [echoScale * 0.98, echoScale * 0.98, 100]],
                    [5.0, [echoScale, echoScale, 100]]
                ]);
                replaceKeyframes(candyEchoT.property("ADBE Rotate Z"), [[0, 11], [2.5, 1], [5.0, -12]]);
                candyEcho.moveBefore(candyBackground);

                var scoreAnchor = findLayer(comp, "SCORE | Radioactive aura");
                var leafPositions = [[78, 114], [562, 104], [70, 378], [568, 376], [183, 74], [462, 414], [320, 420]];
                var leafScales = [8.6, 9.2, 10.4, 9.8, 5.6, 6.2, 4.8];
                var leafRotations = [-28, 34, -42, 48, -12, 22, 6];
                for (i = 0; i < leafPositions.length; i++) {
                    addCandyLeaf(comp, leafItem, i, leafPositions[i], leafScales[i], leafRotations[i], 0.12 + i * 0.06, scoreAnchor);
                }

                var spiralPositions = [[76, 72, 0], [566, 77, 0], [75, 404, 0], [565, 402, 0], [321, 64, 0]];
                var spiralScales = [78, 69, 62, 76, 48];
                var spiralColors = [
                    [[1.0, 0.22, 0.62], [1.0, 0.86, 0.08]],
                    [[0.05, 0.84, 0.88], [1.0, 0.32, 0.68]],
                    [[1.0, 0.78, 0.05], [0.98, 0.26, 0.62]],
                    [[0.12, 0.88, 0.70], [1.0, 0.85, 0.08]],
                    [[1.0, 0.30, 0.70], [0.18, 0.90, 0.82]]
                ];
                for (i = 0; i < spiralPositions.length; i++) {
                    addCandySpiral(comp, i, spiralPositions[i], spiralScales[i], spiralColors[i], 0.10 + i * 0.07, scoreAnchor);
                }

                var confettiColors = [[1.0, 0.14, 0.56], [1.0, 0.86, 0.04], [0.02, 0.86, 0.83], [0.58, 1.0, 0.05], [0.72, 0.28, 1.0]];
                for (i = 0; i < 26; i++) {
                    var side = i % 2 === 0 ? 1 : -1;
                    var x = i < 13 ? 42 + (i * 37) % 230 : 598 - ((i * 41) % 230);
                    if (i >= 13) x = 640 - x;
                    x = i % 4 < 2 ? 34 + (i * 29) % 165 : 606 - (i * 31) % 165;
                    var y = 38 + (i * 67) % 382;
                    addCandyConfetti(comp, i, [x, y, 0], confettiColors[i % confettiColors.length], 0.08 + (i % 7) * 0.05, scoreAnchor);
                }

                var hero = findLayer(comp, "SCORE | Editable gold face");
                try {
                    var glass = hero.property("ADBE Effect Parade").addProperty("CC Glass");
                    setNamedEffectValue(glass, ["Softness"], 7);
                    setNamedEffectValue(glass, ["Height"], 34);
                    setNamedEffectValue(glass, ["Displacement"], 0);
                    setNamedEffectValue(glass, ["Intensity"], 1.25);
                    setNamedEffectValue(glass, ["Light Height"], 42);
                    var lightDirection = null;
                    try { lightDirection = glass.property("Light Direction"); } catch (ignoredLightDirection) {}
                    if (lightDirection) replaceKeyframes(lightDirection, [[0, -55], [2.5, 18], [5.0, 92]]);
                } catch (ignoredGlass) {}
                try {
                    var sweep2 = hero.property("ADBE Effect Parade").addProperty("CC Light Sweep");
                    var sweep2Center = sweep2.property("Center");
                    if (sweep2Center) replaceKeyframes(sweep2Center, [
                        [0.64, [-120, 190]], [1.34, [760, 275]], [2.42, [-120, 182]],
                        [3.16, [760, 286]], [4.04, [-110, 188]], [4.62, [750, 275]]
                    ]);
                    setNamedEffectValue(sweep2, ["Sweep Intensity"], 72);
                    setNamedEffectValue(sweep2, ["Sweep Width"], 58);
                    setNamedEffectValue(sweep2, ["Edge Intensity"], 18);
                } catch (ignoredSweep2) {}

                var scoreSparkPositions = [[116, 205], [202, 286], [294, 194], [382, 278], [475, 203], [530, 285]];
                for (i = 0; i < scoreSparkPositions.length; i++) {
                    var scoreSpark = addStarGlint(comp, 40 + i, [1.0, 1.0, 0.86], 0.92 + i * 0.54,
                        scoreSparkPositions[i][0], scoreSparkPositions[i][1], null);
                    scoreSpark.name = "SCORE SHINE | Traveling glint " + pad(i + 1, 2);
                    scoreSpark.moveBefore(hero);
                }

                var pastelFlash = addFullFrameLayer(comp, "FX CANDY | Pastel impact flash", [1.0, 0.45, 0.78], 0);
                pastelFlash.blendingMode = BlendingMode.SCREEN;
                replaceKeyframes(pastelFlash.property("ADBE Transform Group").property("ADBE Opacity"), [
                    [0, 0], [0.50, 0], [0.64, 24], [0.82, 0], [2.43, 0], [2.52, 8], [2.66, 0],
                    [3.78, 0], [3.86, 12], [4.02, 0], [5.0, 0]
                ]);
                pastelFlash.moveAfter(findLayer(comp, "OUTRO | Fade to black"));

                comp.time = 1.55;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: serializeItem(comp),
                    background: serializeItem(backgroundItem),
                    leaf: serializeItem(leafItem),
                    cc_glass_requested: true,
                    leaf_instances: leafPositions.length,
                    candy_spirals: spiralPositions.length,
                    confetti_layers: 26,
                    score_glints: scoreSparkPositions.length
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function commandPolishCandyRewardVariant(args) {
        return withUndo("Codex: Polish candy reward glass", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                var i;
                for (i = 1; i <= comp.numLayers; i++) {
                    var layer = comp.layer(i);
                    if (layer.name.indexOf("CANDY | Animated spiral") === 0) {
                        layer.blendingMode = BlendingMode.NORMAL;
                        try { layer.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.82"; } catch (ignoredSpiralOpacity) {}
                    }
                }

                var hero = findLayer(comp, "SCORE | Editable gold face");
                var heroEffects = hero.property("ADBE Effect Parade");
                for (i = heroEffects.numProperties; i >= 1; i--) {
                    var heroEffect = heroEffects.property(i);
                    var effectLabel = String(heroEffect.name).toLowerCase() + " " + String(heroEffect.matchName).toLowerCase();
                    if (effectLabel.indexOf("glass") >= 0) heroEffect.remove();
                }
                try {
                    var bevel = heroEffects.addProperty("ADBE Bevel Alpha");
                    setNamedEffectValue(bevel, ["Edge Thickness", "Thickness"], 4.5);
                    setNamedEffectValue(bevel, ["Light Angle"], 128);
                    setNamedEffectValue(bevel, ["Light Intensity", "Intensity"], 0.72);
                } catch (ignoredBevel) {}

                var glassPass = hero.duplicate();
                glassPass.name = "SCORE | CC Glass specular pass";
                glassPass.moveBefore(hero);
                var glassTextProperty = glassPass.property("ADBE Text Properties").property("ADBE Text Document");
                var glassDocument = glassTextProperty.value;
                glassDocument.applyFill = true;
                glassDocument.fillColor = [1.0, 0.88, 0.10];
                glassDocument.applyStroke = false;
                glassTextProperty.setValue(glassDocument);
                var glassEffects = glassPass.property("ADBE Effect Parade");
                for (i = glassEffects.numProperties; i >= 1; i--) glassEffects.property(i).remove();
                try {
                    var glass = glassEffects.addProperty("CC Glass");
                    setNamedEffectValue(glass, ["Softness"], 4.5);
                    setNamedEffectValue(glass, ["Height"], 27);
                    setNamedEffectValue(glass, ["Displacement"], 0);
                    setNamedEffectValue(glass, ["Intensity"], 1.65);
                    setNamedEffectValue(glass, ["Light Height"], 46);
                    var direction = null;
                    try { direction = glass.property("Light Direction"); } catch (ignoredDirection) {}
                    if (direction) replaceKeyframes(direction, [[0, -65], [1.25, -20], [2.5, 24], [3.75, 68], [5.0, 110]]);
                } catch (ignoredGlassPass) {}
                try {
                    var glassGlow = glassEffects.addProperty("ADBE Glo2");
                    setNamedEffectValue(glassGlow, ["Glow Threshold"], 64);
                    setNamedEffectValue(glassGlow, ["Glow Radius"], 7);
                    setNamedEffectValue(glassGlow, ["Glow Intensity"], 0.52);
                } catch (ignoredGlassGlow) {}
                glassPass.blendingMode = BlendingMode.SCREEN;
                try { glassPass.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.48"; } catch (ignoredGlassOpacity) {}

                var colorStars = [
                    [1.0, 0.18, 0.66], [0.05, 0.92, 0.88], [1.0, 0.87, 0.04],
                    [0.62, 1.0, 0.04], [0.76, 0.32, 1.0], [1.0, 0.52, 0.12]
                ];
                var colorStarPositions = [[72, 174], [566, 160], [126, 356], [516, 354], [264, 72], [402, 410], [48, 270], [592, 286]];
                var scoreAnchor = findLayer(comp, "SCORE | Radioactive aura");
                for (i = 0; i < colorStarPositions.length; i++) {
                    var colorStar = addStarGlint(comp, 70 + i, colorStars[i % colorStars.length],
                        0.58 + (i % 4) * 0.62, colorStarPositions[i][0], colorStarPositions[i][1], scoreAnchor);
                    colorStar.name = "CANDY STAR | Color pulse " + pad(i + 1, 2);
                }

                comp.time = 2.5;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: serializeItem(comp),
                    separate_glass_pass: true,
                    colored_star_pulses: colorStarPositions.length
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function addAnimatedGoldRamp(layer) {
        var effects = layer.property("ADBE Effect Parade");
        try {
            var ramp = effects.addProperty("ADBE Ramp");
            try { ramp.moveTo(1); } catch (ignoredRampMove) {}
            var startPoint = null;
            var endPoint = null;
            try { startPoint = ramp.property("Start of Ramp"); } catch (ignoredRampStart) {}
            try { endPoint = ramp.property("End of Ramp"); } catch (ignoredRampEnd) {}
            if (startPoint) replaceKeyframes(startPoint, [[0, [310, 165]], [2.5, [326, 178]], [5.0, [306, 168]]]);
            if (endPoint) replaceKeyframes(endPoint, [[0, [326, 326]], [2.5, [310, 306]], [5.0, [330, 322]]]);
            setNamedEffectValue(ramp, ["Start Color"], [1.0, 1.0, 0.58]);
            setNamedEffectValue(ramp, ["End Color"], [1.0, 0.54, 0.015]);
            setNamedEffectValue(ramp, ["Ramp Shape"], 1);
            setNamedEffectValue(ramp, ["Ramp Scatter", "Scatter"], 24);
            setNamedEffectValue(ramp, ["Blend With Original"], 8);
            return ramp;
        } catch (ignoredRamp) {
            return null;
        }
    }

    function commandAddCandyVolumeVariant(args) {
        return withUndo("Codex: Add candy score volume", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                var hero = findLayer(comp, "SCORE | Editable gold face");
                addAnimatedGoldRamp(hero);

                var glassPass = findLayer(comp, "SCORE | CC Glass specular pass");
                try { glassPass.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.66"; } catch (ignoredGlassBoost) {}

                var gloss = hero.duplicate();
                gloss.name = "SCORE | Clipped upper gloss volume";
                gloss.moveBefore(glassPass);
                var glossText = gloss.property("ADBE Text Properties").property("ADBE Text Document");
                var glossDocument = glossText.value;
                glossDocument.applyFill = true;
                glossDocument.fillColor = [1.0, 1.0, 0.92];
                glossDocument.applyStroke = false;
                glossText.setValue(glossDocument);
                var glossEffects = gloss.property("ADBE Effect Parade");
                for (var i = glossEffects.numProperties; i >= 1; i--) glossEffects.property(i).remove();
                try {
                    var glossRamp = glossEffects.addProperty("ADBE Ramp");
                    var glossStart = null;
                    var glossEnd = null;
                    try { glossStart = glossRamp.property("Start of Ramp"); } catch (ignoredGlossStart) {}
                    try { glossEnd = glossRamp.property("End of Ramp"); } catch (ignoredGlossEnd) {}
                    if (glossStart) replaceKeyframes(glossStart, [[0, [310, 176]], [2.5, [330, 186]], [5.0, [308, 176]]]);
                    if (glossEnd) replaceKeyframes(glossEnd, [[0, [320, 277]], [2.5, [305, 260]], [5.0, [328, 276]]]);
                    setNamedEffectValue(glossRamp, ["Start Color"], [1.0, 1.0, 1.0]);
                    setNamedEffectValue(glossRamp, ["End Color"], [0.0, 0.0, 0.0]);
                    setNamedEffectValue(glossRamp, ["Ramp Shape"], 1);
                    setNamedEffectValue(glossRamp, ["Ramp Scatter", "Scatter"], 18);
                } catch (ignoredGlossRamp) {}
                gloss.blendingMode = BlendingMode.SCREEN;
                try { gloss.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.28"; } catch (ignoredGlossOpacity) {}

                try {
                    var glossSweep = glossEffects.addProperty("CC Light Sweep");
                    var glossSweepCenter = glossSweep.property("Center");
                    if (glossSweepCenter) replaceKeyframes(glossSweepCenter, [
                        [0.72, [-100, 168]], [1.42, [750, 258]], [2.62, [-110, 176]],
                        [3.34, [760, 270]], [4.16, [-90, 180]], [4.66, [730, 255]]
                    ]);
                    setNamedEffectValue(glossSweep, ["Sweep Intensity"], 58);
                    setNamedEffectValue(glossSweep, ["Sweep Width"], 46);
                } catch (ignoredGlossSweep) {}

                comp.time = 2.5;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: serializeItem(comp),
                    gold_gradient: true,
                    clipped_gloss_pass: true,
                    cc_glass_pass_boosted: true
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function commandFixCandyGoldVariant(args) {
        return withUndo("Codex: Fix candy gold color volume", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                var hero = findLayer(comp, "SCORE | Editable gold face");
                var heroEffects = hero.property("ADBE Effect Parade");
                for (var i = 1; i <= heroEffects.numProperties; i++) {
                    var effect = heroEffects.property(i);
                    if (effect.matchName === "ADBE Ramp") {
                        effect.property("ADBE Ramp-0002").setValue([1.0, 0.96, 0.20, 1.0]);
                        effect.property("ADBE Ramp-0004").setValue([1.0, 0.52, 0.008, 1.0]);
                        effect.property("ADBE Ramp-0006").setValue(18);
                        effect.property("ADBE Ramp-0007").setValue(0.04);
                        break;
                    }
                }

                var gloss = findLayer(comp, "SCORE | Clipped upper gloss volume");
                try { gloss.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.18"; } catch (ignoredGlossReduce) {}

                var glassPass = findLayer(comp, "SCORE | CC Glass specular pass");
                try { glassPass.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.56"; } catch (ignoredGlassOpacity) {}
                var glassEffects = glassPass.property("ADBE Effect Parade");
                for (i = 1; i <= glassEffects.numProperties; i++) {
                    var glassEffect = glassEffects.property(i);
                    if (glassEffect.matchName === "CC Glass") {
                        try { glassEffect.property("CC Glass-0009").setValue(76); } catch (ignoredLightIntensity) {}
                        try { glassEffect.property("CC Glass-0017").setValue(30); } catch (ignoredAmbient) {}
                        try { glassEffect.property("CC Glass-0018").setValue(66); } catch (ignoredDiffuse) {}
                        try { glassEffect.property("CC Glass-0019").setValue(92); } catch (ignoredSpecular) {}
                        try { glassEffect.property("CC Glass-0020").setValue(0.08); } catch (ignoredRoughness) {}
                        try { glassEffect.property("CC Glass-0021").setValue(62); } catch (ignoredMetal) {}
                        break;
                    }
                }

                comp.time = 2.5;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: serializeItem(comp),
                    gold_colors_rgba_fixed: true,
                    controlled_glass_shading: true
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function commandFinalizeCandyRewardVariant(args) {
        return withUndo("Codex: Finalize candy reward", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                var hero = findLayer(comp, "SCORE | Editable gold face");
                var heroEffects = hero.property("ADBE Effect Parade");
                var ramp = heroEffects.addProperty("ADBE Ramp");
                try { ramp.moveTo(1); } catch (ignoredRampMove) {}
                // Moving an effect invalidates the ExtendScript Property handle in some AE builds.
                // Reacquire it from the parade before touching its child properties.
                ramp = heroEffects.property(1);
                replaceKeyframes(ramp.property("ADBE Ramp-0001"), [[0, [316, 170]], [2.5, [326, 180]], [5.0, [312, 172]]]);
                ramp.property("ADBE Ramp-0002").setValue([1.0, 0.98, 0.28, 1.0]);
                replaceKeyframes(ramp.property("ADBE Ramp-0003"), [[0, [326, 323]], [2.5, [314, 307]], [5.0, [330, 320]]]);
                ramp.property("ADBE Ramp-0004").setValue([1.0, 0.58, 0.012, 1.0]);
                ramp.property("ADBE Ramp-0005").setValue(1);
                ramp.property("ADBE Ramp-0006").setValue(14);
                ramp.property("ADBE Ramp-0007").setValue(0);

                var glassPass = findLayer(comp, "SCORE | CC Glass specular pass");
                try { glassPass.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.30"; } catch (ignoredGlassOpacity) {}
                var glassEffects = glassPass.property("ADBE Effect Parade");
                for (var i = 1; i <= glassEffects.numProperties; i++) {
                    var effect = glassEffects.property(i);
                    if (effect.matchName === "CC Glass") {
                        effect.property("CC Glass-0009").setValue(68);
                        effect.property("CC Glass-0017").setValue(24);
                        effect.property("CC Glass-0018").setValue(58);
                        effect.property("CC Glass-0019").setValue(96);
                        effect.property("CC Glass-0020").setValue(0.06);
                        effect.property("CC Glass-0021").setValue(52);
                        break;
                    }
                }

                comp.time = 2.5;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: serializeItem(comp),
                    direct_gold_gradient: true,
                    restrained_cc_glass: true
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function addFloatingDopeAsset(comp, item, name, position, scale, startRotation, endRotation, delay, anchorLayer) {
        var layer = comp.layers.add(item);
        layer.name = name;
        layer.blendingMode = BlendingMode.NORMAL;
        layer.motionBlur = true;
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([item.width / 2, item.height / 2, 0]);
        replaceKeyframes(transform.property("ADBE Position"), [
            [delay, [position[0] - 8, position[1] + 9, 0]],
            [delay + 0.34, [position[0], position[1], 0]],
            [2.5, [position[0] + 8, position[1] - 7, 0]],
            [5.0, [position[0] - 5, position[1] + 5, 0]]
        ]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [delay, [0, 0, 100]],
            [delay + 0.23, [scale * 1.18, scale * 1.18, 100]],
            [delay + 0.42, [scale, scale, 100]],
            [2.55, [scale * 1.08, scale * 1.08, 100]],
            [4.45, [scale, scale, 100]],
            [5.0, [scale * 0.72, scale * 0.72, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [
            [delay, startRotation], [2.5, (startRotation + endRotation) * 0.5], [5.0, endRotation]
        ]);
        replaceKeyframes(transform.property("ADBE Opacity"), [
            [delay, 0], [delay + 0.19, 94], [4.45, 86], [5.0, 0]
        ]);
        try {
            var glow = layer.property("ADBE Effect Parade").addProperty("ADBE Glo2");
            setNamedEffectValue(glow, ["Glow Threshold"], 74);
            setNamedEffectValue(glow, ["Glow Radius"], 5);
            setNamedEffectValue(glow, ["Glow Intensity"], 0.20);
        } catch (ignoredGlow) {}
        if (anchorLayer) layer.moveAfter(anchorLayer);
        return layer;
    }

    function commandRefineCandyRewardVariant(args) {
        return withUndo("Codex: Rounded candy score and dope details", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                var i;
                for (i = 1; i <= comp.numLayers; i++) {
                    var oldLayer = comp.layer(i);
                    if (oldLayer.name.indexOf("CANDY | Animated spiral") === 0) oldLayer.enabled = false;
                }

                var assetFolder = findOrCreateProjectFolder("Codex Assets");
                var capsuleItem = importStill(args.capsule_path, assetFolder);
                var stampItem = importStill(args.stamp_path, assetFolder);
                var scoreAnchor = findLayer(comp, "SCORE | Radioactive aura");
                var capsulePositions = [[58, 164], [582, 184], [176, 418], [474, 72]];
                var capsuleScales = [3.8, 3.5, 3.2, 2.9];
                var capsuleRotations = [[-34, 128], [26, -152], [58, 226], [-18, 166]];
                for (i = 0; i < capsulePositions.length; i++) {
                    addFloatingDopeAsset(comp, capsuleItem, "DOPE | Floating capsule " + pad(i + 1, 2),
                        capsulePositions[i], capsuleScales[i], capsuleRotations[i][0], capsuleRotations[i][1],
                        0.10 + i * 0.08, scoreAnchor);
                }
                var stampPositions = [[142, 66], [544, 92], [82, 356], [558, 366]];
                var stampScales = [3.15, 2.85, 3.35, 3.0];
                var stampRotations = [[-18, 112], [24, -126], [-31, 154], [16, -142]];
                for (i = 0; i < stampPositions.length; i++) {
                    addFloatingDopeAsset(comp, stampItem, "DOPE | Floating blotter stamp " + pad(i + 1, 2),
                        stampPositions[i], stampScales[i], stampRotations[i][0], stampRotations[i][1],
                        0.18 + i * 0.07, scoreAnchor);
                }

                var hero = findLayer(comp, "SCORE | Editable gold face");
                var heroEffects = hero.property("ADBE Effect Parade");
                for (i = heroEffects.numProperties; i >= 1; i--) {
                    if (heroEffects.property(i).matchName === "ADBE Bevel Alpha") heroEffects.property(i).remove();
                }

                var domeGradient = addFullFrameLayer(comp, "SCORE | Rounded gold volume gradient", [1, 1, 1], 100);
                domeGradient.blendingMode = BlendingMode.NORMAL;
                var domeEffects = domeGradient.property("ADBE Effect Parade");
                var ramp = domeEffects.addProperty("ADBE Ramp");
                replaceKeyframes(ramp.property("ADBE Ramp-0001"), [[0, [322, 164]], [2.5, [310, 176]], [5.0, [326, 166]]]);
                ramp.property("ADBE Ramp-0002").setValue([1.0, 1.0, 0.48, 1.0]);
                replaceKeyframes(ramp.property("ADBE Ramp-0003"), [[0, [318, 324]], [2.5, [330, 310]], [5.0, [314, 322]]]);
                ramp.property("ADBE Ramp-0004").setValue([1.0, 0.54, 0.015, 1.0]);
                ramp.property("ADBE Ramp-0005").setValue(1);
                ramp.property("ADBE Ramp-0006").setValue(32);
                ramp.property("ADBE Ramp-0007").setValue(0);
                try {
                    var broadSweep = domeEffects.addProperty("CC Light Sweep");
                    var broadCenter = broadSweep.property("Center");
                    if (broadCenter) replaceKeyframes(broadCenter, [
                        [0.62, [-120, 165]], [1.38, [760, 285]], [2.42, [-120, 172]],
                        [3.20, [760, 292]], [4.02, [-120, 168]], [4.68, [760, 280]]
                    ]);
                    setNamedEffectValue(broadSweep, ["Sweep Intensity"], 42);
                    setNamedEffectValue(broadSweep, ["Sweep Width"], 132);
                    setNamedEffectValue(broadSweep, ["Edge Intensity"], 8);
                } catch (ignoredBroadSweep) {}
                domeGradient.moveBefore(hero);

                var domeMatte = hero.duplicate();
                domeMatte.name = "SCORE | Rounded volume alpha matte";
                var domeMatteEffects = domeMatte.property("ADBE Effect Parade");
                for (i = domeMatteEffects.numProperties; i >= 1; i--) domeMatteEffects.property(i).remove();
                var domeMatteText = domeMatte.property("ADBE Text Properties").property("ADBE Text Document");
                var domeMatteDocument = domeMatteText.value;
                domeMatteDocument.applyFill = true;
                domeMatteDocument.fillColor = [1, 1, 1];
                domeMatteDocument.applyStroke = false;
                domeMatteText.setValue(domeMatteDocument);
                domeMatte.moveBefore(domeGradient);
                try { domeGradient.trackMatteType = TrackMatteType.ALPHA; } catch (ignoredTrackMatte) {}

                var bumpLayer = hero.duplicate();
                bumpLayer.name = "SCORE | Soft spherical bump map";
                var bumpEffects = bumpLayer.property("ADBE Effect Parade");
                for (i = bumpEffects.numProperties; i >= 1; i--) bumpEffects.property(i).remove();
                var bumpText = bumpLayer.property("ADBE Text Properties").property("ADBE Text Document");
                var bumpDocument = bumpText.value;
                bumpDocument.applyFill = true;
                bumpDocument.fillColor = [1, 1, 1];
                bumpDocument.applyStroke = false;
                bumpText.setValue(bumpDocument);
                try {
                    var bumpBlur = bumpEffects.addProperty("ADBE Gaussian Blur 2");
                    bumpBlur.property("ADBE Gaussian Blur 2-0001").setValue(24);
                    bumpBlur.property("ADBE Gaussian Blur 2-0002").setValue(1);
                } catch (ignoredBumpBlur) {}
                bumpLayer.enabled = false;
                bumpLayer.shy = true;
                bumpLayer.moveAfter(findLayer(comp, "BG | Emerald safety"));

                var glassPass = findLayer(comp, "SCORE | CC Glass specular pass");
                var glassEffects = glassPass.property("ADBE Effect Parade");
                for (i = 1; i <= glassEffects.numProperties; i++) {
                    var glass = glassEffects.property(i);
                    if (glass.matchName === "CC Glass") {
                        glass.property("CC Glass-0002").setValue(bumpLayer.index);
                        glass.property("CC Glass-0003").setValue(6);
                        glass.property("CC Glass-0004").setValue(18);
                        glass.property("CC Glass-0005").setValue(30);
                        glass.property("CC Glass-0006").setValue(0);
                        glass.property("CC Glass-0009").setValue(78);
                        glass.property("CC Glass-0012").setValue(52);
                        replaceKeyframes(glass.property("CC Glass-0014"), [[0, -52], [1.25, -12], [2.5, 28], [3.75, 68], [5.0, 108]]);
                        glass.property("CC Glass-0017").setValue(44);
                        glass.property("CC Glass-0018").setValue(56);
                        glass.property("CC Glass-0019").setValue(82);
                        glass.property("CC Glass-0020").setValue(0.12);
                        glass.property("CC Glass-0021").setValue(12);
                        break;
                    }
                }
                glassPass.blendingMode = BlendingMode.SCREEN;
                glassPass.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.34";

                comp.time = 2.5;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: serializeItem(comp),
                    rounded_gradient_matte: true,
                    soft_bump_glass: true,
                    capsule_instances: capsulePositions.length,
                    blotter_instances: stampPositions.length
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function keepScoreVisibleThroughOutro(comp) {
        var editedLayers = 0;
        for (var i = 1; i <= comp.numLayers; i++) {
            var layer = comp.layer(i);
            if (layer.name.indexOf("SCORE |") !== 0) continue;
            var transform = layer.property("ADBE Transform Group");
            var opacity = transform ? transform.property("ADBE Opacity") : null;
            if (!opacity || opacity.numKeys < 1) continue;
            var removed = false;
            for (var k = opacity.numKeys; k >= 1; k--) {
                if (opacity.keyTime(k) >= 4.55) {
                    opacity.removeKey(k);
                    removed = true;
                }
            }
            if (removed) editedLayers++;
        }
        return editedLayers;
    }

    function updateCandyScoreText(comp, scoreText) {
        var changed = 0;
        for (var i = 1; i <= comp.numLayers; i++) {
            var layer = comp.layer(i);
            if (layer.name.indexOf("SCORE |") !== 0) continue;
            var sourceText = null;
            try { sourceText = layer.property("ADBE Text Properties").property("ADBE Text Document"); } catch (ignoredText) {}
            if (!sourceText) continue;
            var document = sourceText.value;
            document.text = String(scoreText);
            sourceText.setValue(document);
            try { recenterTextAnchor(layer, 2.5); } catch (ignoredAnchor) {}
            changed++;
        }
        return changed;
    }

    function intensifyCandyBackground(comp, level) {
        var main = findLayer(comp, "BG CANDY | Main rotating ribbons");
        var echo = findLayer(comp, "BG CANDY | Counter rotating echo");
        var coverMultiplier = 1 + level * 0.11;
        var mainScale = coverScaleFor(comp, main.source, 1.18 * coverMultiplier);
        var echoScale = coverScaleFor(comp, echo.source, 1.32 * coverMultiplier);
        replaceKeyframes(main.property("ADBE Transform Group").property("ADBE Scale"), [
            [0, [mainScale, mainScale, 100]],
            [2.5, [mainScale * 1.035, mainScale * 1.035, 100]],
            [5.0, [mainScale, mainScale, 100]]
        ]);
        replaceKeyframes(echo.property("ADBE Transform Group").property("ADBE Scale"), [
            [0, [echoScale, echoScale, 100]],
            [2.5, [echoScale * 0.98, echoScale * 0.98, 100]],
            [5.0, [echoScale, echoScale, 100]]
        ]);
        replaceKeyframes(main.property("ADBE Transform Group").property("ADBE Rotate Z"), [
            [0, -6 - level * 4], [2.5, 2 + level * 3], [5.0, 10 + level * 8]
        ]);
        replaceKeyframes(echo.property("ADBE Transform Group").property("ADBE Rotate Z"), [
            [0, 11 + level * 4], [2.5, 1 - level * 3], [5.0, -12 - level * 8]
        ]);
        echo.property("ADBE Transform Group").property("ADBE Opacity").setValue(Math.min(34, 18 + level * 4));
    }

    function addWildCandyDetails(comp, level, scoreText, capsuleItem, stampItems) {
        var scoreAnchor = findLayer(comp, "SCORE | Radioactive aura");
        var positions = [
            [224, 62], [414, 68], [36, 252], [604, 286],
            [132, 425], [516, 422], [92, 116], [552, 132]
        ];
        var stampPositions = [
            [250, 424], [394, 421], [38, 322], [602, 145],
            [273, 54], [374, 57], [108, 252], [532, 258]
        ];
        var pairs = level * 2;
        for (var i = 0; i < pairs; i++) {
            var p = positions[i % positions.length];
            var capsule = addFloatingDopeAsset(comp, capsuleItem,
                "DOPE WILD " + scoreText + " | Capsule " + pad(i + 1, 2), p,
                1.75 + (i % 3) * 0.30, -48 - i * 13, 150 + level * 42 + i * 31,
                0.06 + (i % 5) * 0.05, scoreAnchor);
            capsule.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.88";

            var sp = stampPositions[i % stampPositions.length];
            var stampItem = stampItems[(i + level - 1) % stampItems.length];
            var stamp = addFloatingDopeAsset(comp, stampItem,
                "DOPE WILD " + scoreText + " | Blotter " + pad(i + 1, 2), sp,
                2.05 + (i % 4) * 0.22, -26 - i * 17, 118 + level * 48 + i * 37,
                0.11 + (i % 5) * 0.045, scoreAnchor);
            stamp.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.90";
        }

        var colors = [[1.0, 0.12, 0.58], [1.0, 0.86, 0.02], [0.02, 0.90, 0.86], [0.56, 1.0, 0.04], [0.72, 0.24, 1.0]];
        var confettiCount = level * 6;
        for (i = 0; i < confettiCount; i++) {
            var cx = 24 + (i * 83 + level * 31) % 592;
            var cy = 28 + (i * 59 + level * 47) % 420;
            var confetti = addCandyConfetti(comp, 200 + level * 30 + i, [cx, cy, 0],
                colors[(i + level) % colors.length], 0.04 + (i % 8) * 0.035, scoreAnchor);
            confetti.name = "CONFETTI WILD " + scoreText + " | " + pad(i + 1, 2);
        }

        var stars = level * 2;
        for (i = 0; i < stars; i++) {
            var sx = 38 + (i * 127 + level * 53) % 564;
            var sy = 36 + (i * 91 + level * 37) % 400;
            var star = addStarGlint(comp, 300 + level * 20 + i, colors[(i + 2) % colors.length],
                0.52 + (i % 5) * 0.44, sx, sy, scoreAnchor);
            star.name = "CANDY STAR WILD " + scoreText + " | " + pad(i + 1, 2);
        }

        var flash = addFullFrameLayer(comp, "FX WILD " + scoreText + " | Chroma pulse", [1.0, 0.18, 0.62], 0);
        flash.blendingMode = BlendingMode.SCREEN;
        var peak = 4 + level * 3;
        replaceKeyframes(flash.property("ADBE Transform Group").property("ADBE Opacity"), [
            [0, 0], [0.52, 0], [0.64, peak], [0.78, 0],
            [1.86, 0], [1.96, peak * 0.65], [2.10, 0],
            [3.12, 0], [3.22, peak * 0.80], [3.36, 0], [5.0, 0]
        ]);
        flash.moveAfter(findLayer(comp, "OUTRO | Fade to black"));
        return { pairs: pairs, confetti: confettiCount, stars: stars };
    }

    function commandBuildCandyScoreFamily(args) {
        return withUndo("Codex: Build escalating candy score family", function () {
            var template = findComp(args.template_comp);
            var assetFolder = findOrCreateProjectFolder("Codex Assets");
            var capsuleItem = importStill(args.capsule_path, assetFolder);
            var stampItems = [];
            var i;
            for (i = 0; i < args.stamp_paths.length; i++) stampItems.push(importStill(args.stamp_paths[i], assetFolder));
            if (stampItems.length < 1) throw new Error("At least one blotter asset is required.");

            var normalized = [];
            var normalizeComps = args.normalize_comps || [];
            for (i = 0; i < normalizeComps.length; i++) {
                try {
                    var existing = findComp(normalizeComps[i]);
                    normalized.push({ comp: existing.name, edited_layers: keepScoreVisibleThroughOutro(existing) });
                } catch (ignoredMissingNormalizeComp) {}
            }

            var created = [];
            var summaries = [];
            try {
                for (i = 0; i < args.targets.length; i++) {
                    var target = args.targets[i];
                    if (projectItemNameExists(target.name)) throw new Error("Target composition already exists: " + target.name);
                    var comp = template.duplicate();
                    created.push(comp);
                    comp.name = target.name;
                    var textLayers = updateCandyScoreText(comp, target.score);
                    var visibleLayers = keepScoreVisibleThroughOutro(comp);
                    intensifyCandyBackground(comp, target.level);
                    var details = addWildCandyDetails(comp, target.level, String(target.score), capsuleItem, stampItems);
                    summaries.push({
                        comp: serializeItem(comp), score: target.score, level: target.level,
                        text_layers: textLayers, outro_fade_removed_from: visibleLayers,
                        extra_capsule_blotter_pairs: details.pairs,
                        extra_confetti: details.confetti, extra_stars: details.stars
                    });
                }
                var last = created[created.length - 1];
                if (last) { last.time = 2.5; last.openInViewer(); }
                return { template: serializeItem(template), normalized: normalized, variants: summaries };
            } catch (error) {
                for (i = created.length - 1; i >= 0; i--) {
                    try { created[i].remove(); } catch (ignoredRemove) {}
                }
                throw error;
            }
        });
    }

    function commandBuildModularRewardVariant(args) {
        return withUndo("Codex: Build modular psychedelic reward", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var template = null;
            var i;
            for (i = 1; i <= source.numLayers; i++) {
                if (source.layer(i) instanceof TextLayer) {
                    template = source.layer(i).property("ADBE Text Properties").property("ADBE Text Document").value;
                    break;
                }
            }
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                for (i = comp.numLayers; i >= 1; i--) comp.layer(i).remove();
                comp.duration = args.duration || 5.0;
                comp.workAreaStart = 0;
                comp.workAreaDuration = comp.duration;
                comp.motionBlur = true;
                comp.shutterAngle = 220;
                comp.shutterPhase = -110;

                var assetFolder = findOrCreateProjectFolder("Codex Assets");
                var radialItem = importStill(args.background_path, assetFolder);
                var frameItem = importStill(args.frame_path, assetFolder);
                var leavesItem = importStill(args.leaves_path, assetFolder);
                var smokeItem = importStill(args.smoke_path, assetFolder);

                var emerald = addFullFrameLayer(comp, "BG | Emerald safety", [0.005, 0.038, 0.015], 100);

                var radialA = addModularAssetLayer(comp, radialItem, "BG | Rotating radial core", BlendingMode.NORMAL, 100, 1.16);
                var radialAT = radialA.property("ADBE Transform Group");
                var radialAScale = coverScaleFor(comp, radialItem, 1.16);
                replaceKeyframes(radialAT.property("ADBE Scale"), [
                    [0, [radialAScale, radialAScale, 100]], [2.50, [radialAScale * 1.022, radialAScale * 1.022, 100]],
                    [5.0, [radialAScale, radialAScale, 100]]
                ]);
                replaceKeyframes(radialAT.property("ADBE Rotate Z"), [[0, -7], [5.0, 11]]);

                var leavesBack = addModularAssetLayer(comp, leavesItem, "LEAVES | RGBA slow parallax", BlendingMode.NORMAL, 100, 1.0);
                var leavesBackT = leavesBack.property("ADBE Transform Group");
                var leavesScale = coverScaleFor(comp, leavesItem, 1.0);
                replaceKeyframes(leavesBackT.property("ADBE Scale"), [
                    [0, [leavesScale, leavesScale, 100]], [2.5, [leavesScale * 1.018, leavesScale * 1.018, 100]],
                    [5.0, [leavesScale, leavesScale, 100]]
                ]);
                replaceKeyframes(leavesBackT.property("ADBE Position"), [
                    [0, [comp.width / 2 - 3, comp.height / 2 + 2, 0]],
                    [2.5, [comp.width / 2 + 4, comp.height / 2 - 3, 0]],
                    [5.0, [comp.width / 2 - 3, comp.height / 2 + 2, 0]]
                ]);
                replaceKeyframes(leavesBackT.property("ADBE Rotate Z"), [[0, -1.6], [2.5, 1.4], [5.0, -1.6]]);

                var frame = addModularAssetLayer(comp, frameItem, "FRAME | RGBA purple smoke portal", BlendingMode.NORMAL, 100, 1.0);
                var frameT = frame.property("ADBE Transform Group");
                var frameScale = coverScaleFor(comp, frameItem, 1.0);
                replaceKeyframes(frameT.property("ADBE Scale"), [
                    [0, [frameScale, frameScale, 100]], [2.5, [frameScale * 1.012, frameScale * 1.012, 100]],
                    [5.0, [frameScale, frameScale, 100]]
                ]);
                replaceKeyframes(frameT.property("ADBE Opacity"), [[0, 88], [0.72, 100], [1.18, 96], [3.6, 100], [5.0, 88]]);

                var smokeDepth = addModularAssetLayer(comp, smokeItem, "SMOKE | RGBA green depth", BlendingMode.NORMAL, 0, 1.04);
                var smokeDepthT = smokeDepth.property("ADBE Transform Group");
                var smokeScale = coverScaleFor(comp, smokeItem, 1.04);
                replaceKeyframes(smokeDepthT.property("ADBE Scale"), [
                    [0, [0, 0, 100]], [0.22, [smokeScale * 0.25, smokeScale * 0.25, 100]],
                    [0.76, [smokeScale, smokeScale, 100]], [2.50, [smokeScale * 1.07, smokeScale * 1.07, 100]],
                    [5.0, [smokeScale * 1.13, smokeScale * 1.13, 100]]
                ]);
                replaceKeyframes(smokeDepthT.property("ADBE Rotate Z"), [[0, -5], [2.5, 3], [5.0, -2]]);
                replaceKeyframes(smokeDepthT.property("ADBE Opacity"), [[0, 0], [0.18, 54], [0.82, 27], [4.30, 20], [5.0, 0]]);
                addAnimatedTurbulence(smokeDepth, 19, 82, 1.4);

                var scoreText = String(args.text || "2500");
                var scorePosition = [comp.width / 2, comp.height / 2 + 4, 0];
                var limeAura = comp.layers.addText(scoreText);
                limeAura.name = "SCORE | Radioactive aura";
                var auraFit = styleDynamicRewardText(limeAura, scoreText, template, [0.48, 1.0, 0.02], [0.18, 0.72, 0.01], 34, scorePosition, 535, 240);
                limeAura.blendingMode = BlendingMode.ADD;
                try {
                    var auraBlur = limeAura.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    auraBlur.property(1).setValue(17);
                } catch (ignoredAuraBlur) {}
                animateDynamicRewardText(limeAura, scorePosition, auraFit, 0.01, 2.5);
                replaceKeyframes(limeAura.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [0.48, 0], [0.68, 54], [4.30, 48], [4.86, 0]]);

                var extrusionPosition = [scorePosition[0] + 7, scorePosition[1] + 10, 0];
                var extrusion = comp.layers.addText(scoreText);
                extrusion.name = "SCORE | Deep black extrusion";
                var extrusionFit = styleDynamicRewardText(extrusion, scoreText, template, [0.004, 0.006, 0.008], [0.0, 0.0, 0.0], 25, extrusionPosition, 535, 240);
                animateDynamicRewardText(extrusion, extrusionPosition, extrusionFit, 0, 3.5);

                var rim = comp.layers.addText(scoreText);
                rim.name = "SCORE | Lime rim";
                var rimFit = styleDynamicRewardText(rim, scoreText, template, [0.015, 0.025, 0.018], [0.63, 1.0, 0.0], 22, scorePosition, 535, 240);
                animateDynamicRewardText(rim, scorePosition, rimFit, 0, 3);
                try {
                    var rimGlow = rim.property("ADBE Effect Parade").addProperty("ADBE Glo2");
                    if (rimGlow.property(6)) rimGlow.property(6).setValue(0.72);
                } catch (ignoredRimGlow) {}

                var hero = comp.layers.addText(scoreText);
                hero.name = "SCORE | Editable gold face";
                var heroFit = styleDynamicRewardText(hero, scoreText, template, [1.0, 0.72, 0.015], [0.015, 0.015, 0.018], 10, scorePosition, 535, 240);
                animateDynamicRewardText(hero, scorePosition, heroFit, 0, 3);
                try {
                    var sweep = hero.property("ADBE Effect Parade").addProperty("CC Light Sweep");
                    var sweepCenter = sweep.property("Center");
                    if (sweepCenter) replaceKeyframes(sweepCenter, [
                        [0.72, [-90, 180]], [1.46, [730, 285]], [2.80, [-100, 190]], [3.54, [740, 280]]
                    ]);
                    try { if (sweep.property("Sweep Intensity")) sweep.property("Sweep Intensity").setValue(48); } catch (ignoredIntensity) {}
                    try { if (sweep.property("Sweep Width")) sweep.property("Sweep Width").setValue(72); } catch (ignoredSweepWidth) {}
                } catch (ignoredSweep) {}
                try { hero.property("ADBE Effect Parade").addProperty("ADBE Glo2"); } catch (ignoredHeroGlow) {}
                try {
                    var scoreLink = "thisComp.layer(\"SCORE | Editable gold face\").text.sourceText";
                    limeAura.property("ADBE Text Properties").property("ADBE Text Document").expression = scoreLink;
                    extrusion.property("ADBE Text Properties").property("ADBE Text Document").expression = scoreLink;
                    rim.property("ADBE Text Properties").property("ADBE Text Document").expression = scoreLink;
                } catch (ignoredScoreLink) {}

                var starColors = [[1.0, 0.88, 0.05], [0.63, 1.0, 0.02], [0.92, 0.28, 1.0], [1.0, 1.0, 0.68]];
                var starPositions = [
                    [116, 122], [523, 116], [94, 332], [549, 337], [188, 84], [451, 86],
                    [155, 385], [487, 387], [272, 105], [375, 365], [83, 222], [560, 226]
                ];
                for (i = 0; i < starPositions.length; i++) {
                    addStarGlint(comp, i, starColors[i % starColors.length],
                        0.70 + (i % 4) * 0.19 + Math.floor(i / 4) * 1.18,
                        starPositions[i][0], starPositions[i][1], null);
                }

                var smokeFront = addModularAssetLayer(comp, smokeItem, "SMOKE | RGBA green foreground curl", BlendingMode.NORMAL, 0, 1.08);
                var smokeFrontT = smokeFront.property("ADBE Transform Group");
                var smokeFrontScale = coverScaleFor(comp, smokeItem, 1.08);
                replaceKeyframes(smokeFrontT.property("ADBE Scale"), [
                    [0, [0, 0, 100]], [0.20, [smokeFrontScale * 0.18, smokeFrontScale * 0.18, 100]],
                    [0.70, [smokeFrontScale * 0.98, smokeFrontScale * 0.98, 100]],
                    [1.28, [smokeFrontScale * 1.12, smokeFrontScale * 1.12, 100]],
                    [5.0, [smokeFrontScale * 1.20, smokeFrontScale * 1.20, 100]]
                ]);
                replaceKeyframes(smokeFrontT.property("ADBE Position"), [
                    [0, [comp.width / 2, comp.height / 2 + 15, 0]],
                    [2.5, [comp.width / 2 - 7, comp.height / 2 - 8, 0]],
                    [5.0, [comp.width / 2 + 5, comp.height / 2 - 16, 0]]
                ]);
                replaceKeyframes(smokeFrontT.property("ADBE Rotate Z"), [[0, 4], [2.5, -4], [5.0, 2]]);
                replaceKeyframes(smokeFrontT.property("ADBE Opacity"), [[0, 0], [0.16, 62], [0.78, 30], [1.34, 14], [4.30, 10], [5.0, 0]]);
                addAnimatedTurbulence(smokeFront, 27, 64, -1.8);

                var flash = addFullFrameLayer(comp, "FX | Lime impact flash", [0.58, 1.0, 0.0], 0);
                flash.blendingMode = BlendingMode.ADD;
                replaceKeyframes(flash.property("ADBE Transform Group").property("ADBE Opacity"), [
                    [0, 0], [0.54, 0], [0.66, 22], [0.82, 0], [3.70, 0], [3.78, 9], [3.92, 0], [5.0, 0]
                ]);

                var outro = addFullFrameLayer(comp, "OUTRO | Fade to black", [0, 0, 0], 0);
                replaceKeyframes(outro.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [4.28, 0], [5.0, 100]]);

                comp.time = 1.18;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: compSnapshot(comp, 300),
                    editable_score: scoreText,
                    modular_assets: {
                        radial: serializeItem(radialItem), frame: serializeItem(frameItem),
                        leaves: serializeItem(leavesItem), smoke: serializeItem(smokeItem)
                    }
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function commandBuildRewardVariant(args) {
        return withUndo("Codex: Build polished reward variant", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var textTemplate = null;
            for (var i = 1; i <= source.numLayers; i++) {
                if (source.layer(i) instanceof TextLayer) {
                    textTemplate = source.layer(i).property("ADBE Text Properties").property("ADBE Text Document").value;
                    break;
                }
            }
            if (!textTemplate) throw new Error("Source composition has no text layer to use as a style reference.");
            textTemplate.text = args.text;
            textTemplate.fontSize = 192;
            textTemplate.tracking = -8;

            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                for (i = comp.numLayers; i >= 1; i--) comp.layer(i).remove();
                comp.motionBlur = true;
                comp.shutterAngle = 200;
                comp.shutterPhase = -100;

                var assetFolder = findOrCreateProjectFolder("Codex Assets");
                var backgroundItem = importStill(args.background_path, assetFolder);
                var smokeItem = importStill(args.smoke_path, assetFolder);
                var background = comp.layers.add(backgroundItem);
                background.name = "BG | Generated cosmic lime";
                var bgTransform = background.property("ADBE Transform Group");
                bgTransform.property("ADBE Position").setValue([comp.width / 2, comp.height / 2]);
                setKeyframes(bgTransform.property("ADBE Scale"), [[0, [103, 103, 100]], [comp.duration, [110, 110, 100]]]);
                setKeyframes(bgTransform.property("ADBE Position"), [[0, [comp.width / 2, comp.height / 2, 0]], [comp.duration, [comp.width / 2 - 5, comp.height / 2 - 3, 0]]]);

                var shade = addFullFrameLayer(comp, "BG | Gentle contrast veil", [0.015, 0.035, 0.045], 5);
                shade.blendingMode = BlendingMode.NORMAL;

                try {
                    var decorItem = findComp("decor");
                    var rays = comp.layers.add(decorItem);
                    rays.name = "BG | Slow radial motion";
                    rays.blendingMode = BlendingMode.ADD;
                    var raysTransform = rays.property("ADBE Transform Group");
                    raysTransform.property("ADBE Opacity").setValue(3);
                    raysTransform.property("ADBE Scale").setValue([128, 128, 100]);
                    setKeyframes(raysTransform.property("ADBE Rotate Z"), [[0, -7], [comp.duration, 12]]);
                } catch (ignoredDecor) {}

                addSmokeAssetLayer(comp, smokeItem, "FX | Smoke depth", false);

                var glowText = comp.layers.addText(args.text);
                glowText.name = "SCORE | Lime underglow";
                styleTextLayer(glowText, textTemplate, [0.38, 1.0, 0.04], [0.15, 0.55, 0.02], 28, [320, 244, 0]);
                glowText.blendingMode = BlendingMode.ADD;
                try {
                    var glowBlur = glowText.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    glowBlur.property(1).setValue(18);
                } catch (ignoredGlowBlur) {}
                animateRewardText(glowText, [320, 244], 3);
                glowText.property("ADBE Transform Group").property("ADBE Opacity").setValueAtTime(1.18, 42);
                glowText.property("ADBE Transform Group").property("ADBE Opacity").setValueAtTime(4.35, 42);

                var shadowText = comp.layers.addText(args.text);
                shadowText.name = "SCORE | Deep extrusion";
                styleTextLayer(shadowText, textTemplate, [0.025, 0.035, 0.07], [0, 0, 0], 23, [325, 251, 0]);
                animateRewardText(shadowText, [325, 251], 4);

                var mainText = comp.layers.addText(args.text);
                mainText.name = "SCORE | Gold hero";
                styleTextLayer(mainText, textTemplate, [1.0, 0.61, 0.018], [0.005, 0.005, 0.008], 14, [320, 244, 0]);
                animateRewardText(mainText, [320, 244], 3);
                try { mainText.property("ADBE Effect Parade").addProperty("ADBE Glo2"); } catch (ignoredGlow) {}
                try {
                    var sweep = mainText.property("ADBE Effect Parade").addProperty("CC Light Sweep");
                    var centerProperty = sweep.property("Center");
                    if (centerProperty) setKeyframes(centerProperty, [[0.82, [70, 215]], [1.55, [575, 265]]]);
                } catch (ignoredSweep) {}

                var lime = [0.42, 1.0, 0.03];
                var gold = [1.0, 0.72, 0.02];
                for (i = 0; i < 18; i++) addParticle(comp, i, i % 3 === 0 ? gold : lime, false);
                for (i = 0; i < 7; i++) addParticle(comp, 18 + i, i % 2 === 0 ? gold : lime, true);

                addSmokeAssetLayer(comp, smokeItem, "FX | Smoke hero reveal", true);

                var outro = addFullFrameLayer(comp, "OUTRO | Fade to black", [0, 0, 0], 0);
                setKeyframes(outro.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [4.34, 0], [5.0, 100]]);

                comp.time = 1.18;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: compSnapshot(comp, 250),
                    background: serializeItem(backgroundItem),
                    smoke: serializeItem(smokeItem)
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function commandEnhanceRewardVariant(args) {
        return withUndo("Codex: Enrich psychedelic reward variant", function () {
            var source = findComp(args.source_comp);
            if (projectItemNameExists(args.new_name)) throw new Error("Target composition already exists: " + args.new_name);
            var comp = source.duplicate();
            comp.name = args.new_name;
            try {
                comp.motionBlur = true;
                comp.shutterAngle = 200;
                comp.shutterPhase = -100;

                var background = findLayer(comp, "BG | Generated cosmic lime");
                var bgTransform = background.property("ADBE Transform Group");
                replaceKeyframes(bgTransform.property("ADBE Scale"), [
                    [0, [103, 103, 100]], [0.72, [106, 106, 100]], [1.32, [104, 104, 100]],
                    [2.45, [107, 107, 100]], [3.55, [104.5, 104.5, 100]], [comp.duration, [110, 110, 100]]
                ]);
                replaceKeyframes(bgTransform.property("ADBE Rotate Z"), [
                    [0, -0.45], [1.25, 0.55], [2.55, -0.22], [3.75, 0.62], [comp.duration, 0.15]
                ]);

                var contrast = findLayer(comp, "BG | Gentle contrast veil");
                contrast.property("ADBE Transform Group").property("ADBE Opacity").setValue(0);

                var sunnyLift = addFullFrameLayer(comp, "GRADE | Sunny cel lift", [1.0, 0.72, 0.24], 0);
                sunnyLift.blendingMode = BlendingMode.SCREEN;
                var sunnyOpacity = sunnyLift.property("ADBE Transform Group").property("ADBE Opacity");
                replaceKeyframes(sunnyOpacity, [[0, 7], [0.78, 11], [1.42, 7], [3.42, 9], [4.34, 6], [5.0, 0]]);
                sunnyLift.moveBefore(background);

                var colorKick = addFullFrameLayer(comp, "GRADE | Coral beat", [1.0, 0.22, 0.08], 0);
                colorKick.blendingMode = BlendingMode.SCREEN;
                var kickOpacity = colorKick.property("ADBE Transform Group").property("ADBE Opacity");
                replaceKeyframes(kickOpacity, [
                    [0, 0], [0.48, 0], [0.64, 4], [0.92, 0], [3.42, 0], [3.60, 3], [3.88, 0], [5.0, 0]
                ]);
                colorKick.moveBefore(background);

                var smokeFront = findLayer(comp, "FX | Smoke hero reveal");
                var smokeFrontTransform = smokeFront.property("ADBE Transform Group");
                replaceKeyframes(smokeFrontTransform.property("ADBE Scale"), [
                    [0, [0, 0, 100]], [0.16, [18, 18, 100]], [0.28, [47, 47, 100]],
                    [0.82, [108, 108, 100]], [1.18, [124, 124, 100]]
                ]);
                replaceKeyframes(smokeFrontTransform.property("ADBE Opacity"), [
                    [0, 0], [0.10, 88], [0.30, 92], [0.82, 48], [1.18, 0]
                ]);

                var smokeDepth = findLayer(comp, "FX | Smoke depth");
                var smokeDepthTransform = smokeDepth.property("ADBE Transform Group");
                replaceKeyframes(smokeDepthTransform.property("ADBE Scale"), [
                    [0, [0, 0, 100]], [0.18, [12, 12, 100]], [0.34, [38, 38, 100]],
                    [1.02, [117, 117, 100]], [1.52, [134, 134, 100]]
                ]);
                replaceKeyframes(smokeDepthTransform.property("ADBE Opacity"), [
                    [0, 0], [0.12, 48], [0.38, 70], [1.02, 32], [1.52, 0]
                ]);

                var outro = findLayer(comp, "OUTRO | Fade to black");
                var pulseColors = [[1.0, 0.73, 0.22], [0.55, 1.0, 0.22], [1.0, 0.34, 0.19], [0.96, 0.87, 0.56]];
                addPsychedelicPulse(comp, 0, pulseColors[0], 0.38, outro);
                addPsychedelicPulse(comp, 1, pulseColors[1], 0.58, outro);
                addPsychedelicPulse(comp, 2, pulseColors[2], 3.30, outro);
                addPsychedelicPulse(comp, 3, pulseColors[3], 3.48, outro);

                var moteColors = [
                    [1.0, 0.78, 0.22], [0.64, 1.0, 0.18], [1.0, 0.34, 0.18],
                    [0.98, 0.88, 0.57], [0.30, 0.92, 0.78], [0.76, 0.36, 0.62]
                ];
                var i;
                for (i = 0; i < 14; i++) {
                    addPsychedelicMote(comp, i, moteColors[i % moteColors.length],
                        0.28 + (i % 5) * 0.075, 1.34 + (i % 3) * 0.16,
                        145 + (i % 5) * 25, i * 53 + 14, outro);
                }
                for (i = 0; i < 10; i++) {
                    addPsychedelicMote(comp, 14 + i, moteColors[(i + 2) % moteColors.length],
                        1.48 + (i % 5) * 0.23, 1.38 + (i % 4) * 0.12,
                        128 + (i % 5) * 29, i * 67 + 31, outro);
                }
                for (i = 0; i < 12; i++) {
                    addPsychedelicMote(comp, 24 + i, moteColors[(i + 4) % moteColors.length],
                        3.18 + (i % 4) * 0.085, 0.92 + (i % 3) * 0.11,
                        132 + (i % 6) * 24, i * 59 + 8, outro);
                }

                comp.time = 1.18;
                comp.openInViewer();
                return {
                    source: serializeItem(source),
                    variant: compSnapshot(comp, 250),
                    added_motes: 36,
                    added_pulses: 4,
                    smoke_starts_at_zero: true
                };
            } catch (error) {
                try { comp.remove(); } catch (ignoredRemove) {}
                throw error;
            }
        });
    }

    function addPsyJackAssetLayer(comp, item, name, position, maxWidth, maxHeight, opacity, blendMode) {
        var layer = comp.layers.add(item);
        layer.name = name;
        layer.blendingMode = blendMode || BlendingMode.NORMAL;
        layer.motionBlur = true;
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([item.width / 2, item.height / 2, 0]);
        transform.property("ADBE Position").setValue(position);
        var fit = Math.min(maxWidth / Math.max(1, item.width), maxHeight / Math.max(1, item.height)) * 100;
        transform.property("ADBE Scale").setValue([fit, fit, 100]);
        transform.property("ADBE Opacity").setValue(opacity === undefined ? 100 : opacity);
        return { layer: layer, fit: fit };
    }

    function stylePsyJackText(layer, text, fontSize, fillColor, strokeColor, strokeWidth, position, maxWidth) {
        var sourceText = layer.property("ADBE Text Properties").property("ADBE Text Document");
        var document = sourceText.value;
        document.text = text;
        try { document.font = "Impact"; } catch (ignoredImpact) {}
        document.fontSize = fontSize;
        document.tracking = text === "PSYCHEDELIC" ? 22 : 4;
        document.applyFill = true;
        document.fillColor = fillColor;
        document.applyStroke = true;
        document.strokeColor = strokeColor;
        document.strokeWidth = strokeWidth;
        document.strokeOverFill = false;
        try { document.justification = ParagraphJustification.CENTER_JUSTIFY; } catch (ignoredJustification) {}
        sourceText.setValue(document);
        recenterTextAnchor(layer, 0);
        var rect = layer.sourceRectAtTime(0, false);
        var fit = Math.min(100, maxWidth / Math.max(1, rect.width) * 100);
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Position").setValue(position);
        transform.property("ADBE Scale").setValue([fit, fit, 100]);
        layer.motionBlur = true;
        return fit;
    }

    function animatePsyJackText(layer, start, finalPosition, fitScale, rotation) {
        var transform = layer.property("ADBE Transform Group");
        replaceKeyframes(transform.property("ADBE Position"), [
            [start, [320, 224, 0]], [start + 0.18, [320, 224, 0]],
            [start + 0.54, [finalPosition[0], finalPosition[1] - 9, 0]],
            [start + 0.78, [finalPosition[0], finalPosition[1] + 4, 0]],
            [start + 1.02, [finalPosition[0], finalPosition[1], 0]],
            [5.4, [finalPosition[0] - 4, finalPosition[1] + 2, 0]],
            [7.7, [finalPosition[0] + 3, finalPosition[1] - 2, 0]],
            [11.3, [finalPosition[0], finalPosition[1], 0]]
        ]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.18, [0, 0, 100]],
            [start + 0.50, [fitScale * 1.28, fitScale * 1.28, 100]],
            [start + 0.68, [fitScale * 0.88, fitScale * 0.88, 100]],
            [start + 0.84, [fitScale * 1.08, fitScale * 1.08, 100]],
            [start + 1.02, [fitScale, fitScale, 100]],
            [4.7, [fitScale, fitScale, 100]], [5.2, [fitScale * 1.035, fitScale * 0.98, 100]],
            [6.1, [fitScale * 0.98, fitScale * 1.04, 100]], [7.7, [fitScale, fitScale, 100]],
            [10.6, [fitScale * 1.06, fitScale * 1.06, 100]], [11.3, [fitScale, fitScale, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [
            [start, rotation - 18], [start + 0.50, rotation + 5], [start + 0.72, rotation - 3],
            [start + 1.02, rotation], [5.3, rotation - 1.5], [6.3, rotation + 1.3],
            [7.7, rotation], [11.3, rotation]
        ]);
        replaceKeyframes(transform.property("ADBE Opacity"), [
            [start, 0], [start + 0.15, 0], [start + 0.32, 100], [11.3, 100]
        ]);
    }

    function addPsyJackOrbitAsset(comp, item, name, index, start, scale, opacity) {
        var layer = comp.layers.add(item);
        layer.name = name;
        layer.motionBlur = true;
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([item.width / 2, item.height / 2, 0]);
        var angle = (index * 47 + 16) * Math.PI / 180;
        var angle2 = angle + 1.2;
        var radius = 178 + (index % 4) * 22;
        var radius2 = radius + (index % 2 === 0 ? 24 : -18);
        replaceKeyframes(transform.property("ADBE Position"), [
            [start, [320, 224, 0]],
            [start + 0.46, [320 + Math.cos(angle) * radius, 232 + Math.sin(angle) * radius * 0.72, 0]],
            [5.7, [320 + Math.cos(angle + 0.72) * radius2, 232 + Math.sin(angle + 0.72) * radius2 * 0.72, 0]],
            [8.2, [320 + Math.cos(angle2) * radius, 232 + Math.sin(angle2) * radius * 0.72, 0]],
            [11.3, [320 + Math.cos(angle2 + 0.72) * radius2, 232 + Math.sin(angle2 + 0.72) * radius2 * 0.72, 0]]
        ]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.30, [scale * 1.22, scale * 1.22, 100]],
            [start + 0.48, [scale, scale, 100]], [6.3, [scale * 1.10, scale * 1.10, 100]],
            [8.2, [scale * 0.92, scale * 0.92, 100]], [11.3, [scale, scale, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [
            [start, index * 31 - 48], [5.7, index * 31 + 155], [8.2, index * 31 + 320], [11.3, index * 31 + 540]
        ]);
        replaceKeyframes(transform.property("ADBE Opacity"), [
            [start, 0], [start + 0.20, 0], [start + 0.40, opacity], [11.3, opacity]
        ]);
        return layer;
    }

    function commandBuildPsychedelicJackpot(args) {
        return withUndo("Codex: Build psychedelic jackpot", function () {
            var stageName = args.stage_name || "Psychedelic_Jackpot_STAGE";
            var finalName = args.final_name || "Psychedelic_Jackpot_FINAL";
            if (projectItemNameExists(stageName)) throw new Error("Target composition already exists: " + stageName);
            if (projectItemNameExists(finalName)) throw new Error("Target composition already exists: " + finalName);
            var project = requireProject();
            var stage = null;
            var finalComp = null;
            try {
                var fps = 29.9700012207031;
                var duration = 12.0;
                var scenesFolder = findOrCreateProjectFolder("Scenes");
                var assetFolder = findOrCreateProjectFolder("Codex Assets");
                var bgItem = importStill(args.background_path, assetFolder);
                var raysItem = importStill(args.rays_path, assetFolder);
                var cloudsItem = importStill(args.clouds_path, assetFolder);
                var eyeOpenItem = importStill(args.eye_open_path, assetFolder);
                var eyeClosedItem = importStill(args.eye_closed_path, assetFolder);
                var waveItem = importStill(args.wave_path, assetFolder);
                var capsuleItem = args.capsule_path ? importStill(args.capsule_path, assetFolder) : null;
                var stampItems = [];
                var i;
                if (args.stamp_paths) {
                    for (i = 0; i < args.stamp_paths.length; i++) stampItems.push(importStill(args.stamp_paths[i], assetFolder));
                }

                stage = project.items.addComp(stageName, 640, 480, 1, duration, fps);
                stage.parentFolder = scenesFolder;
                stage.motionBlur = true;
                stage.shutterAngle = 210;
                stage.shutterPhase = -105;

                var bg = addModularAssetLayer(stage, bgItem, "BG | Cosmic blotter paper", BlendingMode.NORMAL, 100, 1.08);
                var bgScale = coverScaleFor(stage, bgItem, 1.08);
                var bgTransform = bg.property("ADBE Transform Group");
                replaceKeyframes(bgTransform.property("ADBE Scale"), [[0, [bgScale, bgScale, 100]], [6, [bgScale * 1.07, bgScale * 1.07, 100]], [12, [bgScale * 1.02, bgScale * 1.02, 100]]]);
                replaceKeyframes(bgTransform.property("ADBE Rotate Z"), [[0, -1.2], [6, 1.5], [12, -0.4]]);

                var rays = addModularAssetLayer(stage, raysItem, "RAYS | Clockwise neon fan", BlendingMode.SCREEN, 82, 1.20);
                var raysScale = coverScaleFor(stage, raysItem, 1.20);
                var raysT = rays.property("ADBE Transform Group");
                replaceKeyframes(raysT.property("ADBE Scale"), [[0, [raysScale, raysScale, 100]], [6, [raysScale * 1.05, raysScale * 1.05, 100]], [12, [raysScale, raysScale, 100]]]);
                replaceKeyframes(raysT.property("ADBE Rotate Z"), [[0, -12], [4.7, 16], [7.8, 48], [12, 118]]);
                replaceKeyframes(raysT.property("ADBE Opacity"), [[0, 32], [1.1, 48], [2.3, 82], [7.8, 88], [11.3, 76]]);

                var rayEcho = rays.duplicate();
                rayEcho.name = "RAYS | Counter rotating echo";
                var rayEchoT = rayEcho.property("ADBE Transform Group");
                replaceKeyframes(rayEchoT.property("ADBE Rotate Z"), [[0, 22], [4.7, -8], [7.8, -44], [12, -126]]);
                replaceKeyframes(rayEchoT.property("ADBE Opacity"), [[0, 10], [2.3, 24], [7.8, 34], [11.3, 26]]);

                var cloudBack = addModularAssetLayer(stage, cloudsItem, "CLOUDS | Rear slow billow", BlendingMode.SCREEN, 44, 1.24);
                var cloudBackScale = coverScaleFor(stage, cloudsItem, 1.24);
                var cloudBackT = cloudBack.property("ADBE Transform Group");
                replaceKeyframes(cloudBackT.property("ADBE Position"), [[0, [302, 235, 0]], [4, [336, 224, 0]], [8, [310, 247, 0]], [12, [340, 232, 0]]]);
                replaceKeyframes(cloudBackT.property("ADBE Scale"), [[0, [cloudBackScale, cloudBackScale, 100]], [6, [cloudBackScale * 1.12, cloudBackScale * 1.12, 100]], [12, [cloudBackScale * 1.04, cloudBackScale * 1.04, 100]]]);
                addAnimatedTurbulence(cloudBack, 24, 150, 2.4);
                try {
                    var rearBlur = cloudBack.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    rearBlur.property(1).setValue(14);
                } catch (ignoredRearBlur) {}

                var waveResult = addPsyJackAssetLayer(stage, waveItem, "WAVE | Liquid rainbow undertow", [320, 386, 0], 610, 250, 90, BlendingMode.SCREEN);
                var wave = waveResult.layer;
                var waveFit = waveResult.fit;
                var waveT = wave.property("ADBE Transform Group");
                replaceKeyframes(waveT.property("ADBE Position"), [[0, [308, 390, 0]], [2.4, [329, 380, 0]], [4.8, [308, 392, 0]], [7.6, [333, 378, 0]], [10.5, [314, 394, 0]], [12, [328, 384, 0]]]);
                replaceKeyframes(waveT.property("ADBE Scale"), [[0, [waveFit * 0.92, waveFit * 0.92, 100]], [4.7, [waveFit * 1.05, waveFit * 0.96, 100]], [7.8, [waveFit * 0.96, waveFit * 1.08, 100]], [12, [waveFit, waveFit, 100]]]);
                replaceKeyframes(waveT.property("ADBE Rotate Z"), [[0, -4], [4.8, 4], [7.8, -6], [12, 5]]);
                addAnimatedTurbulence(wave, 18, 86, 3.2);

                var eyeClosedResult = addPsyJackAssetLayer(stage, eyeClosedItem, "EYE | Closed anticipation", [320, 228, 0], 470, 230, 100, BlendingMode.NORMAL);
                var closedEye = eyeClosedResult.layer;
                var closedScale = eyeClosedResult.fit;
                var closedT = closedEye.property("ADBE Transform Group");
                replaceKeyframes(closedT.property("ADBE Scale"), [[0, [closedScale * 0.92, closedScale * 0.92, 100]], [0.8, [closedScale * 1.03, closedScale * 0.96, 100]], [1.15, [closedScale, closedScale, 100]]]);
                replaceHoldKeyframes(closedT.property("ADBE Opacity"), [[0, 100], [1.15, 100], [1.16, 0], [12, 0]]);

                var eyeOpenResult = addPsyJackAssetLayer(stage, eyeOpenItem, "EYE | Seven fps awakening", [320, 228, 0], 470, 230, 0, BlendingMode.NORMAL);
                var openEye = eyeOpenResult.layer;
                var eyeScale = eyeOpenResult.fit;
                var eyeT = openEye.property("ADBE Transform Group");
                replaceHoldKeyframes(eyeT.property("ADBE Opacity"), [[0, 0], [1.15, 0], [1.16, 100], [12, 100]]);
                var eyeScaleProperty = eyeT.property("ADBE Scale");
                while (eyeScaleProperty.numKeys > 0) eyeScaleProperty.removeKey(1);
                var eyeKeys = [
                    [0, [eyeScale, 0, 100]], [1.16, [eyeScale, 0, 100]], [1.30, [eyeScale, eyeScale * 0.12, 100]],
                    [1.44, [eyeScale, eyeScale * 0.27, 100]], [1.58, [eyeScale, eyeScale * 0.46, 100]],
                    [1.72, [eyeScale, eyeScale * 0.67, 100]], [1.86, [eyeScale, eyeScale * 0.86, 100]],
                    [2.00, [eyeScale, eyeScale, 100]], [2.22, [eyeScale, eyeScale, 100]],
                    [4.4, [eyeScale * 1.03, eyeScale * 0.98, 100]], [6.2, [eyeScale * 0.98, eyeScale * 1.03, 100]],
                    [7.7, [eyeScale, eyeScale, 100]], [11.3, [eyeScale * 1.04, eyeScale * 1.04, 100]]
                ];
                for (i = 0; i < eyeKeys.length; i++) eyeScaleProperty.setValueAtTime(eyeKeys[i][0], eyeKeys[i][1]);
                for (i = 1; i <= eyeScaleProperty.numKeys; i++) {
                    try {
                        if (i <= 8) eyeScaleProperty.setInterpolationTypeAtKey(i, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
                        else eyeScaleProperty.setInterpolationTypeAtKey(i, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
                    } catch (ignoredEyeEase) {}
                }
                replaceKeyframes(eyeT.property("ADBE Position"), [[0, [320, 228, 0]], [2.10, [320, 228, 0]], [2.78, [320, 133, 0]], [4.8, [316, 137, 0]], [6.5, [324, 132, 0]], [7.7, [320, 136, 0]], [11.3, [320, 136, 0]]]);
                try {
                    var eyeGlow = openEye.property("ADBE Effect Parade").addProperty("ADBE Glo2");
                    setNamedEffectValue(eyeGlow, ["Glow Threshold", "ADBE Glo2-0001"], 48);
                    setNamedEffectValue(eyeGlow, ["Glow Radius", "ADBE Glo2-0003"], 18);
                    setNamedEffectValue(eyeGlow, ["Glow Intensity", "ADBE Glo2-0002"], 0.7);
                } catch (ignoredEyeGlow) {}

                var cloudFront = addModularAssetLayer(stage, cloudsItem, "CLOUDS | Foreground rolling frame", BlendingMode.SCREEN, 78, 1.06);
                var cloudFrontScale = coverScaleFor(stage, cloudsItem, 1.06);
                var cloudFrontT = cloudFront.property("ADBE Transform Group");
                replaceKeyframes(cloudFrontT.property("ADBE Position"), [[0, [336, 248, 0]], [3.2, [305, 235, 0]], [6.2, [333, 252, 0]], [9.1, [300, 239, 0]], [12, [330, 246, 0]]]);
                replaceKeyframes(cloudFrontT.property("ADBE Scale"), [[0, [cloudFrontScale, cloudFrontScale, 100]], [6, [cloudFrontScale * 1.08, cloudFrontScale * 1.08, 100]], [12, [cloudFrontScale * 1.02, cloudFrontScale * 1.02, 100]]]);
                addAnimatedTurbulence(cloudFront, 36, 98, 4.0);

                var orbitCount = 0;
                if (capsuleItem) {
                    for (i = 0; i < 6; i++) {
                        addPsyJackOrbitAsset(stage, capsuleItem, "DOPE | Orbit capsule " + pad(i + 1, 2), i, 2.55 + (i % 3) * 0.12, 4.0 + (i % 2) * 0.8, 72);
                        orbitCount++;
                    }
                }
                for (i = 0; i < 8 && stampItems.length > 0; i++) {
                    addPsyJackOrbitAsset(stage, stampItems[i % stampItems.length], "DOPE | Orbit blotter " + pad(i + 1, 2), 10 + i, 2.62 + (i % 4) * 0.10, 4.1 + (i % 3) * 0.6, 76);
                    orbitCount++;
                }

                var psychShadow = stage.layers.addText("PSYCHEDELIC");
                psychShadow.name = "TITLE | Psychedelic black depth";
                var psychShadowFit = stylePsyJackText(psychShadow, "PSYCHEDELIC", 94, [0.01, 0.01, 0.015], [0.01, 0.01, 0.015], 15, [320, 262, 0], 538);
                animatePsyJackText(psychShadow, 2.28, [320, 254], psychShadowFit, -1);

                var psychGlow = stage.layers.addText("PSYCHEDELIC");
                psychGlow.name = "TITLE | Psychedelic lime aura";
                var psychGlowFit = stylePsyJackText(psychGlow, "PSYCHEDELIC", 94, [0.40, 1.0, 0.03], [0.40, 1.0, 0.03], 17, [320, 254, 0], 538);
                animatePsyJackText(psychGlow, 2.28, [320, 254], psychGlowFit, -1);
                psychGlow.blendingMode = BlendingMode.ADD;
                psychGlow.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.48";
                try {
                    var psychBlur = psychGlow.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    psychBlur.property(1).setValue(9);
                } catch (ignoredPsychBlur) {}

                var psychFace = stage.layers.addText("PSYCHEDELIC");
                psychFace.name = "TITLE | Psychedelic face";
                var psychFit = stylePsyJackText(psychFace, "PSYCHEDELIC", 94, [1.0, 0.18, 0.64], [0.025, 0.01, 0.04], 8, [320, 254, 0], 538);
                animatePsyJackText(psychFace, 2.28, [320, 254], psychFit, -1);

                var jackpotShadow = stage.layers.addText("JACKPOT");
                jackpotShadow.name = "TITLE | Jackpot black depth";
                var jackpotShadowFit = stylePsyJackText(jackpotShadow, "JACKPOT", 160, [0.01, 0.01, 0.015], [0.01, 0.01, 0.015], 18, [320, 365, 0], 548);
                animatePsyJackText(jackpotShadow, 2.38, [320, 357], jackpotShadowFit, 1);

                var jackpotGlow = stage.layers.addText("JACKPOT");
                jackpotGlow.name = "TITLE | Jackpot magenta aura";
                var jackpotGlowFit = stylePsyJackText(jackpotGlow, "JACKPOT", 160, [1.0, 0.10, 0.64], [0.56, 1.0, 0.03], 21, [320, 357, 0], 548);
                animatePsyJackText(jackpotGlow, 2.38, [320, 357], jackpotGlowFit, 1);
                jackpotGlow.blendingMode = BlendingMode.ADD;
                jackpotGlow.property("ADBE Transform Group").property("ADBE Opacity").expression = "value * 0.52";
                try {
                    var jackpotBlur = jackpotGlow.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    jackpotBlur.property(1).setValue(10);
                } catch (ignoredJackpotBlur) {}

                var jackpotFace = stage.layers.addText("JACKPOT");
                jackpotFace.name = "TITLE | Jackpot gold face";
                var jackpotFit = stylePsyJackText(jackpotFace, "JACKPOT", 160, [1.0, 0.72, 0.02], [0.02, 0.01, 0.03], 10, [320, 357, 0], 548);
                animatePsyJackText(jackpotFace, 2.38, [320, 357], jackpotFit, 1);
                try {
                    var sweep = jackpotFace.property("ADBE Effect Parade").addProperty("CC Light Sweep");
                    var centerProperty = setNamedEffectValue(sweep, ["Center", "CC Light Sweep-0001"], [70, 330]);
                    if (centerProperty) replaceKeyframes(centerProperty, [[2.86, [70, 330]], [3.42, [575, 365]], [5.10, [80, 340]], [5.58, [560, 365]], [7.05, [72, 340]], [7.48, [568, 360]], [10.10, [72, 340]], [10.62, [570, 360]]]);
                    setNamedEffectValue(sweep, ["Sweep Intensity", "CC Light Sweep-0002"], 56);
                    setNamedEffectValue(sweep, ["Edge Intensity", "CC Light Sweep-0004"], 82);
                    setNamedEffectValue(sweep, ["Edge Thickness", "CC Light Sweep-0005"], 7);
                } catch (ignoredJackpotSweep) {}

                var particleColors = [[0.60, 1.0, 0.03], [1.0, 0.14, 0.66], [0.05, 0.92, 1.0], [1.0, 0.78, 0.02], [0.73, 0.20, 1.0]];
                for (i = 0; i < 16; i++) addPsychedelicMote(stage, i, particleColors[i % particleColors.length], 2.34 + (i % 4) * 0.07, 1.42 + (i % 3) * 0.16, 145 + (i % 6) * 25, i * 53 + 11, null);
                for (i = 0; i < 18; i++) addPsychedelicMote(stage, 30 + i, particleColors[(i + 2) % particleColors.length], 7.68 + (i % 5) * 0.05, 1.48 + (i % 4) * 0.14, 160 + (i % 6) * 27, i * 59 + 7, null);
                for (i = 0; i < 18; i++) {
                    var sx = 34 + (i * 113) % 572;
                    var sy = 28 + (i * 79) % 414;
                    addStarGlint(stage, 500 + i, particleColors[(i + 1) % particleColors.length], 1.82 + (i % 7) * 1.25, sx, sy, null);
                }

                finalComp = project.items.addComp(finalName, 640, 480, 1, duration, fps);
                finalComp.parentFolder = scenesFolder;
                finalComp.motionBlur = true;
                finalComp.shutterAngle = 210;
                finalComp.shutterPhase = -105;

                var baseScene = finalComp.layers.add(stage);
                baseScene.name = "SCENE | Living psychedelic stage";
                replaceKeyframes(baseScene.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 100], [7.72, 100], [8.12, 0], [12, 0]]);
                try {
                    var liquid = baseScene.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
                    var liquidAmount = liquid.property("ADBE Turbulent Displace-0002") || liquid.property(1);
                    var liquidSize = liquid.property("ADBE Turbulent Displace-0003") || liquid.property(2);
                    var liquidEvolution = liquid.property("ADBE Turbulent Displace-0006") || liquid.property(6);
                    if (liquidAmount) replaceKeyframes(liquidAmount, [[0, 0], [4.55, 0], [5.15, 12], [6.25, 28], [7.75, 58], [8.1, 22]]);
                    if (liquidSize) liquidSize.setValue(94);
                    if (liquidEvolution) replaceKeyframes(liquidEvolution, [[0, 0], [4.55, 0], [8.1, 1080]]);
                } catch (ignoredLiquid) {}
                try {
                    var waveWarp = baseScene.property("ADBE Effect Parade").addProperty("ADBE Wave Warp");
                    if (waveWarp.property(2)) replaceKeyframes(waveWarp.property(2), [[0, 0], [4.7, 0], [5.5, 7], [6.7, 18], [7.9, 32]]);
                    if (waveWarp.property(3)) waveWarp.property(3).setValue(118);
                    if (waveWarp.property(4)) waveWarp.property(4).setValue(90);
                } catch (ignoredWaveWarp) {}

                var kaleidoScene = finalComp.layers.add(stage);
                kaleidoScene.name = "FINALE | Kaleidoscope overload";
                var kaleidoTransform = kaleidoScene.property("ADBE Transform Group");
                replaceKeyframes(kaleidoTransform.property("ADBE Opacity"), [[0, 0], [7.72, 0], [8.12, 100], [11.35, 100]]);
                replaceKeyframes(kaleidoTransform.property("ADBE Scale"), [[0, [100, 100, 100]], [7.72, [100, 100, 100]], [8.5, [126, 126, 100]], [9.4, [92, 92, 100]], [10.25, [142, 142, 100]], [11.1, [108, 108, 100]]]);
                replaceKeyframes(kaleidoTransform.property("ADBE Rotate Z"), [[0, 0], [7.72, 0], [8.7, 18], [9.6, -14], [10.5, 34], [11.2, 86]]);
                var kaleidaAdded = false;
                try {
                    var kaleida = kaleidoScene.property("ADBE Effect Parade").addProperty("CC Kaleida");
                    kaleidaAdded = true;
                    var kaleidaCenter = setNamedEffectValue(kaleida, ["Center", "CC Kaleida-0001"], [320, 232]);
                    var kaleidaSize = setNamedEffectValue(kaleida, ["Size", "CC Kaleida-0002"], 150);
                    var kaleidaRotation = setNamedEffectValue(kaleida, ["Rotation", "CC Kaleida-0003"], 0);
                    if (kaleidaCenter) replaceKeyframes(kaleidaCenter, [[7.72, [320, 232]], [9.0, [292, 246]], [10.0, [350, 218]], [11.25, [320, 240]]]);
                    if (kaleidaSize) replaceKeyframes(kaleidaSize, [[7.72, 235], [8.6, 128], [9.5, 82], [10.4, 46], [11.25, 24]]);
                    if (kaleidaRotation) replaceKeyframes(kaleidaRotation, [[7.72, 0], [8.7, 56], [9.6, -38], [10.5, 132], [11.25, 260]]);
                } catch (ignoredKaleida) {
                    try {
                        var mirrorA = kaleidoScene.property("ADBE Effect Parade").addProperty("ADBE Mirror");
                        if (mirrorA.property(1)) mirrorA.property(1).setValue([320, 240]);
                        if (mirrorA.property(2)) mirrorA.property(2).setValue(45);
                        var mirrorB = kaleidoScene.property("ADBE Effect Parade").addProperty("ADBE Mirror");
                        if (mirrorB.property(1)) mirrorB.property(1).setValue([320, 240]);
                        if (mirrorB.property(2)) mirrorB.property(2).setValue(135);
                    } catch (ignoredMirrorFallback) {}
                }
                try {
                    var finaleTurbulence = kaleidoScene.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
                    var finaleAmount = finaleTurbulence.property("ADBE Turbulent Displace-0002") || finaleTurbulence.property(1);
                    var finaleEvolution = finaleTurbulence.property("ADBE Turbulent Displace-0006") || finaleTurbulence.property(6);
                    if (finaleAmount) replaceKeyframes(finaleAmount, [[7.72, 4], [8.6, 16], [9.6, 38], [10.6, 72], [11.25, 24]]);
                    if (finaleEvolution) replaceKeyframes(finaleEvolution, [[7.72, 0], [11.25, 1440]]);
                } catch (ignoredFinaleTurbulence) {}

                var flash = addFullFrameLayer(finalComp, "FINALE | White lime overload flash", [0.84, 1.0, 0.72], 0);
                flash.blendingMode = BlendingMode.ADD;
                replaceKeyframes(flash.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [10.58, 0], [10.78, 18], [10.92, 88], [11.10, 16], [11.24, 0]]);

                var outro = addFullFrameLayer(finalComp, "OUTRO | Single full screen fade", [0, 0, 0], 0);
                replaceKeyframes(outro.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [11.18, 0], [12.0, 100]]);

                finalComp.time = 3.35;
                finalComp.openInViewer();
                return {
                    stage: compSnapshot(stage, 80),
                    final_comp: compSnapshot(finalComp, 40),
                    duration: duration,
                    eye_open_fps: 7,
                    orbit_assets: orbitCount,
                    kaleida_effect_added: kaleidaAdded,
                    final_fade_is_global_only: true
                };
            } catch (error) {
                try { if (finalComp) finalComp.remove(); } catch (ignoredRemoveFinal) {}
                try { if (stage) stage.remove(); } catch (ignoredRemoveStage) {}
                throw error;
            }
        });
    }

    function commandPolishPsychedelicJackpot(args) {
        return withUndo("Codex: Polish psychedelic jackpot", function () {
            var stage = findComp(args.stage_comp || "Psychedelic_Jackpot_STAGE_v01");
            var finalComp = findComp(args.final_comp || "Psychedelic_Jackpot_FINAL_v01");
            var openEye = findLayer(stage, "EYE | Seven fps awakening");
            var eyeScaleProperty = openEye.property("ADBE Transform Group").property("ADBE Scale");
            var xScale = eyeScaleProperty.valueAtTime(2.0, false)[0];
            while (eyeScaleProperty.numKeys > 0) eyeScaleProperty.removeKey(1);
            var eyeKeys = [
                [0, [xScale, 0, 100]], [1.16, [xScale, xScale * 0.045, 100]],
                [1.30, [xScale, xScale * 0.12, 100]], [1.44, [xScale, xScale * 0.27, 100]],
                [1.58, [xScale, xScale * 0.46, 100]], [1.72, [xScale, xScale * 0.67, 100]],
                [1.86, [xScale, xScale * 0.86, 100]], [2.00, [xScale, xScale, 100]],
                [2.22, [xScale, xScale, 100]], [4.4, [xScale * 1.03, xScale * 0.98, 100]],
                [6.2, [xScale * 0.98, xScale * 1.03, 100]], [7.7, [xScale, xScale, 100]],
                [11.3, [xScale * 1.04, xScale * 1.04, 100]]
            ];
            var i;
            for (i = 0; i < eyeKeys.length; i++) eyeScaleProperty.setValueAtTime(eyeKeys[i][0], eyeKeys[i][1]);
            for (i = 1; i <= eyeScaleProperty.numKeys; i++) {
                try {
                    if (i <= 8) eyeScaleProperty.setInterpolationTypeAtKey(i, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
                    else eyeScaleProperty.setInterpolationTypeAtKey(i, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
                } catch (ignoredEyePolishEase) {}
            }

            var baseScene = findLayer(finalComp, "SCENE | Living psychedelic stage");
            replaceKeyframes(baseScene.property("ADBE Transform Group").property("ADBE Scale"), [
                [0, [104, 104, 100]], [4.45, [104, 104, 100]], [5.45, [112, 112, 100]],
                [6.6, [118, 118, 100]], [7.72, [124, 124, 100]], [8.12, [124, 124, 100]]
            ]);
            try {
                var baseEffects = baseScene.property("ADBE Effect Parade");
                for (i = 1; i <= baseEffects.numProperties; i++) {
                    if (baseEffects.property(i).matchName === "ADBE Wave Warp" && baseEffects.property(i).property(6)) {
                        baseEffects.property(i).property(6).setValue(2);
                    }
                }
            } catch (ignoredPinning) {}

            var kaleidoScene = findLayer(finalComp, "FINALE | Kaleidoscope overload");
            replaceKeyframes(kaleidoScene.property("ADBE Transform Group").property("ADBE Scale"), [
                [0, [160, 160, 100]], [7.72, [160, 160, 100]], [8.50, [178, 178, 100]],
                [9.40, [166, 166, 100]], [10.25, [194, 194, 100]], [11.10, [184, 184, 100]]
            ]);
            finalComp.time = 8.8;
            finalComp.openInViewer();
            return {
                stage: serializeItem(stage),
                final_comp: serializeItem(finalComp),
                eye_slit_added: true,
                melt_overscan_percent: 124,
                kaleidoscope_minimum_overscan_percent: 160,
                black_corner_fix: true
            };
        });
    }

    function setHoldVisibility(layer, start, end, duration) {
        var opacity = layer.property("ADBE Transform Group").property("ADBE Opacity");
        var keys = [];
        if (start <= 0) {
            keys.push([0, 100]);
        } else {
            keys.push([0, 0]);
            keys.push([Math.max(0, start - 0.001), 0]);
            keys.push([start, 100]);
        }
        if (end < duration - 0.001) {
            keys.push([Math.max(start, end - 0.001), 100]);
            keys.push([end, 0]);
            keys.push([duration, 0]);
        } else {
            keys.push([duration, 100]);
        }
        replaceHoldKeyframes(opacity, keys);
    }

    function commandRebuildPsychedelicJackpotV02(args) {
        return withUndo("Codex: Rebuild psychedelic jackpot depth", function () {
            var sourceStage = findComp(args.source_stage || "Psychedelic_Jackpot_STAGE_v01");
            var sourceFinal = findComp(args.source_final || "Psychedelic_Jackpot_FINAL_v01");
            var stageName = args.stage_name || "Psychedelic_Jackpot_STAGE_v02";
            var finalName = args.final_name || "Psychedelic_Jackpot_FINAL_v02";
            if (projectItemNameExists(stageName)) throw new Error("Target composition already exists: " + stageName);
            if (projectItemNameExists(finalName)) throw new Error("Target composition already exists: " + finalName);
            var stage = null;
            var finalComp = null;
            try {
                var assetFolder = findOrCreateProjectFolder("Codex Assets");
                var closedItem = importStill(args.eye_closed_path, assetFolder);
                var eye20Item = importStill(args.eye_20_path, assetFolder);
                var eye40Item = importStill(args.eye_40_path, assetFolder);
                var eye65Item = importStill(args.eye_65_path, assetFolder);
                var openItem = importStill(args.eye_open_path, assetFolder);
                var riverItem = importStill(args.river_path, assetFolder);
                var shrineItem = importStill(args.shrine_path, assetFolder);

                stage = sourceStage.duplicate();
                stage.name = stageName;
                stage.motionBlur = true;
                var duration = stage.duration;
                var disableNames = [
                    "EYE | Closed anticipation", "EYE | Seven fps awakening",
                    "WAVE | Liquid rainbow undertow", "CLOUDS | Rear slow billow",
                    "CLOUDS | Foreground rolling frame"
                ];
                var i;
                for (i = 0; i < disableNames.length; i++) {
                    try { findLayer(stage, disableNames[i]).enabled = false; } catch (ignoredDisableOld) {}
                }

                var rays = findLayer(stage, "RAYS | Clockwise neon fan");
                var rayEcho = findLayer(stage, "RAYS | Counter rotating echo");
                var raysT = rays.property("ADBE Transform Group");
                var rayEchoT = rayEcho.property("ADBE Transform Group");
                replaceKeyframes(raysT.property("ADBE Position"), [[0, [320, 42, 0]], [6, [318, 38, 0]], [12, [320, 42, 0]]]);
                replaceKeyframes(rayEchoT.property("ADBE Position"), [[0, [320, 42, 0]], [6, [322, 46, 0]], [12, [320, 42, 0]]]);
                replaceKeyframes(raysT.property("ADBE Rotate Z"), [[0, -12], [6, 20], [12, 68]]);
                replaceKeyframes(rayEchoT.property("ADBE Rotate Z"), [[0, 18], [6, -16], [12, -72]]);
                replaceKeyframes(raysT.property("ADBE Opacity"), [[0, 26], [0.75, 34], [1.55, 72], [7.7, 78], [12, 68]]);
                replaceKeyframes(rayEchoT.property("ADBE Opacity"), [[0, 8], [1.55, 18], [7.7, 28], [12, 20]]);

                var scenicAnchor = findLayer(stage, "TITLE | Psychedelic black depth");
                var riverResult = addPsyJackAssetLayer(stage, riverItem, "DEPTH | Perspective rainbow river", [320, 354, 0], 730, 610, 100, BlendingMode.NORMAL);
                var river = riverResult.layer;
                river.moveAfter(scenicAnchor);
                var riverFit = riverResult.fit;
                var riverT = river.property("ADBE Transform Group");
                replaceKeyframes(riverT.property("ADBE Position"), [[0, [320, 358, 0]], [4, [316, 352, 0]], [8, [325, 357, 0]], [12, [320, 350, 0]]]);
                replaceKeyframes(riverT.property("ADBE Scale"), [[0, [riverFit * 1.02, riverFit * 1.02, 100]], [4, [riverFit, riverFit, 100]], [8, [riverFit * 1.045, riverFit * 1.045, 100]], [12, [riverFit * 1.01, riverFit * 1.01, 100]]]);
                try {
                    var riverTurbulence = river.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
                    var riverAmount = riverTurbulence.property("ADBE Turbulent Displace-0002") || riverTurbulence.property(1);
                    var riverSize = riverTurbulence.property("ADBE Turbulent Displace-0003") || riverTurbulence.property(2);
                    var riverEvolution = riverTurbulence.property("ADBE Turbulent Displace-0006") || riverTurbulence.property(6);
                    if (riverAmount) replaceKeyframes(riverAmount, [[0, 2], [4, 4], [8, 6], [12, 3]]);
                    if (riverSize) riverSize.setValue(185);
                    if (riverEvolution) replaceKeyframes(riverEvolution, [[0, 0], [12, 540]]);
                } catch (ignoredRiverTurbulence) {}

                var portal = stage.layers.addShape();
                portal.name = "DEPTH | Solid cosmic eye portal";
                addOutlinedEllipseGroup(portal, [536, 286], [0, 0], [0.035, 0.004, 0.10], [0.52, 1.0, 0.03], 11, 100);
                addOutlinedEllipseGroup(portal, [488, 244], [0, 0], [0.075, 0.008, 0.16], [1.0, 0.12, 0.64], 6, 100);
                var portalT = portal.property("ADBE Transform Group");
                portalT.property("ADBE Position").setValue([320, 236, 0]);
                replaceKeyframes(portalT.property("ADBE Scale"), [[0, [96, 96, 100]], [0.70, [99, 99, 100]], [1.55, [103, 103, 100]], [6, [101, 101, 100]], [12, [103, 103, 100]]]);
                portal.moveBefore(river);
                try {
                    var portalGlow = portal.property("ADBE Effect Parade").addProperty("ADBE Glo2");
                    setNamedEffectValue(portalGlow, ["Glow Threshold", "ADBE Glo2-0001"], 52);
                    setNamedEffectValue(portalGlow, ["Glow Radius", "ADBE Glo2-0003"], 24);
                    setNamedEffectValue(portalGlow, ["Glow Intensity", "ADBE Glo2-0002"], 0.85);
                } catch (ignoredPortalGlow) {}

                var eyeItems = [closedItem, eye20Item, eye40Item, eye65Item, openItem];
                var eyeNames = [
                    "EYE DRAWN | 00 closed", "EYE DRAWN | 01 twenty percent",
                    "EYE DRAWN | 02 forty percent", "EYE DRAWN | 03 sixty five percent",
                    "EYE DRAWN | 04 fully open"
                ];
                var starts = [0, 0.74, 0.99, 1.24, 1.49];
                var ends = [0.74, 0.99, 1.24, 1.49, duration];
                var eyeLayers = [];
                for (i = 0; i < eyeItems.length; i++) {
                    var eyeResult = addPsyJackAssetLayer(stage, eyeItems[i], eyeNames[i], [320, 236, 0], 514, 286, 0, BlendingMode.NORMAL);
                    var eyeLayer = eyeResult.layer;
                    setHoldVisibility(eyeLayer, starts[i], ends[i], duration);
                    eyeLayer.moveBefore(portal);
                    eyeLayers.push(eyeLayer);
                }
                try {
                    var openGlow = eyeLayers[4].property("ADBE Effect Parade").addProperty("ADBE Glo2");
                    setNamedEffectValue(openGlow, ["Glow Threshold", "ADBE Glo2-0001"], 58);
                    setNamedEffectValue(openGlow, ["Glow Radius", "ADBE Glo2-0003"], 12);
                    setNamedEffectValue(openGlow, ["Glow Intensity", "ADBE Glo2-0002"], 0.48);
                } catch (ignoredOpenGlow) {}

                var shrine = addModularAssetLayer(stage, shrineItem, "FOREGROUND | Cloud eye shrine", BlendingMode.NORMAL, 100, 1.0);
                shrine.moveBefore(eyeLayers[4]);
                var shrineT = shrine.property("ADBE Transform Group");
                var shrineScale = coverScaleFor(stage, shrineItem, 1.0);
                replaceKeyframes(shrineT.property("ADBE Scale"), [[0, [shrineScale, shrineScale, 100]], [6, [shrineScale * 1.012, shrineScale * 1.012, 100]], [12, [shrineScale, shrineScale, 100]]]);
                var keyerName = "none";
                try {
                    var keylight = shrine.property("ADBE Effect Parade").addProperty("Keylight (1.2)");
                    var screenColour = setNamedEffectValue(keylight, ["Screen Colour", "Screen Color"], [0, 1, 0]);
                    if (!screenColour && keylight.property(2)) keylight.property(2).setValue([0, 1, 0]);
                    keyerName = "Keylight (1.2)";
                } catch (ignoredKeylight) {
                    try {
                        var linearKey = shrine.property("ADBE Effect Parade").addProperty("ADBE Linear Color Key");
                        setNamedEffectValue(linearKey, ["Key Color", "ADBE Linear Color Key-0002"], [0, 1, 0]);
                        setNamedEffectValue(linearKey, ["Matching Tolerance", "ADBE Linear Color Key-0003"], 32);
                        setNamedEffectValue(linearKey, ["Matching Softness", "ADBE Linear Color Key-0004"], 3);
                        keyerName = "Linear Color Key";
                    } catch (ignoredLinearKey) {}
                }

                finalComp = sourceFinal.duplicate();
                finalComp.name = finalName;
                var baseScene = findLayer(finalComp, "SCENE | Living psychedelic stage");
                var kaleidoScene = findLayer(finalComp, "FINALE | Kaleidoscope overload");
                baseScene.replaceSource(stage, false);
                kaleidoScene.replaceSource(stage, false);
                replaceKeyframes(baseScene.property("ADBE Transform Group").property("ADBE Scale"), [[0, [102, 102, 100]], [6.8, [102, 102, 100]], [7.72, [108, 108, 100]], [8.12, [110, 110, 100]]]);
                var baseEffects = baseScene.property("ADBE Effect Parade");
                for (i = 1; i <= baseEffects.numProperties; i++) {
                    var effect = baseEffects.property(i);
                    if (effect.matchName === "ADBE Turbulent Displace") {
                        var amount = effect.property("ADBE Turbulent Displace-0002") || effect.property(1);
                        var evolution = effect.property("ADBE Turbulent Displace-0006") || effect.property(6);
                        if (amount) replaceKeyframes(amount, [[0, 0], [6.65, 0], [7.15, 6], [7.72, 18], [8.12, 14]]);
                        if (evolution) replaceKeyframes(evolution, [[0, 0], [6.65, 0], [8.12, 360]]);
                    } else if (effect.matchName === "ADBE Wave Warp") {
                        if (effect.property(2)) replaceKeyframes(effect.property(2), [[0, 0], [6.80, 0], [7.30, 3], [7.90, 7]]);
                        if (effect.property(3)) effect.property(3).setValue(210);
                        if (effect.property(5)) effect.property(5).setValue(0.35);
                        if (effect.property(6)) effect.property(6).setValue(2);
                    }
                }

                stage.time = 1.26;
                finalComp.time = 3.35;
                finalComp.openInViewer();
                return {
                    stage: compSnapshot(stage, 110),
                    final_comp: compSnapshot(finalComp, 30),
                    eye_frames: eyeLayers.length,
                    eye_step_fps: 4,
                    global_melt_starts_at: 6.65,
                    shrine_keyer: keyerName,
                    solid_portal_core: true,
                    perspective_river: true
                };
            } catch (error) {
                try { if (finalComp) finalComp.remove(); } catch (ignoredRemoveFinal) {}
                try { if (stage) stage.remove(); } catch (ignoredRemoveStage) {}
                throw error;
            }
        });
    }

    function addPsychedelicGreenKey(layer) {
        var keyerName = "none";
        try {
            var keylight = layer.property("ADBE Effect Parade").addProperty("Keylight (1.2)");
            var screenColour = setNamedEffectValue(keylight, ["Screen Colour", "Screen Color"], [0, 1, 0]);
            if (!screenColour && keylight.property(2)) keylight.property(2).setValue([0, 1, 0]);
            keyerName = "Keylight (1.2)";
        } catch (ignoredKeylight) {
            try {
                var linearKey = layer.property("ADBE Effect Parade").addProperty("ADBE Linear Color Key");
                setNamedEffectValue(linearKey, ["Key Color", "ADBE Linear Color Key-0002"], [0, 1, 0]);
                setNamedEffectValue(linearKey, ["Matching Tolerance", "ADBE Linear Color Key-0003"], 32);
                setNamedEffectValue(linearKey, ["Matching Softness", "ADBE Linear Color Key-0004"], 3);
                keyerName = "Linear Color Key";
            } catch (ignoredLinearKey) {}
        }
        return keyerName;
    }

    function commandRebuildPsychedelicJackpotV03(args) {
        return withUndo("Codex: Rebuild psychedelic jackpot organic depth", function () {
            var sourceStage = findComp(args.source_stage || "Psychedelic_Jackpot_STAGE_v02");
            var sourceFinal = findComp(args.source_final || "Psychedelic_Jackpot_FINAL_v02");
            var stageName = args.stage_name || "Psychedelic_Jackpot_STAGE_v03";
            var finalName = args.final_name || "Psychedelic_Jackpot_FINAL_v03";
            if (projectItemNameExists(stageName)) throw new Error("Target composition already exists: " + stageName);
            if (projectItemNameExists(finalName)) throw new Error("Target composition already exists: " + finalName);
            var stage = null;
            var finalComp = null;
            try {
                var assetFolder = findOrCreateProjectFolder("Codex Assets");
                var eye00Item = importStill(args.eye_00_path, assetFolder);
                var eye25Item = importStill(args.eye_25_path, assetFolder);
                var eye50Item = importStill(args.eye_50_path, assetFolder);
                var eye75Item = importStill(args.eye_75_path, assetFolder);
                var eye100Item = importStill(args.eye_100_path, assetFolder);
                var riverItem = importStill(args.river_path, assetFolder);
                var shrineItem = importStill(args.shrine_path, assetFolder);

                stage = sourceStage.duplicate();
                stage.name = stageName;
                stage.motionBlur = true;
                var duration = stage.duration;
                var disableNames = [
                    "EYE DRAWN | 00 closed", "EYE DRAWN | 01 twenty percent",
                    "EYE DRAWN | 02 forty percent", "EYE DRAWN | 03 sixty five percent",
                    "EYE DRAWN | 04 fully open", "FOREGROUND | Cloud eye shrine",
                    "DEPTH | Perspective rainbow river", "DEPTH | Solid cosmic eye portal"
                ];
                var i;
                for (i = 0; i < disableNames.length; i++) {
                    try { findLayer(stage, disableNames[i]).enabled = false; } catch (ignoredDisableV02) {}
                }

                var rays = findLayer(stage, "RAYS | Clockwise neon fan");
                var rayEcho = findLayer(stage, "RAYS | Counter rotating echo");
                var raysT = rays.property("ADBE Transform Group");
                var rayEchoT = rayEcho.property("ADBE Transform Group");
                replaceKeyframes(raysT.property("ADBE Position"), [[0, [320, 52, 0]], [6, [316, 46, 0]], [12, [320, 52, 0]]]);
                replaceKeyframes(rayEchoT.property("ADBE Position"), [[0, [320, 52, 0]], [6, [324, 58, 0]], [12, [320, 52, 0]]]);
                replaceKeyframes(raysT.property("ADBE Rotate Z"), [[0, -14], [6, 18], [12, 64]]);
                replaceKeyframes(rayEchoT.property("ADBE Rotate Z"), [[0, 20], [6, -14], [12, -68]]);
                replaceKeyframes(raysT.property("ADBE Opacity"), [[0, 20], [0.68, 28], [1.40, 76], [7.7, 80], [12, 66]]);
                replaceKeyframes(rayEchoT.property("ADBE Opacity"), [[0, 5], [1.40, 16], [7.7, 25], [12, 18]]);

                var scenicAnchor = findLayer(stage, "TITLE | Psychedelic black depth");
                var riverResult = addPsyJackAssetLayer(stage, riverItem, "DEPTH V03 | Wide perspective rainbow river", [320, 350, 0], 950, 720, 100, BlendingMode.NORMAL);
                var river = riverResult.layer;
                river.moveAfter(scenicAnchor);
                var riverFit = riverResult.fit;
                var riverT = river.property("ADBE Transform Group");
                replaceKeyframes(riverT.property("ADBE Position"), [[0, [320, 354, 0]], [4, [311, 348, 0]], [8, [329, 355, 0]], [12, [320, 348, 0]]]);
                replaceKeyframes(riverT.property("ADBE Scale"), [[0, [riverFit * 1.00, riverFit * 1.00, 100]], [4, [riverFit * 1.025, riverFit * 1.025, 100]], [8, [riverFit * 1.055, riverFit * 1.055, 100]], [12, [riverFit * 1.01, riverFit * 1.01, 100]]]);
                try {
                    var riverTurbulence = river.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
                    var riverAmount = riverTurbulence.property("ADBE Turbulent Displace-0002") || riverTurbulence.property(1);
                    var riverSize = riverTurbulence.property("ADBE Turbulent Displace-0003") || riverTurbulence.property(2);
                    var riverEvolution = riverTurbulence.property("ADBE Turbulent Displace-0006") || riverTurbulence.property(6);
                    if (riverAmount) replaceKeyframes(riverAmount, [[0, 1.5], [4, 2.5], [8, 3.5], [12, 2]]);
                    if (riverSize) riverSize.setValue(240);
                    if (riverEvolution) replaceKeyframes(riverEvolution, [[0, 0], [12, 430]]);
                } catch (ignoredRiverTurbulence) {}

                var portal = stage.layers.addShape();
                portal.name = "DEPTH V03 | Solid eye portal core";
                addOutlinedEllipseGroup(portal, [420, 192], [0, 0], [0.10, 0.015, 0.18], [0.52, 1.0, 0.03], 7, 100);
                addOutlinedEllipseGroup(portal, [398, 172], [0, 0], [0.035, 0.003, 0.065], [1.0, 0.12, 0.64], 4, 100);
                var portalT = portal.property("ADBE Transform Group");
                portalT.property("ADBE Position").setValue([320, 226, 0]);
                replaceKeyframes(portalT.property("ADBE Scale"), [[0, [96, 96, 100]], [0.68, [98, 98, 100]], [1.40, [103, 103, 100]], [6, [100, 100, 100]], [12, [102, 102, 100]]]);
                portal.moveBefore(river);
                try {
                    var portalGlow = portal.property("ADBE Effect Parade").addProperty("ADBE Glo2");
                    setNamedEffectValue(portalGlow, ["Glow Threshold", "ADBE Glo2-0001"], 55);
                    setNamedEffectValue(portalGlow, ["Glow Radius", "ADBE Glo2-0003"], 20);
                    setNamedEffectValue(portalGlow, ["Glow Intensity", "ADBE Glo2-0002"], 0.70);
                } catch (ignoredPortalGlow) {}

                var recessed = addModularAssetLayer(stage, shrineItem, "MIDGROUND V03 | Recessed cloud banks", BlendingMode.NORMAL, 31, 0.91);
                recessed.moveAfter(portal);
                var recessedT = recessed.property("ADBE Transform Group");
                replaceKeyframes(recessedT.property("ADBE Position"), [[0, [316, 232, 0]], [6, [324, 228, 0]], [12, [316, 232, 0]]]);
                var recessedScale = coverScaleFor(stage, shrineItem, 0.91);
                replaceKeyframes(recessedT.property("ADBE Scale"), [[0, [recessedScale, recessedScale, 100]], [6, [recessedScale * 1.018, recessedScale * 1.018, 100]], [12, [recessedScale, recessedScale, 100]]]);
                var recessedKeyer = addPsychedelicGreenKey(recessed);
                try {
                    var recessBlur = recessed.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    if (recessBlur.property(1)) recessBlur.property(1).setValue(3.5);
                    if (recessBlur.property(3)) recessBlur.property(3).setValue(1);
                } catch (ignoredRecessBlur) {}

                var eyeItems = [eye00Item, eye25Item, eye50Item, eye75Item, eye100Item];
                var eyeNames = [
                    "EYE V03 | 00 closed", "EYE V03 | 01 twenty five percent",
                    "EYE V03 | 02 fifty percent", "EYE V03 | 03 seventy five percent",
                    "EYE V03 | 04 fully open"
                ];
                var starts = [0, 0.68, 0.92, 1.16, 1.40];
                var ends = [0.68, 0.92, 1.16, 1.40, duration];
                var eyeLayers = [];
                for (i = 0; i < eyeItems.length; i++) {
                    var eyeResult = addPsyJackAssetLayer(stage, eyeItems[i], eyeNames[i], [320, 226, 0], 438, 260, 100, BlendingMode.NORMAL);
                    var eyeLayer = eyeResult.layer;
                    setHoldVisibility(eyeLayer, starts[i], ends[i], duration);
                    eyeLayer.moveBefore(portal);
                    eyeLayers.push(eyeLayer);
                }
                try {
                    var openGlow = eyeLayers[4].property("ADBE Effect Parade").addProperty("ADBE Glo2");
                    setNamedEffectValue(openGlow, ["Glow Threshold", "ADBE Glo2-0001"], 62);
                    setNamedEffectValue(openGlow, ["Glow Radius", "ADBE Glo2-0003"], 10);
                    setNamedEffectValue(openGlow, ["Glow Intensity", "ADBE Glo2-0002"], 0.40);
                } catch (ignoredOpenGlow) {}

                var shrine = addModularAssetLayer(stage, shrineItem, "FOREGROUND V03 | Asymmetric cloud shrine", BlendingMode.NORMAL, 100, 1.0);
                shrine.moveBefore(eyeLayers[0]);
                var shrineT = shrine.property("ADBE Transform Group");
                var shrineScale = coverScaleFor(stage, shrineItem, 1.0);
                replaceKeyframes(shrineT.property("ADBE Position"), [[0, [320, 240, 0]], [3, [317, 241, 0]], [6, [321, 238, 0]], [9, [324, 241, 0]], [12, [320, 240, 0]]]);
                replaceKeyframes(shrineT.property("ADBE Scale"), [[0, [shrineScale, shrineScale, 100]], [6, [shrineScale * 1.012, shrineScale * 1.012, 100]], [12, [shrineScale, shrineScale, 100]]]);
                var shrineKeyer = addPsychedelicGreenKey(shrine);
                try {
                    var shadow = shrine.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
                    setNamedEffectValue(shadow, ["Opacity", "ADBE Drop Shadow-0002"], 38);
                    setNamedEffectValue(shadow, ["Direction", "ADBE Drop Shadow-0003"], 180);
                    setNamedEffectValue(shadow, ["Distance", "ADBE Drop Shadow-0004"], 8);
                    setNamedEffectValue(shadow, ["Softness", "ADBE Drop Shadow-0005"], 22);
                } catch (ignoredShrineShadow) {}

                finalComp = sourceFinal.duplicate();
                finalComp.name = finalName;
                var baseScene = findLayer(finalComp, "SCENE | Living psychedelic stage");
                var kaleidoScene = findLayer(finalComp, "FINALE | Kaleidoscope overload");
                baseScene.replaceSource(stage, false);
                kaleidoScene.replaceSource(stage, false);
                replaceKeyframes(baseScene.property("ADBE Transform Group").property("ADBE Scale"), [[0, [102, 102, 100]], [7.0, [102, 102, 100]], [7.72, [106, 106, 100]], [8.12, [108, 108, 100]]]);
                var baseEffects = baseScene.property("ADBE Effect Parade");
                for (i = 1; i <= baseEffects.numProperties; i++) {
                    var effect = baseEffects.property(i);
                    if (effect.matchName === "ADBE Turbulent Displace") {
                        var amount = effect.property("ADBE Turbulent Displace-0002") || effect.property(1);
                        var evolution = effect.property("ADBE Turbulent Displace-0006") || effect.property(6);
                        if (amount) replaceKeyframes(amount, [[0, 0], [7.0, 0], [7.40, 4], [7.72, 10], [8.12, 8]]);
                        if (evolution) replaceKeyframes(evolution, [[0, 0], [7.0, 0], [8.12, 300]]);
                    } else if (effect.matchName === "ADBE Wave Warp") {
                        if (effect.property(2)) replaceKeyframes(effect.property(2), [[0, 0], [7.15, 0], [7.45, 2], [7.90, 4]]);
                        if (effect.property(3)) effect.property(3).setValue(240);
                        if (effect.property(5)) effect.property(5).setValue(0.28);
                        if (effect.property(6)) effect.property(6).setValue(2);
                    }
                }

                stage.time = 1.42;
                finalComp.time = 3.35;
                finalComp.openInViewer();
                return {
                    stage: serializeItem(stage),
                    final_comp: serializeItem(finalComp),
                    eye_frames: eyeLayers.length,
                    eye_step_fps: 4.17,
                    eye_display_width: 438,
                    global_melt_starts_at: 7.0,
                    shrine_keyer: shrineKeyer,
                    recessed_keyer: recessedKeyer,
                    asymmetric_cloud_depth: true,
                    wide_perspective_river: true
                };
            } catch (error) {
                try { if (finalComp) finalComp.remove(); } catch (ignoredRemoveFinal) {}
                try { if (stage) stage.remove(); } catch (ignoredRemoveStage) {}
                throw error;
            }
        });
    }

    function commandPolishPsychedelicJackpotV03(args) {
        return withUndo("Codex: Tighten psychedelic jackpot eye portal", function () {
            var stage = findComp(args.stage || "Psychedelic_Jackpot_STAGE_v03");
            var finalComp = findComp(args.final_comp || "Psychedelic_Jackpot_FINAL_v03");
            var portal = findLayer(stage, "DEPTH V03 | Solid eye portal core");
            var root = portal.property("ADBE Root Vectors Group");
            var sizes = [[420, 192], [398, 172]];
            var fills = [[0.10, 0.015, 0.18], [0.035, 0.003, 0.065]];
            var strokes = [[0.52, 1.0, 0.03], [1.0, 0.12, 0.64]];
            var widths = [7, 4];
            var editedGroups = 0;
            for (var i = 1; i <= root.numProperties && editedGroups < 2; i++) {
                var group = root.property(i);
                if (!group || group.matchName !== "ADBE Vector Group") continue;
                var vectors = group.property("ADBE Vectors Group");
                if (!vectors) continue;
                var index = editedGroups;
                for (var j = 1; j <= vectors.numProperties; j++) {
                    var property = vectors.property(j);
                    if (property.matchName === "ADBE Vector Shape - Ellipse") {
                        property.property("ADBE Vector Ellipse Size").setValue(sizes[index]);
                    } else if (property.matchName === "ADBE Vector Graphic - Fill") {
                        property.property("ADBE Vector Fill Color").setValue(fills[index]);
                    } else if (property.matchName === "ADBE Vector Graphic - Stroke") {
                        property.property("ADBE Vector Stroke Color").setValue(strokes[index]);
                        property.property("ADBE Vector Stroke Width").setValue(widths[index]);
                    }
                }
                editedGroups++;
            }
            try {
                var effects = portal.property("ADBE Effect Parade");
                for (var k = 1; k <= effects.numProperties; k++) {
                    var effect = effects.property(k);
                    if (effect.matchName === "ADBE Glo2") {
                        setNamedEffectValue(effect, ["Glow Radius", "ADBE Glo2-0003"], 13);
                        setNamedEffectValue(effect, ["Glow Intensity", "ADBE Glo2-0002"], 0.46);
                    }
                }
            } catch (ignoredPortalEffectPolish) {}
            stage.time = 1.50;
            finalComp.time = 1.50;
            finalComp.openInViewer();
            return {
                stage: serializeItem(stage),
                final_comp: serializeItem(finalComp),
                edited_portal_groups: editedGroups,
                portal_outer_size: sizes[0],
                portal_inner_size: sizes[1],
                black_emblem_arc_removed: true
            };
        });
    }

    function removeKeysAtOrAfter(property, cutoff) {
        if (!property) return;
        for (var i = property.numKeys; i >= 1; i--) {
            if (property.keyTime(i) >= cutoff - 0.0001) property.removeKey(i);
        }
    }

    function commandBuildPsychedelicJackpotSecondHalf(args) {
        return withUndo("Codex: Build psychedelic jackpot 200000 finale", function () {
            var stage = findComp(args.stage || "Psychedelic_Jackpot_STAGE_v03");
            var finalComp = findComp(args.final_comp || "Psychedelic_Jackpot_FINAL_v03");
            var sourceName = args.kaleido_source || "Psychedelic_Jackpot_KALEIDO_SOURCE_v04";
            var secondName = args.second_half_comp || "Psychedelic_Jackpot_SECOND_HALF_v04";
            var backupName = args.backup_name || "Psychedelic_Jackpot_FINAL_v03_before_200000";
            var zoomStart = args.zoom_start === undefined ? 5.55 : args.zoom_start;
            var crossfadeEnd = args.crossfade_end === undefined ? 6.15 : args.crossfade_end;
            var zoomEnd = args.zoom_end === undefined ? 11.22 : args.zoom_end;
            var scoreText = String(args.score_text || "200000");
            if (projectItemNameExists(sourceName)) throw new Error("Target composition already exists: " + sourceName);
            if (projectItemNameExists(secondName)) throw new Error("Target composition already exists: " + secondName);

            var backup = null;
            if (!projectItemNameExists(backupName)) {
                backup = finalComp.duplicate();
                backup.name = backupName;
            }

            var kaleidoSource = null;
            var secondHalf = null;
            var secondLayer = null;
            try {
                kaleidoSource = stage.duplicate();
                kaleidoSource.name = sourceName;
                for (var i = 1; i <= kaleidoSource.numLayers; i++) {
                    var sourceLayer = kaleidoSource.layer(i);
                    if (sourceLayer.name.indexOf("TITLE |") === 0) sourceLayer.enabled = false;
                }

                secondHalf = requireProject().items.addComp(secondName, finalComp.width, finalComp.height,
                    finalComp.pixelAspect, finalComp.duration, finalComp.frameRate);
                secondHalf.parentFolder = finalComp.parentFolder;
                secondHalf.bgColor = [0.015, 0.0, 0.035];

                var baseScene = findLayer(finalComp, "SCENE | Living psychedelic stage");
                var baseTransform = baseScene.property("ADBE Transform Group");
                var baseScaleProperty = baseTransform.property("ADBE Scale");
                var basePositionProperty = baseTransform.property("ADBE Position");
                var baseOpacityProperty = baseTransform.property("ADBE Opacity");
                var baseScale = baseScaleProperty.valueAtTime(zoomStart, false);
                var basePosition = basePositionProperty.valueAtTime(zoomStart, false);
                var baseOpacity = baseOpacityProperty.valueAtTime(zoomStart, false);

                var background = secondHalf.layers.add(kaleidoSource);
                background.name = "KALEIDO BG | Clean user stage without titles";
                var backgroundTransform = background.property("ADBE Transform Group");
                backgroundTransform.property("ADBE Anchor Point").setValue([stage.width / 2, stage.height / 2, 0]);
                backgroundTransform.property("ADBE Position").setValue(basePosition);
                backgroundTransform.property("ADBE Scale").setValue(baseScale);
                background.motionBlur = true;
                try {
                    var kaleida = background.property("ADBE Effect Parade").addProperty("CC Kaleida");
                    var kaleidaCenter = kaleida.property("CC Kaleida-0001") || kaleida.property(1);
                    var kaleidaSize = kaleida.property("CC Kaleida-0002") || kaleida.property(2);
                    var kaleidaMirror = kaleida.property("CC Kaleida-0003") || kaleida.property(3);
                    var kaleidaRotation = kaleida.property("CC Kaleida-0004") || kaleida.property(4);
                    if (kaleidaCenter) replaceKeyframes(kaleidaCenter, [[zoomStart, [320, 226]], [8.4, [310, 238]], [zoomEnd, [326, 232]]]);
                    if (kaleidaSize) replaceKeyframes(kaleidaSize, [[zoomStart, 255], [7.5, 178], [9.25, 108], [zoomEnd, 54]]);
                    if (kaleidaMirror) kaleidaMirror.setValue(6);
                    if (kaleidaRotation) replaceKeyframes(kaleidaRotation, [[zoomStart, 0], [7.4, 34], [9.3, 112], [zoomEnd, 224]]);
                } catch (ignoredKaleida) {}
                try {
                    var turbulence = background.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
                    var amount = turbulence.property("ADBE Turbulent Displace-0002") || turbulence.property(2);
                    var size = turbulence.property("ADBE Turbulent Displace-0003") || turbulence.property(3);
                    var evolution = turbulence.property("ADBE Turbulent Displace-0006") || turbulence.property(6);
                    if (amount) replaceKeyframes(amount, [[zoomStart, 2], [8.5, 8], [10.4, 16], [zoomEnd, 21]]);
                    if (size) size.setValue(170);
                    if (evolution) replaceKeyframes(evolution, [[zoomStart, 0], [zoomEnd, 720]]);
                } catch (ignoredTurbulence) {}

                var scoreDepth = secondHalf.layers.addText(scoreText);
                scoreDepth.name = "SCORE 200000 | Black violet depth";
                stylePsyJackText(scoreDepth, scoreText, 178, [0.025, 0.0, 0.055], [0.0, 0.0, 0.0], 18, [329, 254], 570);
                var scoreAura = secondHalf.layers.addText(scoreText);
                scoreAura.name = "SCORE 200000 | Magenta lime aura";
                stylePsyJackText(scoreAura, scoreText, 178, [1.0, 0.08, 0.58], [0.52, 1.0, 0.03], 18, [320, 244], 570);
                scoreAura.blendingMode = BlendingMode.ADD;
                scoreAura.property("ADBE Transform Group").property("ADBE Opacity").setValue(58);
                try {
                    var auraBlur = scoreAura.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    if (auraBlur.property(1)) auraBlur.property(1).setValue(10);
                    if (auraBlur.property(3)) auraBlur.property(3).setValue(1);
                } catch (ignoredAuraBlur) {}

                var scoreFace = secondHalf.layers.addText(scoreText);
                scoreFace.name = "SCORE 200000 | Gold dimensional face";
                stylePsyJackText(scoreFace, scoreText, 178, [1.0, 0.72, 0.015], [0.045, 0.0, 0.075], 10, [320, 244], 570);
                try {
                    var ramp = scoreFace.property("ADBE Effect Parade").addProperty("ADBE Ramp");
                    setNamedEffectValue(ramp, ["Start of Ramp"], [320, 172]);
                    setNamedEffectValue(ramp, ["End of Ramp"], [320, 310]);
                    setNamedEffectValue(ramp, ["Start Color"], [1.0, 1.0, 0.72]);
                    setNamedEffectValue(ramp, ["End Color"], [1.0, 0.39, 0.0]);
                    setNamedEffectValue(ramp, ["Ramp Scatter", "Scatter"], 12);
                } catch (ignoredRamp) {}
                try {
                    var sweep = scoreFace.property("ADBE Effect Parade").addProperty("CC Light Sweep");
                    var sweepCenter = sweep.property("Center") || sweep.property(1);
                    if (sweepCenter) replaceKeyframes(sweepCenter, [[6.25, [-120, 214]], [7.12, [760, 264]], [8.55, [-120, 220]], [9.35, [760, 270]], [10.30, [-120, 220]], [11.05, [760, 270]]]);
                    setNamedEffectValue(sweep, ["Sweep Intensity"], 68);
                    setNamedEffectValue(sweep, ["Sweep Width"], 58);
                    setNamedEffectValue(sweep, ["Edge Intensity"], 86);
                    setNamedEffectValue(sweep, ["Edge Thickness"], 8);
                } catch (ignoredSweep) {}
                try {
                    var shadow = scoreFace.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
                    setNamedEffectValue(shadow, ["Opacity", "ADBE Drop Shadow-0002"], 62);
                    setNamedEffectValue(shadow, ["Direction", "ADBE Drop Shadow-0003"], 135);
                    setNamedEffectValue(shadow, ["Distance", "ADBE Drop Shadow-0004"], 8);
                    setNamedEffectValue(shadow, ["Softness", "ADBE Drop Shadow-0005"], 12);
                } catch (ignoredScoreShadow) {}

                var scoreLayers = [scoreDepth, scoreAura, scoreFace];
                for (i = 0; i < scoreLayers.length; i++) {
                    var scoreOpacity = scoreLayers[i].property("ADBE Transform Group").property("ADBE Opacity");
                    var targetOpacity = i === 1 ? 58 : 100;
                    replaceKeyframes(scoreOpacity, [[0, 0], [5.95, 0], [6.32, targetOpacity], [zoomEnd, targetOpacity], [11.55, targetOpacity]]);
                }

                secondLayer = finalComp.layers.add(secondHalf);
                secondLayer.name = "SECOND HALF | One continuous kaleidoscope score zoom";
                secondLayer.motionBlur = true;
                var secondTransform = secondLayer.property("ADBE Transform Group");
                secondTransform.property("ADBE Anchor Point").setValue([320, 240, 0]);
                secondTransform.property("ADBE Position").setValue([320, 240, 0]);
                replaceKeyframes(secondTransform.property("ADBE Scale"), [[0, [100, 100, 100]], [zoomStart, [100, 100, 100]], [crossfadeEnd, [108, 108, 100]], [7.55, [142, 142, 100]], [9.10, [202, 202, 100]], [10.35, [268, 268, 100]], [zoomEnd, [330, 330, 100]]]);
                replaceKeyframes(secondTransform.property("ADBE Opacity"), [[0, 0], [zoomStart, 0], [crossfadeEnd, 100], [finalComp.duration, 100]]);

                var flash = findLayer(finalComp, "FINALE | White lime overload flash");
                secondLayer.moveAfter(flash);
                var oldKaleido = null;
                try { oldKaleido = findLayer(finalComp, "FINALE | Kaleidoscope overload"); oldKaleido.enabled = false; } catch (ignoredOldKaleido) {}

                removeKeysAtOrAfter(baseOpacityProperty, zoomStart);
                setKeyframes(baseOpacityProperty, [[zoomStart, baseOpacity], [crossfadeEnd, 0], [finalComp.duration, 0]]);
                removeKeysAtOrAfter(baseScaleProperty, zoomStart);
                setKeyframes(baseScaleProperty, [[zoomStart, baseScale], [crossfadeEnd, [baseScale[0] * 1.08, baseScale[1] * 1.08, 100]]]);

                secondHalf.time = 7.2;
                finalComp.time = 7.2;
                finalComp.openInViewer();
                return {
                    backup: backup ? serializeItem(backup) : null,
                    kaleido_source: serializeItem(kaleidoSource),
                    second_half_comp: serializeItem(secondHalf),
                    final_comp: serializeItem(finalComp),
                    score_text: scoreText,
                    zoom_start: zoomStart,
                    zoom_end: zoomEnd,
                    final_zoom_percent: 330,
                    first_half_preserved: true,
                    old_kaleidoscope_disabled: oldKaleido ? true : false
                };
            } catch (error) {
                try { if (secondLayer) secondLayer.remove(); } catch (ignoredRemoveLayer) {}
                try { if (secondHalf) secondHalf.remove(); } catch (ignoredRemoveSecond) {}
                try { if (kaleidoSource) kaleidoSource.remove(); } catch (ignoredRemoveSource) {}
                throw error;
            }
        });
    }

    function commandRebuildPsychedelicJackpotPortalZoom(args) {
        return withUndo("Codex: Rebuild psychedelic jackpot pupil portal zoom", function () {
            var finalComp = findComp(args.final_comp || "Psychedelic_Jackpot_FINAL_v03");
            var secondHalf = findComp(args.second_half_comp || "Psychedelic_Jackpot_SECOND_HALF_v04");
            var secondLayer = findLayer(finalComp, "SECOND HALF | One continuous kaleidoscope score zoom");
            var baseScene = findLayer(finalComp, "SCENE | Living psychedelic stage");
            var flash = findLayer(finalComp, "FINALE | White lime overload flash");
            var portalStart = args.portal_start === undefined ? 5.55 : args.portal_start;
            var portalFull = args.portal_full === undefined ? 6.55 : args.portal_full;
            var zoomEnd = args.zoom_end === undefined ? 11.22 : args.zoom_end;

            var oldMatte = null;
            try { oldMatte = findLayer(finalComp, "PORTAL MATTE | Pupil expansion"); } catch (ignoredOldMatte) {}
            if (oldMatte) oldMatte.remove();

            var secondTransform = secondLayer.property("ADBE Transform Group");
            secondTransform.property("ADBE Anchor Point").setValue([320, 240, 0]);
            secondTransform.property("ADBE Position").setValue([320, 240, 0]);
            replaceKeyframes(secondTransform.property("ADBE Scale"), [
                [0, [0, 0, 100]],
                [portalStart, [0, 0, 100]],
                [5.75, [40, 40, 100]],
                [5.95, [70, 70, 100]],
                [6.15, [100, 100, 100]],
                [6.35, [135, 135, 100]],
                [portalFull, [170, 170, 100]],
                [7.40, [220, 220, 100]],
                [8.70, [300, 300, 100]],
                [9.80, [390, 390, 100]],
                [10.70, [490, 490, 100]],
                [zoomEnd, [600, 600, 100]]
            ]);
            replaceKeyframes(secondTransform.property("ADBE Opacity"), [[0, 100], [finalComp.duration, 100]]);
            secondLayer.motionBlur = true;

            var matte = finalComp.layers.addShape();
            matte.name = "PORTAL MATTE | Pupil expansion";
            addOutlinedEllipseGroup(matte, [900, 900], [0, 0], [1, 1, 1], null, 0, 100);
            var matteTransform = matte.property("ADBE Transform Group");
            matteTransform.property("ADBE Anchor Point").setValue([0, 0, 0]);
            matteTransform.property("ADBE Position").setValue([320, 240, 0]);
            replaceKeyframes(matteTransform.property("ADBE Scale"), [
                [0, [0, 0, 100]],
                [portalStart, [0, 0, 100]],
                [5.75, [16, 16, 100]],
                [5.95, [28, 28, 100]],
                [6.15, [45, 45, 100]],
                [6.35, [62, 62, 100]],
                [portalFull, [90, 90, 100]],
                [6.72, [115, 115, 100]],
                [finalComp.duration, [115, 115, 100]]
            ]);
            matte.motionBlur = true;
            matte.moveBefore(secondLayer);
            try { secondLayer.trackMatteType = TrackMatteType.ALPHA; } catch (ignoredPortalMatte) {}

            var baseTransform = baseScene.property("ADBE Transform Group");
            var baseOpacity = baseTransform.property("ADBE Opacity");
            var originalOpacity = baseOpacity.valueAtTime(Math.max(0, portalStart - 0.05), false);
            replaceKeyframes(baseOpacity, [
                [0, originalOpacity],
                [portalStart, originalOpacity],
                [portalFull - 0.02, originalOpacity],
                [portalFull, 0],
                [finalComp.duration, 0]
            ]);

            var scoreNames = [
                "SCORE 200000 | Black violet depth",
                "SCORE 200000 | Magenta lime aura",
                "SCORE 200000 | Gold dimensional face"
            ];
            for (var i = 0; i < scoreNames.length; i++) {
                var scoreLayer = findLayer(secondHalf, scoreNames[i]);
                var scoreTransform = scoreLayer.property("ADBE Transform Group");
                var targetScale = scoreTransform.property("ADBE Scale").valueAtTime(7.2, false);
                var portalScale = [targetScale[0] * 0.56, targetScale[1] * 0.56, 100];
                var targetOpacity = i === 1 ? 58 : 100;
                replaceKeyframes(scoreTransform.property("ADBE Scale"), [
                    [0, [0, 0, 100]],
                    [portalStart, [0, 0, 100]],
                    [5.82, [0, 0, 100]],
                    [6.08, [portalScale[0] * 0.22, portalScale[1] * 0.22, 100]],
                    [6.34, [portalScale[0] * 0.72, portalScale[1] * 0.72, 100]],
                    [6.58, [portalScale[0] * 1.12, portalScale[1] * 1.12, 100]],
                    [6.76, portalScale],
                    [finalComp.duration, portalScale]
                ]);
                replaceKeyframes(scoreTransform.property("ADBE Opacity"), [
                    [0, 0],
                    [portalStart, 0],
                    [5.82, 0],
                    [6.12, targetOpacity],
                    [finalComp.duration, targetOpacity]
                ]);
            }

            secondLayer.moveAfter(flash);
            matte.moveBefore(secondLayer);
            try { secondLayer.trackMatteType = TrackMatteType.ALPHA; } catch (ignoredPortalMatteAgain) {}
            finalComp.time = 6.20;
            finalComp.openInViewer();
            return {
                final_comp: serializeItem(finalComp),
                second_half_comp: serializeItem(secondHalf),
                portal_matte: serializeLayer(matte),
                portal_start: portalStart,
                portal_full: portalFull,
                zoom_end: zoomEnd,
                final_zoom_percent: 600,
                score_scales_from_zero: true,
                kaleidoscope_scales_from_pupil: true,
                crossfade_removed: true
            };
        });
    }

    function addJointRolledRoundedRect(layer, size, offset, roundness, fillColor, strokeColor, strokeWidth, opacity) {
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        var rect = vectors.addProperty("ADBE Vector Shape - Rect");
        rect.property("ADBE Vector Rect Size").setValue(size);
        rect.property("ADBE Vector Rect Position").setValue(offset || [0, 0]);
        try { rect.property("ADBE Vector Rect Roundness").setValue(roundness || 0); } catch (ignoredRoundness) {}
        if (fillColor) {
            var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
            fill.property("ADBE Vector Fill Color").setValue(fillColor);
            fill.property("ADBE Vector Fill Opacity").setValue(opacity === undefined ? 100 : opacity);
        }
        if (strokeColor && strokeWidth > 0) {
            var stroke = vectors.addProperty("ADBE Vector Graphic - Stroke");
            stroke.property("ADBE Vector Stroke Color").setValue(strokeColor);
            stroke.property("ADBE Vector Stroke Width").setValue(strokeWidth);
            stroke.property("ADBE Vector Stroke Opacity").setValue(opacity === undefined ? 100 : opacity);
            try { stroke.property("ADBE Vector Stroke Line Join").setValue(2); } catch (ignoredJoin) {}
        }
        return group;
    }

    function styleJointRolledText(layer, text, font, fontSize, fillColor, strokeColor, strokeWidth, position) {
        var sourceText = layer.property("ADBE Text Properties").property("ADBE Text Document");
        var document = sourceText.value;
        document.text = text;
        try { document.font = font || "Modak"; } catch (ignoredFont) {}
        document.fontSize = fontSize;
        document.tracking = -7;
        document.autoLeading = false;
        document.leading = fontSize * 0.71;
        document.applyFill = true;
        document.fillColor = fillColor;
        document.applyStroke = true;
        document.strokeColor = strokeColor;
        document.strokeWidth = strokeWidth;
        document.strokeOverFill = false;
        try { document.justification = ParagraphJustification.CENTER_JUSTIFY; } catch (ignoredJustification) {}
        sourceText.setValue(document);
        var rect = layer.sourceRectAtTime(0, false);
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Anchor Point").setValue([rect.left + rect.width / 2, rect.top + rect.height / 2, 0]);
        transform.property("ADBE Position").setValue(position);
        return rect;
    }

    function animateJointRolledTitle(layer, position, baseScale, delay) {
        var transform = layer.property("ADBE Transform Group");
        var start = 1.48 + (delay || 0);
        replaceKeyframes(transform.property("ADBE Position"), [
            [start, [position[0], position[1] + 34, 0]],
            [start + 0.20, [position[0], position[1] - 12, 0]],
            [start + 0.34, [position[0], position[1] + 7, 0]],
            [start + 0.52, [position[0], position[1], 0]],
            [2.70, [position[0] - 2, position[1] - 2, 0]],
            [3.50, [position[0] + 2, position[1] + 1, 0]],
            [4.25, [position[0] - 1, position[1] - 2, 0]],
            [5.00, [position[0], position[1], 0]]
        ]);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]],
            [start + 0.18, [baseScale * 1.24, baseScale * 0.82, 100]],
            [start + 0.32, [baseScale * 0.91, baseScale * 1.10, 100]],
            [start + 0.52, [baseScale, baseScale, 100]],
            [2.70, [baseScale * 1.012, baseScale * 0.988, 100]],
            [3.50, [baseScale * 0.99, baseScale * 1.015, 100]],
            [4.25, [baseScale * 1.013, baseScale * 0.99, 100]],
            [5.00, [baseScale, baseScale, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [
            [start, -8], [start + 0.20, 4], [start + 0.34, -2.2], [start + 0.52, 0],
            [2.70, -0.8], [3.50, 0.7], [4.25, -0.6], [5.00, 0]
        ]);
        replaceKeyframes(transform.property("ADBE Opacity"), [[start, 0], [start + 0.10, 100], [5.00, 100]]);
        layer.motionBlur = true;
    }

    function addJointRolledPuff(comp, name, front, center, start) {
        var layer = comp.layers.addShape();
        layer.name = name;
        var cream = front ? [0.95, 0.89, 0.70] : [0.24, 0.31, 0.10];
        var offsets = [
            [-92, 2, 88, 66], [-55, -45, 96, 80], [0, -58, 118, 92], [58, -42, 100, 78],
            [94, 5, 86, 68], [62, 48, 102, 74], [0, 61, 128, 82], [-64, 48, 100, 72]
        ];
        for (var i = 0; i < offsets.length; i++) {
            var color = cream;
            if (front && i % 3 === 1) color = [0.83, 0.78, 0.56];
            addShapeGroup(layer, "ellipse", [offsets[i][2], offsets[i][3]], [offsets[i][0], offsets[i][1]], color, front ? 96 : 82);
        }
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Position").setValue(center);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.13, front ? [82, 72, 100] : [94, 86, 100]],
            [start + 0.28, front ? [112, 106, 100] : [126, 118, 100]],
            [start + 0.78, front ? [172, 158, 100] : [186, 170, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[start, front ? -7 : 8], [start + 0.78, front ? 6 : -5]]);
        replaceKeyframes(transform.property("ADBE Opacity"), [
            [start, 0], [start + 0.05, front ? 100 : 76], [start + 0.30, front ? 78 : 48], [start + 0.78, 0]
        ]);
        try {
            var turbulence = layer.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
            turbulence.property(1).setValue(front ? 13 : 20);
            turbulence.property(2).setValue(front ? 58 : 72);
        } catch (ignoredPuffTurbulence) {}
        layer.motionBlur = true;
        return layer;
    }

    function addJointRolledGlint(comp, index, position, start, color) {
        var layer = comp.layers.addShape();
        layer.name = "DETAIL | Matte glint " + pad(index + 1, 2);
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        var star = vectors.addProperty("ADBE Vector Shape - Star");
        star.property("ADBE Vector Star Type").setValue(1);
        star.property("ADBE Vector Star Points").setValue(4);
        star.property("ADBE Vector Star Inner Radius").setValue(1.5);
        star.property("ADBE Vector Star Outer Radius").setValue(8 + (index % 3) * 3);
        star.property("ADBE Vector Star Rotation").setValue(45);
        var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
        fill.property("ADBE Vector Fill Color").setValue(color);
        var transform = layer.property("ADBE Transform Group");
        transform.property("ADBE Position").setValue(position);
        replaceKeyframes(transform.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.10, [128, 128, 100]],
            [start + 0.24, [72, 72, 100]], [start + 0.42, [0, 0, 100]]
        ]);
        replaceKeyframes(transform.property("ADBE Opacity"), [[start, 0], [start + 0.07, 90], [start + 0.28, 64], [start + 0.42, 0]]);
        replaceKeyframes(transform.property("ADBE Rotate Z"), [[start, -20], [start + 0.42, 24]]);
        layer.motionBlur = true;
        return layer;
    }

    function commandBuildJointRolledV2(args) {
        return withUndo("Codex: Build Joint Rolled earthy v2", function () {
            var project = requireProject();
            var compName = args.new_name || "JOINT_ROLLED_1_EARTHY";
            var oldComp = null;
            for (var p = 1; p <= project.numItems; p++) {
                if (project.item(p) instanceof CompItem && project.item(p).name === compName) {
                    oldComp = project.item(p);
                    break;
                }
            }
            if (oldComp) oldComp.remove();

            var rollItem = findProjectItem(args.roll_item);
            var finalItem = findProjectItem(args.final_item);
            if (!(rollItem instanceof FootageItem) || !(finalItem instanceof FootageItem)) {
                throw new Error("Joint Rolled requires a keyed roll sequence and a final joint still.");
            }

            var comp = project.items.addComp(compName, 640, 480, 1, 5.0, 30);
            comp.parentFolder = findOrCreateProjectFolder("02_COMPS");
            comp.motionBlur = true;
            comp.shutterAngle = 210;
            comp.shutterPhase = -105;
            comp.workAreaStart = 0;
            comp.workAreaDuration = 5.0;
            comp.bgColor = [0.18, 0.10, 0.045];

            var darkBrown = [0.105, 0.055, 0.025];
            var burntBrown = [0.31, 0.17, 0.065];
            var tobacco = [0.48, 0.29, 0.11];
            var mustard = [0.80, 0.60, 0.20];
            var olive = [0.29, 0.39, 0.105];
            var herb = [0.17, 0.29, 0.075];
            var cream = [0.95, 0.89, 0.70];
            var paper = [0.92, 0.86, 0.69];

            var base = addFullFrameLayer(comp, "BG | Burnt rolling table", darkBrown, 100);
            var table = addFullFrameLayer(comp, "BG | Tobacco paper surface", burntBrown, 100);
            var tableT = table.property("ADBE Transform Group");
            replaceKeyframes(tableT.property("ADBE Scale"), [[0, [102, 102, 100]], [2.45, [106, 106, 100]], [5.0, [103, 103, 100]]]);
            replaceKeyframes(tableT.property("ADBE Rotate Z"), [[0, -0.6], [2.45, 0.7], [5.0, -0.35]]);

            var centerMat = comp.layers.addShape();
            centerMat.name = "BG | Worn paper halo";
            addOutlinedEllipseGroup(centerMat, [570, 360], [0, 0], tobacco, darkBrown, 9, 78);
            var centerMatT = centerMat.property("ADBE Transform Group");
            centerMatT.property("ADBE Position").setValue([320, 252, 0]);
            centerMatT.property("ADBE Rotate Z").setValue(-4);
            replaceKeyframes(centerMatT.property("ADBE Scale"), [[0, [100, 100, 100]], [2.5, [105, 102, 100]], [5.0, [101, 104, 100]]]);
            try {
                var matTurbulence = centerMat.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
                matTurbulence.property(1).setValue(16);
                matTurbulence.property(2).setValue(115);
            } catch (ignoredMatTurbulence) {}

            var stripeColors = [mustard, olive, paper];
            for (var s = 0; s < 9; s++) {
                var stripe = comp.layers.addShape();
                stripe.name = "BG | Faded paper stripe " + pad(s + 1, 2);
                addJointRolledRoundedRect(stripe, [760, 18 + (s % 3) * 5], [0, 0], 8, stripeColors[s % stripeColors.length], null, 0, 100);
                var stripeT = stripe.property("ADBE Transform Group");
                stripeT.property("ADBE Position").setValue([320, 26 + s * 57, 0]);
                stripeT.property("ADBE Rotate Z").setValue(-16 + (s % 2) * 4);
                stripeT.property("ADBE Opacity").setValue(5 + (s % 3) * 3);
                replaceKeyframes(stripeT.property("ADBE Position"), [
                    [0, [315, 26 + s * 57, 0]], [2.5, [325, 22 + s * 57, 0]], [5.0, [315, 26 + s * 57, 0]]
                ]);
            }

            for (var f = 0; f < 26; f++) {
                var fleck = comp.layers.addShape();
                fleck.name = "BG | Herb crumb " + pad(f + 1, 2);
                var fleckW = 4 + (f % 4) * 2;
                var fleckH = 3 + ((f + 2) % 3) * 2;
                addOutlinedEllipseGroup(fleck, [fleckW, fleckH], [0, 0], f % 4 === 0 ? mustard : (f % 3 === 0 ? olive : herb), darkBrown, 1.0, 92);
                var fleckT = fleck.property("ADBE Transform Group");
                var fx = 28 + ((f * 83) % 584);
                var fy = 30 + ((f * 47) % 414);
                fleckT.property("ADBE Position").setValue([fx, fy, 0]);
                fleckT.property("ADBE Rotate Z").setValue((f * 37) % 180);
                replaceKeyframes(fleckT.property("ADBE Position"), [[0, [fx, fy, 0]], [2.5, [fx + (f % 2 ? 4 : -4), fy - 3, 0]], [5.0, [fx, fy, 0]]]);
            }

            var roll = comp.layers.add(rollItem);
            roll.name = "ROLL | Keyed paper and herb";
            roll.inPoint = 0;
            roll.outPoint = Math.min(1.90, comp.duration);
            var rollT = roll.property("ADBE Transform Group");
            rollT.property("ADBE Anchor Point").setValue([rollItem.width / 2, rollItem.height / 2, 0]);
            replaceKeyframes(rollT.property("ADBE Position"), [
                [0, [320, 248, 0]], [0.85, [316, 252, 0]], [1.45, [322, 246, 0]], [1.74, [320, 244, 0]]
            ]);
            replaceKeyframes(rollT.property("ADBE Scale"), [
                [0, [96, 96, 100]], [1.18, [102, 102, 100]], [1.48, [108, 108, 100]], [1.72, [119, 119, 100]]
            ]);
            replaceKeyframes(rollT.property("ADBE Rotate Z"), [[0, -1.2], [1.10, 0.7], [1.48, -1.0], [1.72, 1.8]]);
            replaceKeyframes(rollT.property("ADBE Opacity"), [[0, 100], [1.60, 100], [1.82, 0]]);
            roll.motionBlur = true;

            for (var r = 0; r < 16; r++) {
                var ray = comp.layers.addShape();
                ray.name = "IMPACT | Paper dash " + pad(r + 1, 2);
                addJointRolledRoundedRect(ray, [8 + (r % 3) * 4, 54 + (r % 4) * 11], [0, -104], 5,
                    r % 3 === 0 ? cream : (r % 3 === 1 ? mustard : olive), darkBrown, 1.5, 92);
                var rayT = ray.property("ADBE Transform Group");
                rayT.property("ADBE Position").setValue([320, 262, 0]);
                rayT.property("ADBE Rotate Z").setValue(r * 22.5 + (r % 2 ? 4 : -3));
                replaceKeyframes(rayT.property("ADBE Scale"), [[1.43, [0, 0, 100]], [1.64, [116, 116, 100]], [1.98, [164, 164, 100]]]);
                replaceKeyframes(rayT.property("ADBE Opacity"), [[1.43, 0], [1.56, 92], [1.77, 64], [1.98, 0]]);
                ray.motionBlur = true;
            }

            addJointRolledPuff(comp, "PUFF | Olive depth", false, [320, 262, 0], 1.44);
            addJointRolledPuff(comp, "PUFF | Cream cartoon cloud", true, [320, 258, 0], 1.47);

            var badgeShadow = comp.layers.addShape();
            badgeShadow.name = "TITLE | Brown paper shadow";
            addJointRolledRoundedRect(badgeShadow, [442, 166], [0, 0], 28, darkBrown, darkBrown, 7, 100);
            animateJointRolledTitle(badgeShadow, [327, 151, 0], 100, 0.03);

            var badge = comp.layers.addShape();
            badge.name = "TITLE | Olive paper patch";
            addJointRolledRoundedRect(badge, [436, 160], [0, 0], 28, olive, darkBrown, 7, 100);
            animateJointRolledTitle(badge, [320, 143, 0], 100, 0);

            var titleShadow = comp.layers.addText("JOINT\rROLLED");
            titleShadow.name = "TITLE | Ink offset";
            styleJointRolledText(titleShadow, "JOINT\rROLLED", args.font || "Modak", 94, darkBrown, darkBrown, 10, [326, 151, 0]);
            animateJointRolledTitle(titleShadow, [326, 151, 0], 100, 0.08);

            var title = comp.layers.addText("JOINT\rROLLED");
            title.name = "TITLE | Cream Modak face";
            styleJointRolledText(title, "JOINT\rROLLED", args.font || "Modak", 94, cream, darkBrown, 7, [320, 143, 0]);
            animateJointRolledTitle(title, [320, 143, 0], 100, 0.05);

            var joint = comp.layers.add(finalItem);
            joint.name = "JOINT 1 | Slash slot A";
            joint.inPoint = 1.48;
            joint.outPoint = 5.0;
            var jointT = joint.property("ADBE Transform Group");
            jointT.property("ADBE Anchor Point").setValue([finalItem.width / 2, finalItem.height / 2, 0]);
            replaceKeyframes(jointT.property("ADBE Position"), [
                [1.48, [320, 286, 0]], [1.67, [258, 338, 0]], [1.82, [282, 328, 0]], [2.02, [270, 334, 0]],
                [2.75, [268, 331, 0]], [3.50, [272, 336, 0]], [4.25, [268, 332, 0]], [5.0, [270, 334, 0]]
            ]);
            replaceKeyframes(jointT.property("ADBE Scale"), [
                [1.48, [0, 0, 100]], [1.66, [55, 55, 100]], [1.82, [39, 48, 100]], [2.02, [44, 44, 100]],
                [2.75, [45, 43, 100]], [3.50, [43, 45, 100]], [4.25, [45, 43, 100]], [5.0, [44, 44, 100]]
            ]);
            replaceKeyframes(jointT.property("ADBE Rotate Z"), [
                [1.48, -150], [1.67, -58], [1.82, -76], [2.02, -68],
                [2.75, -69.5], [3.50, -67], [4.25, -69], [5.0, -68]
            ]);
            replaceKeyframes(jointT.property("ADBE Opacity"), [[1.48, 0], [1.55, 100], [5.0, 100]]);
            joint.motionBlur = true;

            for (var h = 0; h < 20; h++) {
                var burst = comp.layers.addShape();
                burst.name = "DETAIL | Flying herb " + pad(h + 1, 2);
                var bw = 5 + (h % 4) * 2;
                var bh = 4 + ((h + 1) % 3) * 2;
                addOutlinedEllipseGroup(burst, [bw, bh], [0, 0], h % 5 === 0 ? mustard : (h % 2 === 0 ? herb : olive), darkBrown, 1.2, 100);
                var burstT = burst.property("ADBE Transform Group");
                var angle = (h * 41 + 9) * Math.PI / 180;
                var radius = 90 + (h % 6) * 24;
                var tx = 320 + Math.cos(angle) * radius;
                var ty = 268 + Math.sin(angle) * radius * 0.62;
                replaceKeyframes(burstT.property("ADBE Position"), [
                    [1.48, [320, 266, 0]], [1.76, [tx, ty, 0]], [2.35, [tx + Math.cos(angle) * 28, ty + 26, 0]]
                ]);
                replaceKeyframes(burstT.property("ADBE Scale"), [[1.48, [0, 0, 100]], [1.62, [130, 130, 100]], [2.35, [55, 55, 100]]]);
                replaceKeyframes(burstT.property("ADBE Rotate Z"), [[1.48, h * 17], [2.35, h % 2 ? 260 : -240]]);
                replaceKeyframes(burstT.property("ADBE Opacity"), [[1.48, 0], [1.58, 100], [2.05, 82], [2.35, 0]]);
                burst.motionBlur = true;
            }

            var glintPositions = [[212, 243], [304, 377], [392, 281], [445, 356], [187, 361], [420, 222]];
            for (var g = 0; g < glintPositions.length; g++) {
                addJointRolledGlint(comp, g, [glintPositions[g][0], glintPositions[g][1], 0], 2.18 + g * 0.46,
                    g % 2 === 0 ? cream : mustard);
            }

            var vignette = comp.layers.addShape();
            vignette.name = "FG | Soft ink vignette";
            addJointRolledRoundedRect(vignette, [650, 490], [0, 0], 22, null, darkBrown, 34, 55);
            vignette.property("ADBE Transform Group").property("ADBE Position").setValue([320, 240, 0]);
            vignette.property("ADBE Transform Group").property("ADBE Opacity").setValue(74);
            try {
                var vignetteBlur = vignette.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                vignetteBlur.property(1).setValue(18);
            } catch (ignoredVignetteBlur) {}

            comp.time = 2.18;
            comp.openInViewer();
            return {
                comp: compSnapshot(comp, 250),
                style: "earthy_hand_drawn_rolling_paper",
                palette: "tobacco_olive_mustard_cream",
                joint_slot: { x: 270, y: 334, rotation: -68, scale: 44 },
                future_slots: [{ x: 340, y: 334 }, { x: 410, y: 334 }]
            };
        });
    }

    function commandBuildJointRolledApproved(args) {
        return withUndo("Codex: Build approved Joint Rolled concept", function () {
            var project = requireProject();
            var compName = args.new_name || "JOINT_ROLLED_1_APPROVED";
            var oldComp = null;
            for (var p = 1; p <= project.numItems; p++) {
                if (project.item(p) instanceof CompItem && project.item(p).name === compName) {
                    oldComp = project.item(p);
                    break;
                }
            }
            if (oldComp) oldComp.remove();

            var rollItem = findProjectItem(args.roll_item);
            var backgroundItem = findProjectItem(args.background_item);
            var titleItem = findProjectItem(args.title_item);
            var jointItem = findProjectItem(args.joint_item);
            var burstItem = findProjectItem(args.burst_item);

            var comp = project.items.addComp(compName, 640, 480, 1, 5.0, 30);
            comp.parentFolder = findOrCreateProjectFolder("02_COMPS");
            comp.motionBlur = true;
            comp.shutterAngle = 220;
            comp.shutterPhase = -110;
            comp.workAreaStart = 0;
            comp.workAreaDuration = 5.0;
            comp.bgColor = [0.22, 0.12, 0.045];

            var darkBrown = [0.10, 0.052, 0.021];
            var cream = [0.95, 0.88, 0.66];
            var mustard = [0.79, 0.57, 0.16];
            var olive = [0.30, 0.39, 0.085];
            var herb = [0.16, 0.27, 0.055];

            var safety = addFullFrameLayer(comp, "BG | Dark tobacco safety", darkBrown, 100);

            var background = comp.layers.add(backgroundItem);
            background.name = "BG | Approved tobacco paper plate";
            background.outPoint = 5.0;
            var bgT = background.property("ADBE Transform Group");
            bgT.property("ADBE Anchor Point").setValue([backgroundItem.width / 2, backgroundItem.height / 2, 0]);
            var bgScale = coverScaleFor(comp, backgroundItem, 1.04);
            replaceKeyframes(bgT.property("ADBE Position"), [
                [0, [320, 240, 0]], [1.40, [316, 243, 0]], [1.62, [323, 237, 0]],
                [2.20, [320, 240, 0]], [3.60, [317, 238, 0]], [5.0, [320, 240, 0]]
            ]);
            replaceKeyframes(bgT.property("ADBE Scale"), [
                [0, [bgScale, bgScale, 100]], [1.40, [bgScale * 1.018, bgScale * 1.018, 100]],
                [1.62, [bgScale * 1.075, bgScale * 1.075, 100]], [2.10, [bgScale * 1.035, bgScale * 1.035, 100]],
                [5.0, [bgScale * 1.065, bgScale * 1.065, 100]]
            ]);
            replaceKeyframes(bgT.property("ADBE Rotate Z"), [[0, -0.25], [1.62, 0.9], [2.10, -0.2], [5.0, 0.25]]);
            background.motionBlur = true;

            for (var d = 0; d < 10; d++) {
                var dust = comp.layers.addShape();
                dust.name = "BG | Drifting herb speck " + pad(d + 1, 2);
                addOutlinedEllipseGroup(dust, [4 + d % 3 * 2, 3 + (d + 1) % 3 * 2], [0, 0], d % 3 === 0 ? mustard : herb, darkBrown, 1.0, 76);
                var dustT = dust.property("ADBE Transform Group");
                var dx = 55 + ((d * 97) % 530);
                var dy = 38 + ((d * 53) % 392);
                replaceKeyframes(dustT.property("ADBE Position"), [[0, [dx, dy, 0]], [2.5, [dx + (d % 2 ? 6 : -5), dy - 7, 0]], [5.0, [dx, dy, 0]]]);
                replaceKeyframes(dustT.property("ADBE Rotate Z"), [[0, d * 21], [5.0, d % 2 ? d * 21 + 90 : d * 21 - 80]]);
                dustT.property("ADBE Opacity").setValue(64);
            }

            var roll = comp.layers.add(rollItem);
            roll.name = "ROLL | MiniMax keyed paper";
            roll.inPoint = 0;
            roll.outPoint = 1.86;
            var rollT = roll.property("ADBE Transform Group");
            rollT.property("ADBE Anchor Point").setValue([rollItem.width / 2, rollItem.height / 2, 0]);
            replaceKeyframes(rollT.property("ADBE Position"), [
                [0, [320, 246, 0]], [0.72, [316, 250, 0]], [1.20, [322, 245, 0]],
                [1.42, [320, 252, 0]], [1.58, [320, 242, 0]]
            ]);
            replaceKeyframes(rollT.property("ADBE Scale"), [
                [0, [98, 98, 100]], [1.18, [103, 103, 100]], [1.40, [94, 88, 100]],
                [1.56, [116, 124, 100]], [1.78, [128, 128, 100]]
            ]);
            replaceKeyframes(rollT.property("ADBE Rotate Z"), [[0, -0.7], [1.18, 0.8], [1.42, -1.6], [1.58, 2.8]]);
            replaceKeyframes(rollT.property("ADBE Opacity"), [[0, 100], [1.58, 100], [1.78, 0]]);
            roll.motionBlur = true;

            var burstHold = comp.layers.add(burstItem);
            burstHold.name = "BURST | Approved paper smoke hold";
            burstHold.inPoint = 1.38;
            burstHold.outPoint = 5.0;
            var burstHoldT = burstHold.property("ADBE Transform Group");
            burstHoldT.property("ADBE Anchor Point").setValue([burstItem.width / 2, burstItem.height / 2, 0]);
            replaceKeyframes(burstHoldT.property("ADBE Position"), [
                [1.38, [320, 268, 0]], [1.64, [320, 252, 0]], [2.05, [320, 258, 0]],
                [3.20, [316, 255, 0]], [4.10, [323, 260, 0]], [5.0, [320, 258, 0]]
            ]);
            replaceKeyframes(burstHoldT.property("ADBE Scale"), [
                [1.38, [0, 0, 100]], [1.59, [53, 47, 100]], [1.76, [43, 47, 100]],
                [1.98, [46, 46, 100]], [3.20, [47, 46, 100]], [4.10, [46, 47, 100]], [5.0, [46, 46, 100]]
            ]);
            replaceKeyframes(burstHoldT.property("ADBE Rotate Z"), [[1.38, -13], [1.62, 4], [1.82, -2], [2.05, 0], [5.0, 1.2]]);
            replaceKeyframes(burstHoldT.property("ADBE Opacity"), [[1.38, 0], [1.49, 100], [2.15, 100], [3.0, 86], [5.0, 82]]);
            burstHold.motionBlur = true;

            var burstPunch = comp.layers.add(burstItem);
            burstPunch.name = "BURST | Fast paper impact echo";
            burstPunch.inPoint = 1.38;
            burstPunch.outPoint = 2.24;
            var burstPunchT = burstPunch.property("ADBE Transform Group");
            burstPunchT.property("ADBE Anchor Point").setValue([burstItem.width / 2, burstItem.height / 2, 0]);
            burstPunchT.property("ADBE Position").setValue([320, 260, 0]);
            replaceKeyframes(burstPunchT.property("ADBE Scale"), [[1.38, [0, 0, 100]], [1.57, [42, 42, 100]], [1.84, [62, 62, 100]], [2.22, [78, 78, 100]]]);
            replaceKeyframes(burstPunchT.property("ADBE Rotate Z"), [[1.38, 16], [2.22, -9]]);
            replaceKeyframes(burstPunchT.property("ADBE Opacity"), [[1.38, 0], [1.50, 74], [1.76, 40], [2.22, 0]]);
            burstPunch.motionBlur = true;

            var titleShadow = comp.layers.add(titleItem);
            titleShadow.name = "TITLE | Approved offset print shadow";
            titleShadow.inPoint = 1.42;
            titleShadow.outPoint = 5.0;
            var titleShadowT = titleShadow.property("ADBE Transform Group");
            titleShadowT.property("ADBE Anchor Point").setValue([titleItem.width / 2, titleItem.height / 2, 0]);
            replaceKeyframes(titleShadowT.property("ADBE Position"), [[1.42, [327, 165, 0]], [1.68, [327, 133, 0]], [1.90, [327, 145, 0]], [2.10, [327, 141, 0]], [5.0, [327, 141, 0]]]);
            replaceKeyframes(titleShadowT.property("ADBE Scale"), [[1.42, [0, 0, 100]], [1.64, [37, 30, 100]], [1.82, [28, 35, 100]], [2.04, [31.4, 31.4, 100]], [5.0, [31.4, 31.4, 100]]]);
            replaceKeyframes(titleShadowT.property("ADBE Rotate Z"), [[1.42, -12], [1.68, 4], [1.88, -2], [2.08, 0], [5.0, 0]]);
            replaceKeyframes(titleShadowT.property("ADBE Opacity"), [[1.42, 0], [1.50, 46], [5.0, 46]]);
            titleShadow.blendingMode = BlendingMode.MULTIPLY;
            titleShadow.motionBlur = true;

            var title = comp.layers.add(titleItem);
            title.name = "TITLE | Approved paper cutout";
            title.inPoint = 1.42;
            title.outPoint = 5.0;
            var titleT = title.property("ADBE Transform Group");
            titleT.property("ADBE Anchor Point").setValue([titleItem.width / 2, titleItem.height / 2, 0]);
            replaceKeyframes(titleT.property("ADBE Position"), [
                [1.42, [320, 156, 0]], [1.66, [320, 121, 0]], [1.83, [320, 139, 0]], [2.05, [320, 133, 0]],
                [2.70, [318, 131, 0]], [3.40, [322, 135, 0]], [4.18, [318, 132, 0]], [5.0, [320, 133, 0]]
            ]);
            replaceKeyframes(titleT.property("ADBE Scale"), [
                [1.42, [0, 0, 100]], [1.62, [39, 31, 100]], [1.80, [28, 35, 100]], [2.02, [31, 31, 100]],
                [2.70, [31.6, 30.6, 100]], [3.40, [30.6, 31.5, 100]], [4.18, [31.5, 30.7, 100]], [5.0, [31, 31, 100]]
            ]);
            replaceKeyframes(titleT.property("ADBE Rotate Z"), [[1.42, -12], [1.66, 4], [1.83, -2], [2.05, 0], [2.70, -0.8], [3.40, 0.7], [4.18, -0.6], [5.0, 0]]);
            replaceKeyframes(titleT.property("ADBE Opacity"), [[1.42, 0], [1.50, 100], [5.0, 100]]);
            title.motionBlur = true;

            var jointShadow = comp.layers.add(jointItem);
            jointShadow.name = "JOINT 1 | Ink offset shadow";
            jointShadow.inPoint = 1.46;
            jointShadow.outPoint = 5.0;
            var jointShadowT = jointShadow.property("ADBE Transform Group");
            jointShadowT.property("ADBE Anchor Point").setValue([jointItem.width / 2, jointItem.height / 2, 0]);
            replaceKeyframes(jointShadowT.property("ADBE Position"), [[1.46, [326, 290, 0]], [1.70, [218, 356, 0]], [1.86, [239, 336, 0]], [2.08, [226, 347, 0]], [5.0, [226, 347, 0]]]);
            replaceKeyframes(jointShadowT.property("ADBE Scale"), [[1.46, [0, 0, 100]], [1.68, [23, 23, 100]], [1.84, [15, 20, 100]], [2.06, [18.3, 18.3, 100]], [5.0, [18.3, 18.3, 100]]]);
            replaceKeyframes(jointShadowT.property("ADBE Rotate Z"), [[1.46, -105], [1.70, 8], [1.86, -5], [2.08, 0], [5.0, 0]]);
            replaceKeyframes(jointShadowT.property("ADBE Opacity"), [[1.46, 0], [1.53, 42], [5.0, 42]]);
            jointShadow.blendingMode = BlendingMode.MULTIPLY;
            try {
                var jointShadowBlur = jointShadow.property("ADBE Effect Parade").addProperty("ADBE Box Blur2");
                if (jointShadowBlur.property(1)) jointShadowBlur.property(1).setValue(14);
                if (jointShadowBlur.property(2)) jointShadowBlur.property(2).setValue(3);
            } catch (ignoredJointShadowFastBlur) {
                try {
                    var jointShadowGaussian = jointShadow.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    if (jointShadowGaussian.property(1)) jointShadowGaussian.property(1).setValue(14);
                    if (jointShadowGaussian.property(3)) jointShadowGaussian.property(3).setValue(1);
                } catch (ignoredJointShadowGaussian) {}
            }
            jointShadow.motionBlur = true;

            var joint = comp.layers.add(jointItem);
            joint.name = "JOINT 1 | Approved slash slot A";
            joint.inPoint = 1.46;
            joint.outPoint = 5.0;
            var jointT = joint.property("ADBE Transform Group");
            jointT.property("ADBE Anchor Point").setValue([jointItem.width / 2, jointItem.height / 2, 0]);
            replaceKeyframes(jointT.property("ADBE Position"), [
                [1.46, [320, 282, 0]], [1.68, [210, 348, 0]], [1.84, [231, 328, 0]], [2.06, [218, 339, 0]],
                [2.78, [216, 336, 0]], [3.45, [221, 341, 0]], [4.20, [216, 337, 0]], [5.0, [218, 339, 0]]
            ]);
            replaceKeyframes(jointT.property("ADBE Scale"), [
                [1.46, [0, 0, 100]], [1.66, [22, 22, 100]], [1.82, [14, 20, 100]], [2.04, [18, 18, 100]],
                [2.78, [18.4, 17.6, 100]], [3.45, [17.6, 18.4, 100]], [4.20, [18.3, 17.7, 100]], [5.0, [18, 18, 100]]
            ]);
            replaceKeyframes(jointT.property("ADBE Rotate Z"), [[1.46, -105], [1.68, 8], [1.84, -5], [2.06, 0], [2.78, -1.6], [3.45, 1.4], [4.20, -1.2], [5.0, 0]]);
            replaceKeyframes(jointT.property("ADBE Opacity"), [[1.46, 0], [1.53, 100], [5.0, 100]]);
            joint.motionBlur = true;

            for (var h = 0; h < 14; h++) {
                var particle = comp.layers.addShape();
                particle.name = "IMPACT | Loose herb crumb " + pad(h + 1, 2);
                var pw = 5 + (h % 4) * 2;
                var ph = 4 + ((h + 2) % 3) * 2;
                addOutlinedEllipseGroup(particle, [pw, ph], [0, 0], h % 4 === 0 ? mustard : (h % 2 === 0 ? olive : herb), darkBrown, 1.1, 100);
                var particleT = particle.property("ADBE Transform Group");
                var angle = (h * 51 + 17) * Math.PI / 180;
                var radius = 105 + (h % 5) * 27;
                var px = 320 + Math.cos(angle) * radius;
                var py = 262 + Math.sin(angle) * radius * 0.68;
                replaceKeyframes(particleT.property("ADBE Position"), [[1.46, [320, 260, 0]], [1.78, [px, py, 0]], [2.48, [px + Math.cos(angle) * 24, py + 34, 0]]]);
                replaceKeyframes(particleT.property("ADBE Scale"), [[1.46, [0, 0, 100]], [1.60, [135, 135, 100]], [2.48, [55, 55, 100]]]);
                replaceKeyframes(particleT.property("ADBE Rotate Z"), [[1.46, h * 19], [2.48, h % 2 ? 270 : -250]]);
                replaceKeyframes(particleT.property("ADBE Opacity"), [[1.46, 0], [1.55, 100], [2.12, 82], [2.48, 0]]);
                particle.motionBlur = true;
            }

            var flash = addFullFrameLayer(comp, "IMPACT | Warm paper flash", cream, 0);
            replaceKeyframes(flash.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [1.46, 0], [1.52, 22], [1.64, 0], [5.0, 0]]);

            var glints = [[150, 236], [291, 390], [420, 265], [506, 345], [255, 248], [468, 205]];
            for (var g = 0; g < glints.length; g++) {
                addJointRolledGlint(comp, g, [glints[g][0], glints[g][1], 0], 2.12 + g * 0.47, g % 2 === 0 ? cream : mustard);
            }

            comp.time = 2.20;
            comp.openInViewer();
            return {
                comp: compSnapshot(comp, 220),
                style: "approved_earthy_paper_cutout",
                joint_slots: [{ x: 218, y: 339 }, { x: 340, y: 339 }, { x: 462, y: 339 }],
                title_scale: 31,
                joint_scale: 18
            };
        });
    }

    function addApprovedJointCountPuff(comp, slotX, start, ordinal) {
        var darkBrown = [0.10, 0.052, 0.021];
        var olive = [0.30, 0.39, 0.085];
        var cream = [0.95, 0.88, 0.66];
        var paperShade = [0.78, 0.70, 0.49];
        var cloud = [
            [-35, 2, 46, 31], [-19, -23, 46, 38], [8, -29, 54, 43],
            [35, -7, 42, 33], [25, 23, 49, 35], [-11, 28, 52, 37]
        ];

        var back = comp.layers.addShape();
        back.name = "COUNT " + ordinal + " | Olive puff depth";
        for (var b = 0; b < cloud.length; b++) {
            addOutlinedEllipseGroup(back, [cloud[b][2], cloud[b][3]], [cloud[b][0], cloud[b][1]], olive, darkBrown, 2.0, 88);
        }
        var backT = back.property("ADBE Transform Group");
        backT.property("ADBE Position").setValue([slotX, 342, 0]);
        replaceKeyframes(backT.property("ADBE Scale"), [
            [start, [0, 0, 100]], [start + 0.10, [72, 62, 100]],
            [start + 0.27, [116, 104, 100]], [start + 0.62, [158, 142, 100]]
        ]);
        replaceKeyframes(backT.property("ADBE Rotate Z"), [[start, 8], [start + 0.62, -5]]);
        replaceKeyframes(backT.property("ADBE Opacity"), [[start, 0], [start + 0.05, 76], [start + 0.28, 45], [start + 0.62, 0]]);
        back.motionBlur = true;

        var front = comp.layers.addShape();
        front.name = "COUNT " + ordinal + " | Cream paper puff";
        for (var f = 0; f < cloud.length; f++) {
            var frontColor = f % 3 === 1 ? paperShade : cream;
            addOutlinedEllipseGroup(front, [cloud[f][2] * 0.82, cloud[f][3] * 0.82], [cloud[f][0] * 0.78, cloud[f][1] * 0.78], frontColor, darkBrown, 1.8, 96);
        }
        var frontT = front.property("ADBE Transform Group");
        frontT.property("ADBE Position").setValue([slotX, 340, 0]);
        replaceKeyframes(frontT.property("ADBE Scale"), [
            [start + 0.02, [0, 0, 100]], [start + 0.11, [64, 56, 100]],
            [start + 0.25, [102, 94, 100]], [start + 0.58, [142, 128, 100]]
        ]);
        replaceKeyframes(frontT.property("ADBE Rotate Z"), [[start + 0.02, -7], [start + 0.58, 6]]);
        replaceKeyframes(frontT.property("ADBE Opacity"), [[start + 0.02, 0], [start + 0.07, 100], [start + 0.27, 62], [start + 0.58, 0]]);
        try {
            var puffTurbulence = front.property("ADBE Effect Parade").addProperty("ADBE Turbulent Displace");
            if (puffTurbulence.property(1)) puffTurbulence.property(1).setValue(9);
            if (puffTurbulence.property(2)) puffTurbulence.property(2).setValue(46);
        } catch (ignoredCountPuffTurbulence) {}
        front.motionBlur = true;

        var sparkle = addJointRolledGlint(comp, 20 + ordinal, [slotX - 12, 282, 0], start + 0.31, cream);
        sparkle.name = "COUNT " + ordinal + " | Matte arrival glint";
        return { back: back, front: front, sparkle: sparkle };
    }

    function addApprovedJointToSlot(comp, slotX, delay, ordinal) {
        var sourceJoint = findLayer(comp, "JOINT 1 | Approved slash slot A");
        var sourceShadow = findLayer(comp, "JOINT 1 | Ink offset shadow");
        var puff = addApprovedJointCountPuff(comp, slotX, 1.50 + delay, ordinal);

        var shadow = sourceShadow.duplicate();
        shadow.name = "JOINT " + ordinal + " | Blurred ink shadow";
        shadow.inPoint = 1.46 + delay;
        shadow.outPoint = 5.0;
        var shadowT = shadow.property("ADBE Transform Group");
        replaceKeyframes(shadowT.property("ADBE Position"), [
            [1.46 + delay, [326, 290, 0]], [1.70 + delay, [slotX, 356, 0]],
            [1.86 + delay, [slotX + 21, 336, 0]], [2.08 + delay, [slotX + 8, 347, 0]], [5.0, [slotX + 8, 347, 0]]
        ]);
        replaceKeyframes(shadowT.property("ADBE Scale"), [
            [1.46 + delay, [0, 0, 100]], [1.68 + delay, [23, 23, 100]],
            [1.84 + delay, [15, 20, 100]], [2.06 + delay, [18.3, 18.3, 100]], [5.0, [18.3, 18.3, 100]]
        ]);
        replaceKeyframes(shadowT.property("ADBE Rotate Z"), [[1.46 + delay, -105], [1.70 + delay, 8], [1.86 + delay, -5], [2.08 + delay, 0], [5.0, 0]]);
        replaceKeyframes(shadowT.property("ADBE Opacity"), [[1.46 + delay, 0], [1.53 + delay, 42], [5.0, 42]]);

        var joint = sourceJoint.duplicate();
        joint.name = "JOINT " + ordinal + " | Approved slash slot " + (ordinal === 2 ? "B" : "C");
        joint.inPoint = 1.46 + delay;
        joint.outPoint = 5.0;
        var jointT = joint.property("ADBE Transform Group");
        replaceKeyframes(jointT.property("ADBE Position"), [
            [1.46 + delay, [320, 282, 0]], [1.68 + delay, [slotX - 8, 348, 0]],
            [1.84 + delay, [slotX + 13, 328, 0]], [2.06 + delay, [slotX, 339, 0]],
            [2.78 + delay, [slotX - 2, 336, 0]], [3.45 + delay, [slotX + 3, 341, 0]],
            [4.20 + delay, [slotX - 2, 337, 0]], [5.0, [slotX, 339, 0]]
        ]);
        replaceKeyframes(jointT.property("ADBE Scale"), [
            [1.46 + delay, [0, 0, 100]], [1.66 + delay, [22, 22, 100]],
            [1.82 + delay, [14, 20, 100]], [2.04 + delay, [18, 18, 100]],
            [2.78 + delay, [18.4, 17.6, 100]], [3.45 + delay, [17.6, 18.4, 100]],
            [4.20 + delay, [18.3, 17.7, 100]], [5.0, [18, 18, 100]]
        ]);
        replaceKeyframes(jointT.property("ADBE Rotate Z"), [[1.46 + delay, -105], [1.68 + delay, 8], [1.84 + delay, -5], [2.06 + delay, 0], [2.78 + delay, -1.6], [3.45 + delay, 1.4], [4.20 + delay, -1.2], [5.0, 0]]);
        replaceKeyframes(jointT.property("ADBE Opacity"), [[1.46 + delay, 0], [1.53 + delay, 100], [5.0, 100]]);

        joint.moveToBeginning();
        shadow.moveAfter(joint);
        puff.front.moveAfter(shadow);
        puff.sparkle.moveAfter(puff.front);
        puff.back.moveAfter(puff.sparkle);
        return { joint: joint, shadow: shadow, puff: puff };
    }

    function commandBuildJointRolledCountVariants(args) {
        return withUndo("Codex: Build Joint Rolled count variants", function () {
            var project = requireProject();
            var source = findComp(args.source_comp || "JOINT_ROLLED_1_APPROVED");
            var counts = args.counts || [2, 3];
            var output = [];
            for (var c = 0; c < counts.length; c++) {
                var count = Number(counts[c]);
                if (count !== 2 && count !== 3) throw new Error("Joint Rolled count must be 2 or 3.");
                var targetName = "JOINT_ROLLED_" + count + "_APPROVED";
                var oldComp = null;
                for (var p = 1; p <= project.numItems; p++) {
                    if (project.item(p) instanceof CompItem && project.item(p).name === targetName) {
                        oldComp = project.item(p);
                        break;
                    }
                }
                if (oldComp) oldComp.remove();

                var copy = source.duplicate();
                copy.name = targetName;
                copy.parentFolder = findOrCreateProjectFolder("02_COMPS");
                addApprovedJointToSlot(copy, 340, 0.09, 2);
                if (count === 3) {
                    addApprovedJointToSlot(copy, 462, 0.18, 3);
                    try {
                        copy.markerProperty.setValueAtTime(3.20, new MarkerValue("LOVE PACK PUFF EXTENSION POINT"));
                    } catch (ignoredLovePackMarker) {}
                }
                copy.time = 2.42;
                output.push({
                    id: copy.id,
                    name: copy.name,
                    width: copy.width,
                    height: copy.height,
                    duration: copy.duration,
                    fps: copy.frameRate,
                    layers: copy.numLayers,
                    count: count,
                    love_pack_extension_marker: count === 3 ? 3.20 : null
                });
            }
            if (output.length) findComp(output[output.length - 1].id).openInViewer();
            return { source: serializeItem(source), variants: output };
        });
    }

    function addLovePackBeer(comp, beerItem, index) {
        var targets = [[218, 292, 0], [340, 286, 0], [462, 292, 0]];
        var starts = [[-90, 176, 0], [320, -130, 0], [730, 176, 0]];
        var startRotations = [-54, 18, 58];
        var finalRotations = [-12, 0, 12];
        var delay = index * 0.09;
        var target = targets[index];
        var gatherX = 304 + index * 16;

        var shadow = comp.layers.add(beerItem);
        shadow.name = "LOVE PACK | Beer " + (index + 1) + " blurred shadow";
        shadow.inPoint = 3.0 + delay;
        shadow.outPoint = 4.70;
        var shadowT = shadow.property("ADBE Transform Group");
        shadowT.property("ADBE Anchor Point").setValue([beerItem.width / 2, beerItem.height / 2, 0]);
        replaceKeyframes(shadowT.property("ADBE Position"), [
            [3.00 + delay, [starts[index][0] + 7, starts[index][1] + 9, 0]],
            [3.31 + delay, [target[0] + (index === 1 ? 0 : (index === 0 ? 13 : -13)) + 7, target[1] - 13 + 9, 0]],
            [3.59 + delay, [target[0] + 7, target[1] + 9, 0]], [4.08, [target[0] + 7, target[1] + 9, 0]],
            [4.46, [gatherX + 7, 297, 0]], [4.62, [327, 295, 0]]
        ]);
        replaceKeyframes(shadowT.property("ADBE Scale"), [
            [3.00 + delay, [0, 0, 100]], [3.23 + delay, [15.0, 15.0, 100]],
            [3.43 + delay, [10.4, 13.0, 100]], [3.62 + delay, [12.1, 12.1, 100]],
            [4.08, [12.1, 12.1, 100]], [4.46, [7.5, 7.5, 100]], [4.62, [0, 0, 100]]
        ]);
        replaceKeyframes(shadowT.property("ADBE Rotate Z"), [[3.00 + delay, startRotations[index]], [3.48 + delay, finalRotations[index] + 7], [3.68 + delay, finalRotations[index]], [4.08, finalRotations[index]], [4.62, index === 1 ? 180 : (index === 0 ? -210 : 210)]]);
        replaceKeyframes(shadowT.property("ADBE Opacity"), [[3.00 + delay, 0], [3.11 + delay, 34], [4.42, 34], [4.62, 0]]);
        shadow.blendingMode = BlendingMode.MULTIPLY;
        try {
            var beerShadowBlur = shadow.property("ADBE Effect Parade").addProperty("ADBE Box Blur2");
            if (beerShadowBlur.property(1)) beerShadowBlur.property(1).setValue(14);
            if (beerShadowBlur.property(2)) beerShadowBlur.property(2).setValue(3);
        } catch (ignoredBeerShadowBlur) {}
        shadow.motionBlur = true;

        var beer = comp.layers.add(beerItem);
        beer.name = "LOVE PACK | Beer " + (index + 1) + " hero";
        beer.inPoint = 3.0 + delay;
        beer.outPoint = 4.70;
        var beerT = beer.property("ADBE Transform Group");
        beerT.property("ADBE Anchor Point").setValue([beerItem.width / 2, beerItem.height / 2, 0]);
        replaceKeyframes(beerT.property("ADBE Position"), [
            [3.00 + delay, starts[index]],
            [3.31 + delay, [target[0] + (index === 1 ? 0 : (index === 0 ? 13 : -13)), target[1] - 13, 0]],
            [3.59 + delay, target], [4.08, target], [4.46, [gatherX, 288, 0]], [4.62, [320, 286, 0]]
        ]);
        replaceKeyframes(beerT.property("ADBE Scale"), [
            [3.00 + delay, [0, 0, 100]], [3.23 + delay, [15.0, 15.0, 100]],
            [3.43 + delay, [10.4, 13.0, 100]], [3.62 + delay, [12.1, 12.1, 100]],
            [4.08, [12.1, 12.1, 100]], [4.46, [7.5, 7.5, 100]], [4.62, [0, 0, 100]]
        ]);
        replaceKeyframes(beerT.property("ADBE Rotate Z"), [[3.00 + delay, startRotations[index]], [3.48 + delay, finalRotations[index] + 7], [3.68 + delay, finalRotations[index]], [4.08, finalRotations[index]], [4.62, index === 1 ? 180 : (index === 0 ? -210 : 210)]]);
        replaceKeyframes(beerT.property("ADBE Opacity"), [[3.00 + delay, 0], [3.08 + delay, 100], [4.54, 100], [4.64, 0]]);
        beer.motionBlur = true;
        return { beer: beer, shadow: shadow };
    }

    function addLovePackHeart(comp, index, position, start, color) {
        var darkBrown = [0.10, 0.052, 0.021];
        var layer = comp.layers.addShape();
        layer.name = "LOVE PACK | Floating heart " + pad(index + 1, 2);
        var group = layer.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
        var vectors = group.property("ADBE Vectors Group");
        var path = vectors.addProperty("ADBE Vector Shape - Group");
        var heart = new Shape();
        heart.vertices = [[0, 17], [-18, 0], [-18, -9], [-11, -17], [0, -9], [11, -17], [18, -9], [18, 0]];
        heart.inTangents = [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]];
        heart.outTangents = [[0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0], [0, 0]];
        heart.closed = true;
        path.property("ADBE Vector Shape").setValue(heart);
        var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
        fill.property("ADBE Vector Fill Color").setValue(color);
        var stroke = vectors.addProperty("ADBE Vector Graphic - Stroke");
        stroke.property("ADBE Vector Stroke Color").setValue(darkBrown);
        stroke.property("ADBE Vector Stroke Width").setValue(2.2);
        var t = layer.property("ADBE Transform Group");
        replaceKeyframes(t.property("ADBE Position"), [[start, [position[0], position[1] + 12, 0]], [start + 0.32, position], [start + 1.15, [position[0] + (index % 2 ? 8 : -8), position[1] - 10, 0]], [7.0, position]]);
        replaceKeyframes(t.property("ADBE Scale"), [[start, [0, 0, 100]], [start + 0.16, [128, 128, 100]], [start + 0.34, [82, 82, 100]], [6.2, [94, 94, 100]], [7.0, [82, 82, 100]]]);
        replaceKeyframes(t.property("ADBE Rotate Z"), [[start, -22], [start + 0.34, 9], [7.0, index % 2 ? 18 : -14]]);
        replaceKeyframes(t.property("ADBE Opacity"), [[start, 0], [start + 0.08, 92], [6.65, 82], [7.0, 70]]);
        layer.motionBlur = true;
        return layer;
    }

    function commandBuildLovePackFinale(args) {
        return withUndo("Codex: Build Love Pack finale", function () {
            var project = requireProject();
            var source = findComp(args.source_comp || "JOINT_ROLLED_3_APPROVED");
            var beerItem = findProjectItem(args.beer_item);
            var giftItem = findProjectItem(args.gift_item);
            var targetName = args.new_name || "LOVE_PACK_APPROVED";
            var oldComp = null;
            for (var p = 1; p <= project.numItems; p++) {
                if (project.item(p) instanceof CompItem && project.item(p).name === targetName) {
                    oldComp = project.item(p);
                    break;
                }
            }
            if (oldComp) oldComp.remove();

            var originalDuration = source.duration;
            var comp = source.duplicate();
            comp.name = targetName;
            comp.parentFolder = findOrCreateProjectFolder("02_COMPS");
            comp.duration = 7.0;
            comp.workAreaStart = 0;
            comp.workAreaDuration = 7.0;
            for (var i = 1; i <= comp.numLayers; i++) {
                var baseLayer = comp.layer(i);
                if (baseLayer.outPoint >= originalDuration - comp.frameDuration * 1.5) baseLayer.outPoint = 7.0;
            }

            var dustyPink = [0.62, 0.17, 0.30];
            var deepRose = [0.34, 0.055, 0.14];
            var cream = [0.96, 0.86, 0.64];
            var mustard = [0.80, 0.58, 0.17];
            var darkBrown = [0.10, 0.052, 0.021];
            var baseBackground = findLayer(comp, "BG | Approved tobacco paper plate");

            var pink = addFullFrameLayer(comp, "LOVE PACK BG | Dusty pink takeover", dustyPink, 0);
            pink.outPoint = 7.0;
            replaceKeyframes(pink.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [4.30, 0], [4.72, 100], [7.0, 100]]);
            pink.moveBefore(baseBackground);

            var halo = comp.layers.addShape();
            halo.name = "LOVE PACK BG | Cream paper halo";
            addOutlinedEllipseGroup(halo, [580, 410], [0, 0], cream, darkBrown, 7, 100);
            var haloT = halo.property("ADBE Transform Group");
            haloT.property("ADBE Position").setValue([320, 268, 0]);
            replaceKeyframes(haloT.property("ADBE Scale"), [[4.35, [0, 0, 100]], [4.82, [106, 92, 100]], [5.18, [96, 100, 100]], [7.0, [101, 98, 100]]]);
            replaceKeyframes(haloT.property("ADBE Rotate Z"), [[4.35, -8], [5.18, 3], [7.0, -2]]);
            replaceKeyframes(haloT.property("ADBE Opacity"), [[4.35, 0], [4.68, 52], [7.0, 42]]);
            halo.moveBefore(pink);

            var rayColors = [deepRose, cream, mustard];
            for (var r = 0; r < 12; r++) {
                var ray = comp.layers.addShape();
                ray.name = "LOVE PACK BG | Retro ray " + pad(r + 1, 2);
                addJointRolledRoundedRect(ray, [34 + (r % 3) * 8, 380], [0, -236], 18, rayColors[r % rayColors.length], null, 0, 100);
                var rayT = ray.property("ADBE Transform Group");
                rayT.property("ADBE Position").setValue([320, 276, 0]);
                rayT.property("ADBE Rotate Z").setValue(r * 30);
                replaceKeyframes(rayT.property("ADBE Scale"), [[4.38, [0, 0, 100]], [4.80, [100, 100, 100]], [7.0, [108, 103, 100]]]);
                replaceKeyframes(rayT.property("ADBE Rotate Z"), [[4.38, r * 30 - 7], [7.0, r * 30 + 7]]);
                replaceKeyframes(rayT.property("ADBE Opacity"), [[4.38, 0], [4.76, r % 3 === 1 ? 12 : 20], [7.0, r % 3 === 1 ? 9 : 16]]);
                ray.moveBefore(halo);
            }

            var title = findLayer(comp, "TITLE | Approved paper cutout");
            var titleShadow = findLayer(comp, "TITLE | Approved offset print shadow");
            setKeyframes(title.property("ADBE Transform Group").property("ADBE Position"), [[4.20, [320, 133, 0]], [4.50, [320, 226, 0]], [4.64, [320, 270, 0]], [5.0, [320, 270, 0]], [7.0, [320, 270, 0]]]);
            setKeyframes(title.property("ADBE Transform Group").property("ADBE Scale"), [[4.20, [31, 31, 100]], [4.50, [18, 18, 100]], [4.64, [0, 0, 100]], [5.0, [0, 0, 100]], [7.0, [0, 0, 100]]]);
            setKeyframes(title.property("ADBE Transform Group").property("ADBE Rotate Z"), [[4.20, 0], [4.64, 165], [5.0, 165], [7.0, 165]]);
            setKeyframes(title.property("ADBE Transform Group").property("ADBE Opacity"), [[4.20, 100], [4.55, 100], [4.66, 0], [5.0, 0], [7.0, 0]]);
            setKeyframes(titleShadow.property("ADBE Transform Group").property("ADBE Position"), [[4.20, [327, 141, 0]], [4.50, [327, 234, 0]], [4.64, [327, 278, 0]], [5.0, [327, 278, 0]], [7.0, [327, 278, 0]]]);
            setKeyframes(titleShadow.property("ADBE Transform Group").property("ADBE Scale"), [[4.20, [31.4, 31.4, 100]], [4.50, [18.4, 18.4, 100]], [4.64, [0, 0, 100]], [5.0, [0, 0, 100]], [7.0, [0, 0, 100]]]);
            setKeyframes(titleShadow.property("ADBE Transform Group").property("ADBE Opacity"), [[4.20, 46], [4.54, 40], [4.66, 0], [5.0, 0], [7.0, 0]]);

            var approvedBurst = findLayer(comp, "BURST | Approved paper smoke hold");
            setKeyframes(approvedBurst.property("ADBE Transform Group").property("ADBE Opacity"), [[4.08, 82], [4.46, 48], [4.68, 0], [5.0, 0], [7.0, 0]]);

            var jointNames = ["JOINT 1 | Approved slash slot A", "JOINT 2 | Approved slash slot B", "JOINT 3 | Approved slash slot C"];
            var shadowNames = ["JOINT 1 | Ink offset shadow", "JOINT 2 | Blurred ink shadow", "JOINT 3 | Blurred ink shadow"];
            var jointSlots = [218, 340, 462];
            for (var j = 0; j < 3; j++) {
                var joint = findLayer(comp, jointNames[j]);
                var jt = joint.property("ADBE Transform Group");
                setKeyframes(jt.property("ADBE Position"), [[4.08, [jointSlots[j], 339, 0]], [4.46, [304 + j * 16, 302, 0]], [4.62, [320, 292, 0]], [5.0, [320, 292, 0]], [7.0, [320, 292, 0]]]);
                var jointScale = jt.property("ADBE Scale");
                for (var sk = jointScale.numKeys; sk >= 1; sk--) {
                    if (jointScale.keyTime(sk) >= 3.0) jointScale.removeKey(sk);
                }
                setKeyframes(jointScale, [[3.00, [18, 18, 100]], [3.20, [6.4, 6.4, 100]], [3.38, [8.2, 8.2, 100]], [3.58, [7.5, 7.5, 100]], [4.08, [7.5, 7.5, 100]], [4.46, [4.2, 4.2, 100]], [4.62, [0, 0, 100]], [5.0, [0, 0, 100]], [7.0, [0, 0, 100]]]);
                setKeyframes(jt.property("ADBE Rotate Z"), [[4.08, 0], [4.62, j === 1 ? 180 : (j === 0 ? -220 : 220)], [5.0, j === 1 ? 180 : (j === 0 ? -220 : 220)], [7.0, j === 1 ? 180 : (j === 0 ? -220 : 220)]]);
                setKeyframes(jt.property("ADBE Opacity"), [[4.08, 100], [4.54, 100], [4.64, 0], [5.0, 0], [7.0, 0]]);
                var jointShadow = findLayer(comp, shadowNames[j]);
                var jointShadowScale = jointShadow.property("ADBE Transform Group").property("ADBE Scale");
                for (var ssk = jointShadowScale.numKeys; ssk >= 1; ssk--) {
                    if (jointShadowScale.keyTime(ssk) >= 3.0) jointShadowScale.removeKey(ssk);
                }
                setKeyframes(jointShadowScale, [[3.00, [18.3, 18.3, 100]], [3.20, [6.6, 6.6, 100]], [3.38, [8.4, 8.4, 100]], [3.58, [7.7, 7.7, 100]], [4.08, [7.7, 7.7, 100]], [4.42, [4.4, 4.4, 100]], [4.60, [0, 0, 100]], [5.0, [0, 0, 100]], [7.0, [0, 0, 100]]]);
                setKeyframes(jointShadow.property("ADBE Transform Group").property("ADBE Opacity"), [[4.04, 42], [4.42, 22], [4.60, 0], [5.0, 0], [7.0, 0]]);
            }

            for (var b = 0; b < 3; b++) addLovePackBeer(comp, beerItem, b);

            // Keep the smaller joints visually in front of the arriving beer
            // bottles. Later-added gift/puff layers still cover both groups.
            for (var jo = 2; jo >= 0; jo--) {
                var frontJoint = findLayer(comp, jointNames[jo]);
                var frontJointShadow = findLayer(comp, shadowNames[jo]);
                frontJoint.moveToBeginning();
                frontJointShadow.moveAfter(frontJoint);
            }

            var gift = comp.layers.add(giftItem);
            gift.name = "LOVE PACK | Gift box hero";
            gift.inPoint = 4.48;
            gift.outPoint = 7.0;
            var giftT = gift.property("ADBE Transform Group");
            giftT.property("ADBE Anchor Point").setValue([giftItem.width / 2, giftItem.height / 2, 0]);
            replaceKeyframes(giftT.property("ADBE Position"), [[4.48, [320, 302, 0]], [4.82, [320, 260, 0]], [5.04, [320, 278, 0]], [5.28, [320, 268, 0]], [6.05, [317, 265, 0]], [7.0, [320, 270, 0]]]);
            replaceKeyframes(giftT.property("ADBE Scale"), [[4.48, [0, 0, 100]], [4.80, [37, 30.5, 100]], [5.02, [28.5, 34, 100]], [5.26, [32.5, 32.5, 100]], [6.05, [33.0, 32.0, 100]], [7.0, [32.5, 32.5, 100]]]);
            replaceKeyframes(giftT.property("ADBE Rotate Z"), [[4.48, -14], [4.80, 4], [5.02, -2], [5.26, 0], [6.05, 0.8], [7.0, 0]]);
            replaceKeyframes(giftT.property("ADBE Opacity"), [[4.48, 0], [4.58, 100], [7.0, 100]]);
            gift.motionBlur = true;

            addJointRolledPuff(comp, "LOVE PACK PUFF | Rose depth", false, [320, 280, 0], 4.38);
            addJointRolledPuff(comp, "LOVE PACK PUFF | Cream gift cloud", true, [320, 276, 0], 4.42);
            var flash = addFullFrameLayer(comp, "LOVE PACK PUFF | Warm flash", cream, 0);
            replaceKeyframes(flash.property("ADBE Transform Group").property("ADBE Opacity"), [[0, 0], [4.42, 0], [4.50, 38], [4.62, 0], [7.0, 0]]);

            var heartPositions = [[78, 146, 0], [562, 142, 0], [86, 362, 0], [552, 354, 0], [174, 74, 0], [486, 82, 0]];
            for (var h = 0; h < heartPositions.length; h++) {
                addLovePackHeart(comp, h, heartPositions[h], 4.92 + h * 0.17, h % 3 === 0 ? cream : (h % 3 === 1 ? mustard : deepRose));
            }
            var glintPositions = [[132, 244], [515, 250], [220, 416], [435, 410], [307, 74], [587, 283]];
            for (var g = 0; g < glintPositions.length; g++) {
                addJointRolledGlint(comp, 40 + g, [glintPositions[g][0], glintPositions[g][1], 0], 5.08 + g * 0.28, g % 2 === 0 ? cream : mustard);
            }

            try { comp.markerProperty.setValueAtTime(4.42, new MarkerValue("LOVE PACK PUFF")); } catch (ignoredLovePackPuffMarker) {}
            try { comp.markerProperty.setValueAtTime(5.26, new MarkerValue("LOVE PACK HOLD")); } catch (ignoredLovePackHoldMarker) {}
            comp.time = 5.30;
            comp.openInViewer();
            return {
                comp: { id: comp.id, name: comp.name, width: comp.width, height: comp.height, duration: comp.duration, fps: comp.frameRate, layers: comp.numLayers },
                beer_entry: 3.0,
                puff: 4.42,
                gift_hold: 5.26,
                style: "dusty_pink_earthy_love_pack"
            };
        });
    }

    function commandUpgradeHurryUpAssets(args) {
        return withUndo("Codex: Upgrade Hurry Up with illustrated assets", function () {
            var project = requireProject();
            var source = findComp(args.source_comp || "HURRY_UP_MASTER");
            var titleItem = findProjectItem(args.title_item);
            var clockItem = findProjectItem(args.clock_item);
            var targetName = args.new_name || "HURRY_UP_MASTER_ASSET_V2";
            var oldComp = null;
            for (var p = 1; p <= project.numItems; p++) {
                if (project.item(p) instanceof CompItem && project.item(p).name === targetName) {
                    oldComp = project.item(p);
                    break;
                }
            }
            if (oldComp) oldComp.remove();

            var comp = source.duplicate();
            comp.name = targetName;
            comp.parentFolder = findOrCreateProjectFolder("01_COMPS");
            comp.motionBlur = true;
            comp.shutterAngle = 220;
            comp.shutterPhase = -110;

            var oldTitleNames = ["HURRY UP - HERO", "TITLE ECHO - 1", "TITLE ECHO - 2"];
            for (var ot = 0; ot < oldTitleNames.length; ot++) {
                try { findLayer(comp, oldTitleNames[ot]).enabled = false; } catch (ignoredOldTitle) {}
            }

            var gold = [1.0, 0.69, 0.06];
            var cream = [1.0, 0.91, 0.55];
            var magenta = [0.94, 0.02, 0.54];
            var purple = [0.32, 0.015, 0.48];

            // A physical clock first owns the center of the vortex, then parks above
            // the title so the time motif remains readable throughout the hold.
            var clockHalo = comp.layers.addShape();
            clockHalo.name = "CLOCK ASSET | Gold time halo";
            addOutlinedEllipseGroup(clockHalo, [430, 430], [0, 0], null, gold, 7, 86);
            addOutlinedEllipseGroup(clockHalo, [392, 392], [0, 0], null, magenta, 3, 66);
            var clockHaloT = clockHalo.property("ADBE Transform Group");
            replaceKeyframes(clockHaloT.property("ADBE Position"), [
                [0.08, [320, 244, 0]], [1.08, [320, 244, 0]],
                [1.54, [320, 112, 0]], [3.10, [317, 110, 0]], [5.0, [320, 112, 0]]
            ]);
            replaceKeyframes(clockHaloT.property("ADBE Scale"), [
                [0.08, [0, 0, 100]], [0.36, [112, 112, 100]], [0.62, [94, 94, 100]],
                [1.08, [100, 100, 100]], [1.54, [44, 44, 100]],
                [3.10, [45.5, 45.5, 100]], [5.0, [44, 44, 100]]
            ]);
            replaceKeyframes(clockHaloT.property("ADBE Rotate Z"), [[0.08, -120], [1.08, 235], [1.54, 272], [5.0, 286]]);
            replaceKeyframes(clockHaloT.property("ADBE Opacity"), [[0.08, 0], [0.28, 88], [1.20, 62], [1.54, 34], [5.0, 27]]);
            clockHalo.blendingMode = BlendingMode.ADD;
            clockHalo.motionBlur = true;

            var clockGlow = comp.layers.add(clockItem);
            clockGlow.name = "CLOCK ASSET | Soft magenta aura";
            clockGlow.blendingMode = BlendingMode.ADD;
            clockGlow.motionBlur = true;
            var clockGlowT = clockGlow.property("ADBE Transform Group");
            clockGlowT.property("ADBE Anchor Point").setValue([clockItem.width / 2, clockItem.height / 2, 0]);
            replaceKeyframes(clockGlowT.property("ADBE Position"), [
                [0.08, [320, 244, 0]], [1.08, [320, 244, 0]],
                [1.54, [320, 112, 0]], [3.10, [317, 110, 0]], [5.0, [320, 112, 0]]
            ]);
            replaceKeyframes(clockGlowT.property("ADBE Scale"), [
                [0.08, [0, 0, 100]], [0.36, [36, 36, 100]], [0.62, [30, 30, 100]],
                [1.08, [32, 32, 100]], [1.54, [15.4, 15.4, 100]],
                [3.10, [15.9, 15.9, 100]], [5.0, [15.4, 15.4, 100]]
            ]);
            replaceKeyframes(clockGlowT.property("ADBE Rotate Z"), [[0.08, -28], [0.36, 8], [0.62, -4], [1.08, 0], [3.10, -1.3], [5.0, 0]]);
            replaceKeyframes(clockGlowT.property("ADBE Opacity"), [[0.08, 0], [0.28, 56], [1.10, 34], [1.54, 19], [5.0, 14]]);
            try {
                var clockBlur = clockGlow.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                clockBlur.property("ADBE Gaussian Blur 2-0001").setValue(24);
                clockBlur.property("ADBE Gaussian Blur 2-0002").setValue(1);
            } catch (ignoredClockBlur) {}

            var clock = comp.layers.add(clockItem);
            clock.name = "CLOCK ASSET | Illustrated pocket clock";
            clock.motionBlur = true;
            var clockT = clock.property("ADBE Transform Group");
            clockT.property("ADBE Anchor Point").setValue([clockItem.width / 2, clockItem.height / 2, 0]);
            replaceKeyframes(clockT.property("ADBE Position"), [
                [0.08, [320, 244, 0]], [1.08, [320, 244, 0]], [1.30, [320, 213, 0]],
                [1.54, [320, 112, 0]], [2.35, [323, 114, 0]], [3.10, [317, 110, 0]],
                [4.05, [322, 113, 0]], [5.0, [320, 112, 0]]
            ]);
            replaceKeyframes(clockT.property("ADBE Scale"), [
                [0.08, [0, 0, 100]], [0.36, [35, 35, 100]], [0.62, [29, 29, 100]],
                [0.84, [32.5, 32.5, 100]], [1.08, [31, 31, 100]],
                [1.54, [14.8, 14.8, 100]], [2.35, [15.2, 15.2, 100]],
                [3.10, [14.7, 14.7, 100]], [4.05, [15.1, 15.1, 100]], [5.0, [14.8, 14.8, 100]]
            ]);
            replaceKeyframes(clockT.property("ADBE Rotate Z"), [
                [0.08, -28], [0.36, 8], [0.62, -4], [0.84, 2.2], [1.08, 0],
                [1.54, -2], [2.35, 1.2], [3.10, -1.5], [4.05, 1.1], [5.0, 0]
            ]);
            replaceKeyframes(clockT.property("ADBE Opacity"), [[0.08, 0], [0.22, 100], [5.0, 100]]);

            // A fast sweep over the dial makes the clock active despite being a
            // single flattened illustration.
            var minuteHand = comp.layers.addShape();
            minuteHand.name = "CLOCK ASSET | One-second sweep";
            addJointRolledRoundedRect(minuteHand, [10, 110], [0, -48], 5, magenta, gold, 2.4, 100);
            var minuteT = minuteHand.property("ADBE Transform Group");
            replaceKeyframes(minuteT.property("ADBE Position"), [[0.08, [320, 244, 0]], [1.08, [320, 244, 0]], [1.54, [320, 112, 0]], [5.0, [320, 112, 0]]]);
            replaceKeyframes(minuteT.property("ADBE Scale"), [[0.08, [0, 0, 100]], [0.30, [100, 100, 100]], [1.08, [100, 100, 100]], [1.54, [47.7, 47.7, 100]], [5.0, [47.7, 47.7, 100]]]);
            replaceKeyframes(minuteT.property("ADBE Rotate Z"), [[0.08, -72], [1.08, 648], [1.54, 700], [5.0, 865]]);
            replaceKeyframes(minuteT.property("ADBE Opacity"), [[0.08, 0], [0.22, 96], [1.54, 80], [5.0, 68]]);
            minuteHand.blendingMode = BlendingMode.ADD;
            minuteHand.motionBlur = true;

            var hourHand = comp.layers.addShape();
            hourHand.name = "CLOCK ASSET | Counter hand";
            addJointRolledRoundedRect(hourHand, [13, 76], [0, -31], 6, gold, purple, 2.2, 100);
            var hourT = hourHand.property("ADBE Transform Group");
            replaceKeyframes(hourT.property("ADBE Position"), [[0.08, [320, 244, 0]], [1.08, [320, 244, 0]], [1.54, [320, 112, 0]], [5.0, [320, 112, 0]]]);
            replaceKeyframes(hourT.property("ADBE Scale"), [[0.08, [0, 0, 100]], [0.30, [100, 100, 100]], [1.08, [100, 100, 100]], [1.54, [47.7, 47.7, 100]], [5.0, [47.7, 47.7, 100]]]);
            replaceKeyframes(hourT.property("ADBE Rotate Z"), [[0.08, 38], [1.08, 398], [1.54, 422], [5.0, 486]]);
            replaceKeyframes(hourT.property("ADBE Opacity"), [[0.08, 0], [0.22, 100], [1.54, 88], [5.0, 76]]);
            hourHand.motionBlur = true;

            var hub = comp.layers.addShape();
            hub.name = "CLOCK ASSET | Animated hand hub";
            addOutlinedEllipseGroup(hub, [27, 27], [0, 0], cream, purple, 4, 100);
            var hubT = hub.property("ADBE Transform Group");
            replaceKeyframes(hubT.property("ADBE Position"), [[0.08, [320, 244, 0]], [1.08, [320, 244, 0]], [1.54, [320, 112, 0]], [5.0, [320, 112, 0]]]);
            replaceKeyframes(hubT.property("ADBE Scale"), [[0.08, [0, 0, 100]], [0.30, [100, 100, 100]], [1.08, [100, 100, 100]], [1.54, [47.7, 47.7, 100]], [5.0, [47.7, 47.7, 100]]]);
            replaceKeyframes(hubT.property("ADBE Opacity"), [[0.08, 0], [0.22, 100], [5.0, 100]]);

            var titleEcho = comp.layers.add(titleItem);
            titleEcho.name = "HURRY UP ASSET | Magenta impact echo";
            titleEcho.blendingMode = BlendingMode.ADD;
            titleEcho.motionBlur = true;
            var echoT = titleEcho.property("ADBE Transform Group");
            echoT.property("ADBE Anchor Point").setValue([titleItem.width / 2, titleItem.height / 2, 0]);
            replaceKeyframes(echoT.property("ADBE Position"), [[0.46, [320, 242, 0]], [0.82, [320, 284, 0]], [1.42, [320, 292, 0]], [5.0, [320, 292, 0]]]);
            replaceKeyframes(echoT.property("ADBE Scale"), [[0.46, [0, 0, 100]], [0.78, [38, 38, 100]], [1.02, [31, 31, 100]], [1.42, [29, 29, 100]], [5.0, [29, 29, 100]]]);
            replaceKeyframes(echoT.property("ADBE Rotate Z"), [[0.46, -720], [0.90, 14], [1.42, 0], [5.0, 0]]);
            replaceKeyframes(echoT.property("ADBE Opacity"), [[0.46, 0], [0.67, 48], [1.08, 26], [1.48, 0], [5.0, 0]]);
            try {
                var titleBlur = titleEcho.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                titleBlur.property("ADBE Gaussian Blur 2-0001").setValue(18);
                titleBlur.property("ADBE Gaussian Blur 2-0002").setValue(1);
            } catch (ignoredTitleBlur) {}

            var title = comp.layers.add(titleItem);
            title.name = "HURRY UP ASSET | Illustrated hero";
            title.motionBlur = true;
            var titleT = title.property("ADBE Transform Group");
            titleT.property("ADBE Anchor Point").setValue([titleItem.width / 2, titleItem.height / 2, 0]);
            replaceKeyframes(titleT.property("ADBE Position"), [
                [0.48, [320, 242, 0]], [0.80, [320, 278, 0]], [1.04, [320, 300, 0]],
                [1.28, [320, 286, 0]], [1.52, [320, 292, 0]], [2.35, [320, 288, 0]],
                [3.10, [320, 293, 0]], [4.05, [320, 288, 0]], [5.0, [320, 292, 0]]
            ]);
            replaceKeyframes(titleT.property("ADBE Scale"), [
                [0.48, [0, 0, 100]], [0.80, [33.2, 33.2, 100]], [1.02, [26.0, 32.5, 100]],
                [1.25, [29.8, 26.7, 100]], [1.52, [27.8, 27.8, 100]],
                [2.35, [28.3, 28.3, 100]], [3.10, [27.7, 27.7, 100]],
                [4.05, [28.2, 28.2, 100]], [5.0, [27.8, 27.8, 100]]
            ]);
            replaceKeyframes(titleT.property("ADBE Rotate Z"), [
                [0.48, -720], [0.80, 10], [1.02, -5], [1.25, 2.4], [1.52, 0],
                [2.35, -0.8], [3.10, 0.7], [4.05, -0.6], [5.0, 0]
            ]);
            replaceKeyframes(titleT.property("ADBE Opacity"), [[0.48, 0], [0.60, 100], [5.0, 100]]);

            // Preserve the original foreground sparkle hierarchy over the new title.
            try { title.moveAfter(findLayer(comp, "Sparkle 1")); } catch (ignoredTitleOrder) {}
            try { titleEcho.moveAfter(title); } catch (ignoredEchoOrder) {}
            try { hub.moveAfter(titleEcho); } catch (ignoredHubOrder) {}
            try { hourHand.moveAfter(hub); } catch (ignoredHourOrder) {}
            try { minuteHand.moveAfter(hourHand); } catch (ignoredMinuteOrder) {}
            try { clock.moveAfter(minuteHand); } catch (ignoredClockOrder) {}
            try { clockGlow.moveAfter(clock); } catch (ignoredClockGlowOrder) {}
            try { clockHalo.moveAfter(clockGlow); } catch (ignoredHaloOrder) {}

            try { comp.markerProperty.setValueAtTime(0.48, new MarkerValue("ILLUSTRATED TITLE IMPACT")); } catch (ignoredTitleMarker) {}
            try { comp.markerProperty.setValueAtTime(1.54, new MarkerValue("CLOCK PARK + TITLE HOLD")); } catch (ignoredClockMarker) {}
            comp.time = 2.35;
            comp.openInViewer();
            return {
                comp: { id: comp.id, name: comp.name, width: comp.width, height: comp.height, duration: comp.duration, fps: comp.frameRate, layers: comp.numLayers },
                title_asset: serializeItem(titleItem),
                clock_asset: serializeItem(clockItem),
                title_impact: 0.48,
                clock_park: 1.54,
                style: "illustrated_gold_magenta_time_vortex"
            };
        });
    }

    function commandImportAsset(args) {
        return withUndo("Codex: Import asset", function () {
            var file = new File(args.path);
            if (!file.exists) throw new Error("Asset file does not exist: " + file.fsName);
            var options = new ImportOptions(file);
            if (args.sequence) {
                options.sequence = true;
                options.forceAlphabetical = true;
            }
            var item = requireProject().importFile(options);
            if (args.sequence && args.fps && item instanceof FootageItem) item.mainSource.conformFrameRate = args.fps;
            if (args.folder !== undefined && args.folder !== null) {
                var folder = findProjectItem(args.folder, "folder");
                if (!(folder instanceof FolderItem)) throw new Error("Target item is not a folder.");
                item.parentFolder = folder;
            }
            return serializeItem(item);
        });
    }

    function commandAddLayer(args) {
        return withUndo("Codex: Add layer", function () {
            var comp = findComp(args.comp);
            var item = findProjectItem(args.item);
            if (!(item instanceof AVItem)) throw new Error("Only footage and compositions can be added as AV layers.");
            var layer = comp.layers.add(item);
            if (args.name) layer.name = args.name;
            if (args.start_time !== undefined) layer.startTime = args.start_time;
            applyTransform(layer, args);
            return { comp: serializeItem(comp), layer: serializeLayer(layer) };
        });
    }

    function commandAddText(args) {
        return withUndo("Codex: Add text", function () {
            var comp = findComp(args.comp);
            var layer = comp.layers.addText(args.text || "");
            if (args.name) layer.name = args.name;
            var sourceText = layer.property("ADBE Text Properties").property("ADBE Text Document");
            var document = sourceText.value;
            document.text = args.text || "";
            if (args.font) document.font = args.font;
            if (args.font_size) document.fontSize = args.font_size;
            if (args.fill_color) {
                document.applyFill = true;
                document.fillColor = args.fill_color;
            }
            sourceText.setValue(document);
            applyTransform(layer, args);
            return { comp: serializeItem(comp), layer: serializeLayer(layer) };
        });
    }

    function commandSetTransform(args) {
        return withUndo("Codex: Set layer transform", function () {
            var comp = findComp(args.comp);
            var layer = findLayer(comp, args.layer);
            applyTransform(layer, args);
            return { comp: serializeItem(comp), layer: serializeLayer(layer) };
        });
    }

    function commandOpenComp(args) {
        var comp = findComp(args.comp);
        if (args.time !== undefined) comp.time = Math.max(0, Math.min(comp.duration, args.time));
        comp.openInViewer();
        return compSnapshot(comp, 50);
    }

    function commandPlayPreview(args) {
        var comp = findComp(args.comp);
        if (args.time !== undefined) comp.time = Math.max(0, Math.min(comp.duration, args.time));
        comp.openInViewer();
        var names = ["Play Current Preview", "RAM Preview", "Preview"];
        var commandId = 0;
        var commandName = null;
        for (var i = 0; i < names.length; i++) {
            try {
                commandId = app.findMenuCommandId(names[i]);
                if (commandId > 0) {
                    commandName = names[i];
                    break;
                }
            } catch (ignored) {}
        }
        if (commandId > 0) app.executeCommand(commandId);
        return {
            comp: serializeItem(comp),
            requested: commandId > 0,
            command_id: commandId || null,
            command_name: commandName,
            note: commandId > 0 ? "Preview command sent to After Effects." : "Composition opened; press Space to start preview on this localized AE build."
        };
    }

    function safeFileStem(name) {
        return String(name).replace(/[\\\/:*?\"<>|]/g, "_").replace(/\s+/g, "_");
    }

    function commandRenderFrames(args, previewFolder) {
        var comp = findComp(args.comp);
        ensureFolder(previewFolder);
        var times = args.times;
        if (!times || !times.length) {
            times = [0, comp.duration * 0.25, comp.duration * 0.5, comp.duration * 0.75];
        }
        var frames = [];
        for (var i = 0; i < times.length; i++) {
            var time = Math.max(0, Math.min(comp.duration - comp.frameDuration, Number(times[i])));
            var fileName = safeFileStem(comp.name) + "_" + pad(i + 1, 2) + "_" + pad(Math.round(time * 1000), 7) + "ms.png";
            var file = new File(previewFolder.fsName + "/" + fileName);
            if (file.exists) file.remove();
            comp.saveFrameToPng(time, file);
            frames.push({ time: time, path: file.fsName });
        }
        return { comp: serializeItem(comp), frames: frames };
    }

    function commandRenderPngSequence(args) {
        var comp = findComp(args.comp);
        if (!args.output_folder) throw new Error("output_folder is required.");
        var outputFolder = new Folder(args.output_folder);
        ensureFolder(outputFolder);
        var totalFrames = Math.round(comp.duration / comp.frameDuration);
        var startFrame = Math.max(0, Math.floor(Number(args.start_frame || 0)));
        var endFrame = args.end_frame === undefined || args.end_frame === null
            ? totalFrames - 1
            : Math.min(totalFrames - 1, Math.floor(Number(args.end_frame)));
        if (endFrame < startFrame) throw new Error("end_frame must not precede start_frame.");
        var prefix = args.prefix ? safeFileStem(args.prefix) : safeFileStem(comp.name);
        var digits = Math.max(1, Math.floor(Number(args.digits || 5)));
        var rendered = [];
        for (var frameIndex = startFrame; frameIndex <= endFrame; frameIndex++) {
            var fileName = prefix + "_" + pad(frameIndex + 1, digits) + ".png";
            var file = new File(outputFolder.fsName + "/" + fileName);
            if (file.exists) file.remove();
            comp.saveFrameToPng(frameIndex * comp.frameDuration, file);
            rendered.push(file.fsName);
        }
        return {
            comp: serializeItem(comp),
            output_folder: outputFolder.fsName,
            total_frames: totalFrames,
            start_frame: startFrame,
            end_frame: endFrame,
            rendered_count: rendered.length,
            first_file: rendered.length ? rendered[0] : null,
            last_file: rendered.length ? rendered[rendered.length - 1] : null
        };
    }

    function commandEnqueueRender(args) {
        var comp = findComp(args.comp);
        var output = new File(args.output_path);
        ensureFolder(output.parent);
        var queueItem = requireProject().renderQueue.items.add(comp);
        try {
            if (args.render_settings_template) queueItem.applyTemplate(args.render_settings_template);
            var module = queueItem.outputModule(1);
            if (args.output_module_template) module.applyTemplate(args.output_module_template);
            module.file = output;
        } catch (error) {
            try { queueItem.remove(); } catch (ignored) {}
            throw error;
        }
        var result = {
            comp: serializeItem(comp),
            queue_index: queueItem.index,
            output_path: output.fsName,
            render_started: args.render_now === true
        };
        if (args.render_now === true) {
            requireProject().renderQueue.render();
            result.render_finished = true;
        }
        return result;
    }

    function commandListRenderTemplates(args) {
        var comp = findComp(args.comp);
        var queueItem = requireProject().renderQueue.items.add(comp);
        try {
            var module = queueItem.outputModule(1);
            var outputTemplates = [];
            var renderTemplates = [];
            var i;
            for (i = 0; i < module.templates.length; i++) outputTemplates.push(String(module.templates[i]));
            for (i = 0; i < queueItem.templates.length; i++) renderTemplates.push(String(queueItem.templates[i]));
            return { comp: serializeItem(comp), output_module_templates: outputTemplates, render_settings_templates: renderTemplates };
        } finally {
            try { queueItem.remove(); } catch (ignoredRemove) {}
        }
    }

    function commandSaveProject(args) {
        var project = requireProject();
        var target = args.path ? new File(args.path) : project.file;
        if (!target) throw new Error("Project is untitled; provide an absolute save path.");
        if (args.path && target.exists && !args.allow_overwrite) {
            throw new Error("Refusing to overwrite existing project without allow_overwrite=true: " + target.fsName);
        }
        ensureFolder(target.parent);
        project.save(target);
        return { name: target.name, path: target.fsName };
    }

    function commandOpenProject(args) {
        if (!args.path) throw new Error("Project path is required.");
        var file = new File(args.path);
        if (!file.exists) throw new Error("Project file does not exist: " + file.fsName);
        var project = app.open(file);
        return {
            name: project.file ? project.file.name : null,
            path: project.file ? project.file.fsName : null,
            item_count: project.numItems
        };
    }

    function dispatch(action, args, previewFolder) {
        if (action === "ping") return commandPing();
        if (action === "get_project") return commandGetProject(args);
        if (action === "get_comp") return compSnapshot(findComp(args.comp), args.max_layers || 250);
        if (action === "create_comp") return commandCreateComp(args);
        if (action === "duplicate_comp") return commandDuplicateComp(args);
        if (action === "duplicate_score_variants") return commandDuplicateScoreVariants(args);
        if (action === "build_reward_variant") return commandBuildRewardVariant(args);
        if (action === "enhance_reward_variant") return commandEnhanceRewardVariant(args);
        if (action === "build_modular_reward_variant") return commandBuildModularRewardVariant(args);
        if (action === "build_candy_reward_variant") return commandBuildCandyRewardVariant(args);
        if (action === "polish_candy_reward_variant") return commandPolishCandyRewardVariant(args);
        if (action === "add_candy_volume_variant") return commandAddCandyVolumeVariant(args);
        if (action === "fix_candy_gold_variant") return commandFixCandyGoldVariant(args);
        if (action === "finalize_candy_reward_variant") return commandFinalizeCandyRewardVariant(args);
        if (action === "refine_candy_reward_variant") return commandRefineCandyRewardVariant(args);
        if (action === "build_candy_score_family") return commandBuildCandyScoreFamily(args);
        if (action === "build_psychedelic_jackpot") return commandBuildPsychedelicJackpot(args);
        if (action === "polish_psychedelic_jackpot") return commandPolishPsychedelicJackpot(args);
        if (action === "rebuild_psychedelic_jackpot_v02") return commandRebuildPsychedelicJackpotV02(args);
        if (action === "rebuild_psychedelic_jackpot_v03") return commandRebuildPsychedelicJackpotV03(args);
        if (action === "polish_psychedelic_jackpot_v03") return commandPolishPsychedelicJackpotV03(args);
        if (action === "build_psychedelic_jackpot_second_half") return commandBuildPsychedelicJackpotSecondHalf(args);
        if (action === "rebuild_psychedelic_jackpot_portal_zoom") return commandRebuildPsychedelicJackpotPortalZoom(args);
        if (action === "build_joint_rolled_v2") return commandBuildJointRolledV2(args);
        if (action === "build_joint_rolled_approved") return commandBuildJointRolledApproved(args);
        if (action === "build_joint_rolled_count_variants") return commandBuildJointRolledCountVariants(args);
        if (action === "build_love_pack_finale") return commandBuildLovePackFinale(args);
        if (action === "upgrade_hurry_up_assets") return commandUpgradeHurryUpAssets(args);
        if (action === "import_asset") return commandImportAsset(args);
        if (action === "add_layer") return commandAddLayer(args);
        if (action === "add_text") return commandAddText(args);
        if (action === "set_transform") return commandSetTransform(args);
        if (action === "open_comp") return commandOpenComp(args);
        if (action === "play_preview") return commandPlayPreview(args);
        if (action === "render_frames") return commandRenderFrames(args, previewFolder);
        if (action === "render_png_sequence") return commandRenderPngSequence(args);
        if (action === "enqueue_render") return commandEnqueueRender(args);
        if (action === "list_render_templates") return commandListRenderTemplates(args);
        if (action === "save_project") return commandSaveProject(args);
        if (action === "open_project") return commandOpenProject(args);
        if (action === "undo") {
            // Adobe's stable command ID is safer here than the localized menu lookup.
            var undoId = 16;
            app.executeCommand(undoId);
            return { undone: true, command_id: undoId };
        }
        throw new Error("Unknown bridge action: " + action);
    }

    function bridgeRoot() {
        return new Folder(Folder.userData.fsName + "/CodexAEBridge");
    }

    var existing = $.global.CodexAEBridge;
    if (existing && existing.version === BRIDGE_VERSION) {
        existing.start();
        return;
    }

    var root = bridgeRoot();
    var inbox = new Folder(root.fsName + "/inbox");
    var outbox = new Folder(root.fsName + "/outbox");
    var previews = new Folder(root.fsName + "/previews");
    ensureFolder(inbox);
    ensureFolder(outbox);
    ensureFolder(previews);

    var bootstrapFile = new File(root.fsName + "/bootstrap.json");
    if (bootstrapFile.exists) {
        var bootstrapResponse = { protocol: 1, completed_at: isoNow(), ok: true, results: [] };
        try {
            var bootstrap = parseJson(readText(bootstrapFile));
            var bootstrapCommands = bootstrap.commands || [];
            for (var bootstrapIndex = 0; bootstrapIndex < bootstrapCommands.length; bootstrapIndex++) {
                var bootstrapCommand = bootstrapCommands[bootstrapIndex];
                bootstrapResponse.results.push({
                    action: bootstrapCommand.action,
                    result: dispatch(bootstrapCommand.action, bootstrapCommand.args || {}, previews)
                });
            }
        } catch (bootstrapError) {
            bootstrapResponse.ok = false;
            bootstrapResponse.error = safeError(bootstrapError);
        }
        writeTextAtomic(new File(root.fsName + "/bootstrap-result.json"), jsonStringify(bootstrapResponse));
        try { bootstrapFile.remove(); } catch (ignoredBootstrapRemove) {}
    }

    $.global.CodexAEBridge = {
        version: BRIDGE_VERSION,
        active: false,
        scheduled: false,
        lastHeartbeatAt: 0,
        lastPollAt: 0,
        root: root,

        writeHeartbeat: function () {
            var active = app.project && app.project.activeItem ? app.project.activeItem : null;
            writeTextAtomic(new File(root.fsName + "/heartbeat.json"), jsonStringify({
                protocol: 1,
                bridge_version: BRIDGE_VERSION,
                written_at: isoNow(),
                app_name: app.name || "Adobe After Effects",
                app_version: app.version,
                project_name: app.project && app.project.file ? app.project.file.name : "Untitled Project",
                project_path: app.project && app.project.file ? app.project.file.fsName : null,
                active_item: active ? active.name : null
            }));
            this.lastHeartbeatAt = new Date().getTime();
        },

        processFile: function (requestFile) {
            var request = null;
            var response;
            try {
                request = parseJson(readText(requestFile));
                if (!request.id || !request.action) throw new Error("Malformed bridge request.");
                if (request.expires_at_ms && new Date().getTime() > Number(request.expires_at_ms)) {
                    throw new Error("Bridge request expired before After Effects could process it.");
                }
                response = {
                    protocol: 1,
                    id: request.id,
                    ok: true,
                    completed_at: isoNow(),
                    result: dispatch(request.action, request.args || {}, previews)
                };
            } catch (error) {
                response = {
                    protocol: 1,
                    id: request && request.id ? request.id : requestFile.displayName.replace(/\.json$/i, ""),
                    ok: false,
                    completed_at: isoNow(),
                    error: safeError(error)
                };
            }
            writeTextAtomic(new File(outbox.fsName + "/" + response.id + ".json"), jsonStringify(response));
            try { requestFile.remove(); } catch (ignored) {}
        },

        poll: function () {
            if (!this.active) return;
            var now = new Date().getTime();
            this.lastPollAt = now;
            if (now - this.lastHeartbeatAt >= HEARTBEAT_MS) this.writeHeartbeat();
            var files = inbox.getFiles("*.json");
            files.sort(function (a, b) { return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0); });
            if (files.length > 0) this.processFile(files[0]);
        },

        _scheduledPoll: function () {
            this.scheduled = false;
            if (!this.active) return;
            try { this.poll(); } catch (ignored) {}
            this.schedule();
        },

        schedule: function () {
            if (!this.active || this.scheduled) return;
            this.scheduled = true;
            app.scheduleTask("$.global.CodexAEBridge._scheduledPoll()", POLL_MS, false);
        },

        start: function () {
            this.active = true;
            this.writeHeartbeat();
            // AE can discard a scheduled task while loading another project. In that
            // case the old flag remains true although no callback is alive anymore.
            if (!this.lastPollAt || new Date().getTime() - this.lastPollAt > 2000) {
                this.scheduled = false;
            }
            this.schedule();
        },

        stop: function () {
            this.active = false;
            this.scheduled = false;
        },

        status: function () {
            return {
                active: this.active,
                version: this.version,
                root: this.root.fsName,
                last_heartbeat_at: this.lastHeartbeatAt
            };
        }
    };

    $.global.CodexAEBridge.start();
}());
