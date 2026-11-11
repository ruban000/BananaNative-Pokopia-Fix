"""Synthetic mesh/task lowering fixtures; no game data or proprietary driver needed.

python3 -m unittest discover -s dm/gfx/tests -p test_vk_android_mesh.py
The companion fixtures/vk_compute_mesh/gpu.c runs the three lowered fixtures on an
Android Vulkan compute queue and checks output words, payloads and guard words.
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'dm/gfx/tools'))
import vk_android_mesh as mesh
import vk_android_spv as spv
import vk_mesh_bound as bound

FIXTURES = pathlib.Path(__file__).parent / 'fixtures/vk_compute_mesh'


class ReviewedTaskBound(unittest.TestCase):
    def test_only_the_reviewed_module_uses_the_bound(self):
        native = b'reviewed module'
        certificate = (hashlib.sha256(native).hexdigest(), 16)
        self.assertEqual(bound.reviewed_bound(native, certificate), 16)
        self.assertEqual(bound.reviewed_bound(native + b'\x00', certificate), 0)
        self.assertEqual(bound.reviewed_bound(native, None), 0)
        self.assertEqual(bound.profile_bounds('../games'), ({}, ''))


@unittest.skipUnless(shutil.which('glslangValidator') and shutil.which('spirv-val') and shutil.which('spirv-as'), 'SPIR-V tools unavailable')
class ComputeMesh(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def compile(self, name, text=None):
        source = self.out / name
        source.write_text(text if text is not None else (FIXTURES / name).read_text())
        dst = source.with_name(name + '.spv')
        subprocess.run(['glslangValidator', '-V', '--target-env', 'vulkan1.2', str(source), '-o', str(dst)], check=True, capture_output=True)
        return dst.read_bytes()

    def validate(self, data, name):
        p = self.out / (name + '.spv'); p.write_bytes(data)
        subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(p)], check=True, capture_output=True)

    def lowered(self, name):
        native = self.compile(name)
        lowered, metadata = mesh.lower(native)
        self.validate(lowered, name + '.compute')
        module = spv.Module(lowered)
        self.assertEqual(module.model, 'cs')
        self.assertNotIn(5283, module.caps)
        self.assertNotIn('SPV_EXT_mesh_shader', module.exts)
        self.assertFalse(any(sc == 3 for ptr,sc in module.vars.values()))
        self.assertEqual({b['binding'] for b in module.resources() if b['set'] == 3}, {0,1,2,3})
        # Output arrays must not consume the native program's already-tight LDS budget.
        original = spv.Module(native)
        shared = sum(sc == 4 or (original.model == 'ts' and sc == 5402)
                     for ptr,sc in original.vars.values())
        self.assertEqual(sum(sc == 4 for ptr,sc in module.vars.values()), shared)
        return native, lowered, metadata

    def test_task_payload_and_shader_capability(self):
        native, lowered, metadata = self.lowered('payload.task')
        self.assertEqual(metadata['payload_words'], 64)
        self.assertIn(1, spv.Module(lowered).caps)
        self.assertTrue(any(x[0] & 65535 == 5294 for x in spv.Module(native).ins))
        self.assertFalse(any(x[0] & 65535 in (5294,5295) for x in spv.Module(lowered).ins))

    def test_triangle_vertex_and_flat_fragment(self):
        native, lowered, metadata = self.lowered('payload.mesh')
        source = mesh.vertex_source(metadata)
        self.assertIn('flat out vec4 attr0', source)
        self.assertIn('invariant gl_Position', source)
        original = spv.Module(native)
        separate = [x[2] for x in original.ins if x[0]&65535 in (129,131,133,136)]
        self.assertGreater(len(separate), 0)
        result = spv.Module(lowered)
        self.assertTrue(all(42 in result.decos.get(i,{}) for i in separate))
        self.assertEqual(sum(x[0]&65535 == 224 for x in result.ins),
                         sum(x[0]&65535 == 224 for x in original.ins))
        self.validate(self.compile('generated.vert', source), 'generated')
        fragment = self.compile('input.frag', '''#version 460
#extension GL_EXT_mesh_shader : require
layout(location=0) perprimitiveEXT in vec4 attr;
layout(location=0) out vec4 color;
void main(){color=attr;}
''')
        flat = mesh.fragment_flat(fragment); self.validate(flat, 'flat')
        m = spv.Module(flat)
        self.assertNotIn(5283, m.caps)
        self.assertTrue(any(14 in d for d in m.decos.values()))
        self.assertFalse(any(5271 in d for d in m.decos.values()))

    def test_mesh_whole_payload_load_keeps_shared_copy(self):
        source = (FIXTURES / 'payload.mesh').read_text()
        source = source.replace('void main() {', 'void main() { uint localCopy[64] = payload;')
        source = source.replace('payload[i]', 'localCopy[i]')
        native = self.compile('whole.mesh', source)
        lowered, _ = mesh.lower(native)
        self.validate(lowered, 'whole_compute')
        module = spv.Module(lowered)
        self.assertTrue(any(sc == 4 for ptr,sc in module.vars.values()))

    def test_points_keep_size_indices_and_culling(self):
        native, lowered, metadata = self.lowered('points.mesh')
        source = mesh.vertex_source(metadata)
        self.assertIn('gl_PointSize=uintBitsToFloat', source)
        self.assertIn('!= 0u) return', source)
        self.validate(self.compile('points.vert', source), 'points_vertex')

    def test_vector_reads_preserve_layout_and_scalar_guards(self):
        import copy
        for fixture in ('payload.mesh', 'points.mesh'):
            _, _, metadata = self.lowered(fixture)
            before = copy.deepcopy(metadata)
            scalar = mesh.vertex_source(metadata)
            vector = mesh.vertex_source(metadata, True)
            self.assertEqual(metadata, before)
            self.assertIn('if (p >= o.w[b+1u]) return;', vector)
            self.assertIn('if (v >= o.w[b]) return;', vector)
            self.validate(self.compile('vector.vert', vector), 'vector_vertex')
            # An odd record stride cannot prove alignment across workgroups.
            odd = copy.deepcopy(metadata)
            odd['record_words'] |= 1
            self.assertEqual(mesh.vertex_source(odd, True), mesh.vertex_source(odd))
            self.assertNotIn('ov.v[', scalar)
            if metadata['record_words'] % 4 == 0:
                self.assertIn('ov.v[', vector)

    def test_bypassed_predicate_spill_has_defined_entry_value(self):
        source = FIXTURES / 'predicate.mesh.spvasm'
        binary = self.out / 'predicate.spv'
        subprocess.run(['spirv-as', '--target-env', 'vulkan1.3', str(source), '-o', str(binary)], check=True, capture_output=True)
        native = spv.Module(binary.read_bytes())
        locals_before = [i for i,(p,sc) in native.vars.items() if sc==7 and native.op(native.pointee(p)[1])==20]
        self.assertEqual(len(locals_before), 1)
        lowered, metadata = mesh.lower(binary.read_bytes())
        self.validate(lowered, 'predicate')
        module = spv.Module(lowered)
        variable = next(x for x in module.ins if x[0]&65535==59 and x[2]==locals_before[0])
        self.assertEqual(len(variable), 5)
        self.assertTrue(any(x[0]&65535==42 and x[2]==variable[4] for x in module.ins))
        # Keep the conditional true write and the actual acceptance computation.
        self.assertTrue(any(x[0]&65535==166 for x in module.ins))
        self.assertTrue(any(x[0]&65535==62 and x[1]==variable[2] for x in module.ins))
        self.validate(self.compile('predicate.vert', mesh.vertex_source(metadata)), 'predicate_vertex')
        # An explicit entry initializer belongs to the input program and wins.
        true = next(x[2] for x in native.ins if x[0]&65535==41)
        for at,x in enumerate(native.ins):
            if x[0]&65535==59 and x[2]==locals_before[0]: native.ins[at]=mesh.ins(59,*x[1:],true)
        preserved, _ = mesh.lower(native.data())
        variable = next(x for x in spv.Module(preserved).ins if x[0]&65535==59 and x[2]==locals_before[0])
        self.assertEqual(variable[4], true)

    def test_struct_payload_is_refused(self):
        src = (FIXTURES / 'payload.task').read_text()
        src = src.replace('taskPayloadSharedEXT uint payload[64];', 'struct Payload { uint words[64]; }; taskPayloadSharedEXT Payload payload;')
        src = src.replace('payload[i]', 'payload.words[i]')
        data = self.compile('struct.task', src)
        with self.assertRaisesRegex(ValueError, 'payload must be uint array'): mesh.lower(data)

    def test_lines_are_refused(self):
        src = (FIXTURES / 'payload.mesh').read_text().replace('triangles,max_vertices=3', 'lines,max_vertices=3')
        src = src.replace('gl_PrimitiveTriangleIndicesEXT[0]=uvec3(0,1,2)', 'gl_PrimitiveLineIndicesEXT[0]=uvec2(0,1)')
        data = self.compile('lines.mesh', src)
        with self.assertRaisesRegex(ValueError, 'only point and triangle'): mesh.lower(data)

    @unittest.skipUnless(shutil.which('spirv-opt'), 'spirv-opt unavailable')
    def test_scheduler_only_accepts_a_proven_emit_range(self):
        task = '''#version 460
#extension GL_EXT_mesh_shader : require
layout(local_size_x=32) in;
taskPayloadSharedEXT uint payload[64];
void main() {
    if (gl_LocalInvocationIndex == 0) payload[1] = 0;
    barrier();
    if (gl_LocalInvocationIndex == 0) {
        uint n = uint(bitCount(gl_LocalInvocationIndex));
        uint slot = ((n * 4u + 16u) & 255u) >> 2u;
        payload[slot] = gl_WorkGroupID.x;
        payload[1] = n;
    }
    barrier();
    uint count = payload[1];
    EmitMeshTasksEXT(count < 65535u ? count : 65535u, 1, 1);
}
'''
        self.assertEqual(bound.prove(self.compile('bounded.task', task), self.out), 32)
        alias = task.replace('((n * 4u + 16u) & 255u) >> 2u', 'gl_WorkGroupID.x & 63u')
        self.assertEqual(bound.prove(self.compile('unknown.task', alias), self.out), 0)
        missing = task.replace('if (gl_LocalInvocationIndex == 0) payload[1] = 0;', '')
        self.assertEqual(bound.prove(self.compile('uninitialized.task', missing), self.out), 0)
        multi = task.replace('65535u, 1, 1', '65535u, 2, 1')
        self.assertEqual(bound.prove(self.compile('multidimensional.task', multi), self.out), 0)


if __name__ == '__main__': unittest.main()
