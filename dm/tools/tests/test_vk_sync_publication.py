"""Production queued submit/replay/wait preserve newer publications of a reused sync."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def function(source, name):
    start = source.index(name)
    start = source.rfind("\n", 0, start) + 1
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def fixture(exec_source):
    worker = (ROOT / "dm/gfx/vk_android/vka_worker.c").read_text()
    nvn = (ROOT / "dm/gfx/nvn/hle_nvn.c").read_text()
    code = r'''
#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan_core.h>
#include "nvn_obj.h"
#include <assert.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#define VLOG(...) do { if (0) printf(__VA_ARGS__); } while (0)
#define VRED(...) VLOG(__VA_ARGS__)
#define VRED_ONCE(...) VLOG(__VA_ARGS__)
#define VKA_ENV(k,d) (d)
#define DM_GAME_RENDER_COND 0
#define NVN(n,r,...) r n(__VA_ARGS__)
enum {SEGQ=128,RC_PEND=64,J_SUB=1};
typedef struct { int kind; uint32_t mode,v,type;uint64_t addr;} pop;
static struct { p_set_rt rt;int has_rt;p_scissor sc;int has_sc;int64_t copy_stride,copy_img_stride;int rc_skip,d;} S;
#pragma GCC diagnostic ignored "-Wunused-variable"
#pragma GCC diagnostic ignored "-Wunused-function"   // (the stubs below and the old render-condition lists: the code under test no longer reaches some)
static int g_rcl;   // the render-condition log: off
static void rcl_op(uint32_t op, const void *a) { (void)op; (void)a; }
static void rc_note(uint64_t a, uint64_t type) { (void)a; (void)type; }
static void vka_rp_note_ts(void) {}
static void vka_rp_zstats(char *b, size_t n) { if (n) b[0] = 0; }
static void vka_draw_peek_commit(uint64_t f) { (void)f; }
static void vka_rw_pre(VkCommandBuffer cb) { (void)cb; }
static void vka_rw_point(VkCommandBuffer cb, int c, uint64_t a, uint64_t b, uint64_t d) { (void)cb; (void)c; (void)a; (void)b; (void)d; }
static void vka_rw_commit(uint64_t f) { (void)f; }
static uint64_t g_frame;
static VkCommandBuffer g_cb;static VkQueryPool g_sqp;static int g_sq_n,g_sq_base,g_nrc_ts,g_nx,g_npost;
static uint64_t g_cur_fence,g_stat_draws,g_sub_draws0,g_sub_frame,g_n_ub,g_rc_ts[RC_PEND];
static pop g_post[64];static _Atomic int g_synclog;static _Atomic uint64_t g_sync_ns;
static int worker_on=1;static uint64_t last_fence=9,done_fence;
static int up(void){return worker_on;}
static uint64_t vka_fence_next(void){return ++last_fence;}
static uint64_t vka_fence_last(void){return last_fence;}
static uint64_t mtl_last_fence(void){return last_fence;}
static uint64_t vka_frame(void){return 77;}
static uint64_t now_ns(void){return 0;}
static double ms_now(void){return 0;}
static int mtl_wait(uint64_t f,uint64_t timeout){(void)timeout;return f<=done_fence;}
static int mtl_wait_acq(uint64_t f,uint64_t timeout){return mtl_wait(f,timeout);}
static void vka_rp_sub_begin(void){}
static uint64_t vka_rp_sub_commit(uint64_t reserved){g_cur_fence=0;return reserved?reserved:vka_fence_next();}
static void post(pop p){g_post[g_npost++]=p;}
static void seg_write(const void*p){(void)p;}
static int rc_known(uint64_t a,uint64_t*v){(void)a;*v=0;return 0;}
static void chunk_check(void){}
static void vka_rp_end(void){}
static int vka_pass_keep(void){return 0;}
static int vka_rp_game_barrier(uint32_t b){(void)b;return 0;}
static int vka_state_op(int*d,unsigned op,const void*a){(void)d;(void)op;(void)a;return 0;}
static int vka_enc_call(void(*fn)(const void*),const void*a,size_t n){(void)fn;(void)a;(void)n;return 0;}
static void buf_fill(uint64_t a,uint64_t b,uint32_t c){(void)a;(void)b;(void)c;}
static void buf_copy(uint64_t a,uint64_t b,uint64_t c){(void)a;(void)b;(void)c;}
static void buf_update(const void*p,uint64_t a,uint64_t b){(void)p;(void)a;(void)b;}
static void vka_barrier(VkCommandBuffer b){(void)b;}
static void clear_color(const void*p){(void)p;}static void clear_ds(const void*p){(void)p;}
static void clear_tex(const void*p){(void)p;}static void copy_bt(const void*p,int n){(void)p;(void)n;}
static void copy_t2t(const void*p){(void)p;}static void not_yet(unsigned op){(void)op;}
static void vka_draw(int*d,p_set_rt*r,int hr,p_scissor*s,int hs,const p_draw*a,const p_draw_idx*b){(void)d;(void)r;(void)hr;(void)s;(void)hs;(void)a;(void)b;}
static void vka_dispatch(int*d,const void*a){(void)d;(void)a;}
typedef struct {int kind;uint64_t serial,fence,frame;int origin_ll;const uint8_t **h;int nh;uint64_t ticket;tex_t t;double period;int has_crop,crop[4];} job_t;
static job_t g_cur,queued[32];static int g_cur_cap,g_in_sub,nqueued;static uint64_t g_pend_serial;static int g_pend_origin;
static void push(const job_t*j){queued[nqueued++]=*j;}
'''
    code += function(exec_source, "static void exec(") + function(exec_source, "void vka_rp_exec(")
    for name in ("static void fence_syncs(", "void mtl_sub_begin(", "void mtl_exec(", "uint64_t mtl_sub_commit("):
        code += function(worker, name)
    code += function(nvn, "NVN(nvnQueueFenceSync,") + function(nvn, "NVN(nvnSyncWait,")
    return code + r'''
typedef struct {op_hdr h;uint64_t arg;} rec;
static rec end={.h={OP_END,8}};
static void record(rec*p,unsigned op,const void*arg){p->h=(op_hdr){op,16};p->arg=(uintptr_t)arg;}
static uint64_t submit(const void*h,int copies){mtl_sub_begin();for(int i=0;i<copies;i++)mtl_exec(h);return mtl_sub_commit();}
static void replay(int i){vka_rp_sub_begin();for(int h=0;h<queued[i].nh;h++)vka_rp_exec(queued[i].h[h],queued[i].fence);vka_rp_sub_commit(queued[i].fence);free(queued[i].h);}
int main(void){
 sync_t s={.magic=MAGIC('S','Y','N','C')};rec a[2];record(a,OP_FENCE_SYNC,&s);a[1]=end;
 // Submit the SAME command list/sync twice, before either delayed job executes.
 assert(submit(a,2)==10&&s.fence==10);assert(submit(a,1)==11&&s.fence==11);done_fence=10;
 assert(nvnSyncWait((NVNsync*)&s,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
 replay(0);assert(s.fence==11);assert(nvnSyncWait((NVNsync*)&s,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
 replay(1);assert(s.fence==11);done_fence=11;assert(nvnSyncWait((NVNsync*)&s,0)==NVN_SYNC_WAIT_RESULT_ALREADY_SIGNALED);
 // A newer direct QueueFenceSync must also survive replay of an older command fence.
 assert(submit(a,1)==12);last_fence=13;nvnQueueFenceSync(NULL,(NVNsync*)&s,0,0);assert(s.fence==13);replay(2);assert(s.fence==13);
 done_fence=12;assert(nvnSyncWait((NVNsync*)&s,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
 // Preserve a later acquire sync's wait-domain discriminator too.
 assert(submit(a,1)==14);s.fence=15;s.fenced=2;replay(3);assert(s.fence==15&&s.fenced==2);
 // Inline fallback still assigns each submission's next fence; no stale reserved mode.
 worker_on=0;assert(submit(a,1)==15&&s.fence==15&&s.fenced==1);done_fence=14;assert(nvnSyncWait((NVNsync*)&s,0)==NVN_SYNC_WAIT_RESULT_TIMEOUT_EXPIRED);
 assert(submit(a,1)==16&&s.fence==16);
 // CALL resumes its caller; JUMP replaces continuation, and duplicate CALLs are allowed.
 sync_t c={0},d={0},unreached={0};rec tail[2],callee[2],head[5];record(tail,OP_FENCE_SYNC,&d);tail[1]=end;record(callee,OP_FENCE_SYNC,&c);callee[1]=end;
 record(head,OP_CALL,callee);record(head+1,OP_CALL,callee);record(head+2,OP_JUMP,tail);record(head+3,OP_FENCE_SYNC,&unreached);head[4]=end;
 worker_on=1;uint64_t f=submit(head,1);assert(c.fence==f&&d.fence==f&&!unreached.fenced);replay(4);assert(c.fence==f&&d.fence==f&&!unreached.fenced);
 c=(sync_t){0};d=(sync_t){0};worker_on=0;f=submit(head,1);assert(c.fence==f&&d.fence==f&&!unreached.fenced);
 // Compare prepublication and actual full exec at the shared depth16 boundary.
 sync_t depthsync[18]={0};rec deep[18][3];for(int i=0;i<18;i++){record(deep[i],OP_CALL,i<17?deep[i+1]:(void*)&end);record(deep[i]+1,OP_FENCE_SYNC,depthsync+i);deep[i][2]=end;}
 worker_on=1;f=submit(deep,1);for(int i=0;i<18;i++)assert(depthsync[i].fenced==(unsigned)(i<=16));replay(5);
 memset(depthsync,0,sizeof depthsync);worker_on=0;f=submit(deep,1);for(int i=0;i<18;i++)assert(depthsync[i].fenced==(unsigned)(i<=16));
 // Both walks stop after handling an undersized record, without reaching trailing sync.
 rec bad[3];record(bad,OP_FENCE_SYNC,&c);bad[0].h.size=0;record(bad+1,OP_FENCE_SYNC,&unreached);bad[2]=end;
 worker_on=1;f=submit(bad,1);assert(c.fence==f&&!unreached.fenced);replay(6);worker_on=0;f=submit(bad,1);assert(c.fence==f&&!unreached.fenced);
 // A reserved replay must not even dereference the sync after publication.
 size_t page=(size_t)sysconf(_SC_PAGESIZE);sync_t *protected=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(protected!=MAP_FAILED);
 rec guarded[2];record(guarded,OP_FENCE_SYNC,protected);guarded[1]=end;worker_on=1;f=submit(guarded,1);assert(protected->fence==f);
 assert(!mprotect(protected,page,PROT_NONE));replay(7);assert(!munmap(protected,page));
 free(g_cur.h);puts("sync prepublication, delayed replay, inline and walkers: passed");
}
'''


class SyncPublication(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_delayed_submission_inline_and_walkers(self):
        source = (ROOT / "dm/gfx/vk_android/vka_exec.c").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / "sync.c"
            binary = Path(tmp) / "sync"
            command = ["cc", "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                       "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                       "-I", str(ROOT / "dm/gfx/nvn"), "-I", str(ROOT / "dm/gfx/nvn/include"),
                       "-I", str(ROOT / "dm/third_party/vulkan-headers/include"), str(c), "-o", str(binary)]
            c.write_text(fixture(source))
            subprocess.run(command, check=True)
            subprocess.run([str(binary)], check=True)
            # Negative control restores the exact old publication behavior in full exec.
            fixed = "case OP_FENCE_SYNC: if (!g_cur_fence) {"
            self.assertEqual(source.count(fixed), 1)
            old = source.replace(fixed, "case OP_FENCE_SYNC: {")
            old = old.replace("s->fence = vka_fence_last() + 1;", "s->fence = g_cur_fence ? g_cur_fence : vka_fence_last() + 1;")
            c.write_text(fixture(old))
            subprocess.run(command, check=True)
            failed = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("s.fence==11", failed.stderr)


if __name__ == "__main__":
    unittest.main()
