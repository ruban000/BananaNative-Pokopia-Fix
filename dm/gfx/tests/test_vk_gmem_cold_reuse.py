"""Cold-word reuse must match the complete search across pool boundaries."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tools'))
sys.path.insert(0, str(HERE))
import vk_android_spv as spv
import test_vk_gmem_cache as reference
from vk_gmem_cold_reuse import rewrite, search_only
from vk_mesh_gmem_probe import helpers


class ColdReuse(reference.GmemCache):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cached = cls.after
        data, cls.changed = rewrite(cls.cached.data())
        cls.after = spv.Module(data)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 's.spv'
            p.write_bytes(data)
            cls.val = subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(p)], capture_output=True, text=True)

    def test_runs(self):
        # The inherited equivalence test executes 200+ input combinations,
        # including zero/unmapped, pool 300, carry and touching/end boundaries.
        self.assertEqual(sorted(h['words'] for h in self.changed), [2, 4])

    def test_scalar_fallback_and_hot_paths_unchanged(self):
        changed = {h['function'] for h in self.changed}
        for h in helpers(self.cached):
            a, b = self.cached.function_range(h['function'])
            c, d = self.after.function_range(h['function'])
            old, new = self.cached.ins[a:b + 1], self.after.ins[c:d + 1]
            if h['function'] not in changed:
                self.assertEqual(old, new)
            else:
                stop = next(i for i, x in enumerate(old) if x[0] & 65535 == 248 and x[1] == h['cold'])
                self.assertEqual(old[:stop], new[:stop])

    def test_unknown_shape_rejected(self):
        with self.assertRaises(ValueError):
            rewrite(self.before.data())


class SearchOnly(reference.GmemCache):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cached = cls.after
        data, cls.changed = search_only(cls.cached.data())
        cls.after = spv.Module(data)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 's.spv'
            p.write_bytes(data)
            cls.val = subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(p)], capture_output=True, text=True)

    def test_runs(self):
        self.assertEqual(len(self.changed), 3)
        self.assertEqual(reference.calls(self.cached), reference.calls(self.after))

    def test_search_loops_preserved(self):
        for opcode in (246, 194):  # both loop merges and every shift/search step
            self.assertEqual([x for x in self.cached.ins if x[0] & 65535 == opcode],
                             [x for x in self.after.ins if x[0] & 65535 == opcode])


if __name__ == '__main__':
    unittest.main()
