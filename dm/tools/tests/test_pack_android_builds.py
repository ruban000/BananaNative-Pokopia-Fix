"""A game with several registered builds (Mario Kart World 1.0.0 and 1.8.0: the same game, another module set and another RomFS) packs from a dump of either:
scripts/pack_android.py dump_modules takes the modules of the build row a dump matches, and optional_keys reads shader_optional.tsv, the shader keys only some builds have."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('pack_android', REPO / 'scripts/pack_android.py')
pa = importlib.util.module_from_spec(spec); spec.loader.exec_module(pa)

A, B, C, D = ('%032x' % n for n in (1, 2, 3, 4))   # build IDs


class Profile:   # what dump_modules asks of a game profile
    dir = Path('.')
    def __init__(self, modules, builds): self._m, self._b = modules, builds
    def words(self, key): return self._m.split()
    def builds(self): return self._b


def nso(path, build_id):
    path.write_bytes(b'NSO0' + bytes(0x3c) + bytes.fromhex(build_id) + bytes(0x10))


class DumpModules(unittest.TestCase):
    G = Profile('main subsdk0 subsdk1 sdk', {'v0': {'display': '1.0.0', 'main': A, 'subsdk0': B, 'sdk': C},
                                             'v524288': {'display': '1.8.0', 'main': D, 'subsdk0': C, 'subsdk1': B, 'sdk': A}})

    def exefs(self, files):
        d = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for n, i in files.items(): nso(d / n, i)
        return d

    def test_every_module_there(self):
        self.assertEqual(pa.dump_modules(self.G, self.exefs({'main': D, 'subsdk0': C, 'subsdk1': B, 'sdk': A})), ['main', 'subsdk0', 'subsdk1', 'sdk'])

    def test_build_with_one_module_less(self):
        self.assertEqual(pa.dump_modules(self.G, self.exefs({'main': A, 'subsdk0': B, 'sdk': C})), ['main', 'subsdk0', 'sdk'])

    def test_unregistered_set_is_left_to_the_caller(self):
        self.assertEqual(pa.dump_modules(self.G, self.exefs({'main': A, 'subsdk0': D, 'sdk': C})), ['main', 'subsdk0', 'subsdk1', 'sdk'])   # ids of no row: the caller says what is missing
        self.assertEqual(pa.dump_modules(self.G, self.exefs({'main': A, 'sdk': C})), ['main', 'subsdk0', 'subsdk1', 'sdk'])


class OptionalKeys(unittest.TestCase):
    def test_file(self):
        d = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (d / 'shader_optional.tsv').write_text('# comment\n00aa\t1.0.0\n11bb\t1.8.0\n')
        g = Profile('main', {}); g.dir = d
        self.assertEqual(pa.optional_keys(g), {'00aa', '11bb'})
        g.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.assertEqual(pa.optional_keys(g), set())

    def test_mkw_profile(self):
        d = REPO / 'dm/games/0400C3F00006E000'
        have = {l.split('\t')[3] for l in (d / 'shader_map.tsv').read_text().splitlines() if l and not l.startswith(('#', 'package\t'))}
        opt = {l.split('\t')[0] for l in (d / 'shader_optional.tsv').read_text().splitlines() if l and not l.startswith('#')}
        self.assertTrue(opt and opt <= have, 'every optional key is a key of the union map')


class AbsentKeys(unittest.TestCase):
    def test_three_tools_share_the_file(self):   # the packers write <package>/_absent.tsv, the library build reads it
        for f in ('scripts/pack_android.py', 'dm/gfx/tools/vk_android_build.py', 'android/app/src/ondevice/java/dev/nxnative/android/PackService.java'):
            self.assertIn('_absent.tsv', (REPO / f).read_text(), f)
        self.assertIn('shader_optional.tsv', (REPO / 'android/app/src/ondevice/java/dev/nxnative/android/PackService.java').read_text())


if __name__ == '__main__':
    unittest.main()
