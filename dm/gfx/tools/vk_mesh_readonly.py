"""Offline input-buffer proof for lowered compute meshes; not a chain default.

The caller must certify that host_outputs are private host allocations, disjoint
from ALL guest buffers and physical addresses reachable by this module. A set or
binding number alone is not that certificate. The current compute-mesh replay
binds its own output arena at (3, 0); guest address maps contain NVN pools only.
Validate SPIR-V before/after use. Unknown write provenance keeps original bytes.
"""
import vk_android_spv as spv


def mark_inputs(data, host_outputs, guest_inputs):
    m = spv.Module(data)
    if host_outputs & guest_inputs:
        return data, [], 'host outputs overlap guest inputs'
    if m.model != 'cs':
        return data, [], 'not compute'
    if any(x[0] & 65535 == 14 and x[1:] not in ([0, 1], [5348, 1]) for x in m.ins):
        return data, [], 'unsupported memory model'
    for x in m.ins:
        op = x[0] & 65535
        # Beyond core 1.0, accept only non-writing subgroup ballot/shuffle and
        # module metadata. This deliberately refuses unfamiliar operations.
        if op > 257 and op not in (330, 339, 345):
            return data, [], f'unsupported opcode {op}'
        if op == 99:
            return data, [], 'image write may alias guest buffers'
        if op == 11 and spv.string_at(x, 2)[0] != 'GLSL.std.450':
            return data, [], 'unknown extended instructions'
        dec = x[2] if op == 71 else x[3] if op == 72 else None
        if dec in (21, 23):
            return data, [], 'volatile or coherent memory'
    resources = [r for r in m.resources() if r['desc'] == spv.VK_STORAGE_BUFFER]
    private = {r['id'] for r in resources if (r['set'], r['binding']) in host_outputs}
    inputs = [r for r in resources if (r['set'], r['binding']) in guest_inputs]
    if not private:
        return data, [], 'no certified host output'
    pointer_types = {i: x[2] for i, x in m.types.items() if x[0] & 65535 == 32}
    pointers = {i: pointer_types[t] for i, (t, _) in m.vars.items() if t in pointer_types}
    definitions = {}
    in_function = False
    for x in m.ins:
        op = x[0] & 65535
        if op == 54:
            in_function = True
        if in_function and len(x) >= 3 and x[1] in pointer_types:
            pointers[x[2]] = pointer_types[x[1]]
            definitions[x[2]] = x
        if op == 56:
            in_function = False

    def is_host_pointer(ident):
        seen = set()
        while ident not in seen:
            seen.add(ident)
            if ident in private:
                return True
            x = definitions.get(ident, [])
            # No phi/select, pointer parameters, loads or address conversions:
            # they require a separate alias/provenance proof, so refuse them.
            if not x or x[0] & 65535 not in (65, 66, 67, 70, 83):
                return False
            ident = x[3]
        return False

    def safe_destination(ident):
        storage = pointers.get(ident)
        return storage in (3, 4, 6, 7) or storage == 12 and is_host_pointer(ident)

    for x in m.ins:
        op = x[0] & 65535
        if op in (62, 63, 64, 228):
            destination = x[1]
        elif 227 <= op <= 242:
            destination = x[3]
        else:
            destination = None
        if destination is not None and not safe_destination(destination):
            return data, [], 'guest, physical or unresolved write/atomic'
        if op == 12 and any(p in pointers and not safe_destination(p) for p in x[5:]):
            return data, [], 'nonlocal extended-instruction pointer output'
    marked = [r for r in inputs if 24 not in m.decos.get(r['id'], {})]
    if not marked:
        return data, [], 'no undecorated input buffers'
    at = next(i for i, x in enumerate(m.ins) if 19 <= x[0] & 65535 <= 39)
    m.ins[at:at] = [[3 << 16 | 71, r['id'], 24] for r in marked]
    return m.data(), [{'set': r['set'], 'binding': r['binding']} for r in marked], 'proved only private host/local writes'


def mark_output_writeonly(data, exclusive_outputs):
    """Prove store-only use of host outputs with no other reachable read alias.

    The caller certifies the host allocation/access contract, including unused
    placeholder descriptors that may name the output arena. This function does
    NOT add Restrict: bound descriptor ranges can overlap even when unused.
    A pointer passed to a function, loaded, converted or otherwise escaping the
    recognized access-chain/copy/store graph refuses the entire transformation.
    """
    m = spv.Module(data)
    if m.model != 'cs':
        return data, [], 'not compute'
    targets = [r for r in m.resources() if r['desc'] == spv.VK_STORAGE_BUFFER
               and (r['set'], r['binding']) in exclusive_outputs]
    additions, report = [], []
    for r in targets:
        if any(d in m.decos.get(r['id'], {}) for d in (21, 23, 24)):
            return data, [], 'incompatible output decoration'
        aliases = {r['id']}
        while True:
            before = len(aliases)
            for x in m.ins:
                if x[0] & 65535 in (65, 66, 67, 70, 83) and x[3] in aliases:
                    aliases.add(x[2])
            if len(aliases) == before:
                break
        in_function, stores = False, 0
        for x in m.ins:
            op = x[0] & 65535
            if op == 54:
                in_function = True
            if op == 56:
                in_function = False
            if op == 59 and len(x) > 4 and x[4] in aliases:
                return data, [], 'output pointer initializer escapes'
            if not in_function:
                continue
            if op in (65, 66, 67, 70, 83) and x[3] in aliases:
                if any(i in aliases for i in x[4:]):
                    return data, [], 'unexpected pointer index'
                continue
            if op == 62 and x[1] in aliases and x[2] not in aliases:
                stores += 1
                continue
            # Result IDs of recognized derived pointers were handled above.
            if any(i in aliases for i in x[1:]):
                return data, [], 'output read, atomic or escaping pointer'
        if stores and 25 not in m.decos.get(r['id'], {}):
            additions.append([3 << 16 | 71, r['id'], 25])
            report.append({'set': r['set'], 'binding': r['binding'], 'stores': stores})
    if not additions:
        return data, [], 'no new store-only outputs'
    at = next(i for i, x in enumerate(m.ins) if 19 <= x[0] & 65535 <= 39)
    m.ins[at:at] = additions
    return m.data(), report, 'proved host output stores only'
