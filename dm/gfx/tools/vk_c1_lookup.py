"""Offline small immutable C1 lookups with proved 32-bit index bounds.

The caller must supply only KIND_C1 bindings and their exact library bytes.
Unknown layouts, bounds and memory operands keep the original load. Integer
constants/selects preserve all payload bits, including float subnormals/NaNs.
This diagnostic tool is not enabled in the production translation chain.
"""
import struct

import vk_android_spv as spv

MAX32 = (1 << 32) - 1


class Bounds:
    def __init__(self, m):
        self.m, self.defs, self.memo, self.visiting = m, {}, {}, set()
        self.glsl = {x[1] for x in m.ins if x[0] & 65535 == 11 and spv.string_at(x, 2)[0] == 'GLSL.std.450'}
        active = False
        for x in m.ins:
            op = x[0] & 65535
            if op == 54:
                active = True
            if active and op not in spv.NO_RESULT_OPS and op != 248 and len(x) >= 3 and x[1] in m.types:
                self.defs[x[2]] = x
            if op == 56:
                active = False

    def ty(self, ident):
        return self.m.consts[ident][0] if ident in self.m.consts else self.defs.get(ident, [0, 0])[1]

    def upper(self, ident):
        if ident in self.memo:
            return self.memo[ident]
        if ident in self.visiting or (self.m.int_type(self.ty(ident)) or (0,))[0] != 32:
            return MAX32
        self.visiting.add(ident)
        value = MAX32
        if ident in self.m.consts:
            words = self.m.consts[ident][1]
            if len(words) == 1:
                value = words[0]
        else:
            x = self.defs.get(ident, [0])
            op = x[0] & 65535
            if op in (83, 113, 114, 124) and len(x) == 4 and (self.m.int_type(self.ty(x[3])) or (0,))[0] == 32:
                value = self.upper(x[3])
            elif op == 199 and len(x) == 5:
                value = min(self.upper(x[3]), self.upper(x[4]))
            elif op in (194, 196) and len(x) == 5 and x[4] in self.m.consts:
                shift = self.m.consts[x[4]][1]
                if len(shift) == 1 and shift[0] < 32:
                    u, n = self.upper(x[3]), shift[0]
                    value = u >> n if op == 194 else u << n if u <= MAX32 >> n else MAX32
            elif op == 12 and len(x) == 7 and x[3] in self.glsl and x[4] == 38:  # UMin
                value = min(self.upper(x[5]), self.upper(x[6]))
            elif op == 169 and len(x) == 6:
                value = max(self.upper(x[4]), self.upper(x[5]))
        self.visiting.remove(ident)
        self.memo[ident] = value
        return value


def fold_c1(data, constants, bindings):
    m = spv.Module(data)
    bounds = Bounds(m)
    # Accept only the translator's exact std140 Block { vec4 table[N]; } layout.
    variables = {}
    for r in m.resources():
        if (r['set'], r['binding']) not in bindings or r['desc'] != spv.VK_UNIFORM_BUFFER or r['count'] != 1:
            continue
        ptr, _ = m.vars[r['id']]
        _, st = m.pointee(ptr)
        structure = m.types[st]
        if m.op(st) != 30 or len(structure) != 3 or 2 not in m.decos.get(st, {}):
            continue
        array = structure[2]
        if m.op(array) != 28 or m.decos.get(array, {}).get(6) != [16]:
            continue
        vector = m.types[array][2]
        if m.op(vector) != 23 or m.types[vector][3] != 4:
            continue
        scalar = m.types[vector][2]
        if m.op(scalar) not in (21, 22) or m.types[scalar][2] != 32:
            continue
        if not any(x == [5 << 16 | 72, st, 0, 35, 0] for x in m.ins):
            continue
        length = m.consts.get(m.types[array][3], (0, []))[1]
        if len(length) != 1:
            continue
        variables[r['id']] = length[0]
    plans = []
    for load in m.ins:
        if load[0] & 65535 != 61 or len(load) != 4:
            continue  # Volatile/alignment/other explicit memory operands are untouched.
        chain = bounds.defs.get(load[3], [])
        if len(chain) != 7 or chain[0] & 65535 not in (65, 66) or chain[3] not in variables:
            continue
        if chain[4] not in m.consts or m.consts[chain[4]][1] != [0]:
            continue
        row, column = chain[5:7]
        rows = bounds.upper(row) + 1
        if rows > min(4, variables[chain[3]]) or bounds.upper(column) > 3 or rows * 16 > len(constants):
            continue
        if m.op(load[1]) not in (21, 22) or m.types[load[1]][2] != 32:
            continue
        plans.append((load, row, column, rows))
    if not plans:
        return data, []
    u32, boolean = m.add_type(21, 32, 0), m.add_type(20)
    vector = m.add_type(23, u32, 4)
    bool_vector = m.add_type(23, boolean, 4)
    replacements, report, tables = {}, [], {}
    for load, row, column, rows in plans:
        vectors = []
        for i in range(rows):
            words = struct.unpack_from('<4I', constants, i * 16)
            if words not in tables:
                ids = [m.add_const(u32, word) for word in words]
                ident = m.new_id()
                m._insert_decl([7 << 16 | 44, vector, ident, *ids])
                tables[words] = ident
            vectors.append(tables[words])
        chosen, instructions = vectors[0], []
        for i in range(1, rows):
            comparison, condition, selected = m.new_id(), m.new_id(), m.new_id()
            constant = m.add_const(bounds.ty(row), i)
            instructions.extend([[5 << 16 | 170, boolean, comparison, row, constant],
                                 [7 << 16 | 80, bool_vector, condition, *([comparison] * 4)],
                                 [6 << 16 | 169, vector, selected, condition, vectors[i], chosen]])
            chosen = selected
        scalar = m.new_id()
        instructions.append([5 << 16 | 77, u32, scalar, chosen, column])
        instructions.append([4 << 16 | (83 if load[1] == u32 else 124), load[1], load[2], scalar])
        replacements[load[2]] = instructions
        report.append({'load': load[2], 'rows': rows, 'row_upper': rows - 1, 'column_upper': bounds.upper(column)})
    rewritten = []
    for x in m.ins:
        if x[0] & 65535 == 61 and x[2] in replacements:
            rewritten.extend(replacements[x[2]])
        else:
            rewritten.append(x)
    m.ins = rewritten
    return m.data(), report
