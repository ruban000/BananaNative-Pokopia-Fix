"""Indirect-count multi-draws on the Vulkan replay: the host's records carry the multi-draw's maximum and stride (NVN_MDI_NS), and the replay's
mdi_plan makes the whole multi-draw at its first draw with a GPU-read count and passes over its other draws. Production code compiled with stubs; no device."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def _between(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


class MultiDrawCount(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_plan(self):
        obj = (ROOT / "dm/gfx/nvn/nvn_obj.h").read_text()
        macros = "\n".join(l for l in obj.splitlines() if re.match(r"#define NVN_MDI_(NS|MAX|STRIDE)\(", l))
        self.assertEqual(macros.count("#define"), 3)
        draw = (ROOT / "dm/gfx/vk_android/vka_draw.c").read_text()
        plan = _between(draw, "static struct { uint64_t cnt, ind0; uint32_t n, stride; } g_mdi;", "// The whole multi-draw with the GPU's count")
        hle = (ROOT / "dm/gfx/nvn/hle_nvn.c").read_text()
        # Both multi-draw entry points record NVN_MDI_NS(max, stride) in every draw
        self.assertEqual(len(re.findall(r"cnt, i, NVN_MDI_NS\(max, stride\)\}\);", hle)), 2)
        prog = r'''
        #include <assert.h>
        #include <stdint.h>
        #include <stdio.h>
        ''' + macros + "\n" + plan + r'''
        int main(void) {
            uint32_t n, st;
            /* the encoding: max in bits 0-10, stride above; out of range gives 0 (each draw on its own) */
            assert(NVN_MDI_MAX(NVN_MDI_NS(1024, 20)) == 1024 && NVN_MDI_STRIDE(NVN_MDI_NS(1024, 20)) == 20);
            assert(NVN_MDI_NS(0, 20) == 0 && NVN_MDI_NS(1025, 20) == 0 && NVN_MDI_NS(4, 0) == 0 && NVN_MDI_NS(4, 1 << 21) == 0);
            uint32_t ns = NVN_MDI_NS(4, 32);
            /* first draw, GPU count available: the whole multi-draw */
            assert(mdi_plan(0x1000, 0, ns, 0x2000, 20, 1, &n, &st) == 1 && n == 4 && st == 32);
            mdi_made(0x1000, 0x2000, n, st);
            /* its later draws: passed over */
            for (int i = 1; i < 4; i++) assert(mdi_plan(0x1000, i, ns, 0x2000 + 32u * i, 20, 1, &n, &st) == -1);
            /* a draw of another multi-draw (other count, other records, past the maximum) is not */
            assert(mdi_plan(0x1004, 1, ns, 0x2000 + 32, 20, 1, &n, &st) == 0);
            assert(mdi_plan(0x1000, 1, ns, 0x3000, 20, 1, &n, &st) == 0);
            assert(mdi_plan(0x1000, 4, ns, 0x2000 + 32 * 4, 20, 1, &n, &st) == 0);
            /* a new first draw forgets the last multi-draw, even when it is not made on the GPU */
            assert(mdi_plan(0x1000, 0, ns, 0x2000, 20, 0, &n, &st) == 0);
            assert(mdi_plan(0x1000, 1, ns, 0x2000 + 32, 20, 1, &n, &st) == 0);
            /* records from before (no maximum), a stride under the record or not a multiple of 4: each draw on its own */
            assert(mdi_plan(0x1000, 0, 0, 0x2000, 20, 1, &n, &st) == 0);
            assert(mdi_plan(0x1000, 0, NVN_MDI_NS(4, 16), 0x2000, 20, 1, &n, &st) == 0);
            assert(mdi_plan(0x1000, 0, NVN_MDI_NS(4, 16), 0x2000, 16, 1, &n, &st) == 1);
            assert(mdi_plan(0x1000, 0, NVN_MDI_NS(4, 22), 0x2000, 20, 1, &n, &st) == 0);
            puts("ok");
            return 0;
        }
        '''
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / "t.c"
            c.write_text(prog)
            exe = Path(d) / "t"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], check=True)
            out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "ok")

    def test_device_gates(self):
        # The count path needs drawIndirectCount and, for a maximum over one draw, multiDrawIndirect; the device logs drawIndirectFirstInstance, which GPU-written
        # records with a non-zero firstInstance (BaseInstance, c0 + 0x644) need, and the readiness report lists both
        dev = (ROOT / "dm/gfx/vk_android/vka_dev.c").read_text()
        line = next(l for l in dev.splitlines() if l.strip().startswith("V.draw_count ="))
        for need in ("f12.drawIndirectCount", "f2.features.multiDrawIndirect", "vk.vkCmdDrawIndirectCount", "vk.vkCmdDrawIndexedIndirectCount"):
            self.assertIn(need, line)
        self.assertIn("draw path: drawIndirectFirstInstance", dev)
        rd = (ROOT / "dm/gfx/vk_android/vka_readiness.c").read_text()
        for name in ("drawIndirectFirstInstance", "multiDrawIndirect", "drawIndirectCount", "shaderDrawParameters"):
            self.assertIn('B("%s"' % name, rd)


if __name__ == "__main__":
    unittest.main()
