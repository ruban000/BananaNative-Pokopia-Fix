"""Ordering boundaries and overflow-safe storage for prepared mesh windows."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('cc'), 'C compiler unavailable')
class MeshPrepare(unittest.TestCase):
    def test_dependencies_and_arena_boundaries(self):
        header = Path(__file__).resolve().parents[1] / 'vk_android'
        source = r'''
#include "vka_mesh_prepare.h"
#include <assert.h>
int main(void) {
    unsigned ordered[] = {OP_BARRIER, OP_WAIT_EVENT, OP_WAIT_SYNC, OP_SIGNAL_EVENT,
        OP_REPORT_COUNTER, OP_RESET_COUNTER, OP_RENDER_COND, OP_SET_RT,
        OP_CLEAR_COLOR, OP_CLEAR_DS, OP_COPY_B2B, OP_COPY_B2T, OP_CLEAR_BUF,
        OP_DISPATCH, OP_TF_STATE, OP_SET_TEX_POOL, OP_SET_SAMP_POOL, 0xffffffffu};
    for (unsigned i = 0; i < sizeof ordered / sizeof *ordered; i++) assert(!vka_mp_state(ordered[i]));
    assert(vka_mp_state(OP_BIND_PROGRAM) && vka_mp_state(OP_BIND_UBO));
    assert(!vka_mp_disjoint(0, 10, 100, 10));
    assert(!vka_mp_disjoint(1, 0, 100, 10));
    assert(!vka_mp_disjoint(UINT64_MAX - 4, 8, 100, 10));
    vka_mp_access reads = {0}, writes = {0};
    assert(vka_mp_add(&reads, 100, 16));
    assert(vka_mp_add(&writes, 116, 16) && vka_mp_independent(&writes, &reads));
    assert(vka_mp_add(&writes, 115, 1) && !vka_mp_independent(&writes, &reads));
    writes.n = 1; reads.all = 1; assert(!vka_mp_independent(&writes, &reads));
    writes.n = 0; assert(vka_mp_independent(&writes, &reads));
    writes.all = 1; assert(!vka_mp_independent(&writes, &reads));
    reads.all = 0; reads.n = VKA_MP_RANGES; assert(!vka_mp_add(&reads, 1000, 1));
    for (uint64_t a = 1; a < 50; a++) for (uint64_t b = 1; b < 50; b++)
        for (uint64_t n = 1; n < 10; n++) for (uint64_t m = 1; m < 10; m++) {
            int common = 0;
            for (uint64_t x = a; x < a + n; x++) for (uint64_t y = b; y < b + m; y++) common |= x == y;
            assert(vka_mp_disjoint(a, n, b, m) == !common);
        }
    uint64_t at = 99;
    assert(!vka_mp_reserve(UINT64_MAX, 1, 256, UINT64_MAX, &at) && at == 99);
    assert(!vka_mp_reserve(0, 1, 0, 1024, &at));
    assert(!vka_mp_reserve(0, 0, 256, 1024, &at));
    assert(vka_mp_reserve(0, 1024, 256, 1024, &at) && at == 0);
    assert(!vka_mp_reserve(1024, 1, 256, 1024, &at));
    for (uint64_t align = 1; align <= 512; align *= 2) {
        uint64_t used = 0;
        for (uint64_t size = 1; size <= 2048; size++) {
            int ok = vka_mp_reserve(used, size, align, 4096, &at);
            if (!ok) { used = 0; assert(vka_mp_reserve(used, size, align, 4096, &at)); }
            assert(at >= used && at % align == 0 && size <= 4096 - at);
            used = at + size;
        }
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(header),
                            '-I', str(header.parent / 'nvn/include'),
                            str(path / 'test.c'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)
