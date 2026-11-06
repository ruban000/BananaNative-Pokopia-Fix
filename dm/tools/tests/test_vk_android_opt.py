"""Pinned Vulkan tensor optimizer: lazy dependency, version guard and C API parity."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from dm.tools.tests import test_dmsh_hmma_vk as hmma
import vk_android_opt as opt


class OptimizerGuard(unittest.TestCase):
    def test_missing_tool(self):
        with self.assertRaisesRegex(ValueError, 'optimizer unavailable'):
            opt.optimizer('/nonexistent/spirv-opt')

    def test_cli_warning_does_not_corrupt_binary(self):
        tool = opt.Optimizer.__new__(opt.Optimizer)
        tool.path, tool.lib = 'test-optimizer', None
        binary = struct.pack('<5I', hmma.spv.MAGIC, 0x10300, 0, 1, 0)
        def run(args, **kwargs):
            self.assertIn('--inline-entry-points-exhaustive', args)
            Path(args[args.index('-o') + 1]).write_bytes(binary)
            return subprocess.CompletedProcess(args, 0, b'warning: function could not be inlined\n', b'')
        diagnostics = []
        with mock.patch.object(opt.subprocess, 'run', side_effect=run):
            result = tool(binary, passes=('--inline-entry-points-exhaustive',), diagnostics=diagnostics)
        self.assertEqual(result, binary)
        self.assertEqual(diagnostics, ['warning: function could not be inlined'])

    def test_wrong_version(self):
        result = subprocess.CompletedProcess([], 0, 'SPIRV-Tools 2025.1\n', '')
        with mock.patch.object(opt.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'requires SPIRV-Tools 2026.4'):
                opt.Optimizer('wrong-version')

    def test_sdk_version_prefix(self):
        identity = 'SPIRV-Tools v2026.4 v2026.4-0-gef96ed7'
        result = subprocess.CompletedProcess([], 0, '', identity + '\n')
        with mock.patch.object(opt.subprocess, 'run', return_value=result):
            tool = opt.Optimizer('sdk-version')
        self.assertEqual(tool.version, opt.VERSION)
        self.assertEqual(tool.identity, identity)
        result.stderr = 'SPIRV-Tools vv2026.4\n'
        with mock.patch.object(opt.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'requires SPIRV-Tools'):
                opt.Optimizer('invalid-prefix')

    def test_unaffected_module_needs_no_optimizer(self):
        # The transform must leave even the original module header/bound intact.
        data = struct.pack('<5I', hmma.spv.MAGIC, 0x10300, 0, 1, 0)
        module = hmma.spv.Module(data)
        with mock.patch.dict(os.environ, {'SPIRV_OPT': '/nonexistent/spirv-opt'}):
            self.assertIs(opt.lower_hmma(module), module)
        self.assertEqual(module.data(), data)


@unittest.skipUnless(hmma.base.XLATE and Path(hmma.base.XLATE).is_file(), 'set XLATE to the pinned translator')
class OptimizerModules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='hmma_optimizer_'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    translate = hmma.base.AmpereLowerings.translate

    def test_hmma_rejects_missing_optimizer(self):
        path, _ = self.translate('missing_optimizer', hmma.fixture_code(), local=[32, 1, 1])
        with mock.patch.dict(os.environ, {'SPIRV_OPT': '/nonexistent/spirv-opt'}):
            with self.assertRaisesRegex(ValueError, 'optimizer unavailable'):
                opt.lower_hmma(hmma.spv.Module(path.read_bytes()))

    @unittest.skipUnless(shutil.which('spirv-opt'), 'no spirv-opt')
    def test_executable_and_shared_library_match(self):
        executable = shutil.which('spirv-opt')
        libdir = Path(executable).resolve().parent.parent / 'lib'
        library = os.environ.get('SPIRV_OPT_LIB') or next((str(libdir / n) for n in
            ('libSPIRV-Tools-opt.so', 'libSPIRV-Tools-opt.dylib') if (libdir / n).is_file()), None)
        if not library: self.skipTest('no SPIRV-Tools optimizer shared library; set SPIRV_OPT_LIB')
        path, _ = self.translate('optimizer_parity', hmma.fixture_code(), local=[32, 1, 1])
        module = hmma.spv.Module(path.read_bytes())
        hmma.spv.compact_hmma(module)
        cli, shared = opt.optimizer(executable), opt.optimizer(library)
        a, b = cli(module.data()), shared(module.data())
        self.assertEqual(a, b)
        self.assertEqual(cli.version, shared.version)
        self.assertEqual(hmma.barrier_signature(hmma.spv.Module(a)), hmma.barrier_signature(module))
        if hmma.base.VAL:
            r = subprocess.run([hmma.base.VAL, '--target-env', 'vulkan1.3', '-'], input=a, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
