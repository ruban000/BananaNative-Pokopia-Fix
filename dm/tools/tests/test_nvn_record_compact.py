"""Compile the production NVN record writers and both readers on a CPU-only host.

--benchmark compares the worktree with --baseline-ref (default HEAD), using the
same optimized production-function fixture. Timings describe CPU recording only.
"""
from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = "dm/gfx/nvn/hle_nvn.c"


def function(source, name):
    start = source.index(name)
    start = source.rfind("\n", 0, start) + 1
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def reader_case(source, op):
    start = source.index("case " + op + ": {")
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def fixture(source):
    code = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/mman.h>
#include <unistd.h>
#include "nvn_obj.h"
#define NVN(name, ret, ...) static ret h_##name(__VA_ARGS__)
#define CB(c) ((cmd_t *)(c))
#define OP_RESERVE 16
static _Atomic unsigned logs, notes;
#define LOG(...) ((void)atomic_fetch_add_explicit(&logs, 1, memory_order_relaxed))
static int cs_on(void) { return 0; }
static void cs_op(uint32_t op, uint32_t size) { (void)op; (void)size; }
static size_t hostseg_size(const cmd_t *o, int empty, size_t want) { (void)o; (void)empty; return want; }
static uint8_t *hostseg_take(const cmd_t *o, size_t n) { (void)o; (void)n; assert(!"unexpected host segment"); abort(); }
'''
    start = source.index("static struct { int on; uint64_t bytes")
    code += source[start:source.index("\n", start)] + "\n"
    code += "static pthread_mutex_t g_cs_mu = PTHREAD_MUTEX_INITIALIZER;\n"
    for name in ("static void put_hdr(", "static uint8_t *align8(",
                 "static void cmd_new_segment(", "static uint8_t *cmd_alloc_keep(",
                 "static uint8_t *cmd_alloc(", "static void *rec("):
        code += function(source, name)
    for line in source.splitlines():
        if line.startswith("#define REC(") or line.startswith("#define RECN("):
            code += line + "\n"
    for name in ("NVN(nvnCommandBufferBeginRecording,", "NVN(nvnCommandBufferEndRecording,",
                 "NVN(nvnCommandBufferSetViewports,", "NVN(nvnCommandBufferSetViewportSwizzles,",
                 "NVN(nvnCommandBufferSetScissor,", "NVN(nvnCommandBufferSetDepthRange,",
                 "NVN(nvnCommandBufferCopyCommands,"):
        code += function(source, name)
    # Use the real once state declaration and stub body, with only its unrelated
    # diagnostic sink replaced. This catches byte wrap and concurrent first use.
    code += "typedef struct { _Atomic uint64_t cnt; void *fn; } nvn_slot_t;\n"
    code += "#define NVN_NFUNC 2\nstatic nvn_slot_t nvn_slot[2];\n"
    code += 'static const char *nvn_names[2] = {"first", "second"};\n'
    code += next(line for line in source.splitlines() if "g_unimpl_seen[NVN_NFUNC]" in line) + "\n"
    code += r'''
static void nvn_shading_rate_note(const char *n,uint64_t a,uint64_t b,uint64_t c,uint64_t d) {
    (void)n;(void)a;(void)b;(void)c;(void)d;
    atomic_fetch_add_explicit(&notes, 1, memory_order_relaxed);
}
'''
    code += function(source, "uint64_t nvn_unimpl_c(")
    code += r'''
typedef struct { float vp[16][4]; int has_vp; uint8_t vp_sw[16][4]; } state;
static state S;
static void metal_read(uint32_t op, const void *a) { switch (op) {
'''
    metal = (ROOT / "dm/gfx/nvn/nvn_mtl.m").read_text()
    android = (ROOT / "dm/gfx/vk_android/vka_draw.c").read_text()
    for op in ("OP_VIEWPORTS", "OP_VP_SWIZZLE"):
        code += reader_case(metal, op)
    code += "default: break; } }\nstatic void vulkan_read(state *d, uint32_t op, const void *a) { switch (op) {\n"
    for op in ("OP_VIEWPORTS", "OP_VP_SWIZZLE"):
        code += reader_case(android, op)
    code += r'''
default: break; } }
static void init(cmd_t *c, void *mem, size_t n) {
    memset(c,0,sizeof *c); c->cur=mem; c->end=c->cur+n;
    h_nvnCommandBufferBeginRecording((NVNcommandBuffer *)c);
}
static void test_readers(void) {
    const int counts[]={0,1,2,16};
    for(unsigned k=0;k<sizeof counts/sizeof *counts;k++) {
        int n=counts[k],first=n==16?0:3;
        float vp[64]; NVNviewportSwizzle sw[64];
        for(int i=0;i<64;i++){vp[i]=(float)(100+i);sw[i]=(NVNviewportSwizzle)(i%8);}
        _Alignas(8) uint8_t mem[2048]; memset(mem,0xa5,sizeof mem); cmd_t c;
        init(&c,mem,sizeof mem);
        h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,first,n,vp);
        size_t vo=c.used;
        h_nvnCommandBufferSetViewportSwizzles((NVNcommandBuffer *)&c,first,n,sw);
        size_t so=c.used-vo;
        h_nvnCommandBufferSetScissor((NVNcommandBuffer *)&c,1,2,3,4);
        NVNcommandHandle handle=h_nvnCommandBufferEndRecording((NVNcommandBuffer *)&c);
        assert((uintptr_t)handle==(uintptr_t)mem);
        assert(vo==(size_t)(16+24*n)); assert(so==((size_t)(16+4*n+7)&~(size_t)7));
        assert(c.used==vo+so+24+8); assert(mem[c.used]==0xa5);
        memset(vp,0,sizeof vp); memset(sw,0,sizeof sw); // recorded values own their lifetime
        memset(&S,0,sizeof S); state d={0}; unsigned seen=0;
        for(size_t pos=0;pos<c.used;) {
            op_hdr *h=(op_hdr *)(mem+pos);assert(h->size>=8&&!(h->size&7)&&pos+h->size<=c.used);
            metal_read(h->op,mem+pos+8);vulkan_read(&d,h->op,mem+pos+8);seen++;pos+=h->size;
        }
        assert(seen==4);assert(!memcmp(&S,&d,sizeof S));assert(S.has_vp);
        for(int i=0;i<16;i++)for(int j=0;j<4;j++) {
            int active=i>=first&&i<first+n;
            assert(S.vp[i][j]==(active?(float)(100+4*(i-first)+j):0));
            assert(S.vp_sw[i][j]==(active?(((4*(i-first)+j)%8)^(2*j)):0));
        }
        // Give each reader exactly the recorded extent at a guard-page edge.
        // Reading a trailing maximum-sized array instead of n would fault.
        size_t page=(size_t)sysconf(_SC_PAGESIZE);
        uint8_t *guard=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        assert(guard!=MAP_FAILED&&!mprotect(guard+page,page,PROT_NONE));
        memcpy(guard+page-vo,mem,vo);metal_read(OP_VIEWPORTS,guard+page-vo+8);vulkan_read(&d,OP_VIEWPORTS,guard+page-vo+8);
        memcpy(guard+page-so,mem+vo,so);metal_read(OP_VP_SWIZZLE,guard+page-so+8);vulkan_read(&d,OP_VP_SWIZZLE,guard+page-so+8);
        assert(!munmap(guard,2*page));
    }
}
static void test_ranges_and_adjacent(void) {
    _Alignas(8) uint8_t mem[1024];cmd_t c;float vp[64]={1,2,3,4};NVNviewportSwizzle sw[64]={0};
    const int bad[][2]={{-1,1},{17,0},{0,-1},{15,2},{INT_MIN,INT_MAX},{INT_MAX,1},{1,INT_MAX}};
    init(&c,mem,sizeof mem);
    for(unsigned i=0;i<sizeof bad/sizeof *bad;i++) {
        h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,bad[i][0],bad[i][1],vp);
        h_nvnCommandBufferSetViewportSwizzles((NVNcommandBuffer *)&c,bad[i][0],bad[i][1],sw);
        assert(!c.used&&c.cur==mem);
    }
    // Highest valid index and the empty range at the end retain valid headers.
    h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,15,1,vp);
    h_nvnCommandBufferSetViewportSwizzles((NVNcommandBuffer *)&c,16,0,sw);
    assert(c.used==56);
    init(&c,mem,64);h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,0,1,vp);
    h_nvnCommandBufferEndRecording((NVNcommandBuffer *)&c);assert(c.used==48);
    uint8_t saved[48];memcpy(saved,mem,48);
    init(&c,mem+48,64);h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,0,1,vp);
    h_nvnCommandBufferEndRecording((NVNcommandBuffer *)&c);assert(!memcmp(saved,mem,48));
}
static _Alignas(8) uint8_t continuation[64];
static unsigned callbacks;
static void next_segment(NVNcommandBuffer *c, NVNcommandBufferMemoryEvent ev, size_t need, void *arg) {
    (void)arg;assert(ev==NVN_COMMAND_BUFFER_MEMORY_EVENT_OUT_OF_COMMAND_MEMORY&&need<=64);
    assert(++callbacks==1);cmd_new_segment((cmd_t *)c,continuation,sizeof continuation);
}
static void replay(const uint8_t *p,state *d,unsigned depth) {
    assert(depth<8);
    for(;;){op_hdr h;memcpy(&h,p,8);const void *a=p+8;
        if(h.op==OP_END)return;
        if(h.op==OP_JUMP){memcpy(&p,a,8);continue;}
        if(h.op==OP_CALL){const uint8_t *child;memcpy(&child,a,8);replay(child,d,depth+1);}
        else{metal_read(h.op,a);vulkan_read(d,h.op,a);}
        assert(h.size>=8);p+=h.size;
    }
}
static void test_segments_and_calls(void) {
    _Alignas(8) uint8_t first[64],parent[64];cmd_t c;float vp[4]={1,2,3,4};
    init(&c,first,sizeof first);c.cb=next_segment;
    h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,0,1,vp);
    vp[0]=9;h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,1,1,vp);
    NVNcommandHandle child=h_nvnCommandBufferEndRecording((NVNcommandBuffer *)&c);
    assert(callbacks==1&&c.used==104);assert(((op_hdr *)(first+40))->op==OP_JUMP);
    init(&c,parent,sizeof parent);h_nvnCommandBufferCopyCommands((NVNcommandBuffer *)&c,1,&child);
    NVNcommandHandle root=h_nvnCommandBufferEndRecording((NVNcommandBuffer *)&c);assert(c.used==24);
    memset(vp,0,sizeof vp);state d={0};memset(&S,0,sizeof S);
    replay((const uint8_t *)(uintptr_t)root,&d,0);assert(!memcmp(&S,&d,sizeof S));
    assert(S.vp[0][0]==1&&S.vp[1][0]==9&&S.vp[0][3]==4&&S.vp[1][3]==4);
}
static pthread_barrier_t ready;
static void *invoke(void *unused) {
    (void)unused;pthread_barrier_wait(&ready);
    for(unsigned i=0;i<1025;i++)assert(nvn_unimpl_c(&nvn_slot[1],11,22,33,44)==0);
    return NULL;
}
static void test_once(void) {
    atomic_store(&logs,0);atomic_store(&notes,0);
    for(unsigned i=0;i<1025;i++)assert(nvn_unimpl_c(&nvn_slot[0],11,22,33,44)==0);
    assert(atomic_load(&logs)==1&&atomic_load(&notes)==1025);
    pthread_t t[8];assert(!pthread_barrier_init(&ready,NULL,8));
    for(int i=0;i<8;i++)assert(!pthread_create(&t[i],NULL,invoke,NULL));
    for(int i=0;i<8;i++)assert(!pthread_join(t[i],NULL));
    assert(!pthread_barrier_destroy(&ready));assert(atomic_load(&logs)==2);
    assert(atomic_load(&notes)==9*1025); // optional per-call diagnostics remain live
}
static uint64_t ticks(void) { struct timespec t;assert(!clock_gettime(CLOCK_MONOTONIC,&t));return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec; }
static volatile uint64_t digest;
static __attribute__((noinline)) void consume(const uint8_t *mem,size_t n) {
    uint64_t v=0;for(size_t i=0;i<n;i+=8){uint64_t q;memcpy(&q,mem+i,8);v+=q;}digest^=v;
}
static void benchmark(int n) {
    const unsigned rounds=4096,batch=256;uint64_t elapsed=0;size_t bytes=0;
    uint8_t *mem=malloc(1<<20);assert(mem);float vp[64];NVNviewportSwizzle sw[64];
    for(int i=0;i<64;i++){vp[i]=(float)i;sw[i]=(NVNviewportSwizzle)(i%8);}
    for(unsigned round=0;round<rounds;round++) {
        cmd_t c;init(&c,mem,1<<20);vp[0]=(float)round;
        uint64_t start=ticks();
        for(unsigned i=0;i<batch;i++) {
            h_nvnCommandBufferSetViewports((NVNcommandBuffer *)&c,0,n,vp);
            h_nvnCommandBufferSetViewportSwizzles((NVNcommandBuffer *)&c,0,n,sw);
            h_nvnCommandBufferSetScissor((NVNcommandBuffer *)&c,1,2,3,4);
            h_nvnCommandBufferSetDepthRange((NVNcommandBuffer *)&c,0,1);
        }
        h_nvnCommandBufferEndRecording((NVNcommandBuffer *)&c);elapsed+=ticks()-start;bytes=c.used;
        consume(mem,bytes);
    }
    printf("n=%d bytes_per_group=%zu ns_per_group=%.2f groups=%u checksum=%llu\n",n,(bytes-8)/batch,(double)elapsed/(rounds*batch),rounds*batch,(unsigned long long)digest);
    free(mem);
}
int main(int argc,char **argv) {
    if(argc>1&&!strcmp(argv[1],"bench")){benchmark(1);benchmark(16);return 0;}
    test_readers();test_ranges_and_adjacent();test_segments_and_calls();test_once();
    puts("production records/readers: counts, bounds, snapshots, guard pages, adjacent recordings, segment jumps, nested calls and concurrent once logs passed");return 0;
}
'''
    return code


def compile_fixture(tmp, source, name, sanitize):
    path = Path(tmp) / (name + ".c")
    binary = Path(tmp) / name
    path.write_text(fixture(source))
    command = ["cc", "-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
               "-pthread", "-I" + str(ROOT / "dm/gfx/nvn"), "-I" + str(ROOT / "dm/gfx/nvn/include")]
    if sanitize:
        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    subprocess.run(command + [str(path), "-o", str(binary)], check=True)
    return binary


class CompactRecords(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_production_writers_readers_and_once_logging(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = compile_fixture(tmp, (ROOT / SOURCE).read_text(), "compact", True)
            subprocess.run([str(binary)], check=True)


def benchmark(ref):
    baseline = subprocess.check_output(["git", "show", ref + ":" + SOURCE], cwd=ROOT, text=True)
    with tempfile.TemporaryDirectory() as tmp:
        old = compile_fixture(tmp, baseline, "baseline", False)
        new = compile_fixture(tmp, (ROOT / SOURCE).read_text(), "candidate", False)
        for i in range(5):
            for label, binary in (("baseline", old), ("candidate", new)) if i % 2 == 0 else (("candidate", new), ("baseline", old)):
                print(f"{label} repetition={i + 1}", flush=True)
                subprocess.run([str(binary), "bench"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--baseline-ref", default="HEAD")
    args, rest = parser.parse_known_args()
    if args.benchmark:
        benchmark(args.baseline_ref)
    else:
        unittest.main(argv=[__file__] + rest)
