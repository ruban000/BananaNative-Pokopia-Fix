"""Pinned local-SSA cleanup for Vulkan's software tensor shaders.

Only modules rewritten by compact_hmma enter this path. The fixed passes remove
the translator's caller temporaries before NIR copy propagation can expand them;
they neither relax floating-point operations nor unroll the compact matrix loop.
SPIRV_OPT names spirv-opt on desktop or libSPIRV-Tools-opt.so on Android.
"""
import ctypes
import functools
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import vk_android_spv as spv

VERSION = '2026.4'  # Android build_spirv_tools.sh: vulkan-sdk-1.4.363.0.
PASSES = (
    '--eliminate-local-single-block', '--eliminate-local-single-store',
    '--ssa-rewrite', '--eliminate-dead-code-aggressive', '--eliminate-dead-variables',
)


class Optimizer:
    MessageConsumer = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_char_p,
                                     ctypes.c_void_p, ctypes.c_char_p)

    class Binary(ctypes.Structure):
        _fields_ = [('code', ctypes.POINTER(ctypes.c_uint32)), ('wordCount', ctypes.c_size_t)]

    def __init__(self, path):
        self.path = path
        self.lib = None
        try:
            if path.endswith(('.so', '.dylib')):
                self._library(path)
            else:
                r = subprocess.run([path, '--version'], capture_output=True, text=True, check=True)
                self.identity = (r.stdout.strip() or r.stderr.strip()).splitlines()[0]
                found = re.match(r'SPIRV-Tools (\S+)', self.identity)
                self.version = found.group(1) if found else ''
        except (OSError, AttributeError, subprocess.SubprocessError, IndexError) as e:
            raise ValueError(f'tensor optimizer unavailable: {path}: {e}') from e
        self.version = self.version.removeprefix('v')  # SDK tags include v; conda's equivalent release omits it.
        if self.version != VERSION:
            raise ValueError(f'tensor optimizer requires SPIRV-Tools {VERSION}, got {self.version!r}: {path}')

    def _library(self, path):
        l = self.lib = ctypes.CDLL(path)
        signatures = {
            'spvSoftwareVersionString': ([], ctypes.c_char_p),
            'spvSoftwareVersionDetailsString': ([], ctypes.c_char_p),
            'spvParseTargetEnv': ([ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)], ctypes.c_bool),
            'spvOptimizerCreate': ([ctypes.c_int], ctypes.c_void_p),
            'spvOptimizerDestroy': ([ctypes.c_void_p], None),
            'spvOptimizerSetMessageConsumer': ([ctypes.c_void_p, self.MessageConsumer], None),
            'spvOptimizerRegisterPassFromFlag': ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_bool),
            'spvOptimizerOptionsCreate': ([], ctypes.c_void_p),
            'spvOptimizerOptionsDestroy': ([ctypes.c_void_p], None),
            'spvOptimizerRun': ([ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
                                 ctypes.POINTER(ctypes.POINTER(self.Binary)), ctypes.c_void_p], ctypes.c_int),
            'spvBinaryDestroy': ([ctypes.POINTER(self.Binary)], None),
        }
        for name, (args, result) in signatures.items():
            f = getattr(l, name); f.argtypes, f.restype = args, result
        self.version = l.spvSoftwareVersionString().decode()
        self.identity = l.spvSoftwareVersionDetailsString().decode().strip()
        env = ctypes.c_int()
        if not l.spvParseTargetEnv(b'vulkan1.3', ctypes.byref(env)):
            raise ValueError(f'tensor optimizer has no vulkan1.3 environment: {path}')
        self.env = env.value

    def __call__(self, data, *, passes=PASSES, diagnostics=None):
        if self.lib is None:
            try:
                # Some passes emit nonfatal diagnostics on stdout. Keep the
                # binary separate so those messages cannot corrupt its header.
                with tempfile.TemporaryDirectory(prefix='dmsh_spirv_opt_') as directory:
                    output = Path(directory) / 'module.spv'
                    r = subprocess.run([self.path, '--target-env=vulkan1.3', *passes, '-o', str(output), '-'],
                                       input=data, capture_output=True)
                    result = output.read_bytes() if r.returncode == 0 else None
            except OSError as e:
                raise ValueError(f'tensor optimizer failed: {e}') from e
            if diagnostics is not None:
                diagnostics.extend(message.decode('utf-8', 'replace').strip()
                                   for message in (r.stdout, r.stderr) if message.strip())
            if r.returncode:
                raise ValueError('tensor optimizer failed: ' + r.stderr.decode('utf-8', 'replace').strip()[:240])
            return result
        l = self.lib
        messages = diagnostics if diagnostics is not None else []
        # The optimizer C wrapper needs a consumer even for nonfatal warnings:
        # otherwise passes that call its empty std::function can abort the host.
        @self.MessageConsumer
        def consume(level, source, position, message):
            if message:
                messages.append(message.decode('utf-8', 'replace'))
        context, options = l.spvOptimizerCreate(self.env), l.spvOptimizerOptionsCreate()
        binary = ctypes.POINTER(self.Binary)()
        try:
            if not context or not options: raise ValueError('tensor optimizer allocation failed')
            l.spvOptimizerSetMessageConsumer(context, consume)
            for flag in passes:
                if not l.spvOptimizerRegisterPassFromFlag(context, flag.encode()):
                    raise ValueError(f'tensor optimizer rejects pass {flag}')
            words = (ctypes.c_uint32 * (len(data) // 4)).from_buffer_copy(data)
            status = l.spvOptimizerRun(context, words, len(data) // 4, ctypes.byref(binary), options)
            if status or not binary: raise ValueError(f'tensor optimizer failed: spvOptimizerRun returned {status}')
            return ctypes.string_at(binary.contents.code, binary.contents.wordCount * 4)
        finally:
            if binary: l.spvBinaryDestroy(binary)
            if options: l.spvOptimizerOptionsDestroy(options)
            if context: l.spvOptimizerDestroy(context)


@functools.lru_cache(maxsize=None)
def _optimizer(path):
    return Optimizer(path)


def optimizer(path=None):
    path = path or os.environ.get('SPIRV_OPT') or shutil.which('spirv-opt')
    if not path: raise ValueError(f'tensor optimizer unavailable: install SPIRV-Tools {VERSION} or set SPIRV_OPT')
    return _optimizer(path)


def lower_hmma(m):
    """Return the same module if absent, otherwise compact and clean its caller SSA."""
    if not spv.compact_hmma(m): return m
    return spv.Module(optimizer()(m.data()))
