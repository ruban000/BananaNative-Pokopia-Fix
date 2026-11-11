"""Experimental Vulkan mesh/task -> compute lowering, independent of the game translator.

Output words go directly to storage buffers, not extra Workgroup memory. The input
module's workgroup and subgroup algorithms are retained. Unsupported pointer or
interface forms are refused rather than approximated. The optional cache is explicitly enabled by DM_VK_MESH_CACHE. Its native-library
entry fingerprint and its own version are checked independently of the base shader
library. This experimental path is not the production shader build chain.
"""
import sys, math, json, pathlib, subprocess, hashlib
import vk_android_spv as spv


def ins(op, *args): return [(len(args) + 1) << 16 | op, *args]
def align(n, a): return (n + a - 1) // a * a


class Lower:
    def __init__(self, data):
        self.m = m = spv.Module(data)
        if m.model not in ('ts', 'ms'): raise ValueError('expected task/mesh module')
        self.stage = m.model
        if self.stage == 'ms' and sum(mode[0] in (27,5298) for mode in m.modes) != 1:
            raise ValueError('only point and triangle mesh output is implemented')
        self.original = list(m.ins)
        self.types = dict(m.types)
        self.valty = {x[2]: x[1] for x in m.ins if len(x) >= 3 and x[1] in m.types and (x[0]&65535) not in (5,6,16,71,72)}
        for vid,(ptr,sc) in m.vars.items():
            if sc == 1 and m.decos.get(vid,{}).get(11,[None])[0] in (24,28):
                raise ValueError('NumWorkgroups/GlobalInvocationId needs explicit logical-grid lowering')
        self.outputs = {i: m.pointee(p)[1] for i,(p,s) in m.vars.items() if s == 3}
        self.payload = [i for i,(p,s) in m.vars.items() if s == 5402]
        if len(self.payload) > 1: raise ValueError('multiple task payloads')
        self.payload = self.payload[0] if self.payload else None
        if self.payload:
            pt = m.pointee(m.vars[self.payload][0])[1]
            if m.op(pt) != 28 or m.int_type(m.types[pt][2]) != (32,0): raise ValueError('payload must be uint array')
            self.payload_words = m.consts[m.types[pt][3]][1][0]
        else: self.payload_words = 0
        self.local = next((m[1:] for m in m.modes if m[0] == 17), None)
        if not self.local: raise ValueError('no literal local size')
        self.local_n = math.prod(self.local)
        self.u = m.add_type(21,32,0); self.b = m.add_type(20); self.v3u = m.add_type(23,self.u,3)
        self.void = m.add_type(19); self.fnvoid = m.add_type(33,self.void)
        self.ptr_u = m.add_type(32,12,self.u)
        self.aux = [self.buffer(k) for k in range(4)]
        self.lane = self.builtin(29, self.u)
        self.real_wg = self.builtin(26, self.v3u)
        self.wg = None
        for i,(p,s) in list(m.vars.items()):
            if s == 1 and m.decos.get(i,{}).get(11) == [26] and i != self.real_wg:
                self.wg = i
        # builtin() reuses an existing variable. Retype that variable and make a new input.
        self.wg = self.real_wg
        self.real_wg = self.global_var(1,self.v3u,26)
        self.retyped = {self.wg}
        self.base = self.global_var(6,self.u)
        self.header = self.global_var(6,self.u)
        self.payload_base = self.global_var(6,self.u) if self.direct_mesh_payload() else None
        if self.payload_base:
            self.decorate(self.aux[1],24)  # NonWritable
        self.fields = {}
        self.record_words = 4
        for i,t in self.outputs.items():
            a,n = self.layout(t)
            off = align(self.record_words,a)
            self.fields[i] = (off,t)
            self.record_words = off+n
        self.record_words = align(self.record_words,4)
        self.meta = {'stage':self.stage, 'local_size':self.local,'payload_words':self.payload_words,
                     'record_words':self.record_words, 'outputs':[]}
        for i,(off,t) in self.fields.items():
            self.meta['outputs'].append({'id':i,'name':m.names.get(i,''),'offset':off,'type':self.type_json(t),'decorations':m.decos.get(i,{})})
        self.meta['modes'] = m.modes
        self.ptrs = {i:(self.c(off),t) for i,(off,t) in self.fields.items()}
        self.removed = set(self.outputs)
        if self.payload_base:
            self.removed.add(self.payload)
        self.ann = []
        # Machine float add/multiply instructions round separately. Letting the
        # ICD fuse them changes depth between independently compiled passes.
        # Invariant on the generated vertex output cannot constrain its compute
        # producer. Keep explicit Fma instructions fused, and forbid new fusion.
        for x in self.original:
            if x[0]&65535 in (129,131,133,136,140,141,142,143,144,145,146,147,148) and 42 not in m.decos.get(x[2],{}):
                self.decorate(x[2],42)

    def direct_mesh_payload(self):
        """Keep the shared copy for anything beyond scalar element reads."""
        if self.stage != 'ms' or not self.payload:
            return False
        payload_types = {i for i,x in self.types.items() if x[0]&65535 == 32 and x[2] == 5402}
        aliases = {self.payload}
        for x in self.original:
            op = x[0]&65535
            if len(x) < 3 or x[1] not in payload_types or 19 <= op <= 39 or op in (5,6,15,16,71,72):
                continue
            if op == 59 and x[2] == self.payload:
                continue
            if (op not in (65,66) or len(x) != 5 or x[3] != self.payload or
                    self.m.int_type(self.valty.get(x[4])) not in ((32,0),(32,1))):
                return False
            aliases.add(x[2])
        for x in self.original:
            op = x[0]&65535
            if op == 61 and x[3] in aliases and (x[3] == self.payload or len(x) != 4):
                return False
            if op in (62,63,64,228,319) and any(i in aliases for i in x[1:3]):
                return False
            if (op == 57 and any(i in aliases for i in x[4:]) or
                op == 12 and any(i in aliases for i in x[5:]) or
                op == 254 and x[1] in aliases):
                return False
            if op in (117,401,402,403) and any(i in aliases for i in x[3:]):
                return False
            if (op in (227,318,5614,5615,6035) or 229 <= op <= 242) and x[3] in aliases:
                return False
        return True

    def c(self,n): return self.m.add_const(self.u,n)
    def emit(self,out,op,ty,*args):
        i = self.m.new_id(); out.append(ins(op,ty,i,*args)); self.valty[i]=ty; return i
    def op(self,out,op,*args): out.append(ins(op,*args))
    def add(self,out,a,b): return self.emit(out,128,self.u,a,b)
    def mul(self,out,a,b): return self.emit(out,132,self.u,a,b)
    def uint(self,out,a):
        t = self.valty.get(a)
        if t == self.u: return a
        if self.m.int_type(t) in ((32,0),(32,1)): return self.emit(out,124,self.u,a)
        raise ValueError(f'index {a} has unsupported type {t}')
    def global_var(self,sc,t,bi=None):
        m=self.m; p=m.add_type(32,sc,t); i=m.new_id(); m._insert_decl(ins(59,p,i,sc)); m.vars[i]=(p,sc)
        if bi is not None: self.decorate(i,11,bi)
        return i
    def builtin(self,bi,t):
        for i,(p,s) in self.m.vars.items():
            if s==1 and self.m.decos.get(i,{}).get(11)==[bi]: return i
        return self.global_var(1,t,bi)
    def decorate(self,i,d,*values):
        m=self.m
        at=next(k for k,x in enumerate(m.ins) if 19 <= (x[0]&65535) <= 39)
        m.ins.insert(at,ins(71,i,d,*values)); m.decos.setdefault(i,{})[d]=list(values)
    def member_dec(self,i,n,d,*values):
        m=self.m; at=next(k for k,x in enumerate(m.ins) if 19 <= (x[0]&65535) <= 39)
        m.ins.insert(at,ins(72,i,n,d,*values))
    def buffer(self,b):
        m=self.m
        rt=m.new_id(); m._insert_decl(ins(29,rt,self.u)); m.types[rt]=ins(29,rt,self.u)
        st=m.new_id(); m._insert_decl(ins(30,st,rt)); m.types[st]=ins(30,st,rt)
        self.decorate(rt,6,4); self.decorate(st,2); self.member_dec(st,0,35,0)
        i=self.global_var(12,st); self.decorate(i,34,3); self.decorate(i,33,b); return i
    def ptr(self,out,b,offset): return self.emit(out,65,self.ptr_u,self.aux[b],self.c(0),offset)
    def read(self,out,b,offset): return self.emit(out,61,self.u,self.ptr(out,b,offset))
    def write(self,out,b,offset,value): self.op(out,62,self.ptr(out,b,offset),value)
    def control(self,out,k): return self.read(out,3,self.c(k))
    def load(self,out,ty,var): return self.emit(out,61,ty,var)
    def extract(self,out,ty,value,*idx): return self.emit(out,81,ty,value,*idx)
    def layout(self,t):
        m=self.m; x=m.types[t]; op=x[0]&65535
        if op==20: return 1,1
        if op in(21,22):
            if x[2]!=32: raise ValueError('non-32-bit mesh output')
            return 1,1
        if op==23:
            _,n=self.layout(x[2]); return (2 if x[3]==2 else 4),n*x[3]
        if op==28:
            a,n=self.layout(x[2]); return a,align(n,a)*m.consts[x[3]][1][0]
        if op==30:
            off=0; a=1
            for e in x[2:]:
                ea,en=self.layout(e); a=max(a,ea); off=align(off,ea)+en
            return a,align(off,a)
        raise ValueError(f'unsupported output type {op}')
    def type_json(self,t):
        m=self.m; x=m.types[t]; op=x[0]&65535
        if op in(20,21,22): return {'op':op,'args':x[2:]}
        if op==23: return {'op':op,'element':self.type_json(x[2]),'count':x[3]}
        if op==28: return {'op':op,'element':self.type_json(x[2]),'count':m.consts[x[3]][1][0], 'stride':align(self.layout(x[2])[1],self.layout(x[2])[0])}
        if op==30:
            off=0; a=[]
            for idx,e in enumerate(x[2:]):
                ea,en=self.layout(e); off=align(off,ea)
                deco={y[3]:y[4:] for y in self.original if (y[0]&65535)==72 and y[1:3]==[t,idx]}
                a.append({'offset':off,'type':self.type_json(e),'decorations':deco}); off+=en
            return {'op':op,'members':a}
        raise ValueError('unsupported type')
    def access(self,out,base,indices):
        off,t=self.ptrs[base]
        for ix in indices:
            x=self.m.types[t]; op=x[0]&65535
            if op==30:
                if ix not in self.m.consts: raise ValueError('dynamic struct index')
                idx=self.m.consts[ix][1][0]; bo=0
                for e in x[2:2+idx]:
                    ea,en=self.layout(e); bo=align(bo,ea)+en
                t=x[2+idx]; bo=align(bo,self.layout(t)[0]); off=self.add(out,off,self.c(bo))
            elif op in(23,28):
                t=x[2]; a,n=self.layout(t); stride=1 if op==23 else align(n,a)
                off=self.add(out,off,self.mul(out,self.uint(out,ix),self.c(stride)))
            else: raise ValueError('access into scalar output')
        return off,t
    def store(self,out,off,t,value):
        m=self.m; x=m.types[t]; op=x[0]&65535
        if op in(20,21,22):
            v=value if t==self.u else self.emit(out,169,self.u,value,self.c(1),self.c(0)) if op==20 else self.emit(out,124,self.u,value)
            b=self.load(out,self.u,self.base); self.write(out,0,self.add(out,b,off),v); return
        if op in(23,28):
            et=x[2]; a,n=self.layout(et); stride=1 if op==23 else align(n,a)
            n=x[3] if op==23 else m.consts[x[3]][1][0]
            for k in range(n): self.store(out,self.add(out,off,self.c(k*stride)),et,self.extract(out,et,value,k))
            return
        if op==30:
            bo=0
            for k,et in enumerate(x[2:]):
                a,n=self.layout(et); bo=align(bo,a)
                self.store(out,self.add(out,off,self.c(bo)),et,self.extract(out,et,value,k)); bo+=n
            return
        raise ValueError('store of unsupported output')
    def lane0(self,out,body):
        yes,end=self.m.new_id(),self.m.new_id(); pred=self.emit(out,170,self.b,self.load(out,self.u,self.lane),self.c(0))
        self.op(out,247,end,0); self.op(out,250,pred,yes,end); self.op(out,248,yes); body(); self.op(out,249,end); self.op(out,248,end)
    def loop_words(self,out,n,body):
        m=self.m; entry,loop,bb,cont,end=[m.new_id() for _ in range(5)]
        # Preheader with a dedicated label makes the Phi independent of the surrounding helper.
        self.op(out,249,entry); self.op(out,248,entry); lane=self.load(out,self.u,self.lane); self.op(out,249,loop); self.op(out,248,loop)
        index,inc=m.new_id(),m.new_id(); out.append(ins(245,self.u,index,lane,entry,inc,cont))
        pred=self.emit(out,176,self.b,index,self.c(n)); self.op(out,246,end,cont,0); self.op(out,250,pred,bb,end)
        self.op(out,248,bb); body(index); self.op(out,249,cont); self.op(out,248,cont)
        out.append(ins(128,self.u,inc,index,self.c(self.local_n))); self.op(out,249,loop); self.op(out,248,end)
    def helper_init(self):
        m=self.m; fn=m.new_id(); out=[ins(54,self.void,fn,0,self.fnvoid),ins(248,m.new_id())]
        wg3=self.load(out,self.v3u,self.real_wg); wg=self.extract(out,self.u,wg3,0)
        if self.stage=='ts':
            tid=self.add(out,wg,self.control(out,4)); self.op(out,62,self.wg,self.emit(out,80,self.v3u,tid,self.c(0),self.c(0)))
            stride=self.control(out,1); self.op(out,62,self.header,self.mul(out,self.add(out,wg,self.control(out,5)),stride))
        else:
            gid=self.add(out,wg,self.control(out,2)); self.op(out,62,self.wg,self.emit(out,80,self.v3u,gid,self.c(0),self.c(0)))
            self.op(out,62,self.base,self.mul(out,wg,self.c(self.record_words)))
            # Map records are (task index, local mesh x, y, z), one per emitted group.
            has_task=self.emit(out,171,self.b,self.control(out,0),self.c(0)); yes,end=m.new_id(),m.new_id()
            self.op(out,247,end,0); self.op(out,250,has_task,yes,end); self.op(out,248,yes)
            mi=self.mul(out,gid,self.c(4)); tid=self.read(out,2,mi)
            coords=[self.read(out,2,self.add(out,mi,self.c(k))) for k in(1,2,3)]
            self.op(out,62,self.wg,self.emit(out,80,self.v3u,*coords))
            if self.payload:
                pb=self.add(out,self.mul(out,tid,self.control(out,1)),self.c(4))
                if self.payload_base:
                    self.op(out,62,self.payload_base,pb)
                else:
                    pu=m.add_type(32,4,self.u)
                    def body(i):
                        v=self.read(out,1,self.add(out,pb,i)); p=self.emit(out,65,pu,self.payload,i); self.op(out,62,p,v)
                    self.loop_words(out,self.payload_words,body)
            self.op(out,249,end); self.op(out,248,end)
            if self.payload and not self.payload_base:
                self.op(out,224,self.c(2),self.c(2),self.c(264))
        self.op(out,253); self.op(out,56); return fn,out
    def helper_emit(self):
        m=self.m; ft=m.add_type(33,self.void,self.u,self.u,self.u); fn=m.new_id(); dims=[m.new_id() for _ in range(3)]
        out=[ins(54,self.void,fn,0,ft),*[ins(55,self.u,i) for i in dims],ins(248,m.new_id())]
        self.op(out,224,self.c(2),self.c(2),self.c(264))
        pb=self.load(out,self.u,self.header)
        def header():
            for k,d in enumerate(dims): self.write(out,1,self.add(out,pb,self.c(k)),d)
        self.lane0(out,header)
        if self.payload:
            pu=m.add_type(32,4,self.u)
            def body(i):
                p=self.emit(out,65,pu,self.payload,i); v=self.load(out,self.u,p)
                self.write(out,1,self.add(out,pb,self.add(out,i,self.c(4))),v)
            self.loop_words(out,self.payload_words,body)
        self.op(out,253); self.op(out,56); return fn,out
    def helper_counts(self):
        m=self.m; ft=m.add_type(33,self.void,self.u,self.u); fn=m.new_id(); v,p=m.new_id(),m.new_id()
        out=[ins(54,self.void,fn,0,ft),ins(55,self.u,v),ins(55,self.u,p),ins(248,m.new_id())]
        def body():
            b=self.load(out,self.u,self.base); self.write(out,0,b,v); self.write(out,0,self.add(out,b,self.c(1)),p)
        self.lane0(out,body); self.op(out,253); self.op(out,56); return fn,out
    def run(self):
        m=self.m
        init,fi=self.helper_init(); emit,fe=self.helper_emit() if self.stage=='ts' else self.helper_counts()
        out=[]; entry=m.entry_function(); inentry=False; first=False
        # Remove mesh-only decorations from output types too. The original output types
        # survive only as value types used by stores; their interface decorations do not.
        outtypes=set()
        def visit(t):
            if t in outtypes:return
            outtypes.add(t); x=m.types[t]; op=x[0]&65535
            if op in(23,28):visit(x[2])
            elif op==30:
                for t2 in x[2:]:visit(t2)
        for t in self.outputs.values():visit(t)
        payload_ptrs={t for t,x in m.types.items() if (x[0]&65535)==32 and x[2]==5402}
        # The translator can read a predicate spill after its conditional write
        # was bypassed, then mask it with an acceptance predicate. Uninitialized
        # Function memory is poison in SPIR-V; even OR with true propagates it.
        # Give local predicate spills a defined false entry value. Explicit stores
        # and all actual visibility tests stay intact; numeric locals are untouched.
        # Main pass must not iterate m.ins while adding type/constant declarations.
        original=list(m.ins)
        for x in original:
            op=x[0]&65535
            if op==17 and x[1]==5283: continue
            if op==10 and spv.string_at(x,1)[0]=='SPV_EXT_mesh_shader':continue
            if op==16 and x[2] in(26,27,28,29,5270,5298):continue
            if op==15:
                _,k=spv.string_at(x,3); x=ins(15,5,x[2],*x[3:k],*[i for i in x[k:] if i not in self.removed],*self.aux,self.real_wg,self.base,self.header)
                if self.payload_base: x=ins(15,*x[1:],self.payload_base)
                # A newly created lane input may be absent from the original interface.
                for variable in (self.lane,self.wg):
                    if variable not in x[k:]:x=ins(15,*x[1:],variable)
            if op in(5,71) and x[1] in self.removed:continue
            if op==71 and x[1]==self.wg and x[2]==11:continue
            if op==71 and x[1] in outtypes and x[2] in(2,11,5271):continue
            if op==72 and x[1] in outtypes and x[3] in(11,5271):continue
            if op==32 and x[1] in payload_ptrs:x=ins(32,x[1],4,x[3])
            if op==59:
                if x[3]==7 and len(x)==4 and m.op(m.pointee(x[1])[1])==20:
                    false = next((y[2] for y in m.ins if y[0]&65535==42 and y[1]==self.b), None)
                    if false is None:
                        false=m.new_id();m._insert_decl(ins(42,self.b,false))
                    x=ins(59,*x[1:],false)
                if x[2] in self.removed:continue
                if x[2]==self.wg:x=ins(59,m.add_type(32,6,self.v3u),self.wg,6)
                elif x[3]==5402:x=ins(59,x[1],x[2],4,*x[4:])
            if op==54:inentry=x[2]==entry;first=inentry
            if op==248 and inentry and first:
                out.append(x);first=False;self.pending_init=True;continue
            if getattr(self,'pending_init',False) and op!=59:
                self.emit(out,57,self.void,init);self.pending_init=False
            if op in(65,66) and x[3] in self.ptrs:
                self.ptrs[x[2]]=self.access(out,x[3],x[4:]); self.removed.add(x[2]);continue
            if self.payload_base and op in (65,66) and x[3] == self.payload:
                base = self.load(out,self.u,self.payload_base)
                index = self.add(out,base,self.uint(out,x[4]))
                self.op(out,65,self.ptr_u,x[2],self.aux[1],self.c(0),index)
                continue
            if op in(65,66) and x[3] in self.retyped:
                self.retyped.add(x[2]); x=list(x); x[1]=m.add_type(32,6,m.pointee(x[1])[1])
            if op==62 and x[1] in self.ptrs:
                off,t=self.ptrs[x[1]];self.store(out,off,t,x[2]);continue
            if op==5295:
                self.emit(out,57,self.void,emit,*x[1:3]);continue
            if op==5294:
                if len(x)>4 and x[4]!=self.payload:raise ValueError('emit uses another task payload')
                self.emit(out,57,self.void,emit,*x[1:4]);self.op(out,253);continue
            if op in(5,71) and x[1] in self.removed:continue
            # Refuse any other executable use of output pointers. Result IDs and
            # scalar literals may equal pointer IDs, so inspect only pointer operands.
            if op in(61,63,64,67,70,83,57,245,169):
                operands=x[3:] if op in(61,67,70,83,57,245,169) else x[1:]
                if any(i in self.ptrs for i in operands):raise ValueError(f'unsupported output pointer use in opcode {op}')
            out.append(x)
        # Declarations added during the pass have to precede functions. Keep the
        # rewritten declarations, then add any fresh declarations not visited.
        visited={id(x) for x in original}
        fresh=[x for x in m.ins if id(x) not in visited]
        firstfn=next(k for k,x in enumerate(out) if (x[0]&65535)==54)
        decl=out[:firstfn]+fresh
        m.ins=[x for x in decl if (x[0]&65535)!=59]+[x for x in decl if (x[0]&65535)==59]+out[firstfn:]+fi+fe
        if 1 not in m.caps:m.ins.insert(0,ins(17,1))
        m.index()
        return m.data(),self.meta


def lower(data):return Lower(data).run()



def vertex_source(meta, vector_loads=False):
    if meta['stage'] != 'ms': raise ValueError('vertex source needs mesh metadata')
    fields=meta['outputs']; modes={x[0]:x[1:] for x in meta['modes']}
    maxp=modes[5270][0]; point=27 in modes; corners=1 if point else 3
    out=['#version 450', 'invariant gl_Position;', 'layout(set=3,binding=0,std430) readonly buffer OutputWords { uint w[]; } o;']
    vary=[]; position=None; pointsize=None; indices=None; cull=None
    for f in fields:
        d={int(k):v for k,v in f['decorations'].items()}; ty=f['type'];et=ty['element'];stride=ty['stride'];off=f['offset']
        if ty['op']!=28:raise ValueError('non-array mesh output')
        if 30 in d:
            loc=d[30][0]; perprim=5271 in d; vary.append((loc,off,stride,et,perprim))
            if et['op']!=23 or et['count']!=4 or et['element']['op']!=22:raise ValueError('non-vec4-float varying')
            out.append(f'layout(location={loc}) {"flat " if perprim else ""}out vec4 attr{loc};')
        elif d.get(11)==[5294] or d.get(11)==[5296]:indices=(off,stride)
        elif d.get(11)==[5299]:cull=(off,stride)
        elif et['op']==30:
            for mem in et['members']:
                bd={int(k):v for k,v in mem['decorations'].items()}
                if bd.get(11)==[0]: position=(off+mem['offset'],stride)
                elif bd.get(11)==[1]:pointsize=(off+mem['offset'],stride)
                elif bd.get(11)==[5299]:cull=(off+mem['offset'],stride)
                else:raise ValueError('unsupported builtin block output')
        else:raise ValueError('unrecognized mesh output')
    if not position or not indices:raise ValueError('missing position or primitive indices')
    def addr(f,index):return f'b + {f[0]}u + ({index}) * {f[1]}u'
    def raw(f,index,comp=0):return f'o.w[{addr(f,index)} + {comp}u]'
    # A second read-only view of the same descriptor leaves the record layout
    # intact. Only use it when every record and element proves 16-byte alignment.
    # Counts, indices, odd strides and non-aligned fields retain scalar accesses.
    vectors = vector_loads and meta['record_words'] % 4 == 0
    if vectors:
        out.append('layout(set=3,binding=0,std430) readonly buffer OutputVectors { uvec4 v[]; } ov;')
    def vec(off,stride,index):
        if vectors and off % 4 == 0 and stride % 4 == 0:
            return f'uintBitsToFloat(ov.v[({addr((off,stride),index)}) / 4u])'
        return 'uintBitsToFloat(uvec4('+','.join(raw((off,stride),index,k) for k in range(4))+'))'
    out+=['void main() {',f'uint slot = uint(gl_VertexIndex) % {maxp*corners}u;',f'uint p = slot / {corners}u;',
          f'uint b = (uint(gl_VertexIndex) / {maxp*corners}u) * {meta["record_words"]}u;',
          'gl_Position=vec4(0.0,0.0,2.0,1.0);','gl_PointSize=1.0;']
    for loc,*rest in vary:out.append(f'attr{loc}=vec4(0.0,0.0,0.0,1.0);')
    out+=['if (p >= o.w[b+1u]) return;']
    if cull:out.append(f'if ({raw(cull,"p")} != 0u) return;')
    out += [f'uint v = o.w[{addr(indices,"p")} + slot % {corners}u];','if (v >= o.w[b]) return;',f'gl_Position={vec(*position,"v")};']
    if point and pointsize:out.append(f'gl_PointSize=uintBitsToFloat({raw(pointsize,"v")});')
    for loc,off,stride,ty,perprim in vary:out.append(f'attr{loc}={vec(off,stride,"p" if perprim else "v")};')
    out+=['}'];return '\n'.join(out)+'\n'


def fragment_flat(data):
    m=spv.Module(data)
    if m.model!='ps':raise ValueError('not a fragment module')
    out=[]
    for x in m.ins:
        op=x[0]&65535
        if op==17 and x[1]==5283:continue
        if op==10 and spv.string_at(x,1)[0]=='SPV_EXT_mesh_shader':continue
        if op==71 and x[2]==5271:
            if 14 not in m.decos.get(x[1],{}):out.append(ins(71,x[1],14))
            continue
        if op==72 and x[3]==5271:out.append(ins(72,x[1],x[2],14));continue
        out.append(x)
    m.ins=out;return m.data()


CACHE_VERSION = 2
CACHE_HEAD = __import__('struct').Struct('<8sQQ9I16s4x')


def cache_chain():
    """The independent experimental cache's lowering and layout contract."""
    root = pathlib.Path(__file__).resolve().parent
    h = hashlib.sha256(b'BananaComputeMeshCache2\0')
    for p in (pathlib.Path(__file__), root / 'vk_android_spv.py', root / 'vk_mesh_bound.py', root / 'vk_gmem_page_index.py', root / 'vk_mesh_gmem_probe.py', root.parent / 'vk_android/vka_page_index.h', root / 'vk_mesh_schedule.comp',
              root.parent / 'vk_android/vka_mesh.h', root.parent / 'vk_android/vka_mesh_cache.h'):
        h.update(p.name.encode() + b'\0' + p.read_bytes())
    return h.hexdigest()[:16]


def entry_fingerprint(entry, binds, c1, modules):
    """vka_shlib_entry_hash, including the exact stored codec bytes."""
    import struct
    import vk_android_delta as delta
    mask = (1 << 64) - 1
    def mix(h, b):
        end = len(b) // 8 * 8
        for i in range(0, end, 8):
            h = ((h ^ int.from_bytes(b[i:i+8], 'little')) * 0x9e3779b97f4a7c15) & mask
            h ^= h >> 29
        for c in b[end:]: h = ((h ^ c) * 1099511628211) & mask
        return h
    h = mix(1469598103934665603, b'DMVKPC1\0')
    for b in (delta.zeroed(entry), binds, c1): h = mix(h, b)
    for variant, size, stored, codec in modules:
        h = mix(h, delta.MOD.pack(0, size, len(stored), variant, codec, 0))
        h = mix(h, bytes(stored))
    h ^= h >> 32
    return h or 1


def build_cache(library, output, jobs=6, vector_loads=False, page_index=()):
    """Incremental sidecar: only task/mesh entries, no translator or native-library rewrite."""
    import concurrent.futures
    import os
    import vk_android_delta as delta
    import vk_mesh_bound
    import zstandard
    output = pathlib.Path(output); output.mkdir(parents=True, exist_ok=True)
    lib = delta.Library(library); chain = cache_chain()
    manifest_path = output / 'manifest.json'
    try:
        audits, audit_stamp = vk_mesh_bound.profile_bounds(lib.game)
        try:
            old_manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            old_manifest = {}
        same_audit = isinstance(old_manifest, dict) and old_manifest.get('task_audit', '') == audit_stamp
        same_pages = isinstance(old_manifest, dict) and old_manifest.get('page_index', []) == sorted(page_index)
        same_vectors = isinstance(old_manifest, dict) and old_manifest.get('vector_loads', False) == vector_loads
        selected = [i for i in range(lib.ne) if lib.m[lib.eo + i * delta.ENTRY.size + 16] in (6, 7)]
        available = {lib.entry(i)[0] for i in selected}
        if set(page_index) - available:
            raise ValueError('unknown page-index keys: ' + ','.join(sorted(set(page_index) - available)))
    except Exception:
        lib.close()
        raise
    def one(i):
        key, entry, binds, c1, mods = lib.entry(i)
        fingerprint = entry_fingerprint(entry, binds, c1, mods)
        dst = output / (key + '.bnmesh')
        if same_pages and (entry[16] != 6 or same_audit) and (entry[16] != 7 or same_vectors) and dst.exists():
            with dst.open('rb') as f: old = f.read(CACHE_HEAD.size)
            if len(old) == CACHE_HEAD.size:
                h = CACHE_HEAD.unpack(old)
                if h[0] == b'BNMESH2\0' and h[1:3] == (int(key,16),fingerprint) and h[10] == CACHE_VERSION and h[-1] == chain.encode() and dst.stat().st_size == CACHE_HEAD.size+h[8]+h[9]:
                    return key, False, h[11]
        native = next(delta.spirv(data, codec, zstandard.ZstdDecompressor()) for variant,size,data,codec in mods if variant == 0)
        cs, meta = lower(native); vs = b''
        import tempfile
        with tempfile.TemporaryDirectory(dir=output) as temp:
            temp = pathlib.Path(temp)
            if key in page_index:
                if entry[16] != 7: raise ValueError('page-index experiment accepts mesh stages only')
                from vk_gmem_page_index import rewrite
                cs, _ = rewrite(cs)
            comp = temp / 'mesh.comp.spv'; comp.write_bytes(cs)
            subprocess.run(['spirv-val','--target-env','vulkan1.3',str(comp)],check=True,capture_output=True)
            if entry[16] == 7:
                source = temp / 'mesh.vert'; source.write_text(vertex_source(meta, vector_loads)); vert = temp / 'mesh.vert.spv'
                subprocess.run(['glslangValidator','-V','--target-env','vulkan1.3',str(source),'-o',str(vert)],check=True,capture_output=True)
                subprocess.run(['spirv-val','--target-env','vulkan1.3',str(vert)],check=True,capture_output=True)
                vs = vert.read_bytes()
            bound = 0
            if entry[16] == 6:
                bound = vk_mesh_bound.reviewed_bound(native, audits.get(key)) or vk_mesh_bound.prove(native, temp)
            modes = {x[0]:x[1:] for x in meta['modes']}
            head = CACHE_HEAD.pack(b'BNMESH2\0',int(key,16),fingerprint,entry[16],meta['record_words'],meta['payload_words'],modes.get(5270,[0])[0],1 if 27 in modes else 3,len(cs),len(vs),CACHE_VERSION,bound,chain.encode())
            pending = temp / 'entry'; pending.write_bytes(head+cs+vs); os.replace(pending,dst)
        return key, True, bound
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex: results = list(ex.map(one, selected))
        scheduler = output / ('schedule-' + chain + '.spv')
        if not scheduler.exists():
            import tempfile
            with tempfile.TemporaryDirectory(dir=output) as tmp:
                binary = pathlib.Path(tmp) / 'schedule.spv'
                source = pathlib.Path(__file__).with_name('vk_mesh_schedule.comp')
                subprocess.run(['glslangValidator','-V','--target-env','vulkan1.3',str(source),'-o',str(binary)],check=True,capture_output=True)
                subprocess.run(['spirv-val','--target-env','vulkan1.3',str(binary)],check=True,capture_output=True)
                os.replace(binary,scheduler)
        manifest = {'version':CACHE_VERSION,'chain':chain,'native_library':lib.version,'task_audit':audit_stamp,'entries':[k for k,_,_ in results],
                    'bounded_tasks':{k:n for k,_,n in results if n},'scheduler':scheduler.name,'vector_loads':vector_loads,'page_index':sorted(page_index)}
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
        print(f'compute mesh cache {chain}: {len(results)} entries, {sum(b for _,b,_ in results)} rebuilt, {len(manifest["bounded_tasks"])} bounded tasks')
    finally: lib.close()


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    h=sub.add_parser('header'); h.add_argument('output')
    c=sub.add_parser('cache'); c.add_argument('library'); c.add_argument('output'); c.add_argument('--jobs',type=int,default=6)
    c.add_argument('--page-index', default='', help='comma-separated mesh keys; requires the matching host GMEM header')
    c.add_argument('--vector-loads', action='store_true', help='experimental aligned vec4 reads; use a separate pipeline cache for A/B')
    l=sub.add_parser('lower'); l.add_argument('source'); l.add_argument('output')
    a=p.parse_args()
    if a.command=='header':
        path=pathlib.Path(a.output);text='#define VKA_MESH_CACHE_CHAIN "'+cache_chain()+'"\n'
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists() or path.read_text()!=text:path.write_text(text)
    elif a.command=='cache':build_cache(a.library,a.output,a.jobs,a.vector_loads,tuple(filter(None,a.page_index.split(','))))
    else:
        data,meta=lower(pathlib.Path(a.source).read_bytes());path=pathlib.Path(a.output);path.write_bytes(data);path.with_suffix('.json').write_text(json.dumps(meta,indent=2)+'\n')


if __name__=='__main__':main()
