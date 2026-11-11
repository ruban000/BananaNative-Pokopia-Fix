"""Differential page coverage, bounded windows and immutable in-flight snapshots."""
from pathlib import Path
import subprocess
import tempfile
import unittest


class PageIndexHost(unittest.TestCase):
    def test_page_ranges_and_reuse(self):
        source = r'''
#include "vka_page_index.h"
#include <assert.h>
#include <stdlib.h>
int main(void) {
    uint64_t lo[] = {0x4000000000ull, 0x4000004001ull, 0x4000008000ull, UINT64_MAX-3};
    uint64_t hi[] = {0x4000003000ull, 0x4000007800ull, 0x4000012000ull, UINT64_MAX};
    uint32_t base, out[33];
    for (uint32_t limit = 0; limit < 32; limit++) {
        uint32_t n = vka_pidx_window(lo, hi, 4, limit, &base);
        assert(n <= limit);
        vka_pidx_build(out, base, n, lo, hi, 4);
        assert(out[n] == 0);
        for (uint32_t p = 0; p < n; p++) {
            uint64_t a = ((uint64_t)base + p) << 12;
            uint32_t expected = 0;
            for (int i = 0; i < 4; i++) if (lo[i] <= a && a + 4096 <= hi[i]) expected = i + 1;
            assert(out[p] == expected);
        }
    }
    for (uint64_t done = 0; done < 12; done++) for (uint64_t fence = 0; fence < 12; fence++) {
        assert(!vka_pidx_writable(1, fence, done));
        assert(vka_pidx_writable(0, fence, done) == (fence <= done));
    }
    uint64_t top = (1ull << 44) - 4096, end = 1ull << 44;
    assert(vka_pidx_window(&top, &end, 1, 4, &base) == 1);
    assert(base == UINT32_MAX);
    vka_pidx_build(out, base, 1, &top, &end, 1);
    assert(out[0] == 1 && out[1] == 0);
    uint64_t overlap_lo[] = {4096, 5000}, overlap_hi[] = {16384, 6000};
    vka_pidx_build(out, 1, 3, overlap_lo, overlap_hi, 2);
    for (int i = 0; i < 4; i++) assert(out[i] == 0);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / 't.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(Path(__file__).resolve().parents[1] / 'vk_android'), str(p / 't.c'), '-o', str(p / 't')], check=True)
            subprocess.run([str(p / 't')], check=True)
