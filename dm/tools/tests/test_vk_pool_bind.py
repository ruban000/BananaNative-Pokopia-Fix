"""Exercise production pool binding helpers against an independent Vulkan-state oracle."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def function(source, name):
    start = source.index(name)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class PoolBindings(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_command_stream(self):
        src = (ROOT / "dm/gfx/vk_android/vka_draw.c").read_text()
        # Production callers must account for layout changes even without a pool read.
        binds = function(src, "static int sets_push(")   # (bind_sets builds the writes, sets_push binds them: the pool binding is there)
        self.assertLess(binds.index("pool_bind_layout(bp, lay->layout)"), binds.index("if (lay->push)"))
        self.assertIn("if (ps) pool_bind(bp, lay->layout, ps)", binds)
        fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
typedef uint64_t VkPipelineLayout;
typedef uint64_t VkDescriptorSet;
typedef int VkPipelineBindPoint;
#define VK_PIPELINE_BIND_POINT_COMPUTE 1
#define VK_NULL_HANDLE 0
static int filter = 1, g_cb = 1;
#define VKA_ENV(name, fallback) filter
static struct { VkPipelineLayout layout; VkDescriptorSet set; } g_pool_bind[2];
static uint64_t g_pool_bind_calls, g_pool_bind_emitted;
static struct { int valid; } DY, VB, VBC, IB;
static void inst_reset(void) {}
static struct { int n; } g_pd[2];
static uint64_t oracle_layout[2], oracle_set[2], emitted;
static void cmd(int cb, int bp, uint64_t layout, unsigned first, unsigned n,
                const uint64_t *sets, unsigned nd, const unsigned *offsets) {
    assert(cb == g_cb && first == 1 && n == 1 && nd == 0 && offsets == NULL);
    assert(layout == oracle_layout[bp]);
    oracle_set[bp] = *sets; emitted++;
}
static struct { void (*vkCmdBindDescriptorSets)(int,int,uint64_t,unsigned,unsigned,const uint64_t*,unsigned,const unsigned*); } vk = {cmd};
'''
        fixture += function(src, "static void pool_bind_layout(") + "\n"
        fixture += function(src, "static void pool_bind(") + "\n"
        fixture += function(src, "void vka_dyn_invalidate(void) {") + r'''
static void draw(int bp, uint64_t layout, uint64_t set) {
    /* Different layouts represent incompatible set 0 layouts in the oracle. */
    if (oracle_layout[bp] != layout) oracle_set[bp] = 0;
    oracle_layout[bp] = layout;
    pool_bind_layout(bp, layout);
    if (set) { pool_bind(bp, layout, set); assert(oracle_set[bp] == set); }
}
static void invalidate(void) {
    vka_dyn_invalidate();
    memset(oracle_layout, 0, sizeof oracle_layout);
    memset(oracle_set, 0, sizeof oracle_set);
}
int main(void) {
    draw(0, 10, 100); draw(0, 10, 100); assert(emitted == 1);
    draw(1, 10, 100); assert(emitted == 2); /* independent bind points */
    draw(0, 10, 200); assert(emitted == 3); /* actual pool set replacement */
    draw(0, 11, 0); draw(0, 10, 200); assert(emitted == 4); /* non-pool disturbance */
    invalidate(); draw(0, 10, 200); assert(emitted == 5);
    filter = 0; draw(0, 10, 200); draw(0, 10, 200); assert(emitted == 7);
    filter = 1; draw(0, 10, 200); assert(emitted == 7);
    uint32_t rng = 1;
    for (int i = 0; i < 10000; i++) {
        rng = rng * 1664525u + 1013904223u;
        if (!(rng & 63)) invalidate();
        filter = !!(rng & 128);
        draw((rng >> 8) & 1, 10 + ((rng >> 9) & 3), (rng & 32) ? 100 + ((rng >> 12) & 3) : 0);
    }
    assert(g_pool_bind_emitted == emitted && emitted < g_pool_bind_calls);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pool.c"
            binary = Path(tmp) / "pool"
            path.write_text(fixture)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", str(path), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
