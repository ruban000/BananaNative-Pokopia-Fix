"""Execute the production module mapper without guest instructions or a platform SDK.

The fixture uses the real module type, mapper and key-page installation. Only the
platform slot values and game-specific window patch are supplied by the fixture.
"""
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
LOADER = ROOT / 'dm/host/loader.c'


def function(source, signature):
    start = source.index(signature)
    end = source.index('\n}', start) + 2
    return source[start:end] + '\n'


def fixture_source(source):
    header = (ROOT / 'dm/host/dm.h').read_text()
    module = header[header.index('typedef struct {', header.index('// Run-time state')):header.index('} module_t;') + len('} module_t;')]
    code = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
typedef void Elf64_Dyn;
typedef void Elf64_Sym;
#define PLAT_APPLE 0
enum {KEY_N = 4};
static void die(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap);
    fputc('\n', stderr); exit(80);
}
static uint64_t plat_slot_key_entry(unsigned i) { return i * 8; }
'''
    code += module + '\n'
    code += r'''
static void install_fps_state(module_t *m) { (void)m; assert(!"fixture has no frame-step page"); }
static void window_apply(module_t *m) {
    // Installing the key page must precede the window patch; code must still be writable.
    assert(((uint64_t *)(m->base + m->key_page_off))[3] == 24);
    m->base[0x100] = 0x5a;
}
static int io_mode, read_calls;
static const char *image_path;
static ssize_t injected_pread(int fd, void *buf, size_t n, off_t off) {
    read_calls++;
    if (io_mode == 1 && read_calls == 1) { errno = EINTR; return -1; }
    if (io_mode == 2) { errno = EIO; return -1; }
    if (io_mode == 3 && read_calls == 2) assert(truncate(image_path, off) == 0);
    if ((io_mode == 1 || io_mode == 3) && n > 113) n = 113;
    return pread(fd, buf, n, off);
}
#define pread injected_pread
'''
    code += function(source, 'static void install_key_page(')
    # The same fixture can measure a baseline revision of the actual mapper.
    if 'static uint8_t *slurp(' in source:
        code += function(source, 'static uint8_t *slurp(')
    else:
        code += function(source, 'static void read_image(')
    start = source.index('// Seal the code area immediately')
    code += function(source[start:], 'static void map_image(')
    code += r'''
static uint64_t number(const char *s) { return strtoull(s, NULL, 0); }
static uint64_t hash_bytes(uint64_t h, const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * UINT64_C(1099511628211);
    return h;
}
static double elapsed(struct timespec a, struct timespec b) {
    return (b.tv_sec - a.tv_sec) * 1000.0 + (b.tv_nsec - a.tv_nsec) / 1e6;
}
int main(int argc, char **argv) {
    assert(argc == 13);
    module_t m = {0}; snprintf(m.name, sizeof m.name, "%s", argv[2]);
    m.patch_off = strtoll(argv[3], NULL, 0); m.text_end = number(argv[4]);
    m.key_page_off = number(argv[5]); m.key_page_size = 0x4000;
    m.image_size = number(argv[6]); m.file_image_len = number(argv[7]);
    m.back_off = number(argv[8]); m.back_len = number(argv[9]);
    for (unsigned i = 0; i < KEY_N; i++) m.key_idx[i] = i;
    io_mode = atoi(argv[10]);
    char path[1024]; snprintf(path, sizeof path, "%s/%s.patched.bin", argv[1], m.name); image_path = path;
    struct timespec a, b; clock_gettime(CLOCK_MONOTONIC, &a);
    map_image(&m, argv[1]); clock_gettime(CLOCK_MONOTONIC, &b);
    struct rusage usage; assert(!getrusage(RUSAGE_SELF, &usage));
    uint64_t h = UINT64_C(14695981039346656037);
    if (m.patch_off < 0) h = hash_bytes(h, m.base + m.patch_off, m.patch_len);
    h = hash_bytes(h, m.base, m.size);
    printf("size=%zu patch=%llu hash=%016llx maxrss=%ld ms=%.3f reads=%d\n", m.size,
           (unsigned long long)m.patch_len, (unsigned long long)h, usage.ru_maxrss, elapsed(a, b), read_calls);
    if (strcmp(argv[11], "-")) {
        FILE *out = fopen(argv[11], "wb"); assert(out);
        if (m.patch_off < 0) assert(fwrite(m.base + m.patch_off, 1, m.patch_len, out) == m.patch_len);
        assert(fwrite(m.base, 1, m.size, out) == m.size); assert(!fclose(out));
    }
    // Protection checks use real accesses; Python expects the child to fault.
    volatile uint8_t *target = NULL;
    if (!strcmp(argv[12], "text")) target = m.base;
    if (!strcmp(argv[12], "key")) target = m.base + m.key_page_off;
    if (!strcmp(argv[12], "patch")) target = m.base + m.patch_off;
    if (!strcmp(argv[12], "back")) target = m.base + m.back_off;
    if (!strcmp(argv[12], "gap")) { volatile uint8_t value = m.base[-1]; (void)value; }
    if (target) *target = 1;
    if (!strcmp(argv[12], "data")) m.base[0x5000] ^= 1;
    size_t pre = m.patch_off < 0 ? (size_t)(0 - (uint64_t)m.patch_off) : 0;
    assert(!munmap(m.base - pre, pre + m.size));
}
'''
    return code


@unittest.skipUnless(shutil.which('cc'), 'C compiler required')
class ModuleImage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.tmp.name)
        fixture = cls.folder / 'mapper.c'
        fixture.write_text(fixture_source(LOADER.read_text()))
        cls.binary = cls.folder / 'mapper'
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(fixture), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_image(self, data, layout, mode=0, action='none', sparse=False):
        image = self.folder / 'fixture.patched.bin'
        if sparse:
            with image.open('wb') as f:
                f.truncate(len(data))
                for start in range(0, len(data), 4096):
                    part = data[start:start + 4096]
                    if any(part):
                        f.seek(start)
                        f.write(part)
        else:
            image.write_bytes(data)
        out = self.folder / 'mapped.bin'
        out.unlink(missing_ok=True)
        command = [str(self.binary), str(self.folder), 'fixture'] + [str(x) for x in layout]
        result = subprocess.run(command + [str(mode), str(out), action], capture_output=True,
                                env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=1:handle_segv=0:handle_sigbus=0'})
        return result, out.read_bytes() if out.exists() else None

    @staticmethod
    def expected(data, layout):
        patch, _, key, size, file_len, back, back_len = layout
        align = lambda n: (n + 0x3fff) & ~0x3fff
        if patch < 0:
            plen = len(data) - file_len - back_len
            front = data[file_len:file_len + plen].ljust(align(plen), b'\0')
            mapped = bytearray(size)
            mapped[:file_len] = data[:file_len]
            if back_len:
                mapped[back:back + back_len] = data[-back_len:]
        else:
            front = b''
            mapped = bytearray(data.ljust(align(len(data)), b'\0'))
        mapped[key:key + 0x4000] = bytes(0x4000)
        struct.pack_into('<4Q', mapped, key, 0, 8, 16, 24)
        mapped[0x100] = 0x5a
        return front + mapped

    def test_bytes_short_reads_holes_and_protections(self):
        layouts = [
            (0x10000, 0x3210, 0xc000, 0, 0, 0, 0),
            (-0x10000, 0x3210, 0x18000, 0x1c000, 0x8000, 0, 0),
            (-0x10000, 0x3210, 0x18000, 0x24000, 0x8000, 0x20000, 0x4000),
        ]
        for layout in layouts:
            size = 0x12345 if layout[0] > 0 else layout[4] + 0x2345 + layout[6]
            data = bytearray((i * 7 + 3) & 255 for i in range(size))
            data[0x4000:0x7000] = bytes(0x3000)  # an actual hole in sparse files
            for mode in (0, 1):
                with self.subTest(layout=layout, interrupted_short_reads=mode):
                    result, mapped = self.run_image(data, layout, mode, sparse=True)
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    self.assertEqual(mapped, self.expected(data, layout))
            for action in ('text', 'key', 'patch') + (('gap',) if layout[0] < 0 else ()) + (('back',) if layout[6] else ()):
                with self.subTest(layout=layout, protection=action):
                    result, _ = self.run_image(data, layout, action=action)
                    self.assertIn(result.returncode, (-signal.SIGSEGV, -signal.SIGBUS), result.stderr.decode())
            result, _ = self.run_image(data, layout, action='data')
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_io_failure_and_truncation(self):
        layout = (0x10000, 0x3210, 0xc000, 0, 0, 0, 0)
        for mode, message in ((2, b'Input/output error'), (3, b'unexpected end of file')):
            result, mapped = self.run_image(bytes(0x12345), layout, mode)
            self.assertEqual(result.returncode, 80, result.stderr.decode())
            self.assertIn(message, result.stderr)
            self.assertIsNone(mapped)

    def test_invalid_layouts(self):
        cases = [
            (b'', (0x10000, 0, 0xc000, 0, 0, 0, 0)),
            (bytes(0x10000), (-0x10000, 0, 0x18000, 0x24000, 0x10000, 0x20000, 0x4000)),
            (bytes(0x12345), (-0x4000, 0, 0x18000, 0x1c000, 0x8000, 0, 0)),
            (bytes(0x12345), (-0x10000, 0, 0xc000, (1 << 64) - 1, 0x8000, 0, 0)),
            (bytes(0x12345), (-0x10000, 0, 0x18000, 0x24000, 0x8000, (1 << 64) - 1, 0x4000)),
            (bytes(0x12345), (0x20000, 0, 0xc000, 0, 0, 0, 0)),
            (bytes(0x12345), (0x10000, (1 << 64) - 1, 0xc000, 0, 0, 0, 0)),
            (bytes(0x12345), (0x10000, 0, (1 << 64) - 1, 0, 0, 0, 0)),
        ]
        for data, layout in cases:
            with self.subTest(layout=layout):
                result, mapped = self.run_image(data, layout)
                self.assertEqual(result.returncode, 80, result.stderr.decode())
                self.assertIsNone(mapped)


if __name__ == '__main__':
    unittest.main()
