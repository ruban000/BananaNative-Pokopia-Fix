"""Diagnostic, fail-closed NonWritable proof for logical fragment modules.

Not in the production translation chain. No floating-point instructions,
bindings, layouts or shader interfaces are rewritten. Validate the input and
output with spirv-val. Unsupported modules are returned byte-for-byte.
"""
import vk_android_spv as spv


def mark_readonly(data):
    m = spv.Module(data)
    if m.model != 'ps':
        return data, [], 'not a fragment module'
    if any(x[0] & 65535 == 14 and x[1:] != [0, 1] for x in m.ins):
        return data, [], 'not Logical GLSL450 memory'
    # No unknown extension operations, atomics or external instruction sets
    # whose side effects this proof cannot describe. Pointer types below must
    # establish the destination class of every store in every function.
    for x in m.ins:
        op = x[0] & 65535
        if op > 257 and op not in (330, 4416, 5380, 5381):
            return data, [], f'unsupported opcode {op}'
        if op == 11 and spv.string_at(x, 2)[0] != 'GLSL.std.450':
            return data, [], 'unknown extended instructions'
        if op == 99 or 227 <= op <= 242:
            return data, [], 'image write or atomic access'
        decoration = x[2] if op == 71 else x[3] if op == 72 else None
        if decoration in (21, 23):
            return data, [], 'volatile or coherent memory'
    pointer_types = {i: x[2] for i, x in m.types.items() if x[0] & 65535 == 32}
    pointers = {i: pointer_types[t] for i, (t, _) in m.vars.items() if t in pointer_types}
    in_function = False
    for x in m.ins:
        op = x[0] & 65535
        if op == 54:
            in_function = True
        if in_function and len(x) >= 3 and x[1] in pointer_types:
            pointers[x[2]] = pointer_types[x[1]]
        if op == 56:
            in_function = False
    for x in m.ins:
        if x[0] & 65535 in (62, 63, 64) and pointers.get(x[1]) not in (3, 6, 7):
            # Scan every function, not only the entry point. One storage write
            # refuses ALL buffers: different guest descriptors may alias.
            return data, [], 'nonlocal or unresolved store/copy destination'
        if x[0] & 65535 == 12 and any(p in pointers and pointers[p] not in (3, 6, 7) for p in x[5:]):
            # GLSL.std.450 Modf/Frexp have pointer output operands. Do not
            # assume every extended instruction is a pure arithmetic operation.
            return data, [], 'nonlocal extended-instruction pointer operand'
    buffers = [r for r in m.resources() if r['desc'] == spv.VK_STORAGE_BUFFER
               and 24 not in m.decos.get(r['id'], {})]
    if not buffers:
        return data, [], 'no undecorated storage buffers'
    at = next(i for i, x in enumerate(m.ins) if 19 <= x[0] & 65535 <= 39)
    m.ins[at:at] = [[3 << 16 | 71, r['id'], 24] for r in buffers]
    return m.data(), [{'set': r['set'], 'binding': r['binding']} for r in buffers], 'proved no storage writes'
