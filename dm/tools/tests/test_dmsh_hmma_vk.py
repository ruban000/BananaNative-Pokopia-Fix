"""Execute original/compact HMMA against a dense matrix oracle, without game files.

This preserves BananaNative's CURRENT policy: ascending-K float32 FMA, one final
half rounding. It does not claim NVIDIA's undocumented accumulation details.
Lavapipe has eight-lane subgroups here: execution deliberately replaces subgroup
barriers with workgroup barriers and derives warp lanes from local invocation X.
Production barriers are checked independently; this is NOT a hardware subgroup test.
XLATE=<pinned translator> python3 -m unittest dm.tools.tests.test_dmsh_hmma_vk -v
"""
import ctypes
import ctypes.util
import math
import random
import struct
import unittest

from dm.tools.tests import test_ampere_lowerings as base

spv = base.spv
_libm = ctypes.CDLL(ctypes.util.find_library('m'))
_fma = _libm.fmaf
_fma.argtypes = [ctypes.c_float] * 3
_fma.restype = ctypes.c_float


def half(x):
    try:
        return struct.unpack('<H', struct.pack('<e', x))[0]
    except OverflowError:
        return 0xfc00 if x < 0 else 0x7c00


def unhalf(x):
    return struct.unpack('<e', struct.pack('<H', x))[0]


def matrices():
    return ([[0.] * 16 for _ in range(16)], [[0.] * 8 for _ in range(16)], [[0.] * 8 for _ in range(16)])


def fragments(a, b, c):
    """Scatter dense matrices into the documented 32-lane fragment layout."""
    result = []
    pack = lambda lo, hi: half(lo) | half(hi) << 16
    for lane in range(32):
        row, pair = divmod(lane, 4)
        k = 2 * pair
        result.extend([pack(a[row][k], a[row][k+1]), pack(a[row+8][k], a[row+8][k+1]),
                       pack(a[row][k+8], a[row][k+9]), pack(a[row+8][k+8], a[row+8][k+9]),
                       pack(b[k][row], b[k+1][row]), pack(b[k+8][row], b[k+9][row]),
                       pack(c[row][k], c[row][k+1]), pack(c[row+8][k], c[row+8][k+1])])
    return result


def oracle(a, b, c, repeats=1):
    # No shared-memory addressing or helper loop structure: ordinary dense matmul.
    d = [[unhalf(half(x)) for x in row] for row in c]
    for _ in range(repeats):
        for row in range(16):
            for col in range(8):
                acc = d[row][col]
                for k in range(16):
                    acc = _fma(unhalf(half(a[row][k])), unhalf(half(b[k][col])), acc)
                d[row][col] = unhalf(half(acc))
    out = []
    for lane in range(32):
        row, pair = divmod(lane, 4)
        for r in (row, row + 8):
            out.append(half(d[r][2*pair]) | half(d[r][2*pair+1]) << 16)
    return out


def fixture_code(rd=12, repeats=1):
    i, pt = base.inst, base.PT
    def imad(dst, a, b, c=255):
        return i(0x224 | pt << 12 | dst << 16 | a << 24 | b << 32, c | 7 << 17)
    def hmma(c):
        return i(0x23c | pt << 12 | rd << 16 | 4 << 24 | 8 << 32, c | 1 << 11)
    code = [i(0x919 | pt << 12 | 250 << 16, 33 << 8), base.mov_i(249, 32), imad(252, 250, 249)]
    for r in range(8):
        code.append(i(0xb82 | pt << 12 | (4+r) << 16 | 252 << 24 | (r*4) << 38 | 3 << 54, 4 << 9))
    code.append(hmma(10))
    for _ in range(repeats-1):
        code.append(hmma(rd))
    code += [base.mov_i(249, 2), imad(254, 250, 249), base.store(254, rd),
             base.mov_i(248, 1), imad(254, 250, 249, 248), base.store(254, rd+1), base.exit_()]
    return code


def barrier_signature(m):
    return [(x[0] & 0xffff, tuple(m.consts[v][1][0] for v in x[1:]))
            for x in m.ins if x[0] & 0xffff in (224, 225)]


def arithmetic_fixture(m):
    """TEST ONLY: provide warp32 shared-memory communication on subgroup8 CPUs."""
    u32 = m.add_type(spv.OP_TYPE_INT, 32, 0)
    two, mask = m.add_const(u32, 2), m.add_const(u32, 31)
    tid, _ = m.builtin_var(spv.SC_INPUT, 27)  # LocalInvocationId
    lane, _ = m.builtin_var(spv.SC_INPUT, 41)
    if tid is None or lane is None:
        raise AssertionError('synthetic HMMA must expose local and subgroup invocation IDs')
    vec3 = m.pointee(m.vars[tid][0])[1]
    body = []
    for x in m.ins:
        op = x[0] & 0xffff
        if op == spv.OP_LOAD and x[3] == lane:
            v, scalar = m.new_id(), m.new_id()
            body += [[4 << 16 | spv.OP_LOAD, vec3, v, tid],
                     [5 << 16 | spv.OP_COMPOSITE_EXTRACT, u32, scalar, v, 0],
                     [5 << 16 | 199, x[1], x[2], scalar, mask]]
        elif op == 224:
            body.append([x[0], two, two, x[3]])
        elif op == 225:
            body.append([x[0], two, x[2]])
        else:
            body.append(x)
    m.ins = body
    m.index()


class OraclePolicy(unittest.TestCase):
    def test_rounding_and_order_discriminators(self):
        a, b, c = matrices()
        c[0][0] = 1.
        a[0][0] = a[0][8] = 2**-11
        b[0][0] = b[8][0] = 1.
        self.assertEqual(oracle(a,b,c)[0] & 65535, 0x3c01)
        a, b, c = matrices()
        a[0][:3], b[0][0], b[1][0], b[2][0] = [4096.,1.,-4096.],4096.,1.,4096.
        self.assertEqual(oracle(a,b,c)[0] & 65535, 0)


@unittest.skipUnless(base.XLATE and base.Path(base.XLATE).is_file(), 'set XLATE to the pinned translator')
class HmmaArithmetic(unittest.TestCase):
    setUpClass = classmethod(base.AmpereLowerings.setUpClass.__func__)
    tearDownClass = classmethod(base.AmpereLowerings.tearDownClass.__func__)
    translate = base.AmpereLowerings.translate
    run_compute = base.AmpereLowerings.run_compute

    def check(self, cases, rd=12, repeats=1):
        if not hasattr(spv, 'compact_hmma'):
            self.skipTest('compact_hmma rewrite not available')
        path, _ = self.translate(f'hmma_{rd}_{repeats}', fixture_code(rd, repeats), local=[32*len(cases),1,1])
        original = spv.Module(path.read_bytes())
        compact = spv.Module(path.read_bytes())
        barriers = barrier_signature(original)
        self.assertEqual(spv.compact_hmma(compact), repeats)
        self.assertEqual(barrier_signature(compact), barriers, 'production barrier scopes/semantics changed')
        data = sum((fragments(*case) for case in cases), [])
        want = sum((oracle(*case, repeats=repeats) for case in cases), [])
        for label, module in [('original', original), ('compact', compact)]:
            arithmetic_fixture(module)
            p = self.tmp / f'{path.stem}_{label}.spv'
            p.write_bytes(module.data())
            if base.VAL:
                v = base.subprocess.run([base.VAL,'--target-env','vulkan1.3',str(p)], capture_output=True,text=True)
                self.assertEqual(v.returncode,0,v.stdout+v.stderr)
            got, _ = self.run_compute(p,struct.pack(f'<{len(data)}I',*data),out_words=len(want))
            for index, (actual, expected) in enumerate(zip(got,want)):
                for shift in (0,16):
                    ah, eh = actual >> shift & 65535, expected >> shift & 65535
                    if math.isnan(unhalf(eh)):
                        self.assertTrue(math.isnan(unhalf(ah)),f'{label} output{index}:{shift}: NaN lost')
                    else:
                        self.assertEqual(ah,eh,f'{label} output{index}:{shift}')

    def test_basis_mapping_and_warp_isolation(self):
        # Sweep K and the source half/register boundaries. Distinct matrices in
        # adjacent warps detect accidental cross-warp reads and shared aliasing.
        for k in range(16):
            cases=[]
            for warp in range(2):
                a,b,c=matrices()
                for row in range(16): a[row][k]=float(row+1+warp*16)
                for col in range(8): b[k][col]=float(col+1)
                cases.append((a,b,c))
            with self.subTest(k=k): self.check(cases)

    def test_rounding_order_and_special_values(self):
        a,b,c=matrices()
        c[0][0]=1.;a[0][0]=a[0][8]=2**-11;b[0][0]=b[8][0]=1.
        a[1][:3]=[4096.,1.,-4096.];b[0][1]=4096.;b[1][1]=1.;b[2][1]=4096.
        c[2][0]=-0.; a[2]=[-0.]*16
        b[0][2]=float('inf');a[3][0]=0.
        c[4][3]=float('nan')
        a[5][0]=2**-24;b[0][4]=1.
        a[6][0]=65504.;b[0][5]=2.
        self.check([(a,b,c)])

    def test_random_inputs_and_source_destination_aliases(self):
        rng=random.Random(1947)
        case=tuple([[rng.randrange(-16,17)/8 for _ in row] for row in matrix] for matrix in matrices())
        for rd in (4,10,12):
            with self.subTest(rd=rd): self.check([case],rd=rd)
        self.check([case], repeats=2)

    def test_finite_half_bit_patterns(self):
        rng = random.Random(28611)
        # Independent signs, exponents and significands exercise rounding beyond
        # the exactly representable small integer/basis cases.
        case = tuple([[unhalf(rng.randrange(0x0400, 0x6000) | rng.randrange(2) << 15)
                       for _ in row] for row in matrix] for matrix in matrices())
        self.check([case])

    def test_uniform_false_predicate_preserves_destinations(self):
        if not hasattr(spv, 'compact_hmma'):
            self.skipTest('compact_hmma rewrite not available')
        code = fixture_code()
        at = next(i for i, ins in enumerate(code) if int.from_bytes(ins, 'little') & 0xfff == 0x23c)
        # !PT is uniformly false; divergent participation in a warp collective
        # has no defined result and is deliberately not used as an oracle.
        code[at] = (int.from_bytes(code[at], 'little') | 1 << 15).to_bytes(16, 'little')
        code[at:at] = [base.mov_i(12, 0x12345678), base.mov_i(13, 0x9abcdef0)]
        p, _ = self.translate('hmma_false', code, local=[32,1,1])
        m = spv.Module(p.read_bytes())
        self.assertEqual(spv.compact_hmma(m), 0)
        self.assertEqual(m.data(), p.read_bytes())
        got, _ = self.run_compute(p, out_words=64)
        self.assertEqual(got, [0x12345678, 0x9abcdef0] * 32)

    def test_no_hmma_is_byte_identical(self):
        p, _ = self.translate('no_hmma', [base.mov_i(0, 17), base.exit_()], local=[32,1,1])
        original = p.read_bytes()
        m = spv.Module(original)
        self.assertEqual(spv.compact_hmma(m), 0)
        self.assertEqual(m.data(), original)

    def test_changed_helper_rejected_without_mutation(self):
        p, _ = self.translate('hmma_tampered', fixture_code(), local=[32,1,1])
        m = spv.Module(p.read_bytes())
        fid = next(i for i, name in m.names.items() if name == 'dmsh_hmma16816_f16')
        start, end = m.function_range(fid)
        fma = next(x for x in m.ins[start:end] if x[0] & 65535 == 12 and x[4] == 50)
        # Swap multiplicand and accumulator: well-typed, but different arithmetic.
        fma[5], fma[7] = fma[7], fma[5]
        m.index()
        before = m.data()
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            spv.compact_hmma(m)
        self.assertEqual(m.data(), before)

    def test_repeated_pass_rejected_without_mutation(self):
        p, _ = self.translate('hmma_twice', fixture_code(), local=[32,1,1])
        m = spv.Module(p.read_bytes())
        self.assertEqual(spv.compact_hmma(m), 1)
        before = m.data()
        # The named helper's rewritten ABI is deliberately not an accepted input.
        with self.assertRaises(ValueError):
            spv.compact_hmma(m)
        self.assertEqual(m.data(), before)


if __name__ == '__main__':
    unittest.main()
