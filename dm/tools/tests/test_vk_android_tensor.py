"""Guarded Vulkan tensor lowering; synthetic modules only.

Structural and source-label checks complement the native subgroup-64 numerical
suite. These tests do not dispatch 32-lane shuffle operations on CPU subgroup8.
"""
from collections import Counter
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from dm.tools.tests import test_dmsh_hmma_vk as hmma
import vk_android_tensor as tensor

spv = hmma.spv


class TensorIdentity(unittest.TestCase):
    def test_no_helper_is_byte_identical(self):
        raw = struct.pack('<5I', spv.MAGIC, 0x10300, 0, 1, 0)
        m = spv.Module(raw); report = {}
        self.assertEqual(tensor.prepare(m, report), 0)
        self.assertEqual(m.data(), raw)
        self.assertEqual(report, {})

    def test_broadcast_source_labels(self):
        def xor(values, mask): return [values[lane ^ mask] for lane in range(64)]
        def both(values, mask):
            peer = xor(values, mask)
            return ([peer[l] if l & mask else values[l] for l in range(64)],
                    [values[l] if l & mask else peer[l] for l in range(64)])
        def tree(values, low, high):
            a, b = both(values, low)
            aa, ab = both(a, high); ba, bb = both(b, high)
            return aa, ba, ab, bb
        source = list(range(64))
        for j, values in enumerate(tree(source, 1, 2)):
            self.assertEqual(values, [(lane & ~3) | j for lane in range(64)])
        permuted = source
        for a, b in ((0, 3), (1, 4)):
            peer = xor(permuted, (1 << a) | (1 << b))
            permuted = [peer[l] if ((l >> a) ^ (l >> b)) & 1 else permuted[l] for l in range(64)]
        for j, values in enumerate(tree(permuted, 8, 16)):
            for c, column in enumerate(both(values, 4)):
                self.assertEqual(column, [(lane & ~31) | ((lane & 3) * 8) | c * 4 | j for lane in range(64)])


@unittest.skipUnless(hmma.base.XLATE and Path(hmma.base.XLATE).is_file(), 'set XLATE to pinned translator')
class TensorModules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='tensor_guards_'))
        instance = cls()
        path, _ = instance.translate('tensor', hmma.fixture_code(), local=[64, 1, 1], subgroup_size=64)
        cls.original = path.read_bytes()

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.tmp, ignore_errors=True)
    translate = hmma.base.AmpereLowerings.translate

    def module(self): return spv.Module(self.original)
    def helper(self, m): return next(i for i, name in m.names.items() if name == 'dmsh_hmma16816_f16')
    def validate(self, m, name):
        if not hmma.base.VAL: self.skipTest('no spirv-val')
        path = self.tmp / (name + '.spv'); path.write_bytes(m.data())
        result = subprocess.run([hmma.base.VAL, '--target-env', 'vulkan1.3', str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_register_helper_contract(self):
        m = self.module(); report = {}
        self.assertEqual(tensor.prepare(m, report), 1)
        self.assertEqual(report['status'], 'optimized')
        a, b = m.function_range(self.helper(m)); body = m.ins[a:b+1]
        counts = Counter(x[0] & 65535 for x in body)
        self.assertEqual(counts[346], 30)
        for op in (59, 62, 65, 224, 245, 246, 345): self.assertEqual(counts[op], 0)
        self.assertEqual(sum(x[0] & 65535 == 12 and x[4] == 50 for x in body), 64)
        self.validate(m, 'register_helper')

    def test_unproven_caller_preserves_whole_module(self):
        m = self.module(); fid = self.helper(m)
        call = next(x for x in m.ins if x[0] & 65535 == 57 and x[3] == fid)
        # Removing the sole lane initialization is valid SPIR-V with an undefined
        # value, but cannot justify replacing the caller's value by a lane ID.
        m.ins = [x for x in m.ins if not (x[0] & 65535 == 62 and x[1] == call[4])]
        m.index(); self.validate(m, 'uninitialized_caller')
        before = m.data(); report = {}
        self.assertEqual(tensor.prepare(m, report), 0)
        self.assertEqual(m.data(), before)
        self.assertEqual(report['status'], 'unchanged')

    def test_divergent_second_call_preserves_whole_module(self):
        path, _ = self.translate('divergent_tensor', hmma.fixture_code(repeats=2),
                                 local=[64, 1, 1], subgroup_size=64)
        m = spv.Module(path.read_bytes()); fid = self.helper(m)
        u = m.add_type(21, 32, 0); boolean = m.add_type(20)
        sixteen = m.add_const(u, 16)
        lane = next(i for i, d in m.decos.items() if d.get(11) == [41])
        raw, condition, yes, merge = [m.new_id() for _ in range(4)]
        calls = [i for i, x in enumerate(m.ins) if x[0] & 65535 == 57 and x[3] == fid]
        self.assertEqual(len(calls), 2)
        at = calls[1]
        self.assertEqual(m.ins[at+1][0] & 65535, 62)
        # The first full call initializes scratch. Only lanes 0..15 execute
        # the second call, so its original scratch reads can still observe
        # other lanes' earlier values. XOR routing cannot read inactive peers.
        call_and_store = m.ins[at:at+2]
        m.ins[at:at+2] = [
            [4 << 16 | 61, u, raw, lane],
            [5 << 16 | 176, boolean, condition, raw, sixteen],
            [3 << 16 | 247, merge, 0],
            [4 << 16 | 250, condition, yes, merge],
            [2 << 16 | 248, yes], *call_and_store,
            [2 << 16 | 249, merge], [2 << 16 | 248, merge],
        ]
        m.index(); self.validate(m, 'divergent_second_call')
        before = m.data(); report = {}
        self.assertEqual(tensor.prepare(m, report), 0)
        self.assertEqual(m.data(), before)
        self.assertEqual(report['status'], 'unchanged')

    def test_changed_helper_is_error_even_with_unproven_caller(self):
        m = self.module(); fid = self.helper(m); a, b = m.function_range(fid)
        fma = next(x for x in m.ins[a:b+1] if x[0] & 65535 == 12 and x[4] == 50)
        fma[5] = fma[7]
        before = m.data()
        with self.assertRaises(ValueError): tensor.prepare(m)
        self.assertEqual(m.data(), before)

    def test_indirect_volatile_annotation_rejected_atomically(self):
        m = self.module(); fid = self.helper(m)
        call = next(x for x in m.ins if x[0] & 65535 == 57 and x[3] == fid)
        group = m.new_id()
        at = next(i for i, x in enumerate(m.ins) if 19 <= x[0] & 65535 <= 39)
        m.ins[at:at] = [[2 << 16 | 73, group], [3 << 16 | 71, group, 21], [3 << 16 | 74, group, call[4]]]
        m.index(); self.validate(m, 'group_volatile'); before = m.data()
        with self.assertRaisesRegex(ValueError, 'indirect annotations'): tensor.prepare(m)
        self.assertEqual(m.data(), before)
        with self.assertRaisesRegex(ValueError, 'indirect annotations'): tensor._promote_readonly(m)
        self.assertEqual(m.data(), before)

    def test_guest_barrier_is_preserved(self):
        m = self.module(); fid = self.helper(m)
        a, b = m.function_range(fid)
        barrier = next(x[:] for x in m.ins[a:b+1] if x[0] & 65535 == 224)
        at = next(i for i, x in enumerate(m.ins) if x[0] & 65535 == 57 and x[3] == fid)
        m.ins.insert(at, barrier); m.index(); self.validate(m, 'guest_barrier')
        self.assertEqual(tensor.prepare(m), 1)
        self.assertEqual([x for x in m.ins if x[0] & 65535 == 224], [barrier])
        self.validate(m, 'guest_barrier_retained')

    def test_scratch_escape_rejected(self):
        m = self.module(); _, scratch, _ = spv._hmma_contract(m, self.helper(m))
        ptr_type = m.vars[scratch][0]; pointee = m.pointee(ptr_type)[1]
        at = next(i for i, x in enumerate(m.ins) if x[0] & 65535 == 253)
        m.ins.insert(at, [4 << 16 | 61, pointee, m.new_id(), scratch])
        m.index(); before = m.data()
        with self.assertRaisesRegex(ValueError, 'scratch referenced'): tensor.prepare(m)
        self.assertEqual(m.data(), before)


NATURAL_FIXTURE = r'''
OpCapability Shader
OpCapability GroupNonUniform
OpMemoryModel Logical GLSL450
OpEntryPoint GLCompute %main "main" %lane
OpExecutionMode %main LocalSize 64 1 1
OpName %loadfn "guarded_load"
OpName %storefn "guarded_store"
OpDecorate %lane BuiltIn SubgroupLocalInvocationId
OpDecorate %inputs ArrayStride 16
OpDecorate %outputs ArrayStride 4
OpDecorate %ib Block
OpMemberDecorate %ib 0 Offset 0
OpDecorate %ob Block
OpMemberDecorate %ob 0 Offset 0
OpDecorate %input DescriptorSet 0
OpDecorate %input Binding 0
OpDecorate %output DescriptorSet 0
OpDecorate %output Binding 1
%void = OpTypeVoid
%u = OpTypeInt 32 0
%bool = OpTypeBool
%k0 = OpConstant %u 0
%k1 = OpConstant %u 1
%k2 = OpConstant %u 2
%k3 = OpConstant %u 3
%k4 = OpConstant %u 4
%k8 = OpConstant %u 8
%k11 = OpConstant %u 11
%k16 = OpConstant %u 16
%k22 = OpConstant %u 22
%k32 = OpConstant %u 32
%k33 = OpConstant %u 33
%k44 = OpConstant %u 44
%k64 = OpConstant %u 64
%k100 = OpConstant %u 100
%k200 = OpConstant %u 200
%k43690 = OpConstant %u 43690
%inputs = OpTypeArray %u %k64
%outputs = OpTypeRuntimeArray %u
%ib = OpTypeStruct %inputs
%ob = OpTypeStruct %outputs
%pib = OpTypePointer Uniform %ib
%pob = OpTypePointer StorageBuffer %ob
%pu = OpTypePointer Uniform %u
%ps = OpTypePointer StorageBuffer %u
%pl = OpTypePointer Input %u
%lane = OpVariable %pl Input
%input = OpVariable %pib Uniform
%output = OpVariable %pob StorageBuffer
%main_type = OpTypeFunction %void
%load_type = OpTypeFunction %u %u %u
%store_type = OpTypeFunction %void %u %u %u
%loadfn = OpFunction %u None %load_type
%li = OpFunctionParameter %u
%lv = OpFunctionParameter %u
%le = OpLabel
%lbit = OpBitwiseAnd %u %lv %k1
%lc = OpINotEqual %bool %lbit %k0
OpSelectionMerge %lfinal None
OpBranchConditional %lc %lyes %lfinal
%lyes = OpLabel
%lp = OpAccessChain %pu %input %k0 %li
%loaded = OpLoad %u %lp
%times = OpIMul %u %loaded %k3
%lb = OpIMul %u %li %k4
%lidx = OpIAdd %u %lb %k1
%lmark = OpAccessChain %ps %output %k0 %lidx
OpStore %lmark %k43690
OpReturnValue %times
%lfinal = OpLabel
OpReturnValue %k0
OpFunctionEnd
%storefn = OpFunction %void None %store_type
%si = OpFunctionParameter %u
%sv = OpFunctionParameter %u
%sr = OpFunctionParameter %u
%se = OpLabel
%sbit = OpBitwiseAnd %u %sv %k2
%sc = OpINotEqual %bool %sbit %k0
OpSelectionMerge %sfinal None
OpBranchConditional %sc %syes %sfinal
%syes = OpLabel
%sbase = OpIMul %u %si %k4
%sidx = OpIAdd %u %sbase %k2
%sp = OpAccessChain %ps %output %k0 %sidx
%sbit2 = OpBitwiseAnd %u %sv %k4
%sc2 = OpINotEqual %bool %sbit2 %k0
OpSelectionMerge %sinner None
OpBranchConditional %sc2 %sleft %sright
%sleft = OpLabel
%svleft = OpIAdd %u %sr %k100
OpStore %sp %svleft
OpBranch %sinner
%sright = OpLabel
%svright = OpIAdd %u %sr %k200
OpStore %sp %svright
OpBranch %sinner
%sinner = OpLabel
OpReturn
%sfinal = OpLabel
OpReturn
OpFunctionEnd
%main = OpFunction %void None %main_type
%entry = OpLabel
%raw = OpLoad %u %lane
%ip = OpAccessChain %pu %input %k0 %raw
%value = OpLoad %u %ip
%base = OpIMul %u %raw %k4
%o0 = OpAccessChain %ps %output %k0 %base
%idx1 = OpIAdd %u %base %k1
%idx2 = OpIAdd %u %base %k2
%idx3 = OpIAdd %u %base %k3
%o1 = OpAccessChain %ps %output %k0 %idx1
%o2 = OpAccessChain %ps %output %k0 %idx2
%o3 = OpAccessChain %ps %output %k0 %idx3
OpStore %o0 %k44
OpStore %o1 %k0
OpStore %o2 %k0
%result = OpFunctionCall %u %loadfn %raw %value
%stored = OpFunctionCall %void %storefn %raw %value %result
OpStore %o3 %result
%mb1 = OpBitwiseAnd %u %value %k8
%mc1 = OpINotEqual %bool %mb1 %k0
OpSelectionMerge %m1 None
OpBranchConditional %mc1 %mt1 %m1
%mt1 = OpLabel
OpStore %o0 %k11
OpReturn
%m1 = OpLabel
%mb2 = OpBitwiseAnd %u %value %k16
%mc2 = OpINotEqual %bool %mb2 %k0
OpSelectionMerge %m2 None
OpBranchConditional %mc2 %mt2 %m2
%mt2 = OpLabel
OpStore %o0 %k22
OpReturn
%m2 = OpLabel
%mb3 = OpBitwiseAnd %u %value %k32
%mc3 = OpINotEqual %bool %mb3 %k0
OpSelectionMerge %m3 None
OpBranchConditional %mc3 %mt3 %m3
%mt3 = OpLabel
OpStore %o0 %k33
OpReturn
%m3 = OpLabel
OpReturn
OpFunctionEnd
'''


@unittest.skipUnless(shutil.which('spirv-as') and hmma.base.VAL, 'needs SPIRV-Tools assembler/validator')
class NaturalReturns(unittest.TestCase):
    def setUp(self): self.tmp = Path(tempfile.mkdtemp(prefix='natural_returns_'))
    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)
    def module(self, source=NATURAL_FIXTURE):
        asm = self.tmp / 'fixture.spvasm'; binary = self.tmp / 'fixture.spv'
        asm.write_text(source)
        subprocess.run(['spirv-as', '--target-env', 'spv1.3', str(asm), '-o', str(binary)], check=True, capture_output=True)
        module = spv.Module(binary.read_bytes()); self.validate(module)
        return module
    def validate(self, module):
        path = self.tmp / 'validate.spv'; path.write_bytes(module.data())
        result = subprocess.run([hmma.base.VAL, '--target-env', 'vulkan1.3', str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    def test_natural_helpers_and_entry_preserve_memory_operations(self):
        m = self.module()
        original_memory = [x[:] for x in m.ins if x[0] & 65535 in (61, 62, 65)]
        a, b = m.function_range(m.entry_function()); original_entry = [x[:] for x in m.ins[a:b+1]]
        result = tensor._natural_helpers(m)
        self.assertEqual(len(result['normalized']), 2)
        a, b = m.function_range(m.entry_function()); self.assertEqual(m.ins[a:b+1], original_entry)
        result = tensor._natural_entry(m); self.assertTrue(result['normalized']); self.assertEqual(result['guards'], 3)
        self.assertEqual([x for x in m.ins if x[0] & 65535 in (61, 62, 65)], original_memory)
        self.validate(m)
        final = tensor.opt.optimizer()(m.data(), passes=tensor.FINAL_PASSES)
        self.validate(spv.Module(final))
        counts = Counter(x[0] & 65535 for x in spv.Module(final).ins)
        self.assertEqual(counts[251], 0); self.assertEqual(counts[57], 0)
    def test_final_passes_cli_shared_parity_with_warning(self):
        executable = shutil.which('spirv-opt')
        if not executable: self.skipTest('no spirv-opt')
        libdir = Path(executable).resolve().parent.parent / 'lib'
        library = next((str(libdir / name) for name in ('libSPIRV-Tools-opt.so', 'libSPIRV-Tools-opt.dylib')
                        if (libdir / name).is_file()), None)
        if not library: self.skipTest('no optimizer shared library')
        raw = self.module().data(); diagnostics = []
        cli = tensor.opt.optimizer(executable)(raw, passes=tensor.FINAL_PASSES, diagnostics=diagnostics)
        shared = tensor.opt.optimizer(library)(raw, passes=tensor.FINAL_PASSES)
        self.assertEqual(cli, shared)
        self.assertTrue(any('could not be inlined' in message for message in diagnostics))
        self.validate(spv.Module(cli))

    def test_nested_early_return_is_not_redirected(self):
        source = NATURAL_FIXTURE.replace('%lyes = OpLabel\n', '''%lyes = OpLabel
%innerbit = OpBitwiseAnd %u %lv %k2
%innercond = OpINotEqual %bool %innerbit %k0
OpSelectionMerge %innermerge None
OpBranchConditional %innercond %early %innermerge
%innermerge = OpLabel
OpBranch %lfinal
%early = OpLabel
''', 1)
        m = self.module(source)
        fid = next(i for i, name in m.names.items() if name == 'guarded_load')
        a, b = m.function_range(fid); original = [x[:] for x in m.ins[a:b+1]]
        result = tensor._natural_helpers(m)
        self.assertIn('inner selection', result['skipped'][fid])
        a, b = m.function_range(fid); self.assertEqual(m.ins[a:b+1], original)
        self.validate(m)
    def test_reversed_entry_guard_is_unchanged(self):
        m = self.module(NATURAL_FIXTURE.replace('OpBranchConditional %mc1 %mt1 %m1', 'OpBranchConditional %mc1 %m1 %mt1'))
        before = m.data(); result = tensor._natural_entry(m)
        self.assertFalse(result['normalized']); self.assertEqual(m.data(), before)


if __name__ == '__main__': unittest.main()
