/* Render a reference frame from GAME_SCENE without changing the project. */
(function () {
    var PROJECT_PATH = "F:/Projects/CnC Pinball GUI V3 Python/okletek/Guitar heroe/gitihiri.aep";
    var OUTPUT_PATH = "F:/Projects/CnC Pinball GUI V3 Python/tmp/ae_game_scene_preview.png";
    var ROOT_COMP_NAME = "GAME_SCENE";
    var projectFile = new File(PROJECT_PATH);
    var outputFile = new File(OUTPUT_PATH);
    var outputFolder = outputFile.parent;
    var comp = null;
    var index;

    app.beginSuppressDialogs();
    try {
        if (!outputFolder.exists) {
            outputFolder.create();
        }
        if (!app.project || !app.project.file || app.project.file.fsName !== projectFile.fsName) {
            app.open(projectFile);
        }
        for (index = 1; index <= app.project.numItems; index += 1) {
            if (app.project.item(index) instanceof CompItem && app.project.item(index).name === ROOT_COMP_NAME) {
                comp = app.project.item(index);
                break;
            }
        }
        if (!comp) {
            throw new Error("Composition not found: " + ROOT_COMP_NAME);
        }
        comp.saveFrameToPng(0, outputFile);
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
