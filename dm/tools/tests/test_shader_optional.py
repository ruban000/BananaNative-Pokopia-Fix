"""shader_optional.tsv (dm/games/README.md): a game with several registered builds names the keys of its shader_map.tsv that only some builds have, `key<TAB>build`.
Every key it names is a key of the map, once, and every build it names is a registered build's display version."""
import re
import unittest
from collections import Counter
from pathlib import Path

GAMES = Path(__file__).resolve().parents[2] / 'games'


def rows(path):
    return [l.split('\t') for l in path.read_text().splitlines() if l and not l.startswith('#')]


class ShaderOptional(unittest.TestCase):
    def test_every_game_with_one(self):
        found = sorted(GAMES.glob('*/shader_optional.tsv'))
        self.assertTrue(found, 'no profile has a shader_optional.tsv')
        for f in found:
            g = f.parent
            have = {r[3] for r in rows(g / 'shader_map.tsv') if len(r) > 3 and r[0] != 'package'}
            opt = rows(f)
            self.assertTrue(all(len(r) == 2 and re.fullmatch(r'[0-9a-f]{16}', r[0]) for r in opt), f'{f}: rows are <key> <build>')
            self.assertEqual([k for k, n in Counter(r[0] for r in opt).items() if n > 1], [], f'{f}: a key twice')
            self.assertTrue({r[0] for r in opt} <= have, f'{f}: a key that is not in shader_map.tsv')
            displays = {r[2] for r in rows(g / 'game.tsv') if r[0] == 'build'} | {'split'}   # 'split': a key the splitter no longer reads (a false BAHS entry)
            self.assertTrue({r[1] for r in opt} <= displays, f'{f}: a build that is neither a display version of a build row of game.tsv nor split ({sorted(displays)})')


if __name__ == '__main__':
    unittest.main()
