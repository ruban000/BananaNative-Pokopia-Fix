"""Translate synthetic formatted Ampere image reads/writes; no game files.

Run with XLATE pointing to a translator built from the current source and
SPIRV_VAL (or spirv-val on PATH) for Vulkan validation.
"""
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


def surface(op, handle, bank=2):
    # PT, R0 value/destination, R254 coordinates (the second coordinate is RZ),
    # direct c[bank][handle*4], 2D, component R. No captured game instructions.
    return (op | 7 << 12 | 254 << 24 | handle << 40 | bank << 54 | 3 << 61 | 1 << 72 | (0 if op == 0xb97 else 7) << 110 | (0 if op == 0xb97 else 1) << 116).to_bytes(16, 'little')


@unittest.skipUnless(XLATE and Path(XLATE).is_file(), 'set XLATE to the freshly built translator')
class ImageFormats(unittest.TestCase):
    def test_formatted_images_keep_known_numeric_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            jobs = []
            cases = {'uint': ('R32Uint', 2), 'sint': ('R32Sint', 2),
                     'float': ('R32Float', 2), 'unorm': ('R8G8B8A8Unorm', 2),
                     'r8uint': ('R8Uint', 2), 'unknown': (None, 2),
                     'other_bank': ('R32Uint', 1)}
            for key, (fmt, bank) in cases.items():
                code = surface(0xb97, 0x48, bank) + surface(0xb9b, 0x4a, bank)
                code += (0x94d | 7 << 12 | 7 << 87).to_bytes(16, 'little')  # EXIT PT
                (p / f'{key}.bin').write_bytes(code)
                jobs.append({'key': key, 'stage': 'cs', 'arch': 'sm86',
                             'code': str(p / f'{key}.bin'), 'tex': {},
                             'img': {str(i): {'fmt': fmt} for i in (0, 1)} if fmt else {},
                             'local': [1, 1, 1], 'shared': 0, 'debug': True})
            (p / 'jobs.json').write_text(json.dumps(jobs))
            r = subprocess.run([XLATE, str(p / 'jobs.json'), str(p / 'out')], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for key, (fmt, bank) in cases.items():
                with self.subTest(case=key):
                    expected = fmt if bank == 2 and fmt else 'Unknown'
                    res = json.loads((p / 'out' / f'{key}.res.json').read_text())
                    self.assertEqual([im['format'] for im in res['images']], [expected] * 2)
                    m = spv.Module((p / 'out' / f'{key}.spv').read_bytes())
                    image_types = [x for x in m.types.values() if x[0] & 0xffff == spv.OP_TYPE_IMAGE]
                    self.assertTrue(image_types)
                    for im in image_types:
                        sample = m.types[im[2]]
                        is_int = expected.endswith(('Uint', 'Sint'))
                        self.assertEqual(sample[0] & 0xffff, spv.OP_TYPE_INT if is_int else spv.OP_TYPE_FLOAT)
                        if is_int:
                            self.assertEqual(sample[3], int(expected.endswith('Sint')))
                        self.assertEqual(im[8] == 0, expected == 'Unknown')
                    ops = {x[0] & 0xffff for x in m.ins}
                    self.assertTrue({spv.OP_IMAGE_READ, spv.OP_IMAGE_WRITE} <= ops)
                    # Production Vulkan lowering supplies formatless capabilities.
                    spv.formatless_storage_caps(m)
                    if expected == 'Unknown':
                        self.assertTrue({55, 56} <= set(m.caps))
                    out = p / f'{key}.spv'
                    out.write_bytes(m.data())
                    if VAL:
                        r = subprocess.run([VAL, '--target-env', 'vulkan1.3', str(out)], capture_output=True, text=True)
                        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
