"""Differential replay of inline vertex bindings, including replacement tails."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('cc'), 'C compiler unavailable')
class StateCopy(unittest.TestCase):
    def test_record_sequences_match_unfiltered_replay(self):
        header = Path(__file__).resolve().parents[1] / 'vk_android'
        source = r'''
#include "vka_statecopy.h"
#include <assert.h>
#include <stdint.h>
static uint32_t seed = 42;
static uint32_t next(void) { seed = seed * 1664525u + 1013904223u; return seed; }
int main(void) {
    for (int stride = 4; stride <= 16; stride *= 2) {
        unsigned char actual[256] = {0}, reference[256] = {0}, input[256], previous[256];
        int used = 0, expected = 0;
        for (int i = 0; i < 10000; i++) {
            int first = next() % 17, count = next() % (17 - first), replace = next() % 2;
            for (int k = 0; k < 256; k++) input[k] = (unsigned char)(next() >> 24);
            for (int repeat = 0; repeat < 2; repeat++) {
                memcpy(previous, reference, sizeof previous);
                int old = expected;
                if (replace) { memset(reference, 0, (size_t)16 * stride); expected = 0; }
                memcpy(reference + first * stride, input, (size_t)count * stride);
                if (first + count > expected) expected = first + count;
                int changed = vka_state_copy(actual, input, stride, 16, first, count, replace, &used, 1);
                assert(used == expected && !memcmp(actual, reference, sizeof actual));
                assert(changed == (old != expected || memcmp(previous, reference, sizeof previous) != 0));
            }
        }
        assert(vka_state_copy(actual, input, stride, 16, -1, 1, 0, &used, 1));
        assert(vka_state_copy(actual, input, stride, 16, 16, 1, 0, &used, 1));
        assert(!memcmp(actual, reference, sizeof actual));
        assert(vka_state_copy(actual, input, stride, 16, 0, 0, 0, 0, 0));
    }
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'test.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(header),
                            str(path / 'test.c'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)
