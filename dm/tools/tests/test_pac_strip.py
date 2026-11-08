"""patch_nso.py's pointer authentication pass for --target android (pac_strip), on hand-assembled words, without game files.
A CPU without Armv8.3 PAuth runs the hint forms as no-ops but faults on retab / retaa (Pokopia's sdk+0x1520bc on a Snapdragon 865)."""
import ast
from pathlib import Path
import re
import struct
from typing import Any
import unittest

NOP, RET = 0xD503201F, 0xD65F03C0
tree = ast.parse((Path(__file__).resolve().parents[1] / 'patch_nso.py').read_text())   # the program loads and rewrites an image: compile only the pass
names = {'RET_X30', 'PAC_HINTS', 'PAC_RETS', 'PAC_ALL', 'PAC_RE', 'PAC_BRANCH_RE'}
def targets(n): return {t.id for t in n.targets if isinstance(t, ast.Name)}
body = [n for n in tree.body if isinstance(n, ast.Assign) and targets(n) & names or isinstance(n, ast.FunctionDef) and n.name == 'pac_strip']
ns: dict[str, Any] = {'struct': struct, 're': re, 'NOP': NOP}
exec(compile(ast.Module(body, []), 'patch_nso.py', 'exec'), ns)
strip = ns['pac_strip']

def img(*w): return bytearray(b''.join(struct.pack('<I', x) for x in w))
def words(b): return list(struct.unpack(f'<{len(b) // 4}I', bytes(b)))

PACIASP, PACIBSP, AUTIASP, AUTIBSP, XPACLRI = 0xD503233F, 0xD503237F, 0xD50323BF, 0xD50323FF, 0xD50320FF
RETAA, RETAB = 0xD65F0BFF, 0xD65F0FFF

class PacStrip(unittest.TestCase):
    def test_function_with_retab(self):   # sdk+0x1520b8 of Pokopia v655360: add sp, sp, #0x40; retab
        add_sp = 0x910103FF
        b = img(PACIBSP, 0xD10103FF, add_sp, RETAB)
        n, other = strip(b, [(0, len(b))])
        self.assertEqual((n, other), (2, []))
        self.assertEqual(words(b), [NOP, 0xD10103FF, add_sp, RET])

    def test_every_hint_and_both_rets(self):
        hints = [0xD503211F, 0xD503215F, 0xD503219F, 0xD50321DF, 0xD503231F, PACIASP, 0xD503235F, PACIBSP, 0xD503239F, AUTIASP, 0xD50323DF, AUTIBSP]
        b = img(*hints, RETAA, RETAB)
        self.assertEqual(strip(b, [(0, len(b))])[0], 14)
        self.assertEqual(words(b), [NOP] * 12 + [RET, RET])

    def test_other_words_stay(self):   # a plain ret, bti c, xpaclri (a hint that is harmless on an unsigned lr), an add, and the neighbouring hint numbers
        keep = [RET, 0xD503245F, XPACLRI, 0x8B000020, NOP, 0xD503201F | 0 << 5, 0xD503205F, 0xD50323FF + 0x20]
        b = img(*keep)
        self.assertEqual(strip(b, [(0, len(b))])[0], 0)
        self.assertEqual(words(b), keep)

    def test_ranges_and_alignment(self):   # only inside the ranges; a match across two words (odd offset) is not a word
        straddle = struct.pack('<I', PACIBSP)
        b = bytearray(b'\0\0' + straddle + b'\0\0') + img(RETAB)   # PACIBSP at offset 2, RETAB at 8
        self.assertEqual(strip(b, [(0, 4)])[0], 0)
        self.assertEqual(bytes(b[2:6]), straddle)
        self.assertEqual(strip(b, [(8, 12)])[0], 1)
        self.assertEqual(words(b[8:12]), [RET])

    def test_odd_match_hides_no_word(self):   # the bytes of PACIBSP shifted by one overlap the word after them
        b = img(0x0000_0000, PACIBSP, PACIBSP)
        self.assertEqual(strip(b, [(0, len(b))])[0], 2)

    def test_register_forms_become_nops(self):   # sdk+0x14bb48 pacia x1, x9; sdk+0x157348 xpaci x30; the z forms and autia / autdb
        pacia, xpaci_lr, paciza, autia, autdb, xpacd = 0xDAC10121, 0xDAC143FE, 0xDAC123E1, 0xDAC11001, 0xDAC11C02, 0xDAC147E0
        b = img(pacia, xpaci_lr, paciza, autia, autdb, xpacd)
        self.assertEqual(strip(b, [(0, len(b))]), (6, []))
        self.assertEqual(words(b), [NOP] * 6)

    def test_branch_forms_are_counted_not_changed(self):
        blraaz, braa, eretaa, brabz = 0xD63F081F, 0xD71F0801, 0xD69F0BFF, 0xD61F0C1F
        plain = [0xD61F0000 | 3 << 5, 0xD63F0000 | 8 << 5, RET, 0xD69F03E0, 0x5AC00000, 0xDAC00000, 0xDAC01000]   # br x3, blr x8, ret, eret, rbit, rbit x, clz x (opcode2 00000: not PAuth)
        b = img(blraaz, braa, eretaa, brabz, *plain)
        n, other = strip(b, [(0, len(b))])
        self.assertEqual(n, 0)
        self.assertEqual(other, [0, 4, 8, 12])
        self.assertEqual(words(b)[:4], [blraaz, braa, eretaa, brabz])   # reported, never changed
        self.assertEqual(words(b)[4:], plain)

if __name__ == '__main__':
    unittest.main()
