# dm/games/game.py's profile-key validation, on a synthetic profile: pso_gpu (the GPU architectures the app's offline pipeline archive is made for, beside pso_mtl4) takes, none and no others,
# and a profile without the key generates its header unchanged; window_fn takes two getters or "-" and a height bound (loader.c window_apply)
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / 'dm/games/game.py'
ID = '0100000000F0F000'
TSV = ('title_id\t' + ID + '\nname\tTest\nprogram_id\t' + ID + '\nmodules\tmain\nromfs_marker\tromfs.img\n'   # what a header needs beside the keys under test
       'mem_reserve\t0x100000\nmem_alias\t0x100000\nmem_stack\t0x100000\napp_pool\t0x100000\n')


def header(rows):
    """game.py header of a synthetic profile with these extra rows: (exit code, its output)"""
    with tempfile.TemporaryDirectory() as d:
        Path(d, ID).mkdir()
        (Path(d, ID) / 'game.tsv').write_text(TSV + rows)
        r = subprocess.run([sys.executable, str(GAME), 'header', ID, str(Path(d, ID) / 'game.h')],
                           env={**os.environ, 'DM_GAMES_DIR': d}, capture_output=True, text=True)
    return r.returncode, (r.stderr + r.stdout).strip()


class PsoGPU(unittest.TestCase):
    def test_valid_tokens(self):
        for rows in ('', 'pso_mtl4\ton\npso_gpu\tg18p\n', 'pso_mtl4\ton\npso_gpu\tapplegpu_g19p\n',   # without the key: the header is made as before, no message
                     'pso_mtl4\ton\npso_gpu\tg18p g19p\n', 'pso_mtl4\ton\npso_gpu\tg14s g15s g16s g17p\n'):
            rc, out = header(rows)
            self.assertEqual((rc, out), (0, ''), rows)

    def test_rejected_tokens(self):
        for rows in ('pso_gpu\tnone\n',                     # none is not a value: absence of the key says no archive of its own
                     'pso_mtl4\ton\npso_gpu\tg18p none\n', 'pso_gpu\tx g18p!\n', 'pso_gpu\tg14s g15s g16s g17p g18p\n',
                     'pso_gpu\tapplegpu_g19p,x\n'):
            rc, out = header(rows)
            self.assertNotEqual(rc, 0, rows)
            self.assertIn(': pso_gpu takes 1-4 whitespace-separated GPU architectures', out, rows)


class WindowFn(unittest.TestCase):
    B = '9e244438c3d8ef1c703a6a85ef99c4f0'

    def rows(self, rc_out):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ID).mkdir()
            (Path(d, ID) / 'game.tsv').write_text(TSV + rc_out)
            subprocess.run([sys.executable, str(GAME), 'header', ID, str(Path(d, ID) / 'game.h')], env={**os.environ, 'DM_GAMES_DIR': d}, check=True, capture_output=True)
            return [l for l in (Path(d, ID) / 'game.h').read_text().splitlines() if 'WINDOW_FN' in l]

    def test_getters_and_bound(self):
        self.assertIn('{"' + self.B + '", 0x5bf770, 0x5bf780, 2880, 1620}', self.rows(f'window_fn\t{self.B}\t0x5bf770\t0x5bf780\t2880x1620\n')[0])
        self.assertIn('{"' + self.B + '", 0, 0xc10190, 2560, 1440}', self.rows(f'window_fn\t{self.B}\t-\t0xc10190\t2560x1440\n')[0])   # no width: the game derives it

    def test_rejected(self):
        for r in (f'{self.B}\t0x1\t-\t2560x1440', f'{self.B}\t-\t-\t2560x1440', f'{self.B}\tx\t0x1\t2560x1440', f'{self.B[:8]}\t-\t0x1\t2560x1440', f'{self.B}\t-\t0x1\t2560'):
            rc, out = header(f'window_fn\t{r}\n')
            self.assertNotEqual(rc, 0, r)
            self.assertIn('window_fn takes', out, r)


if __name__ == '__main__':
    unittest.main()
