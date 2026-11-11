"""Offline candidate: reuse the first word's lookup on a cold multiword load.

The original whole-span hot path stays intact. The cold path calls the existing
single-word cached helper in address order. Each word retains its bounds check,
complete hint/search fallback and zero result outside any mapped pool. This is
outside the production chain until measured on the target driver.
"""
from vk_android_spv import Module
from vk_mesh_gmem_probe import helpers, ins


def search_only(data):
    """Diagnostic alternative for hint-poor workloads; retain all search steps.

    This does not assume that future hints fail: it searches even when a hint
    could succeed. Such workloads may regress, but addresses retain exact lookup.
    """
    m = Module(data)
    matched = helpers(m)
    for h in matched:
        a, b = m.function_range(h['function'])
        body = m.ins[a:b + 1]
        branch = [x for x in body if x[0] & 65535 == 250][1]
        k = body.index(branch)
        merge = body[k - 1]
        if merge != ins(247, branch[2], 0):
            raise ValueError('unknown hint selection')
        label = next(x[1] for x in reversed(body[:k]) if x[0] & 65535 == 248)
        p = next(i + 1 for i, x in enumerate(body) if x == ins(248, branch[2]))
        phi = body[p]
        if phi[0] & 65535 != 245 or len(phi) != 7 or phi[4] != label:
            raise ValueError('unknown hint/search result')
        body[p] = ins(245, phi[1], phi[2], phi[5], phi[6])
        body[k - 1:k + 1] = [ins(249, branch[3])]
        m.ins[a:b + 1] = body
    return m.data(), [dict(function=h['function'], policy='full search on every cold word') for h in matched]


def rewrite(data):
    m = Module(data)
    matched = helpers(m)
    for h in matched:
        a, b = m.function_range(h['function'])
        body = m.ins[a:b + 1]
        h['params'] = [x for x in body if x[0] & 65535 == 55]
        h['type'] = body[0][1]
        branch = [x for x in body if x[0] & 65535 == 250][-1]
        compare = next(x for x in body if len(x) >= 3 and x[2] == branch[1] and x[0] & 65535 == 176)
        h['words'] = m.consts.get(compare[4], (0, []))[1]
        if len(h['words']) != 1 or h['words'][0] not in (1, 2, 3, 4):
            raise ValueError('unknown word count')
        h['words'] = h['words'][0]
    changed = []
    for h in matched:
        n = h['words']
        if n == 1:
            continue
        scalar = [s for s in matched if s['words'] == 1 and s['cache'] == h['cache']
                  and [p[1] for p in s['params']] == [p[1] for p in h['params']]]
        if len(scalar) != 1:
            raise ValueError('no unique compatible scalar fallback')
        u = m.types[h['type']][2]
        boolean = m.add_type(20)
        zero, one = m.add_const(u, 0), m.add_const(u, 1)
        a, b = m.function_range(h['function'])
        body = m.ins[a:b + 1]
        first_branch = next(x for x in body if x[0] & 65535 == 250)
        merge = body[body.index(first_branch) - 1]
        if merge[0] & 65535 != 247:
            raise ValueError('missing hot/cold merge')
        start = next(i for i, x in enumerate(body) if x == ins(248, h['cold']))
        end = next(i for i, x in enumerate(body) if x == ins(248, merge[1]))
        old_phi = body[end + 1]
        if old_phi[0] & 65535 != 245 or len(old_phi) != 7 or old_phi[4] != first_branch[2]:
            raise ValueError('unknown hot/cold result')
        out = [ins(248, h['cold'])]

        def val(op, ty, *args, out=out):
            result = m.new_id()
            out.append(ins(op, ty, result, *args))
            return result

        def cast(value, frm, to):
            return value if frm == to else val(124, to, value)

        pl, ph = h['params']
        lo, hi = cast(pl[2], pl[1], u), cast(ph[2], ph[1], u)
        words = []
        for k in range(n):
            lk = lo if not k else val(128, u, lo, m.add_const(u, 4 * k))
            hk = hi if not k else val(128, u, hi, val(169, u, val(176, boolean, lk, lo), one, zero))
            v = val(57, h['type'], scalar[0]['function'], cast(lk, u, pl[1]), cast(hk, u, ph[1]))
            words.append(val(81, u, v, 0))
        result = val(80, h['type'], *words, *[zero] * (4 - n))
        out.append(ins(249, merge[1]))
        phi = ins(245, old_phi[1], old_phi[2], old_phi[3], old_phi[4], result, h['cold'])
        body = body[:start] + out + [body[end], phi] + body[end + 2:]
        # add_const may have inserted declarations before the function.
        a, b = m.function_range(h['function'])
        m.ins[a:b + 1] = body
        changed.append(dict(function=h['function'], words=n, scalar=scalar[0]['function']))
    return m.data(), changed
