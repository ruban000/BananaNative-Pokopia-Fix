"""Diagnostic counters for the generated GMEM lookup cache, never a default rewrite.

The caller must certify the compute-mesh ABI: record words 2 and 3 are reserved,
the first entry call initializes the record base, and the whole workgroup enters
that call. Counters use those words without changing geometry or the vertex ABI.
Timings of instrumented modules are not performance measurements.
"""
from vk_android_spv import Module


def ins(op, *args):
    return [(len(args) + 1) << 16 | op, *args]


def helpers(m):
    """Recognize the unoptimized cache_raw_loads helper shape; refuse other forms."""
    result = []
    for decl in m.ins:
        if decl[0] & 65535 != 54 or m.op(decl[1]) != 23:
            continue
        ty = m.types[decl[1]]
        if ty[3] != 4 or m.int_type(ty[2]) != (32, 0):
            continue
        a, b = m.function_range(decl[2])
        body = m.ins[a:b + 1]
        params = [x for x in body if x[0] & 65535 == 55]
        if len(params) != 2 or any(m.int_type(x[1]) not in ((32, 0), (32, 1)) for x in params):
            continue
        branches = [x for x in body if x[0] & 65535 == 250]
        if len(branches) != 4:
            raise ValueError('unknown cached load branch shape')
        first = body.index(branches[0])
        cached = [x[3] for x in body[:first] if x[0] & 65535 == 61
                  and m.vars.get(x[3], (0, 0))[1] == 6]
        if len(cached) != 6 or len(set(cached)) != 6:
            raise ValueError('unknown cache entry shape')
        labels = {x[1]: i for i, x in enumerate(body) if x[0] & 65535 == 248}
        cold, search = branches[0][3], branches[1][3]
        if any(body[labels[v] + 1][0] & 65535 != 249 for v in (cold, search)):
            raise ValueError('unknown cold/search block')
        loops = [x for x in body if x[0] & 65535 == 246]
        if len(loops) != 2:
            raise ValueError('expected word loop and complete binary search')
        word = next(x[1] for x in reversed(body[:body.index(branches[1])]) if x[0] & 65535 == 248)
        result.append(dict(function=decl[2], entry=next(iter(labels)), cold=cold,
                           word=word, search=search, cache=cached))
    if not result:
        raise ValueError('no generated cache helpers')
    return result


def instrument(data, record_words, mode):
    """Count calls/cold entries or cold words/binary searches per workgroup."""
    if mode not in ('cache', 'hint'):
        raise ValueError('mode must be cache or hint')
    m = Module(data)
    if m.model != 'cs' or m.entry is None:
        raise ValueError('compute module required')
    matched = helpers(m)
    output = [r['id'] for r in m.resources() if (r['set'], r['binding']) == (3, 0)]
    lane = [v for v, d in m.decos.items() if d.get(11) == [29]]
    if len(output) != 1 or len(lane) != 1:
        raise ValueError('compute-mesh output/lane missing')
    a, b = m.function_range(m.entry_function())
    entry_body = m.ins[a:b + 1]
    first_call = next(x for x in entry_body if x[0] & 65535 == 57)
    ia, ib = m.function_range(first_call[3])
    init = m.ins[ia:ib + 1]
    products = {x[2] for x in init if x[0] & 65535 == 132
                and m.consts.get(x[4], (0, []))[1] == [record_words]}
    bases = [x[1] for x in init if x[0] & 65535 == 62 and x[2] in products
             and m.vars.get(x[1], (0, 0))[1] == 6]
    if len(bases) != 1:
        raise ValueError('cannot prove record base initialization')
    u = m.add_type(21, 32, 0)
    boolean = m.add_type(20)
    void = m.add_type(19)
    ft = m.add_type(33, void)
    pp = m.add_type(32, 6, u)
    ps = m.add_type(32, 12, u)
    c = {v: m.add_const(u, v) for v in (0, 1, 2, 3, 72)}
    counts = [m.new_id(), m.new_id()]
    for v in counts:
        m._insert_decl(ins(59, pp, v, 6, c[0]))
    if m.header[1] >= 0x10400:
        m.entry.extend(counts)
        m.entry[0] = len(m.entry) << 16 | 15

    def val(out, op, ty, *args):
        ident = m.new_id()
        out.append(ins(op, ty, ident, *args))
        return ident

    def pointer(out, offset):
        base = val(out, 61, u, bases[0])
        index = val(out, 128, u, base, c[offset])
        return val(out, 65, ps, output[0], c[0], index)

    fn, label, yes, merge = [m.new_id() for _ in range(4)]
    zero = [ins(54, void, fn, 0, ft), ins(248, label)]
    pred = val(zero, 170, boolean, val(zero, 61, u, lane[0]), c[0])
    zero += [ins(247, merge, 0), ins(250, pred, yes, merge), ins(248, yes)]
    for k in (2, 3):
        zero.append(ins(62, pointer(zero, k), c[0]))
    zero += [ins(249, merge), ins(248, merge), ins(224, c[2], c[2], c[72]), ins(253), ins(56)]
    labels = {}
    for h in matched:
        labels[h['entry' if mode == 'cache' else 'word']] = counts[0]
        labels[h['cold' if mode == 'cache' else 'search']] = counts[1]
    rewritten = []
    current = None
    returns = 0
    for x in m.ins:
        op = x[0] & 65535
        if op == 54:
            current = x[2]
        if op == 253 and current == m.entry_function():
            for k, count in enumerate(counts):
                p = pointer(rewritten, k + 2)
                value = val(rewritten, 61, u, count)
                val(rewritten, 234, u, p, c[2], c[0], value)
            returns += 1
        rewritten.append(x)
        if x is first_call:
            val(rewritten, 57, void, fn)
        if op == 248 and x[1] in labels:
            count = labels[x[1]]
            n = val(rewritten, 128, u, val(rewritten, 61, u, count), c[1])
            rewritten.append(ins(62, count, n))
    if not returns:
        raise ValueError('entry never returns')
    m.ins = rewritten + zero
    return m.data(), dict(mode=mode, helpers=matched, returns=returns,
                          record_words=record_words, counters='per-workgroup uint32')
