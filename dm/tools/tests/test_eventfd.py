"""The bsd service's event descriptors as the host keeps them (dm/host/hle_eventfd.c; dm/host/tests/t_eventfd.c): gRPC's wakeup descriptor (Zelda: Ocarina of Time),
non-blocking and semaphore modes, blocking reads and polls woken by another thread, timeouts. The production source is compiled behind a POSIX shim with AddressSanitizer
and UBSan. Needs a C compiler; skipped without one.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SHIM = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define LOG(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
void dm_thread_native_wait(int entering);
'''


class EventFd(unittest.TestCase):
    def test_eventfd(self):
        compiler = os.environ.get('CC') or shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='eventfd-') as tmp:
            tmp = Path(tmp)
            source = (ROOT / 'dm/host/hle_eventfd.c').read_text()
            self.assertEqual(source.count('#include "hle.h"'), 1)
            (tmp / 'efd.c').write_text(source.replace('#include "hle.h"', SHIM))
            binary = tmp / 'test'
            built = subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined',
                                    '-fno-sanitize-recover=undefined', '-pthread', '-DEFD_SOURCE="' + str(tmp / 'efd.c') + '"',
                                    str(ROOT / 'dm/host/tests/t_eventfd.c'), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
            self.assertIn('t_eventfd: passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
