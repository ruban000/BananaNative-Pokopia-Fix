"""Immutable table folding must prove both indices and retain all payload bits."""
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import vk_android_spv as spv
from vk_c1_lookup import Bounds, fold_c1, MAX32

SHADER = '''#version 450
layout(set=0,binding=8,std140) uniform C1 { vec4 table[16]; } c1;
layout(location=0) flat in uint input_index;
layout(location=0) out vec4 color;
void main() { uint i = INDEX; color = vec4(c1.table[i >> 2u][i & 3u]); }
'''
PAYLOAD = struct.pack('<8I', 17568, 19200, 0x80000000, 0x7fc12345,
                      0xffffffff, 0, 0x7f800000, 0xff800000)


@unittest.skipUnless(all(shutil.which(t) for t in ('glslangValidator', 'spirv-val', 'spirv-opt')), 'SPIR-V tools required')
class C1Lookup(unittest.TestCase):
    def build(self, expression='min(input_index, 4u)', source=SHADER):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root / 'test.frag', root / 'test.spv'
            src.write_text(source.replace('INDEX', expression))
            subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.1', str(src), '-o', str(dst)], check=True, capture_output=True)
            subprocess.run(['spirv-opt', '--ssa-rewrite', str(dst), '-o', str(dst)], check=True)
            return dst.read_bytes()

    def validate(self, data):
        with tempfile.TemporaryDirectory() as directory:
            dst = Path(directory) / 'test.spv'
            dst.write_bytes(data)
            subprocess.run(['spirv-val', '--target-env', 'vulkan1.1', str(dst)], check=True)

    def test_small_table_and_raw_payload_bits(self):
        before = self.build()
        after, report = fold_c1(before, PAYLOAD, {(0, 8)})
        self.assertEqual(len(report), 1)
        self.assertEqual((report[0]['rows'], report[0]['column_upper']), (2, 3))
        self.validate(after)
        m = spv.Module(after)
        constants = {words[0] for _, words in m.consts.values() if len(words) == 1}
        self.assertTrue(set(struct.unpack('<8I', PAYLOAD)) <= constants)
        # Execute the replacement's integer selection for every admitted index.
        defs = Bounds(m).defs
        load = defs[report[0]['load']]
        self.assertEqual(load[0] & 65535, 124)
        extract = defs[load[3]]
        select = defs[extract[3]]
        self.assertEqual(select[0] & 65535, 169)
        vectors = {x[2]: x[3:] for x in m.ins if x[0] & 65535 == 44}
        compare = defs[defs[select[3]][3]]
        row_one = m.consts[compare[4]][1][0]
        for i in range(5):
            v = select[4] if i >> 2 == row_one else select[5]
            actual = m.consts[vectors[v][i & 3]][1][0]
            self.assertEqual(actual, struct.unpack_from('<I', PAYLOAD, i * 4)[0])
        self.assertEqual(fold_c1(after, PAYLOAD, {(0, 8)}), (after, []))

    def test_unproved_indices_and_overflow_stay_unchanged(self):
        for expr in ('input_index', 'min(input_index, 32u)', '(input_index << 2u) >> 2u'):
            with self.subTest(expr=expr):
                before = self.build(expr)
                self.assertEqual(fold_c1(before, PAYLOAD, {(0, 8)}), (before, []))

    def test_bounds_match_unsigned_chain(self):
        # The translator uses signed bitcasts around logically unsigned shifts.
        before = self.build('uint(int(uint(int(min(input_index, 4u))) << 2)) >> 2u')
        after, report = fold_c1(before, PAYLOAD, {(0, 8)})
        self.assertEqual(len(report), 1)
        self.validate(after)
        rng = random.Random(1)
        for x in [0, 1, 4, 5, 0x7fffffff, 0x80000000, MAX32] + [rng.getrandbits(32) for _ in range(1000)]:
            i = ((min(x, 4) << 2) & MAX32) >> 2
            self.assertLessEqual(i >> 2, report[0]['row_upper'])
            self.assertLessEqual(i & 3, report[0]['column_upper'])

    def test_unknown_binding_and_short_payload(self):
        before = self.build()
        self.assertEqual(fold_c1(before, PAYLOAD, {(0, 9)}), (before, []))
        self.assertEqual(fold_c1(before, PAYLOAD[:16], {(0, 8)}), (before, []))

    def test_wrong_layout_and_array_extent(self):
        before = self.build()
        for kind in ('stride', 'extent', 'offset'):
            m = spv.Module(before)
            for x in m.ins:
                op = x[0] & 65535
                if kind == 'stride' and op == 71 and x[2] == 6:
                    x[3] = 32
                if kind == 'offset' and op == 72 and x[3] == 35:
                    x[4] = 16
                if kind == 'extent' and op == 28:
                    x[3] = m.add_const(m.add_type(21, 32, 0), 1)
                    break
            altered = m.data()
            self.assertEqual(fold_c1(altered, PAYLOAD, {(0, 8)}), (altered, []))

    def test_explicit_memory_operands_untouched(self):
        m = spv.Module(self.build())
        for x in m.ins:
            if x[0] & 65535 == 61:
                x.append(1)  # Volatile
                x[0] += 1 << 16
        before = m.data()
        self.assertEqual(fold_c1(before, PAYLOAD, {(0, 8)}), (before, []))


if __name__ == '__main__':
    unittest.main()
