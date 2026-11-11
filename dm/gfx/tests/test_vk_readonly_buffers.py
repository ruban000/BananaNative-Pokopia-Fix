"""A read-only module proof, including writes through aliases and callees."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from vk_readonly_buffers import mark_readonly  # noqa: E402
import vk_android_spv as spv  # noqa: E402

SHADER = '''#version 450
layout(set=0,binding=0,std430) buffer A { uint v[]; } a;
layout(set=0,binding=1,std430) buffer B { uint v[]; } b;
layout(location=0) out vec4 color;
@FUNCTION@
void main() { uint local = a.v[0]; @BODY@ color = vec4(float(local + b.v[0])); }
'''


@unittest.skipUnless(shutil.which('glslangValidator') and shutil.which('spirv-val'), 'SPIR-V tools required')
class ReadonlyBuffers(unittest.TestCase):
    def build(self, body='', function='', shader=SHADER):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source, binary = path / 'test.frag', path / 'test.spv'
            source.write_text(shader.replace('@FUNCTION@', function).replace('@BODY@', body))
            subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.3', str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            before = binary.read_bytes()
            after, marked, reason = mark_readonly(before)
            binary.write_bytes(after)
            subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(binary)], check=True)
            return before, after, marked, reason

    def test_reads_and_local_stores(self):
        before, after, marked, _ = self.build('local += 2u;')
        self.assertEqual(marked, [{'set': 0, 'binding': 0}, {'set': 0, 'binding': 1}])
        self.assertEqual(len(after), len(before) + 24)
        self.assertEqual(mark_readonly(after)[0], after)
        m = spv.Module(after)
        m.ins = [x for x in m.ins if not (x[0] & 65535 == 71 and x[2] == 24)]
        self.assertEqual(m.data(), before)  # The executable instructions are identical.

    def test_one_store_refuses_even_the_other_buffer(self):
        before, after, marked, _ = self.build('b.v[0] = local;')
        self.assertEqual(before, after)
        self.assertEqual(marked, [])

    def test_callee_store(self):
        before, after, marked, _ = self.build('write_value(local);', 'void write_value(uint x) { a.v[1] = x; }')
        self.assertEqual(before, after)
        self.assertEqual(marked, [])

    def test_atomic(self):
        before, after, marked, _ = self.build('local += atomicAdd(b.v[1], 1u);')
        self.assertEqual(before, after)
        self.assertEqual(marked, [])

    def test_coherent_read_refused(self):
        before, after, marked, _ = self.build(shader=SHADER.replace('buffer A', 'coherent buffer A'))
        self.assertEqual(before, after)
        self.assertEqual(marked, [])

    def test_unknown_operation_refused(self):
        shader = SHADER.replace('#version 450', '#version 450\n#extension GL_KHR_shader_subgroup_basic : require')
        before, after, marked, _ = self.build('if (subgroupElect()) local += 1u;', shader=shader)
        self.assertEqual(before, after)
        self.assertEqual(marked, [])

    def test_extended_instruction_copy_out(self):
        shader = SHADER.replace('uint', 'float').replace('local + b.v[0]', 'local + b.v[0]')
        before, after, marked, _ = self.build('local = modf(local, b.v[1]);', shader=shader)
        self.assertEqual(before, after)
        self.assertEqual(marked, [])


if __name__ == '__main__':
    unittest.main()
