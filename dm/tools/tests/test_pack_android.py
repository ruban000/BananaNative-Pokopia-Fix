"""scripts/pack_android.py's swap of a rewritten module into a pack (the phone's packer updates the user's pack in place): the module's files go in from its
staging folder by rename and its key file comes last, so a pack stopped before the key is written has a module without a key, which the launcher refuses.
Also: the check of the modules' builds against every build row of the profile."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'scripts'))
import pack_android   # noqa: E402


class Swap(unittest.TestCase):
    def test_swap_in_replaces_files_and_writes_the_key_last(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'main.patched.bin').write_bytes(b'old'); (out / 'main.fixups.tsv').write_text('old\n')
            st = pack_android.staging(out, 'main'); st.mkdir()
            (st / 'main.patched.bin').write_bytes(b'new'); (st / 'main.fixups.tsv').write_text('new\n'); (st / 'build_src.sha256').write_text('x\n')
            seen = []
            real = os.replace
            def spy(a, b): seen.append(Path(b).name); real(a, b)
            pack_android.os.replace = spy
            try: pack_android.swap_in(out, 'main', 'k target=android')
            finally: pack_android.os.replace = real
            self.assertEqual(seen[-1], 'main.android.key', 'the key must go in last')
            self.assertEqual((out / 'main.patched.bin').read_bytes(), b'new')
            self.assertEqual((out / 'main.fixups.tsv').read_text(), 'new\n')
            self.assertEqual((out / 'main.android.key').read_text(), 'k target=android\n')
            self.assertFalse(st.exists())


class Builds(unittest.TestCase):
    def test_every_build_row_counts(self):
        class G:   # a profile with an update's row and the base's, as dm/games/game.py builds() gives them
            def builds(self): return {'v131072': {'display': 'unknown', 'main': 'aa', 'sdk': 'bb'}, 'v0': {'display': 'unknown', 'main': 'cc', 'sdk': 'dd'}}
        self.assertEqual(pack_android.unregistered(G(), {'main': 'aa', 'sdk': 'bb'}), {}, 'the update, named by the first row')
        self.assertEqual(pack_android.unregistered(G(), {'main': 'cc', 'sdk': 'dd'}), {}, 'the base, named by the last row')
        self.assertEqual(pack_android.unregistered(G(), {'main': 'ee', 'subsdk0': 'ff'}), {'main': ['aa', 'cc']}, 'an unknown main; a module no row names is not listed')


if __name__ == '__main__':
    unittest.main()
