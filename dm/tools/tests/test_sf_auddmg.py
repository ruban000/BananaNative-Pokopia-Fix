"""auddmg:u, the Switch 2 audio device manager (dm/host/hle_sf_auddmg.c), through the host sessions of dm/host/hle_sf.c (dm/host/tests/t_sf_auddmg.c): requests built as
SDK 22.2.8's nn::audio::devmgr client builds them, replies read as its parser reads them. The production sources are compiled behind a POSIX shim with AddressSanitizer and
UBSan. Needs a C compiler; skipped without one.
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
#include "hle_sf.h"
typedef uint32_t Result;
#define RES_OK 0u
#define RES_FAIL 0x2A2u
#define HLE_FORCE(mangled, why, ret, cname, ...) static ret cname(__VA_ARGS__)
#define LOG(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#define die(...) do { LOG(__VA_ARGS__); abort(); } while (0)
uint32_t hle_kevent_new(uint32_t *write_end);
uint32_t hle_dup_readable_handle(uint32_t h);
'''


class SfAuddmg(unittest.TestCase):
    def test_devmgr_session(self):
        compiler = os.environ.get('CC') or shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='sf-auddmg-') as tmp:
            tmp = Path(tmp)
            for name in ('hle_sf.c', 'hle_sf_auddmg.c'):
                source = (ROOT / 'dm/host' / name).read_text()
                self.assertEqual(source.count('#include "hle.h"'), 1)
                (tmp / name).write_text(source.replace('#include "hle.h"', SHIM if name == 'hle_sf.c' else ''))
            binary = tmp / 'test'
            built = subprocess.run([compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined',
                                    '-fno-sanitize-recover=undefined', '-pthread', '-I' + str(ROOT / 'dm/host'),
                                    '-DSF_SOURCE="' + str(tmp / 'hle_sf.c') + '"', '-DAUDDMG_SOURCE="' + str(tmp / 'hle_sf_auddmg.c') + '"',
                                    str(ROOT / 'dm/host/tests/t_sf_auddmg.c'), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr[-4000:])
            self.assertIn('t_sf_auddmg: passed', result.stdout)
            self.assertIn('[RED] sf: auddmg:u IUserSession command 1500', result.stderr)


if __name__ == '__main__':
    unittest.main()
