"""GMEM diagnostics must preserve the original executable instructions and ABI."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
sys.path.insert(0, str(HERE))
import vk_android_spv as spv
from vk_mesh_gmem_probe import helpers, instrument
from test_vk_gmem_cache import SHADER


@unittest.skipUnless(shutil.which('glslangValidator') and shutil.which('spirv-val'), 'SPIR-V tools required')
class GmemProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = SHADER.replace('void main()', '''
layout(set=3,binding=0,std430) buffer MeshOutput { uint w[]; } meshout;
uint record_base;
void init_mesh() {
    record_base=gl_WorkGroupID.x*64u;
    meshout.w[record_base]=gl_LocalInvocationIndex;
}
void main()''').replace('uint lo = io.w[0]', 'init_mesh(); uint lo = io.w[0]')
        with tempfile.TemporaryDirectory() as directory:
            src, dst = Path(directory) / 's.comp', Path(directory) / 's.spv'
            src.write_text(source)
            subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.3', str(src), '-o', str(dst)], check=True, capture_output=True)
            m = spv.Module(dst.read_bytes())
            spv.promote_locals(m)
            spv.loop_pool_search(m)
            table = next(r['id'] for r in m.resources() if (r['set'], r['binding']) == (0, 0))
            spv.cache_raw_loads(m, {table})
            cls.before = m.data()

    def validate(self, data):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 's.spv'
            p.write_bytes(data)
            subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(p)], check=True, capture_output=True)

    def test_original_instructions_and_geometry_abi_preserved(self):
        for mode in ('cache', 'hint'):
            after, report = instrument(self.before, 64, mode)
            self.validate(after)
            before, got = spv.Module(self.before), spv.Module(after)
            self.assertEqual(before.resources(), got.resources())
            # Every old function instruction remains, in its original order.
            a = next(i for i, x in enumerate(before.ins) if x[0] & 65535 == 54)
            it = iter(got.ins)
            for x in before.ins[a:]:
                self.assertTrue(any(y == x for y in it), x)
            self.assertEqual(len(report['helpers']), 3)
            self.assertEqual(report['returns'], 1)
            self.assertEqual(sum(x[0] & 65535 == 234 for x in got.ins), 2)

    def test_unknown_abi_and_mode_rejected(self):
        for words, mode in ((65, 'cache'), (64, 'unknown')):
            with self.assertRaises(ValueError):
                instrument(self.before, words, mode)

    def test_unknown_search_shape_rejected(self):
        m = spv.Module(self.before)
        h = helpers(m)[0]
        a, b = m.function_range(h['function'])
        k = next(i for i in range(a, b) if m.ins[i][0] & 65535 == 246)
        del m.ins[k]
        with self.assertRaisesRegex(ValueError, 'complete binary search'):
            instrument(m.data(), 64, 'cache')

    def test_second_instrumentation_refused(self):
        after, _ = instrument(self.before, 64, 'cache')
        with self.assertRaises(ValueError):
            instrument(after, 64, 'cache')


if __name__ == '__main__':
    unittest.main()
