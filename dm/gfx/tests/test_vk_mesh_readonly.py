"""A writable mesh arena does not authorize a guest-buffer alias assumption."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import vk_android_spv as spv
from vk_mesh_readonly import mark_inputs, mark_output_writeonly

SHADER = '''#version 450
layout(local_size_x=32) in;
layout(set=0,binding=4,std430) buffer InputA { uint v[]; } a;
layout(set=0,binding=5,std430) buffer InputB { uint v[]; } b;
layout(set=3,binding=0,std430) buffer HostOutput { uint v[]; } outbuf;
shared uint local_words[32];
@FUNCTION@
void main() { uint i=gl_LocalInvocationIndex;
local_words[i]=a.v[i]; barrier(); @BODY@ outbuf.v[i]=local_words[i]+b.v[i]; }
'''


@unittest.skipUnless(shutil.which('glslangValidator') and shutil.which('spirv-val'), 'SPIR-V tools required')
class MeshReadonly(unittest.TestCase):
    def build(self, body='', function='', source=SHADER):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root / 'test.comp', root / 'test.spv'
            src.write_text(source.replace('@FUNCTION@', function).replace('@BODY@', body))
            subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.3', str(src), '-o', str(dst)], check=True, capture_output=True)
            return dst.read_bytes()

    def fold(self, before):
        return mark_inputs(before, {(3, 0)}, {(0, 4), (0, 5)})

    def test_guest_reads_and_host_local_writes(self):
        before = self.build('atomicAdd(outbuf.v[i], 1u);')
        after, marked, _ = self.fold(before)
        self.assertEqual(marked, [{'set': 0, 'binding': 4}, {'set': 0, 'binding': 5}])
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'test.spv'
            p.write_bytes(after)
            subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(p)], check=True)
        m = spv.Module(after)
        m.ins = [x for x in m.ins if not (x[0] & 65535 == 71 and x[2] == 24)]
        self.assertEqual(m.data(), before)  # Every executable word is unchanged.
        self.assertEqual(self.fold(after)[0], after)

    def test_guest_store_and_atomic_refuse_all_inputs(self):
        for body in ('a.v[i]=2u;', 'atomicAdd(b.v[i], 1u);'):
            before = self.build(body)
            after, marked, _ = self.fold(before)
            self.assertEqual((after, marked), (before, []))

    def test_callee_store_and_unknown_output(self):
        before = self.build('write_guest(i);', 'void write_guest(uint i) { b.v[i]=1u; }')
        self.assertEqual(self.fold(before)[:2], (before, []))
        before = self.build()
        self.assertEqual(mark_inputs(before, {(3, 1)}, {(0, 4)})[:2], (before, []))
        self.assertEqual(mark_inputs(before, {(3, 0)}, {(3, 0)})[:2], (before, []))

    def test_physical_write_refused(self):
        source = SHADER.replace('#version 450', '#version 450\n#extension GL_EXT_buffer_reference : require')
        source = source.replace('shared uint', 'layout(buffer_reference,std430) buffer Physical { uint v; };\nlayout(push_constant) uniform P { Physical p; } pc;\nshared uint')
        before = self.build('pc.p.v=1u;', source=source)
        self.assertEqual(self.fold(before)[:2], (before, []))

    def test_coherent_and_unknown_operation_refused(self):
        before = self.build(source=SHADER.replace('buffer InputA', 'coherent buffer InputA'))
        self.assertEqual(self.fold(before)[:2], (before, []))
        source = SHADER.replace('#version 450', '#version 450\n#extension GL_KHR_shader_subgroup_basic : require')
        before = self.build('if (subgroupElect()) outbuf.v[0]=0u;', source=source)
        self.assertEqual(self.fold(before)[:2], (before, []))

    def test_extended_instruction_output_refused(self):
        before = self.build('float ignored=modf(1.5, b.v[i]);', source=SHADER.replace('uint v[]', 'float v[]').replace('shared uint', 'shared float'))
        self.assertEqual(self.fold(before)[:2], (before, []))

    def test_store_only_host_output(self):
        before = self.build('atomicAdd(a.v[i], 1u);')
        after, marked, _ = mark_output_writeonly(before, {(3, 0)})
        self.assertEqual(marked, [{'set': 3, 'binding': 0, 'stores': 1}])
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'test.spv'
            p.write_bytes(after)
            subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(p)], check=True)
        m = spv.Module(after)
        m.ins = [x for x in m.ins if not (x[0] & 65535 == 71 and x[2] == 25)]
        self.assertEqual(m.data(), before)
        self.assertEqual(mark_output_writeonly(after, {(3, 0)})[0], after)

    def test_output_load_or_atomic_refused(self):
        for body in ('local_words[i]+=outbuf.v[i];', 'atomicAdd(outbuf.v[i], 1u);'):
            before = self.build(body)
            self.assertEqual(mark_output_writeonly(before, {(3, 0)})[:2], (before, []))

    def test_output_pointer_passed_to_function_refused(self):
        before = self.build('put(outbuf.v[i]);', 'void put(inout uint v) { v=1u; }')
        self.assertEqual(mark_output_writeonly(before, {(3, 0)})[:2], (before, []))


if __name__ == '__main__':
    unittest.main()
