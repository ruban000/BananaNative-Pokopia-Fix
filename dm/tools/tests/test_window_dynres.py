"""loader.c's load-time settings of main, run on a synthetic image: window_fn's size pair in main's data (Ocarina of Time's form) and dynres_fn's
instruction (full resolution unless DM_DYNAMIC_RESOLUTION=1). The production functions are compiled with a minimal module type and the profile rows given here; the leaf-getter
and height-bound forms keep their behaviour (one check each)."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
LOADER = ROOT / 'dm/host/loader.c'
B = 'e0f3ccfaf50065ae4ea01eb5aeb13b1a'
TEXT_END, SIZE, PAIR, INSN, GETW, GETH, BOUND = 0x8000, 0x10000, 0x9568, 0x5bd0, 0x1000, 0x1010, 0x2000
TBZ, B_FIXED = 0x360041e8, 0x1400020f


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


def fixture(rows):
    """C source: levels_apply / window_size / dynres_apply / window_apply from loader.c, with rows = (window_fn rows, dynres_fn rows) as C initialisers"""
    src = LOADER.read_text()
    code = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define LOG(...) (printf(__VA_ARGS__), putchar('\n'))
typedef struct { char name[16]; char build_id[33]; int nro; uint8_t *base; uint64_t text_end; size_t size; } module_t;
'''
    code += f'#define DM_GAME_WINDOW_FN {{{rows[0]}}}\n#define DM_GAME_NWINDOW_FN {rows[0].count("{")}\n'
    empty = '{"", 0, 0, 0}'   # what game.py writes for a profile without dynres_fn
    code += f'#define DM_GAME_DYNRES_FN {{{rows[1] or empty}}}\n#define DM_GAME_NDYNRES_FN {rows[1].count("{")}\n'
    code += '#define DM_GAME_RES_LEVELS {{"", 0, 0, 0, {{0, 0}}}}\n#define DM_GAME_NRES_LEVELS 0\n'   # a profile without res_levels (levels_apply: window_size's size-only fallback)
    for sig in ('static int levels_apply(', 'static void window_size(', 'static void dynres_apply(', 'static void window_apply('):
        code += function(src, sig)
    code += r'''
int main(int argc, char **argv) {
    static uint8_t img[0x10000];
    FILE *f = fopen(argv[1], "rb"); if (!f || fread(img, 1, sizeof img, f) != sizeof img) return 2; fclose(f);
    module_t m = {"main", "", 0, img, 0x8000, sizeof img}; snprintf(m.build_id, sizeof m.build_id, "%s", argv[2]);
    window_apply(&m);
    f = fopen(argv[1], "wb"); fwrite(img, 1, sizeof img, f); fclose(f);
}
'''
    return code


@unittest.skipUnless(shutil.which(os.environ.get('CC', 'cc')), 'C compiler required')
class WindowDynres(unittest.TestCase):
    ROWS = (f'{{"{B}", ~0ull, {PAIR:#x}, 2560, 1440}}', f'{{"{B}", {INSN:#x}, {TBZ:#x}u, {B_FIXED:#x}u}}')

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.dir = Path(cls.tmp.name); cls.bins = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def binary(self, rows):
        if rows not in self.bins:
            c = self.dir / f'f{len(self.bins)}.c'; c.write_text(fixture(rows)); exe = self.dir / f'f{len(self.bins)}'
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-fsanitize=address,undefined', str(c), '-o', str(exe)], check=True)
            self.bins[rows] = exe
        return self.bins[rows]

    def run_image(self, env, rows=None, build=B, pair=(2560, 1440), insn=TBZ, image=None):
        """(log lines, the pair after loading, the instruction after loading)"""
        img = bytearray(SIZE) if image is None else image
        if image is None:
            struct.pack_into('<II', img, PAIR, *pair); struct.pack_into('<I', img, INSN, insn)
        p = self.dir / 'image.bin'; p.write_bytes(img)
        r = subprocess.run([str(self.binary(rows or self.ROWS)), str(p), build], capture_output=True, text=True,
                           env={k: v for k, v in os.environ.items() if k not in ('DM_WINDOW_SIZE', 'DM_DYNAMIC_RESOLUTION')} | env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = p.read_bytes()
        return r.stdout.splitlines(), struct.unpack_from('<II', out, PAIR), struct.unpack_from('<I', out, INSN)[0], out

    def test_pair_size(self):
        log, pair, _, _ = self.run_image({'DM_WINDOW_SIZE': '1920x1080'})
        self.assertEqual(pair, (1920, 1080))
        self.assertIn(f'window: 1920x1080 (DM_WINDOW_SIZE; the size table entry main+{PAIR:#x}; the game\'s own 2560x1440)', log)
        self.assertFalse([l for l in log if l.startswith('[RED]')], log)

    def test_pair_own_and_original(self):
        for env in ({}, {'DM_WINDOW_SIZE': 'original'}, {'DM_WINDOW_SIZE': ''}):
            log, pair, _, _ = self.run_image(env)
            self.assertEqual(pair, (2560, 1440), env)
            self.assertTrue(any("the game's own; the size table entry" in l for l in log), log)

    def test_pair_checks(self):
        log, pair, _, _ = self.run_image({'DM_WINDOW_SIZE': '1920x1080'}, pair=(1920, 1080))   # not the game's own pair: left alone
        self.assertEqual(pair, (1920, 1080)); self.assertTrue(any(l.startswith('[RED] window_fn') and 'not the game\'s own 2560x1440' in l for l in log), log)
        log, pair, _, _ = self.run_image({'DM_WINDOW_SIZE': '1920x1080'}, build='0' * 32)   # another build: nothing written
        self.assertEqual(pair, (2560, 1440)); self.assertTrue(any('has no window_fn or res_levels row' in l for l in log), log)
        rows = (f'{{"{B}", ~0ull, 0x4000, 2560, 1440}}', '')   # in the text: refused
        img = bytearray(SIZE); struct.pack_into('<II', img, 0x4000, 2560, 1440)
        log, _, _, out = self.run_image({'DM_WINDOW_SIZE': '1920x1080'}, rows=rows, image=img)
        self.assertEqual(struct.unpack_from('<II', out, 0x4000), (2560, 1440)); self.assertTrue(any('not a size pair in main\'s data' in l for l in log), log)
        log, pair, _, _ = self.run_image({'DM_WINDOW_SIZE': '3840x2160'})
        self.assertEqual(pair, (3840, 2160)); self.assertTrue(any(l.startswith('[RED] DM_WINDOW_SIZE=3840x2160: above') for l in log), log)

    def test_dynres(self):
        for env in ({}, {'DM_DYNAMIC_RESOLUTION': '0'}):   # the default: full resolution, the controller's own disabled branch
            log, _, insn, _ = self.run_image(env)
            self.assertEqual(insn, B_FIXED, env)
            self.assertTrue(any(l.startswith(f'dynamic resolution: off, full resolution') and f'main+{INSN:#x} 360041e8 -> 1400020f' in l for l in log), log)
        log, _, insn, _ = self.run_image({'DM_DYNAMIC_RESOLUTION': '1'})
        self.assertEqual(insn, TBZ); self.assertTrue(any(l.startswith("dynamic resolution: on, the game's controller") for l in log), log)
        log, _, insn, _ = self.run_image({}, insn=0xd503201f)   # not the game's instruction: left alone
        self.assertEqual(insn, 0xd503201f); self.assertTrue(any(l.startswith('[RED] dynres_fn') for l in log), log)
        log, _, insn, _ = self.run_image({}, build='0' * 32)   # another build: nothing written, nothing said
        self.assertEqual(insn, TBZ); self.assertFalse([l for l in log if 'dynamic resolution' in l or 'dynres_fn' in l], log)
        log, _, insn, _ = self.run_image({'DM_DYNAMIC_RESOLUTION': 'fixed'})
        self.assertEqual(insn, B_FIXED); self.assertTrue(any(l.startswith('[RED] DM_DYNAMIC_RESOLUTION=fixed: must be 0 or 1') for l in log), log)

    def test_other_forms_unchanged(self):
        # Donkey Kong Bananza's getters and Mario Kart World's height bound, with no dynres_fn row: as before, and nothing said of dynamic resolution
        img = bytearray(SIZE)
        struct.pack_into('<II', img, GETW, 0x52800000 | (2880 << 5), 0xd65f03c0); struct.pack_into('<II', img, GETH, 0x52800000 | (1620 << 5), 0xd65f03c0)
        struct.pack_into('<I', img, BOUND, 0x52800015 | (1440 << 5))
        log, _, _, out = self.run_image({'DM_WINDOW_SIZE': '1920x1080'}, rows=(f'{{"{B}", {GETW:#x}, {GETH:#x}, 2880, 1620}}', ''), image=bytearray(img))
        self.assertEqual(((struct.unpack_from('<I', out, GETW)[0] >> 5) & 0xffff, (struct.unpack_from('<I', out, GETH)[0] >> 5) & 0xffff), (1920, 1080))
        self.assertFalse([l for l in log if 'dynamic resolution' in l], log)
        log, _, _, out = self.run_image({'DM_WINDOW_SIZE': '1920x1080'}, rows=(f'{{"{B}", 0, {BOUND:#x}, 2560, 1440}}', ''), image=bytearray(img))
        self.assertEqual((struct.unpack_from('<I', out, BOUND)[0] >> 5) & 0xffff, 1080)
        self.assertTrue(any(l.startswith('window: height bound 1080') for l in log), log)


if __name__ == '__main__':
    unittest.main()
