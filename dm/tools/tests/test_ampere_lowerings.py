"""Ampere instructions Ocarina of Time needed (docs/reference/shaders.md "Ampere F2IP, ATOMG.CAS, layer queries, task read-back and mesh point size"), on synthetic programs only.

F2IP, ATOMG.CAS and the layer query are run: each program is translated, checked by spirv-val, and dispatched on a Vulkan device (Mesa's lavapipe when it is
installed) by vk_compute_run.c, and its results are compared with what the instruction computes (a reference in Python). The task read-back and the mesh point size
plane are checked in the SPIR-V (no mesh pipeline here). No game files are used.
Needs the translator: XLATE=<published dmsh_xlate> python3 -m unittest dm/tools/tests/test_ampere_lowerings.py -v
"""
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'dm/gfx/tools'))
import vk_android_spv as spv

XLATE = os.environ.get('XLATE')
VAL = os.environ.get('SPIRV_VAL') or shutil.which('spirv-val')
LVP = next((p for p in ('/usr/share/vulkan/icd.d/lvp_icd.json', '/etc/vulkan/icd.d/lvp_icd.json') if os.path.exists(p)), None)
CC = shutil.which('cc')
RZ, PT = 255, 7


def inst(lo, hi=0):   # no write barrier (control bits 110..112 = 7): the result is the registers' at once (Translator.cs EmitOpsAmpere)
    return (lo | (hi | 7 << 46) << 64).to_bytes(16, 'little')


def mov_c(rd, bank, off):   # MOV Rd, c[bank][off] (form 5, lane mask 0xf)
    return inst(0xa02 | PT << 12 | rd << 16 | off << 38 | bank << 54, 0xf << 8)


def mov_i(rd, value):       # MOV Rd, imm32 (form 4)
    return inst(0x802 | PT << 12 | rd << 16 | value << 32, 0xf << 8)


def f2ip(rd, ra, rb, rnd=0, s8=False, relu=False, ntz=True):   # F2IP.{U8,S8}.F32[.NTZ] Rd, Ra, Rb, RZ
    return inst(0x243 | PT << 12 | rd << 16 | ra << 24 | rb << 32, RZ | ntz << 10 | relu << 11 | s8 << 12 | rnd << 14)


def atomg_cas(rd, ra, rb, rc, off=0):   # ATOMG.E.CAS.STRONG.GPU PT, Rd, [Ra.64 + off], Rb, Rc
    return inst(0x3a9 | PT << 12 | rd << 16 | ra << 24 | rb << 32 | (off & 0xffffff) << 40, rc | 1 << 8 | 7 << 17)


def txq(rd, ra, handle, mask, bank=2):   # TXQ Rd, Ra (the level), TEX_HEADER_DIMENSION, c[bank] handle, component mask; the second destination RZ
    return inst(0xb6f | PT << 12 | rd << 16 | ra << 24 | handle << 40 | bank << 54, RZ | mask << 8)


def store(x_reg, value_reg):   # SUST.D.2D [R<x_reg>, RZ], R<value_reg> into image slot 0 (red only)
    return inst(0xb9b | PT << 12 | x_reg << 24 | value_reg << 32 | 0x48 << 40 | 2 << 54 | 3 << 61, 1 << 8 | 7 << 46 | 1 << 52)


def exit_():
    return inst(0x94d | PT << 12, 7 << 23)


def f2ip_ref(f, rnd, s8, relu):
    """The byte F2IP.F32.NTZ makes of a float: NaN 0, else rounded (nearest even, or toward zero) and saturated"""
    if math.isnan(f): return 0
    r = math.trunc(f) if rnd == 3 else (f if math.isinf(f) else round(f))   # Python's round is round-half-even
    lo, hi = (0 if relu else -128, 127) if s8 else (0, 255)
    return int(max(lo, min(hi, r))) & 0xff


def f32(x): return struct.unpack('<I', struct.pack('<f', x))[0]


@unittest.skipUnless(XLATE and Path(XLATE).is_file(), 'set XLATE to the freshly built translator')
class AmpereLowerings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='ampere_lower_'))
        cls.run_bin = None
        if LVP and CC:
            b = cls.tmp / 'vk_compute_run'
            r = subprocess.run([CC, '-std=gnu11', '-O1', '-I', str(ROOT / 'dm/third_party/vulkan-headers/include'), str(ROOT / 'dm/tools/tests/vk_compute_run.c'), '-o', str(b), '-l:libvulkan.so.1'],
                               capture_output=True, text=True)
            if r.returncode == 0: cls.run_bin = b

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def translate(self, key, code, **job):
        sph = bytes.fromhex('81040c00') + bytes(0x7c) if job.get('stage') in ('ts', 'ms') else b''   # a task / mesh code area starts with an SPH
        (self.tmp / f'{key}.bin').write_bytes(sph + b''.join(code))
        j = {'key': key, 'stage': 'cs', 'arch': 'sm86', 'code': str(self.tmp / f'{key}.bin'), 'tex': {}, 'img': {'0': {'type': '2d', 'fmt': 'R32Uint'}}, 'local': [1, 1, 1], 'shared': 0, 'debug': True}
        j.update(job)
        (self.tmp / f'{key}.json').write_text(json.dumps([j]))
        r = subprocess.run([XLATE, str(self.tmp / f'{key}.json'), str(self.tmp / 'out')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sp = self.tmp / 'out' / f'{key}.spv'
        if VAL:
            v = subprocess.run([VAL, '--target-env', 'vulkan1.3', str(sp)], capture_output=True, text=True)
            self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        return sp, json.loads((self.tmp / 'out' / f'{key}.res.json').read_text())

    def run_compute(self, sp, ubo=b'', ssbo=None, out_words=1, tex=None):
        """Dispatches the module once: c[3] (its uniform buffer) from ubo, storage buffer 0 from ssbo, image slot 0 out_words wide → (image words, storage words)"""
        if not self.run_bin: self.skipTest('no lavapipe (lvp_icd.json) or C compiler')
        m = spv.Module(sp.read_bytes()); args = []
        img = self.tmp / (sp.stem + '.img'); img.write_bytes(bytes(4 * out_words))
        sb = self.tmp / (sp.stem + '.ssbo')
        for e in m.resources():
            if e['desc'] == spv.VK_UNIFORM_BUFFER:
                u = self.tmp / f'{sp.stem}.ubo{e["binding"]}'; u.write_bytes(ubo.ljust(4096, b'\0')); args.append(f'{e["set"]},{e["binding"]},u,{u}')
            elif e['desc'] == spv.VK_STORAGE_BUFFER:
                sb.write_bytes(ssbo or bytes(16)); args.append(f'{e["set"]},{e["binding"]},s,{sb}')
            elif e['desc'] == spv.VK_STORAGE_IMAGE: args.append(f'{e["set"]},{e["binding"]},i,{img}')
            elif e['desc'] == spv.VK_COMBINED:
                w, h, layers, array = tex; args.append(f'{e["set"]},{e["binding"]},t,-,{w},{h},{layers},{array}')
            else: self.fail(f'resource {e}')
        env = dict(os.environ, VK_ICD_FILENAMES=LVP)
        r = subprocess.run([str(self.run_bin), str(sp), *args], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        words = lambda p: list(struct.unpack(f'<{p.stat().st_size // 4}I', p.read_bytes()))
        return words(img), (words(sb) if ssbo is not None else None)

    def test_f2ip_packs_two_rounded_saturated_bytes(self):
        # (Ra, Rb, rounding, S8, RELU): Ra RZ like Ocarina of Time's mesh programs, then pairs as its compute programs
        nan, inf = float('nan'), float('inf')
        cases = [(None, 200.0, 3, False, False), (None, 127.7, 3, False, False), (None, 300.0, 3, False, False), (None, -4.0, 3, False, False), (None, nan, 3, False, False),
                 (12.5, 13.5, 0, False, False), (254.5, 0.49, 0, False, False), (inf, -inf, 0, False, False), (nan, 1e9, 0, False, False), (-0.0, 255.0, 0, False, False),
                 (-200.0, 100.5, 0, True, False), (-5.0, 127.4, 3, True, True), (-1.5, -2.5, 0, True, False)]
        code, ubo = [], b''
        for i, (a, b, rnd, s8, relu) in enumerate(cases):
            ubo += struct.pack('<II', f32(a if a is not None else 0.0), f32(b))
            code += [mov_c(1, 3, 8 * i), mov_c(2, 3, 8 * i + 4), f2ip(0, RZ if a is None else 1, 2, rnd, s8, relu), mov_i(254, i), store(254, 0)]
        sp, _ = self.translate('f2ip', code + [exit_()])
        got, _ = self.run_compute(sp, ubo, out_words=len(cases))
        for i, (a, b, rnd, s8, relu) in enumerate(cases):
            want = f2ip_ref(b, rnd, s8, relu) | (0 if a is None else f2ip_ref(a, rnd, s8, relu) << 8)
            self.assertEqual(got[i], want, f'case {i}: F2IP of {a}, {b} rounding {rnd} S8 {s8} RELU {relu}: {got[i]:#x}, want {want:#x}')

    def test_f2ip_refuses_what_is_not_known(self):
        for name, i in (('rc', inst(0x243 | PT << 12 | 0 << 16 | 1 << 24 | 2 << 32, 3 | 1 << 10)), ('no_ntz', f2ip(0, 1, 2, ntz=False)), ('round1', f2ip(0, 1, 2, rnd=1))):
            with self.subTest(form=name):
                (self.tmp / f'r_{name}.bin').write_bytes(i + exit_())
                job = {'key': f'r_{name}', 'stage': 'cs', 'arch': 'sm86', 'code': str(self.tmp / f'r_{name}.bin'), 'local': [1, 1, 1], 'shared': 0}
                (self.tmp / f'r_{name}.json').write_text(json.dumps([job]))
                r = subprocess.run([XLATE, str(self.tmp / f'r_{name}.json'), str(self.tmp / 'out')], capture_output=True, text=True)
                self.assertIn('F2IP form other than', r.stdout + r.stderr)

    def test_atomg_cas_swaps_only_on_a_match_and_returns_the_old_word(self):
        # storage buffer slot 0's address from NVN's compute table (c[0] + 0x310), as the game's programs load it; word 0 matches, word 1 does not
        code = [mov_c(2, 0, 0x310), mov_c(3, 0, 0x314), mov_c(4, 3, 0), mov_c(5, 3, 4), atomg_cas(0, 2, 4, 5, 0), mov_i(254, 0), store(254, 0),
                mov_c(6, 3, 8), mov_c(7, 3, 12), atomg_cas(1, 2, 6, 7, 4), mov_i(254, 1), store(254, 1), exit_()]
        sp, res = self.translate('cas', code)
        self.assertEqual(len(res['sbufs']), 1, res)
        ops = {x[0] & 0xffff for x in spv.Module(sp.read_bytes()).ins}
        self.assertIn(230, ops, 'OpAtomicCompareExchange')
        got, mem = self.run_compute(sp, struct.pack('<4I', 7, 9, 6, 1), ssbo=struct.pack('<4I', 7, 5, 0, 0), out_words=2)
        self.assertEqual(got, [7, 5], 'the old words')
        self.assertEqual(mem[:2], [9, 5], 'word 0 swapped (7 == 7), word 1 kept (5 != 6)')

    def test_layer_query_of_an_untyped_texture_is_a_2d_array(self):
        # TXQ R0..R2 (width, height, layers) of texture slot 4 (handle 0x10) that nothing samples, as Ocarina of Time's Effect shaders read a flipbook's layer count
        # (the width and height also go through Ryujinx's resolution scale in the support buffer, which this test leaves 0: only the layer count is compared)
        code = [mov_i(1, 0), txq(0, 1, 0x10, 0x4), mov_i(254, 0), store(254, 0), exit_()]
        sp, res = self.translate('txq', code)
        m = spv.Module(sp.read_bytes())
        dims = [e['dim'] for e in m.resources() if e['desc'] == spv.VK_COMBINED]
        self.assertEqual(len(dims), 1); self.assertTrue(dims[0]['array'], dims)
        got, _ = self.run_compute(sp, out_words=1, tex=(4, 2, 3, 1))
        self.assertEqual(got, [3], 'a 2D array image of 3 layers')
        got, _ = self.run_compute(sp, out_words=1, tex=(4, 2, 1, 1))
        self.assertEqual(got, [1], 'a 2D image (a 2D array view of one layer): the 1 of a 2D header')

    def test_layer_query_of_another_shape_is_valid(self):
        # The answer table types the slot multisampled or cube: the size query's vector is shorter than the component read, which reads 1 (translate() runs spirv-val)
        for t in ('2dms', 'cube'):
            with self.subTest(type=t):
                sp, _ = self.translate(f'txq_{t}', [mov_i(1, 0), txq(0, 1, 0x10, 0x4), mov_i(254, 0), store(254, 0), exit_()], tex={'4': t})
                self.assertTrue(sp.exists())

    def test_width_query_stays_2d(self):
        sp, _ = self.translate('txq_w', [mov_i(1, 0), txq(0, 1, 0x10, 0x1), mov_i(254, 0), store(254, 0), exit_()])
        dims = [e['dim'] for e in spv.Module(sp.read_bytes()).resources() if e['desc'] == spv.VK_COMBINED]
        self.assertEqual([d['array'] for d in dims], [False])

    def test_task_reads_back_its_task_memory(self):
        # ISBEWR.O.ATTR.32 [0x10], R4 (0x927 mode 0xb800), ISBERD.O.ATTR.32 R5, [0x10] (0x923 mode 0xb800), ISBEWR [0x4], R5: Ocarina of Time's task 5d3a096025e375a1
        st = lambda off, rb: inst(0x927 | PT << 12 | RZ << 24 | rb << 32 | off << 40, 0xb800)
        ld = lambda rd, off: inst(0x923 | PT << 12 | rd << 16 | RZ << 24 | off << 40, 0xb800)
        sp, _ = self.translate('task', [mov_i(4, 3), st(0x10, 4), ld(5, 0x10), st(0x4, 5), exit_()], stage='ts', mesh=[0, 0, 128], img={})
        m = spv.Module(sp.read_bytes())
        self.assertTrue(any(sc == 5402 for _, (_, sc) in m.vars.items()), 'a TaskPayloadWorkgroupEXT variable')

    def test_mesh_point_size_plane_is_dropped(self):
        # five planes (point size, then position x y z w) as Ocarina of Time's Foliage mesh programs write them: the layout's -3 plane has no output
        st = lambda off, rb: inst(0x927 | PT << 12 | RZ << 24 | rb << 32 | off << 40, 0xf800)
        code = [mov_i(r, f32(v)) for r, v in ((0, 2.0), (1, 0.25), (2, 0.5), (3, 0.75), (4, 1.0))] + [st(0x80 * p, p) for p in range(5)] + [exit_()]
        sp, _ = self.translate('mesh', code, stage='ms', mesh=[32, 32, 0, -3, 0, 1, 2, 3], local=[32, 1, 1], img={})
        dis = subprocess.run(['spirv-dis', str(sp)], capture_output=True, text=True).stdout if shutil.which('spirv-dis') else ''
        if not dis: self.skipTest('no spirv-dis')
        self.assertIn('BuiltIn Position', dis); self.assertNotIn('BuiltIn PointSize', dis)


if __name__ == '__main__':
    unittest.main()
