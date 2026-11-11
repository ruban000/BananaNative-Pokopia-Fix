#!/usr/bin/env python3
"""Build a separate experimental library for a driver exposing native mesh/task.

Validate and replace only affected mesh/task SPIR-V modules. The normal pack
and its versioned library are not modified. Select the result with DM_VK_SHLIB
in a test run with its own Vulkan pipeline cache.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import pathlib
import shutil
import struct
import subprocess
import tempfile

import vk_android_delta as delta
from vk_android_mesh import ins
import vk_android_spv as spv


def precise_native(data):
    m = spv.Module(data)
    assert m.model in ('ts', 'ms')
    original = list(m.ins)
    nbool = 0
    for x in original:
        if x[0] & 65535 != 59 or len(x) != 4 or x[3] != 7:
            continue
        t = m.pointee(x[1])[1]
        if m.op(t) != 20:
            continue
        f = next((y[2] for y in m.ins if y[0] & 65535 == 42 and y[1] == t), None)
        if f is None:
            f = m.new_id()
            m._insert_decl(ins(42, t, f))
        m.ins[m.ins.index(x)] = ins(59, *x[1:], f)
        nbool += 1
    precise = [ins(71, x[2], 42) for x in original
               if x[0] & 65535 in (129,131,133,136,140,141,142,143,144,145,146,147,148)
               and 42 not in m.decos.get(x[2], {})]
    if precise:
        j = next(i for i, x in enumerate(m.ins) if 19 <= x[0] & 65535 <= 39)
        m.ins[j:j] = precise
    return m.data(), nbool, len(precise)


def build(src, dst, jobs):
    import zstandard
    if dst.exists():
        raise FileExistsError(dst)
    lib = delta.Library(src)
    try:
        def entry(i):
            _, e, _, _, modules = lib.entry(i)
            out = []
            for j, (variant, size, stored, codec) in enumerate(modules, struct.unpack_from('<I', e, 20)[0]):
                native = delta.spirv(stored, codec, zstandard.ZstdDecompressor())
                if len(native) != size:
                    raise ValueError(f'Native module {j} has an invalid size')
                data, predicates, floats = precise_native(native)
                if not (predicates or floats):
                    continue
                with tempfile.NamedTemporaryFile(suffix='.spv') as f:
                    f.write(data)
                    f.flush()
                    subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', f.name], check=True)
                out.append((j, variant, len(data), zstandard.ZstdCompressor(level=3).compress(data), predicates, floats))
            return out

        with tempfile.TemporaryDirectory(dir=dst.parent) as temp:
            tmp = pathlib.Path(temp) / 'library'
            if subprocess.run(['cp', '-c', str(src), str(tmp)], capture_output=True).returncode:
                shutil.copyfile(src, tmp)
            with tmp.open('r+b') as f, ThreadPoolExecutor(max_workers=jobs) as pool:
                f.seek(0, 2)
                end = f.tell()
                if end != lib.blob_off + lib.blob_size:
                    raise ValueError('Expected the native library blob to end at EOF')
                count = predicates = floats = 0
                indices = (i for i in range(lib.ne) if lib.m[lib.eo + i * delta.ENTRY.size + 16] in (6, 7))
                for modules in pool.map(entry, indices):
                    for i, variant, size, data, nbool, nfloat in modules:
                        off = end - lib.blob_off
                        f.seek(end)
                        f.write(data)
                        f.seek(lib.mo + i * delta.MOD.size)
                        f.write(delta.MOD.pack(off & 0xffffffff, size, len(data), variant, delta.VK['CODEC_ZSTD'], off >> 32))
                        end += len(data)
                        count += 1
                        predicates += nbool
                        floats += nfloat
                head = list(delta.HEAD.unpack_from(lib.m, 0))
                head[-4] = end - lib.blob_off
                f.seek(0)
                f.write(delta.HEAD.pack(*head))
            check = delta.Library(tmp)
            check.close()
            # Hard link in the same filesystem; refuse a competing output.
            dst.hardlink_to(tmp)
    finally:
        lib.close()
    print(f'Native mesh probe: {count} modules, {predicates} defined predicates, {floats} separately rounded operations')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('library', type=pathlib.Path)
    p.add_argument('output', type=pathlib.Path)
    p.add_argument('--jobs', type=int, default=6)
    a = p.parse_args()
    build(a.library, a.output, a.jobs)
