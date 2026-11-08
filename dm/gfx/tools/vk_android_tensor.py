"""Vulkan tensor lowering for the pinned software HMMA contract.

Register-only 32-lane XOR routing preserves ordered FMA/final-half arithmetic.
Conservative SSA/control-flow rewrites keep driver compilation tractable. The
caller retains pre-optimization reflection and descriptor placement. This path
requires the existing Vulkan HMMA subgroup-64 runtime contract.
"""
import vk_android_spv as spv
import vk_android_opt as opt

INDIRECT_ANNOTATIONS = {73, 74, 75, 332, 5632, 5633}
FINAL_PASSES = ('--inline-entry-points-exhaustive', *opt.PASSES,
                '--if-conversion', '--simplify-instructions',
                '--eliminate-dead-branches', '--merge-blocks')


def _annotations(m):
    if any(x[0] & 65535 in INDIRECT_ANNOTATIONS for x in m.ins):
        raise ValueError('tensor lowering rejects indirect annotations')



class CallerContractError(ValueError):
    """A valid helper has a caller outside the proven register-routing contract."""


def _caller_contract(m, helper):
    """Prove the pinned lane/base arguments through single-store local values.

    The register algorithm uses subgroup lane directly. It is only equivalent
    when callers pass lane & 31 and the matching flattened-local scratch base.
    Pointer loads must be dominated by their unique store and cannot escape.
    """
    modes = [x for x in m.modes if x[0] == 17]
    if len(modes) != 1 or len(modes[0]) != 4:
        raise CallerContractError('HMMA caller requires literal LocalSize')
    sx, sy, sz = modes[0][1:]
    if not sx or not sy or not sz or sx * sy * sz % 32:
        raise CallerContractError('HMMA caller requires complete logical32 warps')
    entry = m.entry_function()
    a, b = m.function_range(entry)
    definitions = {}; stores = {}; uses = {}; blocks = {}; at_block = {}
    block = None
    for at in range(a, b + 1):
        x = m.ins[at]; op = x[0] & 65535
        if op == 248:
            block = x[1]; blocks[block] = []
        if block is not None:
            at_block[at] = block; blocks[block].append(at)
        if len(x) > 2 and x[1] in m.types:
            definitions[x[2]] = (at, x)
        if op == 62:
            stores.setdefault(x[1], []).append((at, x))
        # Local pointers used by the expression proof may only have plain
        # direct loads/stores or the already fingerprinted helper's two inputs.
        if op == 61:
            uses.setdefault(x[3], []).append((at, 'load' if len(x) == 4 else 'unsafe'))
        elif op == 62:
            uses.setdefault(x[1], []).append((at, 'store' if len(x) == 3 else 'unsafe'))
            uses.setdefault(x[2], []).append((at, 'unsafe'))
        elif op == 57:
            for arg, value in enumerate(x[4:]):
                uses.setdefault(value, []).append((at, 'call' if x[3] == helper and arg < 2 else 'unsafe'))
        elif op not in (54, 55, 56, 59, 248, 249, 250, 251, 246, 247, 253):
            operands = x[3:] if len(x) > 2 and x[1] in m.types else x[1:]
            if op == 12: operands = x[5:]
            elif op == 81: operands = x[3:4]
            elif op in (79, 82): operands = x[3:5]
            for value in operands:
                uses.setdefault(value, []).append((at, 'unsafe'))
    if not blocks:
        raise CallerContractError('HMMA caller has no entry blocks')
    predecessors = {label: set() for label in blocks}
    for label, positions in blocks.items():
        tail = m.ins[positions[-1]]; op = tail[0] & 65535
        edges = tail[1:2] if op == 249 else tail[2:4] if op == 250 else [tail[2], *tail[4::2]] if op == 251 else []
        for target in edges:
            if target not in blocks: raise CallerContractError('HMMA caller has unknown branch target')
            predecessors[target].add(label)
    first = next(iter(blocks)); all_blocks = set(blocks)
    dom = {label: ({first} if label == first else set(all_blocks)) for label in blocks}
    changed = True
    while changed:
        changed = False
        for label in blocks:
            if label == first: continue
            incoming = predecessors[label]
            new = {label} | (set.intersection(*(dom[p] for p in incoming)) if incoming else set())
            if new != dom[label]: dom[label] = new; changed = True
    def dominates(store, load):
        sb, lb = at_block[store], at_block[load]
        return store < load if sb == lb else sb in dom[lb]
    active = set(); cache = {}
    def expression(value, before):
        key = (value, before)
        if key in cache: return cache[key]
        if value in active: raise CallerContractError('cyclic HMMA caller expression')
        active.add(value)
        if value in m.consts:
            ty, words = m.consts[value]
            if m.int_type(ty) not in ((32, 0), (32, 1)) or len(words) != 1:
                raise CallerContractError('non-integer HMMA caller constant')
            result = words[0]
        elif value in m.vars:
            if m.vars[value][1] == 1:
                builtin = m.decos.get(value, {}).get(11)
                if builtin not in ([27], [41]): raise CallerContractError('unexpected HMMA caller input')
                result = ('builtin', builtin[0])
            else:
                writes = stores.get(value, [])
                if m.vars[value][1] != 7 or value in m.decos or len(writes) != 1:
                    raise CallerContractError('HMMA lane/base local is not undecorated single-store')
                if any(kind == 'unsafe' for _, kind in uses.get(value, [])):
                    raise CallerContractError('HMMA lane/base local escapes')
                at, store = writes[0]
                if not dominates(at, before): raise CallerContractError('HMMA lane/base store does not dominate use')
                result = expression(store[2], at)
        else:
            if value not in definitions: raise CallerContractError('HMMA caller expression outside entry')
            at, x = definitions[value]; op = x[0] & 65535
            if op in (61, 83, 124):
                if len(x) != 4: raise CallerContractError('non-plain HMMA caller load/copy')
                result = expression(x[3], at)
            elif op in (65, 66):
                result = ('access', expression(x[3], at), tuple(expression(i, at) for i in x[4:]))
            elif op in (128, 132, 194, 196, 199):
                result = (op, *(expression(i, at) for i in x[3:]))
            else: raise CallerContractError('unexpected HMMA caller expression opcode')
        active.remove(value); cache[key] = result
        return result
    local = lambda axis: ('access', ('builtin', 27), (axis,))
    expected_lane = (199, ('builtin', 41), 31)
    expected_base = (196, (194, (128, local(0), (128, (132, local(1), sx), (132, local(2), sx * sy))), 5), 8)
    calls = 0
    for at, x in enumerate(m.ins):
        if x[0] & 65535 != 57 or x[3] != helper: continue
        if not a < at < b: raise CallerContractError('HMMA call outside entry')
        if len(x) != 15: raise CallerContractError('unexpected original HMMA call ABI')
        if expression(x[4], at) != expected_lane or expression(x[5], at) != expected_base:
            raise CallerContractError('noncanonical HMMA lane/base expression')
        calls += 1
    if not calls: raise CallerContractError('HMMA helper has no callers')
    return calls


def _rewrite_hmma(m, barriers=False):
    targets = [i for i, name in m.names.items() if name == 'dmsh_hmma16816_f16']
    if not targets: return 0
    if len(targets) != 1 or m.model != 'cs': raise ValueError('expected one compute HMMA helper')
    fid = targets[0]
    digest, scratch, removed = spv._hmma_contract(m, fid)
    # Match compact_hmma's full pinned implementation before considering a
    # caller fallback; a changed helper is never silently accepted unchanged.
    if digest != '17475bdb479798b4984357a3d168c67568e4c1cc833da69dc27e5dc145a89f05':
        raise ValueError('tensor software helper contract changed')
    decorated = {i for i in removed if i in m.decos}
    fmas = {x[2] for x in m.ins if x[0] & 65535 == 12 and x[2] in removed and x[4] == 50}
    if decorated and (decorated != fmas or any(m.decos[i] != {42: []} for i in decorated)):
        raise ValueError('tensor helper has unexpected decorations')
    if m.decos.get(scratch):
        raise ValueError('unexpected HMMA scratch decorations')
    start, end = m.function_range(fid)
    # The pinned translator generates these two barriers solely to publish and
    # reuse its dedicated HMMA scratch. Guest HMMA has no external memory-fence
    # guarantee: dropping these emulation barriers is justified by that source
    # contract, not by arbitrary SPIR-V barrier equivalence. Guest barriers in
    # the caller are retained verbatim. Scratch must have no outside observer.
    function = None
    for i, x in enumerate(m.ins):
        op = x[0] & 65535
        if op == 54: function = x[2]
        if op == 56: function = None
        if start <= i <= end: continue
        if op in (5, 71) and x[1] == scratch: continue
        if op == 59 and x[2] == scratch: continue
        if op == 15: continue  # Entry-point interface declarations are not memory accesses.
        if function is None:
            # Types/constants/debug declarations cannot use a Workgroup variable
            # as a value in a valid module. Constant payloads are literal words.
            if op == 59 and len(x) > 4 and x[4] == scratch:
                raise ValueError('HMMA scratch escapes through a global initializer')
            continue
        operands = x[1:]
        if op == 12: operands = x[1:4] + x[5:]  # ExtInst instruction number is literal.
        elif op in (79, 82): operands = x[1:5]  # VectorShuffle / CompositeInsert indices are literal.
        elif op == 81: operands = x[1:4]  # CompositeExtract indices are literal.
        elif op in (246, 247, 249, 250, 251):
            continue  # Structured control operands cannot be Workgroup pointers.
        if scratch in operands: raise ValueError('HMMA scratch referenced outside helper')
    lanes = [i for i, d in m.decos.items() if d.get(11) == [41]]
    if len(lanes) != 1: raise ValueError('expected SubgroupLocalInvocationId input')
    lane_var = lanes[0]
    lane_type = m.pointee(m.vars[lane_var][0])[1]
    if m.int_type(lane_type) != (32, 0): raise ValueError('unexpected subgroup lane type')
    _caller_contract(m, fid)
    # A barrier can synchronize a partial subgroup tangle, whereas a butterfly
    # exchange needs every intermediate source lane active. Prove whole-entry
    # uniform control on an SSA-only copy; never strengthen an unknown caller.
    proof = spv.Module(opt.optimizer()(m.data()))
    try:
        _uniform_entry(proof, _definitions(proof))
    except ValueError as error:
        raise CallerContractError('HMMA participation unproven: ' + str(error)) from error
    calls = spv.compact_hmma(m)
    start, end = m.function_range(fid)
    old = m.ins[start:end+1]
    header = old[0]
    params = [x for x in old if x[0] & 65535 == 55]
    if len(params) != 10: raise ValueError('expected compact value ABI')
    args = [x[2] for x in params]
    nonresults = {56, 62, 224, 246, 249, 250, 254}
    removed = {x[1] if x[0] & 65535 == 248 else x[2] for x in old if x[0] & 65535 not in nonresults}
    precise = any(i in m.decos for i in removed)
    if any(m.decos[i] != {42: []} for i in removed if i in m.decos):
        raise ValueError('unexpected compact helper decoration')
    u = m.add_type(21, 32, 0); f = m.add_type(22, 32); boolean = m.add_type(20)
    v2f = m.add_type(23, f, 2); v2u = m.add_type(23, u, 2)
    glsl = next(x[1] for x in m.ins if x[0] & 65535 == 11 and spv.string_at(x, 2)[0] == 'GLSL.std.450')
    const = {v: m.add_const(u, v) for v in (0, 1, 2, 3, 4, 8, 9, 16, 18, 264, 0xffffffe0)}
    body = [header, *params]; fmas = []

    def ins(op, *a): return [((len(a)+1) << 16) | op, *a]
    def emit(op, *a): body.append(ins(op, *a))
    def val(op, ty, *a):
        ident = m.new_id(); emit(op, ty, ident, *a); return ident
    def add(a, b): return val(128, u, a, b)
    def shl(a, b): return val(196, u, a, const[b])
    def unpack(a): return val(12, v2f, glsl, 62, a)
    def component(a, i): return val(81, f, a, i)
    def xorb(value, mask): return val(346, u, const[3], value, const[mask])
    def different(value, mask): return val(171, boolean, val(199, u, value, const[mask]), const[0])
    def both(value, mask):
        other = xorb(value, mask); condition = different(raw_lane, mask)
        return (val(169, u, condition, other, value), val(169, u, condition, value, other))
    def tree(value, low, high):
        lo0, lo1 = both(value, low)
        v00, v01 = both(lo0, high); v10, v11 = both(lo1, high)
        return (v00, v10, v01, v11)

    def barrier():
        if barriers: emit(224, const[3], const[3], const[264])

    emit(248, m.new_id())
    raw_lane = val(61, u, lane_var)
    warp = val(199, u, raw_lane, const[0xffffffe0])
    row = val(194, u, args[0], const[2])
    pair = val(199, u, args[0], const[3])
    a_base = add(warp, shl(row, 2))
    b_base = add(warp, shl(pair, 3))
    barrier()
    afrags = [tree(args[2+r], 1, 2) for r in range(4)]
    bfrags = []
    for reg in (6, 7):
        value = args[reg]
        for low, high, mask in ((0, 3, 9), (1, 4, 18)):
            lo = val(194, u, raw_lane, const[low])
            hi = val(194, u, raw_lane, const[high])
            condition = different(val(198, u, lo, hi), 1)
            value = val(169, u, condition, xorb(value, mask), value)
        bfrags.append([both(v, 4) for v in tree(value, 8, 16)])
    cc = [unpack(args[8]), unpack(args[9])]
    acc = [component(cc[r], c) for r in range(2) for c in range(2)]
    for k in range(8):
        aa = [unpack(afrags[r + (2 if k >= 4 else 0)][k & 3]) for r in range(2)]
        bb = [unpack(bfrags[1 if k >= 4 else 0][k & 3][c]) for c in range(2)]
        for r in range(2):
            for c in range(2):
                i = 2*r+c
                for half in range(2):
                    acc[i] = val(12, f, glsl, 50, component(aa[r], half), component(bb[c], half), acc[i])
                    fmas.append(acc[i])
    packed = [val(12, u, glsl, 58, val(80, v2f, acc[2*r], acc[2*r+1])) for r in range(2)]
    barrier()
    emit(254, val(80, v2u, *packed)); emit(56)
    rewritten = []; inside = False
    for x in m.ins:
        op = x[0] & 65535
        if op == 71 and x[1] in removed: continue
        if op == 5 and x[1] in removed and x[1] != fid: continue
        if op == 54 and x[2] == fid: rewritten.extend(body); inside = True
        if inside:
            if op == 56: inside = False
            continue
        rewritten.append(x)
    if 65 not in m.caps:
        at = next(i for i, x in enumerate(rewritten) if x[0] & 65535 != 17)
        rewritten.insert(at, ins(17, 65))
    if precise:
        at = next(i for i, x in enumerate(rewritten) if 19 <= x[0] & 65535 <= 39)
        rewritten[at:at] = [ins(71, i, 42) for i in fmas]
    m.ins = rewritten; m.index()
    assert len(fmas) == 64
    return calls



def _promote_readonly(m):
    _annotations(m)
    functions={}; fid=None
    for x in m.ins:
        opcode=x[0]&65535
        if opcode==54:fid=x[2];functions[fid]=[]
        if fid is not None:functions[fid].append(x)
        if opcode==56:fid=None
    plans={}; skipped={}
    for fid,body in functions.items():
        params=[x for x in body if x[0]&65535==55]
        if not params:continue
        if not all(m.op(p[1])==32 and m.pointee(p[1])[0]==7 and m.op(m.pointee(p[1])[1]) in (20,21,22) for p in params):
            skipped[fid]='signature is not entirely scalar Function pointers';continue
        if fid in m.decos or any(p[2] in m.decos for p in params):
            skipped[fid]='function or parameter decorations';continue
        ids={p[2]:m.pointee(p[1])[1] for p in params}
        loads=[]; safe=True
        for x in body:
            opcode=x[0]&65535
            if opcode==55:continue
            refs=set(x[1:])&ids.keys()
            if refs:
                if len(x)!=4 or opcode!=61 or refs!={x[3]} or x[1]!=ids[x[3]]:
                    safe=False;break
                loads.append(x[2])
        if not safe:skipped[fid]='parameter escapes, is written, or uses non-plain load';continue
        fn_type=m.types[body[0][4]]
        if fn_type[2:]!=[body[0][1],*[p[1] for p in params]]:raise ValueError('signature mismatch')
        calls=[x for x in m.ins if x[0]&65535==57 and x[3]==fid]
        if any(len(x)!=4+len(params) or x[1]!=body[0][1] for x in calls):raise ValueError('call signature mismatch')
        if any(m.vars.get(arg)!=(param[1],7) or arg in m.decos for call in calls for arg,param in zip(call[4:],params)):
            skipped[fid]='caller argument is not an undecorated direct Function variable';continue
        plans[fid]=dict(return_type=body[0][1],params=ids,types=list(ids.values()),loads=loads,calls=len(calls))
    # Every eligibility check precedes mutation. No generic instruction operands
    # are replaced; original load result IDs and all their uses remain unchanged.
    for plan in plans.values():plan['function_type']=m.add_type(33,plan['return_type'],*plan['types'])
    rewritten=[];fid=None
    for x in m.ins:
        opcode=x[0]&65535
        if opcode==54:fid=x[2]
        plan=plans.get(fid)
        if opcode==54 and plan:
            rewritten.append([x[0],x[1],x[2],x[3],plan['function_type']])
        elif opcode==55 and plan:
            rewritten.append([x[0],plan['params'][x[2]],x[2]])
        elif opcode==61 and plan and x[3] in plan['params']:
            rewritten.append([4<<16|83,x[1],x[2],x[3]])  # OpCopyObject
        elif opcode==57 and x[3] in plans:
            values=[]
            for ty,ptr in zip(plans[x[3]]['types'],x[4:]):
                value=m.new_id();rewritten.append([4<<16|61,ty,value,ptr]);values.append(value)
            rewritten.append([x[0],x[1],x[2],x[3],*values])
        else:rewritten.append(x)
        if opcode==56:fid=None
    m.ins=rewritten;m.index()
    return dict(promoted={fid:dict(parameters=len(p['params']),plain_loads=len(p['loads']),calls=p['calls']) for fid,p in plans.items()},skipped=skipped)



def _definitions(m):
    result={};inside=False
    for x in m.ins:
        op=x[0]&65535
        if op==54:inside=True
        if inside and len(x)>2 and x[1] in m.types:result[x[2]]=x
        if op==56:inside=False
    return result


def _uniform_entry(m,defs):
    modes=[x for x in m.modes if x[0]==17]
    if len(modes)!=1 or modes[0][1]*modes[0][2]*modes[0][3]%32:
        raise ValueError('LDSM routing requires complete logical32 warps')
    a,b=m.function_range(m.entry_function());body=m.ins[a:b+1]
    pure={81,83,109,112,113,114,124,126,128,130,132,133,134,135,137,138,139,164,165,166,167,168,169,
          170,171,172,173,174,175,176,177,178,179,194,195,196,197,198,199,200}
    cache={};active=set()
    bools={x[2] for x in m.ins if x[0]&65535 in (41,42)}
    glsl={x[1] for x in m.ins if x[0]&65535==11 and spv.string_at(x,2)[0]=='GLSL.std.450'}
    def check(v):
        if v in m.consts or v in bools:return True
        if v in cache:return cache[v]
        if v in active:return True  # Only the separately checked induction cycle can recur.
        x=defs.get(v)
        if x is None:return False
        op=x[0]&65535;active.add(v);result=False
        if op==61 and len(x)==4:
            ptr=x[3];p=defs.get(ptr)
            if p and p[0]&65535 in (65,66):
                result=(m.decos.get(p[3],{}).get(11)==[26] and all(i in m.consts for i in p[4:]))
            else:result=m.decos.get(ptr,{}).get(11)==[26]
        elif op==12 and x[3] in glsl and x[4] in (3,5,80):
            result=all(check(i) for i in x[5:])
        elif op in pure:
            args=x[3:4] if op==81 else x[3:]
            result=all(check(i) for i in args)
        elif op==245 and len(x)==7:
            incoming=(x[3],x[5]);initial=[i for i in incoming if i in m.consts]
            step=defs.get(next((i for i in incoming if i not in m.consts),-1))
            result=(len(initial)==1 and step is not None and step[0]&65535==128 and
                    v in step[3:] and all(i==v or i in m.consts for i in step[3:]))
        active.remove(v);cache[v]=result;return result
    for x in body:
        op=x[0]&65535
        if op in (252,4416,5380):raise ValueError('LDSM routing cannot change active invocations')
        if op in (250,251) and not check(x[1]):
            raise ValueError('LDSM routing requires uniform entry-point control')
    return True


def _rewrite_ldsm(m):
    shuffles=[x for x in m.ins if x[0]&65535==345]
    if not shuffles:return 0
    if m.model!='cs':raise ValueError('LDSM routing requires compute')
    if any(x[0]&65535 in (73,74,75,332,5632,5633) for x in m.ins):
        raise ValueError('LDSM routing rejects indirect annotations')
    defs=_definitions(m);_uniform_entry(m,defs)
    lanes=[i for i,d in m.decos.items() if d.get(11)==[41]]
    if len(lanes)!=1:raise ValueError('LDSM routing expects one subgroup lane input')
    lane=lanes[0];lane_ty=m.pointee(m.vars[lane][0])[1]
    if m.int_type(lane_ty)!=(32,0):raise ValueError('LDSM routing expects uint32 subgroup lane')
    def expr(v,depth=0):
        if depth>24:raise ValueError('LDSM routing source expression too deep')
        if v in m.consts:return m.consts[v][1][0]
        x=defs.get(v)
        if not x:raise ValueError('LDSM routing source is not lane-only SSA')
        op=x[0]&65535
        if op==61 and len(x)==4 and x[3]==lane:return 'lane'
        if op in (83,124):return expr(x[3],depth+1)
        if op in (128,194,196,197,199):return (op,expr(x[3],depth+1),expr(x[4],depth+1))
        raise ValueError('LDSM routing source expression changed')
    warp=(199,'lane',0xffffffe0);low=(199,'lane',31)
    patterns={}
    for group in range(4):
        part=(194,low,2)
        if group:part=(128,part,group*8)
        patterns[(197,warp,part)]=(16,8,4,2,1)
        for column in range(2):
            part=(196,(199,low,3),1)
            if column:part=(128,part,column)
            if group:part=(128,part,group*8)
            patterns[(197,warp,part)]=(1,2,4,8,16)
    plans={};function=None
    for x in m.ins:
        op=x[0]&65535
        if op==54:function=x[2]
        if op==56:function=None
        if op!=345:continue
        if function!=m.entry_function() or len(x)!=6 or m.consts.get(x[3],(None,[]))[1]!=[3]:
            raise ValueError('LDSM routing expects entry-point subgroup shuffles')
        order=patterns.get(expr(x[5]))
        if order is None:raise ValueError('LDSM routing source pattern changed')
        if x[2] in m.decos:raise ValueError('LDSM routing rejects decorated shuffle results')
        plans[x[2]]=order
    # Every guard precedes mutation. All original IDs/annotations on other code survive.
    u=m.add_type(21,32,0);boolean=m.add_type(20)
    const={v:m.add_const(u,v) for v in (0,1,2,3,4,8,16)}
    output=[]
    def emit(op,*args):output.append([((len(args)+1)<<16)|op,*args])
    def val(op,ty,*args,ident=None):
        ident=m.new_id() if ident is None else ident;emit(op,ty,ident,*args);return ident
    for x in m.ins:
        if x[0]&65535!=345:output.append(x);continue
        value=x[4];raw=val(61,u,lane);source=x[5]
        source_type=defs[source][1] if source in defs else m.consts[source][0]
        if source_type!=u:source=val(124,u,source)
        delta=val(198,u,raw,source)
        for i,mask in enumerate(plans[x[2]]):
            exchange=val(346,x[1],const[3],value,const[mask])
            condition=val(171,boolean,val(199,u,delta,const[mask]),const[0])
            value=val(169,x[1],condition,exchange,value,ident=x[2] if i==4 else None)
    m.ins=output;m.index();return len(plans)


def _natural_helpers(m):
    functions={};current=None
    for x in m.ins:
        op=x[0]&65535
        if op==54:current=x[2];functions[current]=[]
        if current is not None:functions[current].append(x)
        if op==56:current=None
    plans={};skipped={}
    for fid,body in functions.items():
        if fid==m.entry_function():skipped[fid]='entry point retained';continue
        returns=[x for x in body if x[0]&65535 in (253,254)]
        if len(returns)<2:continue
        blocks=[]
        for x in body:
            op=x[0]&65535
            if op==248:blocks.append([])
            if blocks and op!=56:blocks[-1].append(x)
        if any(x[0]&65535 in (246,251) for x in body):skipped[fid]='loop or switch';continue
        if not blocks or len(blocks[-1])!=2 or blocks[-1][-1][0]&65535 not in (253,254):
            skipped[fid]='final block is not a simple return';continue
        final=blocks[-1][0][1];ret=blocks[-1][-1];return_op=ret[0]&65535
        merges=[x for x in blocks[0] if x[0]&65535==247]
        branch=blocks[0][-1]
        if len(merges)!=1 or merges[0][1]!=final or branch[0]&65535!=250 or branch[2:4].count(final)!=1:
            skipped[fid]='final return is not the entry selection merge';continue
        if any(x[0]&65535!=return_op for x in returns):
            skipped[fid]='mixed return kinds';continue
        labels={block[0][1] for block in blocks};incoming={};redirect={};valid=True
        for block in blocks[:-1]:
            label=block[0][1];term=block[-1];op=term[0]&65535
            if op in (253,254):
                redirect[label]=term
                incoming[label]=term[1] if return_op==254 else None
            elif op in (249,250):
                targets=term[1:2] if op==249 else term[2:4]
                if any(t not in labels for t in targets):valid=False;break
                if final in targets:incoming[label]=ret[1] if return_op==254 else None
            else:valid=False;break
        if not valid or not redirect or not incoming:
            skipped[fid]='unexpected terminator or predecessor';continue
        if len(redirect)!=1 or next(iter(redirect))!=blocks[-2][0][1] or sum(x[0]&65535==247 and x[1]==final for x in body)!=1:
            skipped[fid]='early return is not the natural outer-arm terminal';continue
        # Serialized block order does not establish construct membership. An
        # early return can be textually last yet still lie inside an inner if.
        # Walk each inner selection until its merge; the redirected terminal
        # must be outside every such construct, including nested selections.
        by_label={block[0][1]:block for block in blocks}
        early=next(iter(redirect));nested=False
        for block in blocks[1:]:
            inner=[x for x in block if x[0]&65535==247]
            if not inner:continue
            if len(inner)!=1 or block[-1][0]&65535!=250:
                nested=True;break
            stop=inner[0][1];pending=list(block[-1][2:4]);seen=set()
            while pending:
                node=pending.pop()
                if node==stop or node in seen:continue
                if node==early:nested=True;break
                seen.add(node);term=by_label[node][-1];op=term[0]&65535
                if op==249:pending.append(term[1])
                elif op==250:pending.extend(term[2:4])
            if nested:break
        if nested:
            skipped[fid]='early return is inside an inner selection';continue
        # The original module is validated; each returned value dominates its
        # return terminator. Redirecting that edge preserves dominance for Phi.
        plans[fid]=dict(final=final,return_type=body[0][1],return_op=return_op,
                        incoming=incoming,redirect=redirect)
    for plan in plans.values():
        if plan['return_op']==254:plan['phi']=m.new_id()
    output=[];current=None;label=None
    for x in m.ins:
        op=x[0]&65535
        if op==54:current=x[2]
        if op==248:label=x[1]
        plan=plans.get(current)
        if plan and label in plan['redirect'] and op in (253,254):
            output.append([2<<16|249,plan['final']])
        elif plan and label==plan['final'] and op==248:
            output.append(x)
            if plan['return_op']==254:
                args=[plan['return_type'],plan['phi']]
                for pred,value in plan['incoming'].items():args.extend((value,pred))
                output.append([((len(args)+1)<<16)|245,*args])
        elif plan and label==plan['final'] and op==254:
            output.append([2<<16|254,plan['phi']])
        else:output.append(x)
        if op==56:current=None;label=None
    m.ins=output;m.index()
    return dict(normalized={fid:dict(early_returns=len(p['redirect']),predecessors=len(p['incoming']),phi=p.get('phi')) for fid,p in plans.items()},skipped=skipped)


def _natural_entry(m):
    entry=m.entry_function(); body=[]; active=False
    for x in m.ins:
        op=x[0]&65535
        if op==54:active=x[2]==entry
        if active:body.append(x)
        if op==56:active=False
    def reject(reason):return dict(normalized=False,reason=reason)
    if not body or m.op(body[0][1])!=19:return reject('entry is not void')
    if any(x[0]&65535 in (245,246,251,252,254,255,4416) for x in body):
        return reject('entry has Phi, loop, switch, or unsupported exit')
    blocks=[]
    for x in body:
        if x[0]&65535==248:blocks.append([])
        if blocks and x[0]&65535!=56:blocks[-1].append(x)
    if len(blocks)<3 or len(blocks)%2!=1:return reject('entry is not an alternating guard chain')
    pairs=[]
    for i in range(0,len(blocks)-1,2):
        header,then,continuation=blocks[i:i+3]
        if len(header)<3 or header[-2][0]&65535!=247 or header[-1][0]&65535!=250:
            return reject('guard lacks terminal selection and conditional branch')
        merge,branch=header[-2:];next_label=continuation[0][1]
        if merge[1]!=next_label or branch[2:4]!=[then[0][1],next_label]:
            return reject('guard is not true-return / false-continuation')
        if any(x[0]&65535 in (247,249,250,253) for x in header[1:-2]):
            return reject('unexpected control within guard')
        if then[-1]!=[1<<16|253] or any(x[0]&65535 in (247,249,250,253) for x in then[1:-1]):
            return reject('then arm is not straight-line return')
        pairs.append((header,then))
    final=blocks[-1]
    if final[-1]!=[1<<16|253] or any(x[0]&65535 in (247,249,250,253) for x in final[1:-1]):
        return reject('final continuation is not straight-line return')
    # Exact accepted control edges give each original continuation one predecessor;
    # no Phi is present, and every then arm is the natural terminal of its if.
    exits=[m.new_id() for _ in pairs]
    replacements={}
    for (header,then),target in zip(pairs,exits):
        replacements[id(header[-2])]=[3<<16|247,target,header[-2][2]]
        replacements[id(then[-1])]=[2<<16|249,target]
    replacements[id(final[-1])]=[2<<16|249,exits[-1]]
    output=[];active=False
    for x in m.ins:
        op=x[0]&65535
        if op==54:active=x[2]==entry
        if active and op==56:
            for i in range(len(exits)-1,-1,-1):
                output.append([2<<16|248,exits[i]])
                output.append([2<<16|249,exits[i-1]] if i else [1<<16|253])
        output.append(replacements.get(id(x),x))
        if op==56:active=False
    m.ins=output;m.index()
    return dict(normalized=True,guards=len(pairs),old_returns=len(pairs)+1,new_returns=1)




def prepare(m, report=None):
    """Atomically rewrite tensor helpers before reflection; leave other modules identical."""
    if not any(name == 'dmsh_hmma16816_f16' for name in m.names.values()):
        return 0
    _annotations(m)
    work = spv.Module(m.data())
    try:
        count = _rewrite_hmma(work)
    except CallerContractError as error:
        if report is not None:
            report.update(status='unchanged', reason=str(error))
        return 0
    promoted = _promote_readonly(work)
    if report is not None:
        report.update(status='optimized', calls=count, readonly=promoted)
    m.ins = work.ins
    m.header = work.header
    m.index()
    return count


def finish(data, optimizer=None):
    """Optimize a prepared, descriptor-placed tensor module; preserve external metadata."""
    run = optimizer or opt.optimizer()
    m = spv.Module(run(data))
    # LDSM routing is optional: any unsupported expression/control keeps the
    # original operations. Its planner validates everything before mutation.
    try:
        changed = _rewrite_ldsm(m)
    except ValueError:
        changed = 0
    if changed:
        m = spv.Module(run(m.data()))
    _natural_helpers(m)
    _natural_entry(m)
    for x in m.ins:
        if x[0] & 65535 == 54:
            x[3] &= ~2  # Inlining is explicit here, after natural single-exit normalization.
    return run(m.data(), passes=FINAL_PASSES)
