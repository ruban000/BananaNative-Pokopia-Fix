"""Page candidates must preserve the complete lookup, boundary and store oracle."""
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch
import test_vk_gmem_cache as reference
import vk_android_spv as spv
from vk_gmem_page_index import rewrite, HEADER
from vk_mesh_gmem_probe import helpers


class PageIndex(reference.GmemCache):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.original = cls.after
        data, cls.changed = rewrite(cls.after.data())
        cls.after = spv.Module(data)
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 's.spv'
            p.write_bytes(data)
            cls.val = subprocess.run(['spirv-val', '--target-env', 'vulkan1.3', str(p)], capture_output=True, text=True)

    def test_runs(self):
        self.assertEqual(len(self.changed), 3)
        self.assertEqual(self.val.returncode, 0, self.val.stderr)

    def test_same_words(self):
        factory = reference.pool_table
        for mode in ('disabled', 'indexed', 'wrong', 'invalid'):
            def table(n, mode=mode):
                words, pools, sink = factory(n)
                base = pools[0][0] >> 12
                count = 0 if mode == 'disabled' else min((pools[-1][1] >> 12) - base, 1 << 19)
                words += [base, count, 0, 0] + [0] * (count + 1)
                for i, (lo, hi, _) in enumerate(pools):
                    for page in range(max(base, (lo + 4095) >> 12), min(base + count, hi >> 12)):
                        words[HEADER + 4 + page - base] = 65535 if mode == 'invalid' else 1 if mode == 'wrong' else i + 1
                return words, pools, sink
            with self.subTest(mode=mode), patch.object(reference, 'pool_table', table):
                super().test_same_words()

    def test_original_search_and_hot_path_kept(self):
        for h in helpers(self.original):
            prefixes = []
            for module in (self.original, self.after):
                a, b = module.function_range(h['function'])
                body = module.ins[a:b + 1]
                branch = next(i for i, x in enumerate(body) if x[0] & 65535 == 250)
                prefixes.append(body[:branch + 1])
            self.assertEqual(*prefixes)
        for opcode in (246,):
            self.assertEqual([x for x in self.original.ins if x[0] & 65535 == opcode], [x for x in self.after.ins if x[0] & 65535 == opcode])
        with self.assertRaises(ValueError):
            rewrite(self.before.data())
