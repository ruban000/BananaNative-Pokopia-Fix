"""Opt-in page candidates for generated cached raw loads; retain exact search.

Requires the host's extended GMEM table (vka_page_index.h). The mesh cache chain
includes this tool and that contract. A zero-page header is the synchronous
original full-search fallback, including allocation/ring pressure on the host.
"""
from vk_android_spv import Module
from vk_mesh_gmem_probe import helpers, ins

HEADER = 8192 * 8


def rewrite(data):
    m = Module(data)
    matched = helpers(m)
    u = m.add_type(21, 32, 0)
    boolean = m.add_type(20)
    ft = m.add_type(33, u, u, u)
    const = {v: m.add_const(u, v) for v in (0, 1, 2, 3, 8, 12, 20, 4096, 8192, HEADER, HEADER + 1, HEADER + 4)}
    changed = []
    for h in matched:
        a, b = m.function_range(h['function'])
        body = m.ins[a:b + 1]
        branch = [x for x in body if x[0] & 65535 == 250][1]
        label = next(x[1] for x in reversed(body[:body.index(branch)]) if x[0] & 65535 == 248)
        merge_at = next(i for i, x in enumerate(body) if x == ins(248, branch[2]))
        phi = body[merge_at + 1]
        if phi[0] & 65535 != 245 or len(phi) != 7 or phi[4] != label:
            raise ValueError('unknown hint result')
        # The generated chunk expression uniquely identifies the cold word's
        # low/high address. Unknown shapes are rejected, not guessed.
        shifts = [x for x in body if x[0] & 65535 == 194 and m.consts.get(x[4], (0, []))[1] == [21]]
        highs = [x for x in body if x[0] & 65535 == 196 and m.consts.get(x[4], (0, []))[1] == [11]]
        tables = {(x[1], x[3]) for x in body if x[0] & 65535 == 65 and x[3] in m.vars}
        if len(shifts) != 1 or len(highs) != 1 or len(tables) != 1:
            raise ValueError('unknown cold address/table')
        ptr, table = tables.pop()
        word = m.pointee(ptr)[1]
        fid, low, high, entry = (m.new_id() for _ in range(4))
        fn = [ins(54, u, fid, 0, ft), ins(55, u, low), ins(55, u, high), ins(248, entry)]

        def val(out, opcode, ty, *args):
            ident = m.new_id()
            out.append(ins(opcode, ty, ident, *args))
            return ident

        def v(opcode, ty, *args, fn=fn):
            return val(fn, opcode, ty, *args)

        def load(at, word=word, ptr=ptr, table=table):
            x = v(61, word, v(65, ptr, table, const[0], at))
            return x if word == u else v(124, u, x)

        def lt(ah, al, bh, bl, equal=False):
            return v(166, boolean, v(176, boolean, ah, bh), v(167, boolean, v(170, boolean, ah, bh), v(178 if equal else 176, boolean, al, bl)))

        base, count = load(const[HEADER]), load(const[HEADER + 1])
        page = v(197, u, v(194, u, low, const[12]), v(196, u, high, const[20]))
        rel = v(130, u, page, base)
        valid = v(167, boolean, v(176, boolean, high, const[4096]), v(167, boolean, v(178, boolean, base, page), v(176, boolean, rel, count)))
        raw = load(v(128, u, const[HEADER + 4], v(169, u, valid, rel, count)))
        index = v(169, u, v(176, boolean, raw, const[8192]), raw, const[0])
        at = v(132, u, index, const[8])
        lo, hi = load(at), load(v(128, u, at, const[1]))
        endlo, endhi = load(v(128, u, at, const[2])), load(v(128, u, at, const[3]))
        inside = v(167, boolean, lt(hi, lo, high, low, True), lt(high, low, endhi, endlo))
        good = v(167, boolean, valid, v(167, boolean, inside, v(171, boolean, index, const[0])))
        answer = v(169, u, good, index, const[0])
        fn += [ins(254, answer), ins(56)]
        insert = []
        candidate = val(insert, 57, u, fid, shifts[0][3], highs[0][3])
        condition = val(insert, 171, boolean, candidate, const[0])
        index = candidate if phi[1] == u else val(insert, 124, phi[1], candidate)
        body[merge_at + 1] = ins(245, phi[1], phi[2], index, phi[4], *phi[5:])
        k = body.index(branch)
        if body[k - 1] != ins(247, branch[2], 0):
            raise ValueError('unknown hint merge')
        body[k] = ins(250, condition, *branch[2:])
        body[k - 1:k - 1] = insert
        a, b = m.function_range(h['function'])
        m.ins[a:b + 1] = body
        m.ins.extend(fn)
        m.index()
        changed.append(h['function'])
    return m.data(), changed
