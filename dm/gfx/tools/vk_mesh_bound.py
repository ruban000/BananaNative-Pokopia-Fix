"""Conservative unsigned interval proof for task emission.

We only use a bounded GPU arena when all emit dimensions are bounded. Unknown
values, aliases, loops and pointer escapes keep the exact CPU-counted path.
SSA promotion is used for analysis only, never for the executed module.
"""
import collections, functools, hashlib, pathlib, re, subprocess
import vk_android_spv as spv

U=(0,0xffffffff)


def profile_bounds(game):
    """Return reviewed bounds and a stamp for the cache's task entries."""
    if not re.fullmatch(r'[0-9a-fA-F]{16}', game):
        return {}, ''
    path = pathlib.Path(__file__).resolve().parents[2] / 'games' / game.upper() / 'vk_task_bounds.tsv'
    if not path.is_file():
        return {}, ''
    data = path.read_bytes()
    bounds = {}
    for line_number, line in enumerate(data.decode().splitlines(), 1):
        row = line.partition('#')[0].split()
        if not row:
            continue
        if (len(row) != 3 or not re.fullmatch(r'[0-9a-f]{16}', row[0]) or
                not re.fullmatch(r'[0-9a-f]{64}', row[1]) or not row[2].isascii() or
                not row[2].isdecimal() or not 0 < int(row[2]) <= 512 or row[0] in bounds):
            raise ValueError(f'{path}:{line_number}: invalid task shader bound')
        bounds[row[0]] = (row[1], int(row[2]))
    return bounds, hashlib.sha256(data).hexdigest()


def reviewed_bound(data, certificate):
    if certificate and hashlib.sha256(data).hexdigest() == certificate[0]:
        return certificate[1]
    return 0


def bound(data):
    m=spv.Module(data)
    if set(m.exts) - {'SPV_EXT_mesh_shader'}:
        return 0
    words=m.ins
    defs={}; blocks={}; pred=collections.defaultdict(set); succ=collections.defaultdict(set); cond={}; selection={}; at=0; emits=[]; stores=[]; code=[]
    for x in words:
        op=x[0]&65535
        if op==248: at=x[1];blocks[at]=[]
        if at: blocks[at].append(x)
        if op==56: at=0
        code.append((op,x,at))
        if op in (41,42,43,45,46,54,55,57,59,61,65,66,83,124,128,130,132,169,170,171,176,194,196,197,198,199,205,245): defs[x[2]]=x,at
        if op==247:selection[x[1]]=at
        if op==249: succ[at].add(x[1]);pred[x[1]].add(at)
        if op==250:
            cond[at]=x[1:4]
            for p in x[2:4]:succ[at].add(p);pred[p].add(at)
        if op==251:
            # Case literal width depends on the selector. Don't try to read
            # branch targets from an unsupported switch representation.
            return 0
        if op==62:stores.append((x[1],x[2],at))
        if op==5294:emits.append((x,at))
    @functools.cache
    def acyclic(block):
        seen=set();todo=list(succ[block])
        while todo:
            z=todo.pop()
            if z==block:return False
            if z not in seen:seen.add(z);todo.extend(succ[z])
        return True
    def guard(b):
        if len(pred[b])==1:
            parent=next(iter(pred[b]))
            if parent in cond and acyclic(parent) and acyclic(b):
                c,y,n=cond[parent]
                if y!=n:return c,b==y
    def access(ptr):
        d=defs.get(ptr)
        if not d:return
        x,_=d
        if x[0]&65535 in (65,66) and len(x)==5 and x[3]==payload:return x[4]
    def union(rs):
        rs=list(rs)
        return (min(r[0] for r in rs),max(r[1] for r in rs)) if rs else U
    def constant(i):
        v=m.consts.get(i)
        if v and len(v[1])==1 and m.int_type(v[0]) in ((32,0),(32,1)):return v[1][0]
    @functools.cache
    def value(i,g=None,depth=0):
        if depth>30 or i not in defs:return U
        x,b=defs[i];op=x[0]&65535
        r=lambda v:value(v,g,depth+1)
        if m.int_type(x[1]) not in ((32,0),(32,1)):return U
        if (c:=constant(i)) is not None:return (c,c)
        if op in (83,124):return r(x[3])
        if op==205:
            arg=defs.get(x[3])
            ty=m.int_type(arg[0][1]) if arg else None
            return (0,ty[0]) if ty else U
        if op in (128,130,132,194,196,199):
            a,c=r(x[3]),r(x[4]);lo,hi=a
            if op==128 and hi+c[1]<=U[1]:return lo+c[0],hi+c[1]
            if op==130 and lo>=c[1]:return lo-c[1],hi-c[0]
            if op==132 and hi*c[1]<=U[1]:return lo*c[0],hi*c[1]
            if op in (194,196) and c[0]==c[1]<32:
                if op==194:return lo>>c[0],hi>>c[0]
                if hi<<c[0]<=U[1]:return lo<<c[0],hi<<c[0]
            if op==199:
                if c[0]==c[1] and c[0]&(c[0]+1)==0 and hi<=c[0]:return a
                return (0,min(hi,c[1]))
        if op==169:
            if g and x[3]==g[0]:return r(x[4 if g[1] else 5])
            a,c=r(x[4]),r(x[5]);cmp=defs.get(x[3])
            if cmp and cmp[0][0]&65535==176 and cmp[0][3:5]==x[4:6]:
                return min(a[0],c[0]),min(a[1],c[1])
            return union((a,c))
        if op==245:
            pairs=list(zip(x[3::2],x[4::2]))
            h=selection.get(b)
            if g and h in cond and cond[h][0]==g[0] and acyclic(h) and acyclic(b):
                _,y,n=cond[h]
                if y!=n and {p for _,p in pairs}=={h if s==b else s for s in (y,n)} and all(s==b or (pred[s]=={h} and succ[s]=={b}) for s in (y,n)):
                    p=y if g[1] else n
                    return r(next(v for v,pr in pairs if pr==(h if p==b else p)))
            return union(r(v) for v,_ in pairs)
        if op==61 and (index:=access(x[3])) is not None and (k:=constant(index)) is not None and initialized(k,b,x):
            values=[]
            for ptr,v,block in stores:
                if (index:=access(ptr)) is None:continue
                rr=value(index,guard(block),depth+1)
                if rr[0]<=k<=rr[1]:values.append(value(v,guard(block),depth+1))
            return union(values)
        return U
    def initialized(k, use, load):
        # One lane writes zero, then a workgroup barrier publishes it before
        # the task body or any load of the count. The split is in the entry
        # block, so all invocations participate.
        g=cond.get(entry)
        if not g:return False
        cond_id,yes,merge=g
        test=defs.get(cond_id,([],0))[0]
        if not test or test[0]&65535!=170 or selection.get(merge)!=entry:return False
        a,b=test[3:5]
        lane=b if constant(a)==0 else a if constant(b)==0 else 0
        l=defs.get(lane,([],0))[0]
        if not l or l[0]&65535!=61 or m.decos.get(l[3],{}).get(11)!=[29]:return False
        if pred[yes]!={entry} or succ[yes]!={merge} or pred[merge]!={entry,yes}:return False
        if not any(constant(access(p))==k and constant(v)==0 and loc==yes for p,v,loc in stores):return False
        # This merge starts with its control barrier (labels and phis aside).
        first=next((x for x in blocks[merge] if x[0]&65535 not in (248,245,8,317)),[])
        if not first or first[0]&65535!=224 or [constant(i) for i in first[1:]]!=[2,2,264]:return False
        published=False
        for x in blocks[use]:
            if x is load:break
            op=x[0]&65535
            if op==224:published=[constant(i) for i in x[1:]]==[2,2,264]
            elif op in (57,62,63,64,228):published=False
        if not published:return False
        if use==merge:return True
        seen=set();todo=[entry]
        while todo:
            b=todo.pop()
            if b==use:return False
            if b!=merge and b not in seen:seen.add(b);todo.extend(succ[b])
        return True

    if len(emits)!=1:return 0
    e,emit_block=emits[0]
    if len(e)!=5:return 0
    payload=e[4]
    if m.vars.get(payload,(0,0))[1]!=5402:return 0
    begin,end=m.function_range(m.entry_function())
    entry_ops=words[begin:end]
    entry=next(x[1] for x in entry_ops if x[0]&65535==248)
    if e not in entry_ops or any(x[0]&65535 in (252,253,254,255,4416) for x in entry_ops):return 0
    if 4441 in m.caps or 4442 in m.caps:return 0
    aliases={payload}
    for op,x,b in code:
        if not (19<=op<=39) and op not in (5,6,15,16,71,72) and len(x)>=3 and x[1] in m.types:
            t=m.types[x[1]]
            if t[0]&65535==32 and t[2]==5402:
                if x[2]==payload and op==59:continue
                if op not in (65,66) or len(x)!=5 or x[3]!=payload:return 0
                aliases.add(x[2])
    for op,x,b in code:
        if op in (63,64,228,319) and x[1] in aliases:return 0
        if (229<=op<=242 or op in (318,5614,5615,6035)) and x[3] in aliases:return 0
        if op==57 and any(i in aliases for i in x[4:]):return 0
        if op==12 and any(i in aliases for i in x[5:]):return 0
        if op==62 and (x[1]==payload or x[2] in aliases):return 0
        if op==254 and x[1] in aliases:return 0
    # Scope this first scheduler to a one-dimensional emitted grid.
    dims=[value(i) for i in e[1:4]]
    return dims[0][1] if dims[1:]==[(1,1),(1,1)] and dims[0][1]<=512 else 0

def prove(data, temp):
    m=spv.Module(data)
    # Give the proof input the same predicate initialization as the lowerer
    # before SPIRV-Tools promotes Function storage to SSA.
    for x in list(m.ins):
        if x[0]&65535!=59 or len(x)!=4 or x[3]!=7:continue
        t=m.pointee(x[1])[1]
        if m.op(t)!=20:continue
        f=next((y[2] for y in m.ins if y[0]&65535==42 and y[1]==t),None)
        if f is None:
            f=m.new_id();m._insert_decl([3<<16|42,t,f])
        m.ins[m.ins.index(x)]=[5<<16|59,*x[1:],f]
    source=temp/'task-native.spv'; dest=temp/'task-ssa.spv'
    source.write_bytes(m.data())
    try:
        subprocess.run(['spirv-opt','--inline-entry-points-exhaustive','--eliminate-dead-functions',
                  '--eliminate-local-single-block','--eliminate-local-single-store','--ssa-rewrite',
                  '--simplify-instructions',str(source),'-o',str(dest)],check=True,capture_output=True)
    except (FileNotFoundError,subprocess.CalledProcessError):
        return 0
    return bound(dest.read_bytes())
