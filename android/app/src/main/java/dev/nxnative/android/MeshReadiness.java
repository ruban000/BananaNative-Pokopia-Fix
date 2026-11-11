package dev.nxnative.android;

import android.content.Context;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;

/** File compatibility for a selected compute path; never rewrites native GPU facts. */
final class MeshReadiness {
    private MeshReadiness() {}

    static String report(File pack, String titleId, List<String> env) {
        String cache = Readiness.envValue(env, "DM_VK_MESH_CACHE");
        if (cache == null || cache.isEmpty()) return "mesh.compute.selected=0\n";
        String explicit = Readiness.envValue(env, "DM_VK_SHLIB");
        File library = explicit != null && !explicit.isEmpty() ? new File(explicit) : pack == null ? null : Pack.libraryOf(pack);
        if (library == null && pack != null) library = Pack.playableLibrary(pack, titleId);
        return Native.meshCache(library == null ? "" : library.getPath(), cache, titleId);
    }

    /** Same override order as game startup, for the all-games report's file check. */
    static List<String> environment(Context c, String titleId) {
        List<String> env = new ArrayList<>();
        File root = AppStorage.root(c);
        env.addAll(read(new File(root, "env.txt")));
        env.addAll(GameActivity.envLines(Prefs.gameEnv(c, titleId)));
        env.addAll(read(new File(root, "env-" + titleId + ".txt")));
        return env;
    }

    private static List<String> read(File file) {
        try { return file.isFile() ? GameActivity.envLines(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8)) : new ArrayList<>(); }
        catch (IOException | SecurityException e) { return new ArrayList<>(); }
    }
}
