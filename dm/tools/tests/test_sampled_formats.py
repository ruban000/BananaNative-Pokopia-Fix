"""Synthetic Ampere sampled-image numeric types and raw register bits; no game files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'dm/gfx/tools'))
import vk_android_spv as spv

XLATE = os.environ.get('XLATE')
VAL = os.environ.get('SPIRV_VAL') or shutil.which('spirv-val')


def fetch(handle, bank=2, opcode=0xb66):
    # PT, R0 result, R254/RZ coordinates, no second destination, 2D,
    # component R, no residency predicate, level zero. TEX and TLD share fields.
    return (opcode | 7 << 12 | 254 << 24 | handle << 40 | bank << 54 |
            1 << 61 | 255 << 64 | 1 << 72 | 7 << 81 | (0 if opcode == 0xb63 else 1) << 87).to_bytes(16, 'little')


def store():
    # Store R0 bits into image slot 0, forcing the fetch to remain live.
    return (0xb9b | 7 << 12 | 254 << 24 | 0x48 << 40 | 2 << 54 |
            3 << 61 | 1 << 72 | 7 << 110 | 1 << 116).to_bytes(16, 'little')


@unittest.skipUnless(XLATE and Path(XLATE).is_file(), 'set XLATE to the freshly built translator')
class SampledFormats(unittest.TestCase):
    def test_sampled_types_and_register_bits(self):
        assert XLATE is not None
        cases = {'uint': ('R8Uint', 2, 2, 0xb66),
                 'sint': ('R8Sint', 2, 2, 0xb66),
                 'float': ('R32Float', 2, 2, 0xb66),
                 'unorm': ('R8Unorm', 2, 2, 0xb66),
                 'unknown': (None, 2, 2, 0xb66),
                 'other_bank': ('R8Uint', 1, 2, 0xb66),
                 'overlap': ('R8Sint', 2, 32, 0xb66),
                 'uint_tex': ('R8Uint', 2, 2, 0xb60),
                 'uint_gather': ('R8Uint', 2, 2, 0xb63),
                 'sint_gather': ('R8Sint', 2, 2, 0xb63)}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            jobs = []
            for key, (fmt, bank, slot, opcode) in cases.items():
                handle = (0x20 + slot * 8) // 4
                code = fetch(handle, bank, opcode) + store()
                code += (0x94d | 7 << 12 | 7 << 87).to_bytes(16, 'little')
                (p / f'{key}.bin').write_bytes(code)
                jobs.append({'key': key, 'stage': 'cs', 'arch': 'sm86',
                             'code': str(p / f'{key}.bin'),
                             'tex': {str(slot): {'fmt': fmt}} if fmt else {},
                             'img': {'0': {'fmt': 'R32Uint'}},
                             'local': [1, 1, 1], 'shared': 0, 'debug': True})
            (p / 'jobs.json').write_text(json.dumps(jobs))
            r = subprocess.run([XLATE, str(p / 'jobs.json'), str(p / 'out')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for key, (fmt, bank, slot, opcode) in cases.items():
                with self.subTest(case=key):
                    expected = fmt if bank == 2 and fmt else 'Unknown'
                    m = spv.Module((p / 'out' / f'{key}.spv').read_bytes())
                    images = [x for x in m.types.values() if x[0] & 0xffff == spv.OP_TYPE_IMAGE]
                    sampled = [x for x in images if x[7] == 1]
                    self.assertEqual(len(sampled), 1)
                    sample = m.types[sampled[0][2]]
                    is_int = expected.endswith(('Uint', 'Sint'))
                    self.assertEqual(sample[0] & 0xffff, spv.OP_TYPE_INT if is_int else spv.OP_TYPE_FLOAT)
                    if is_int:
                        self.assertEqual(sample[3], int(expected.endswith('Sint')))
                    self.assertEqual(sampled[0][8], 0, 'sampled images keep Unknown Image Format')
                    # Same handle word can mean sampled slot 32 or storage slot 0.
                    storage = [x for x in images if x[7] == 2]
                    self.assertEqual(len(storage), 1)
                    self.assertEqual(m.types[storage[0][2]][3], 0, 'storage remains unsigned')
                    ops = {x[0] & 0xffff for x in m.ins}
                    self.assertIn({0xb66: 95, 0xb60: 88, 0xb63: 96}[opcode], ops)
                    self.assertIn(spv.OP_IMAGE_WRITE, ops)
                    # Follow fetched bits through locals, extraction and bitcasts
                    # to the store. Coordinate rescaling may legitimately convert
                    # numeric types in a separate helper function.
                    raw = set()
                    stored = False
                    for ins in m.ins:
                        op = ins[0] & 0xffff
                        if op in (88, 95, 96):
                            raw.add(ins[2])
                        elif op == 62 and ins[2] in raw:  # OpStore local
                            raw.add(ins[1])
                        elif op in (61, 81, 83, 124) and ins[3] in raw:
                            raw.add(ins[2])
                        elif op == 80 and any(x in raw for x in ins[3:]):
                            raw.add(ins[2])
                        elif op in (109, 110, 111, 112):
                            self.assertNotIn(ins[3], raw, 'fetched register bits numerically converted')
                        elif op == spv.OP_IMAGE_WRITE:
                            stored |= ins[3] in raw
                    self.assertTrue(stored, 'the fetched bits reach the image store')
                    if VAL:
                        r = subprocess.run([VAL, '--target-env', 'vulkan1.3', str(p / 'out' / f'{key}.spv')], capture_output=True, text=True)
                        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
