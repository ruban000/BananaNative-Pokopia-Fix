"""Run codec protocol bodies on POSIX, without platform decoder frameworks.

Only hle.h's platform declarations are replaced; the complete production codec
source is compiled. Unused decoding functions are removed by the compiler. The
SystemEvent double implements manual/auto clear, not the SDK's kernel path.
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
typedef uint32_t Result;
#define DM_SYNC_NEVER 0x4e436d44u
#define HLE_FORCE(mangled, why, ret, cname, ...) static ret cname(__VA_ARGS__)
#define LOG(...) do { fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#define die(...) do { LOG(__VA_ARGS__); abort(); } while (0)
void hle_bind_system_event(void *, int);
void hle_signal_system_event(void *);
void hle_clear_system_event(void *);
void *guest_sym(const char *);
'''

class CodecPoll(unittest.TestCase):
    def test_protocol(self):
        compiler = os.environ.get('CC') or shutil.which('cc')
        if not compiler:
            self.skipTest('C compiler unavailable')
        source = (ROOT / 'dm/host/hle_codec_video.c').read_text()
        self.assertEqual(source.count('#include "hle.h"'), 1)
        with tempfile.TemporaryDirectory(prefix='codec-poll-') as tmp:
            tmp = Path(tmp)
            (tmp / 'codec.c').write_text(source.replace('#include "hle.h"', SHIM))
            binary = tmp / 'test'
            command = [compiler, '-std=gnu11', '-O1', '-g', '-Wall', '-Werror',
                       '-Wno-unused-function', '-fsanitize=address,undefined', '-pthread',
                       '-I' + str(ROOT / 'dm/host'), '-I' + str(ROOT / 'dm/third_party/libvpx'),
                       '-DCODEC_SOURCE="' + str(tmp / 'codec.c') + '"',
                       str(ROOT / 'dm/host/tests/t_codec_poll.c'), '-o', str(binary)]
            built = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('t_codec_poll: passed', result.stdout)

if __name__ == '__main__':
    unittest.main()
