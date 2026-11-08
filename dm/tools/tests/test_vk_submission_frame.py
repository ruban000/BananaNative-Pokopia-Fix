"""Production begin/commit/query paths retain a delayed job's original frame."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def function(source, name):
    start = source.index(name)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def fixture(source):
    pre = r'''
#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan_core.h>
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
enum { NSLOT=8, NCHUNK=32, SEGQ=128, GRING=64, NHREC=16, QPS=2*(NCHUNK+1) };
static struct { VkDevice dev; uint32_t qf; VkSemaphore tl; uint64_t tl_queued; pthread_mutex_t qmu; } V = {.qmu=PTHREAD_MUTEX_INITIALIZER};
static struct { VkCommandBuffer cb; uint64_t fence; VkCommandBuffer x[NCHUNK]; } g_slot[NSLOT];
static unsigned g_slot_i;
static VkCommandPool g_pool=(VkCommandPool)(uintptr_t)1;
static VkCommandBuffer g_cb;
static uint64_t g_slot_frame[8]; static uint8_t g_slot_ts[8]; static uint32_t g_slot_parts[8];
static struct { _Atomic uint64_t frame, ns, t0, t1, span; _Atomic int pending; } g_gpu[GRING];
static _Atomic uint64_t g_fstart[64], g_fstart_f[64];
static struct { uint64_t fence, frame, queue, draws, ops, skipped, t_ns; } g_hrec[NHREC];
static uint64_t g_frame, g_job_frame, g_stat_draws, g_stat_ops, g_stat_skipped, g_n_sub, g_rec_ns;
static int g_job_frame_set, worker_on, g_nrc_ts, g_chunk_draws, g_nx, g_sq_n, g_sq_base, g_seg;
static uint64_t g_chunk_d0, g_cur_fence, g_sub_draws0, g_sub_ops0, g_sub_skip0, g_sub_t0, g_sub_frame;
static VkQueryPool g_qp=(VkQueryPool)(uintptr_t)1, g_sqp; static double g_ts_ns=1, g_sq_ns;
static PFN_vkGetQueryPoolResults g_sq_get;
static struct { uint64_t serial; } S={.serial=123};
typedef struct { int kind; uint32_t mode,v,type; uint64_t addr; } pop;
static pop g_post[16], completed_post; static int g_npost;
static uint64_t completed_fence, profiler_frame, query_clock, host_clock, advanced_live_frame, callback_frame;
static uint64_t query_values[NSLOT*QPS];
static char last_log[512];
static void log_line(const char *format, ...) { va_list a; va_start(a,format); vsnprintf(last_log,sizeof last_log,format,a); va_end(a); }
#define VLOG(...) log_line(__VA_ARGS__)
#define VRED(...) log_line(__VA_ARGS__)
#define VRED_ONCE(...) log_line(__VA_ARGS__)
#define VKA_ENV(key,def) (def)
static _Atomic int g_synclog=1;
static uint64_t now_ns(void) { return ++host_clock; }
static double ms_now(void) { return now_ns()/1e6; }
static int vka_worker_on(void) { return worker_on; }
static uint64_t vka_done(void) { return completed_fence; }
static int vka_wait_done(uint64_t fence,uint64_t timeout) { (void)timeout; assert(fence<=completed_fence); return 1; }
static int vka_init(void) { return 1; }
static int vka_hung(void) { return 0; }
static int ts_init(void) { return 1; }
static int seg_ts(void) { return 0; }
static void vka_perf_thread(int n) { (void)n; }
// Simulate the game presenting after the worker latched the job, before queries.
static void vka_ctl_sub(void) { g_frame=advanced_live_frame; }
static void vka_gpu_floor(void) {}
static void vka_gp_sub_begin(VkCommandBuffer cb,uint64_t f) { (void)cb; profiler_frame=f; }
static void vka_enc_start(VkCommandBuffer cb) { (void)cb; }
static void vka_barrier(VkCommandBuffer cb) { (void)cb; }
static void vka_tex_prepare(VkCommandBuffer cb) { (void)cb; }
static void vka_draw_sub_begin(VkCommandBuffer cb,unsigned k) { (void)cb; (void)k; }
void vka_pso_pause(int n) { (void)n; }
static uint64_t vka_fence_next(void) { return completed_fence+1; }
int vka_floor_tail_add(void) { return 1; }
static void post(pop p) { assert(g_npost<16); g_post[g_npost++]=p; }
static void post_run(void *ops,int n) { (void)n; free(ops); }
static void vka_buffer_trace_commit(uint64_t f,int ok) { (void)f; (void)ok; }
static void vka_fence_cpu(uint64_t f,void *ops,int n,void (*run)(void *,int)) { completed_fence=f; run(ops,n); }
static void vka_rp_end(void) {}
static void vka_tex_writeback(VkCommandBuffer cb) { (void)cb; }
static void vka_enc_stop(void) {}
static void vka_gp_commit(VkCommandBuffer cb,uint64_t f) { (void)cb; (void)f; }
static void vka_tex_gw_commit(uint64_t f) { (void)f; }
static void vka_floor_commit(int tail) { (void)tail; }
static void vka_up_flush(void) {}
static void vka_up_throttle(void) {}
static VkResult vka_submit(VkCommandBuffer cb,uint64_t f,uint32_t n,const VkSemaphore *s,const uint64_t *v,const VkPipelineStageFlags *st,VkSemaphore bs) { (void)cb;(void)n;(void)s;(void)v;(void)st;(void)bs;V.tl_queued=f;return VK_SUCCESS; }
static int vka_nan_on(void) { return 0; }
static void vka_nan_commit(uint64_t f) { (void)f; }
static void vka_scratch_retire(uint64_t f) { (void)f; }
static void vka_fence_post(uint64_t f,void *ops,int n,void (*run)(void *,int)) {
    (void)run; assert(n==1); completed_post=((pop *)ops)[0]; callback_frame=completed_post.addr; completed_fence=f; free(ops);
}
static VkResult create_pool(VkDevice d,const VkCommandPoolCreateInfo *i,const VkAllocationCallbacks *a,VkCommandPool *p) { (void)d;(void)i;(void)a;*p=(VkCommandPool)(uintptr_t)1;return 0; }
static VkResult alloc_cbs(VkDevice d,const VkCommandBufferAllocateInfo *i,VkCommandBuffer *b) { (void)d;for(unsigned k=0;k<i->commandBufferCount;k++)b[k]=(VkCommandBuffer)(uintptr_t)(k+1);return 0; }
static VkResult reset_cb(VkCommandBuffer cb,VkCommandBufferResetFlags f) { (void)cb;(void)f;return 0; }
static VkResult begin_cb(VkCommandBuffer cb,const VkCommandBufferBeginInfo *i) { (void)cb;(void)i;return 0; }
static VkResult end_cb(VkCommandBuffer cb) { (void)cb;return 0; }
static VkResult signal_sem(VkDevice d,const VkSemaphoreSignalInfo *i) { (void)d;(void)i;return 0; }
static void barrier(VkCommandBuffer cb,VkPipelineStageFlags s,VkPipelineStageFlags d,VkDependencyFlags f,uint32_t nm,const VkMemoryBarrier *m,uint32_t nb,const VkBufferMemoryBarrier *b,uint32_t ni,const VkImageMemoryBarrier *i) { (void)cb;(void)s;(void)d;(void)f;(void)nm;(void)m;(void)nb;(void)b;(void)ni;(void)i; }
static struct { PFN_vkCreateCommandPool vkCreateCommandPool; PFN_vkAllocateCommandBuffers vkAllocateCommandBuffers; PFN_vkResetCommandBuffer vkResetCommandBuffer; PFN_vkBeginCommandBuffer vkBeginCommandBuffer; PFN_vkEndCommandBuffer vkEndCommandBuffer; PFN_vkCmdPipelineBarrier vkCmdPipelineBarrier; PFN_vkSignalSemaphore vkSignalSemaphore; } vk={create_pool,alloc_cbs,reset_cb,begin_cb,end_cb,barrier,signal_sem};
static VkResult query_create(VkDevice d,const VkQueryPoolCreateInfo *i,const VkAllocationCallbacks *a,VkQueryPool *p) { (void)d;(void)i;(void)a;*p=(VkQueryPool)(uintptr_t)1;return 0; }
static void query_reset(VkCommandBuffer cb,VkQueryPool p,uint32_t f,uint32_t n) { (void)cb;(void)p;memset(query_values+f,0,n*sizeof(uint64_t)); }
static void query_write(VkCommandBuffer cb,VkPipelineStageFlagBits st,VkQueryPool p,uint32_t q) { (void)cb;(void)st;(void)p;query_clock+=1000000;query_values[q]=query_clock; }
static VkResult query_get(VkDevice d,VkQueryPool p,uint32_t f,uint32_t n,size_t size,void *out,VkDeviceSize stride,VkQueryResultFlags flags) { (void)d;(void)p;(void)flags;assert(stride==8&&size>=n*8);memcpy(out,query_values+f,n*8);return 0; }
static struct { PFN_vkCreateQueryPool create; PFN_vkCmdResetQueryPool reset; PFN_vkCmdWriteTimestamp write; PFN_vkGetQueryPoolResults get; } qf={query_create,query_reset,query_write,query_get};
'''
    code = pre
    for name in ("void vka_exec_sub_frame(", "uint64_t vka_exec_frame(", "uint64_t vka_frame_start_ns(",
                 "static void gpu_tag_begin(", "static void gpu_slot_done(", "double mtl_gpu_ms(",
                 "void vka_rp_sub_begin(", "uint64_t vka_rp_sub_commit("):
        code += function(source, name) + "\n"
    return code + r'''
static void record(uint64_t origin,uint64_t live,uint64_t advanced,uint64_t commit_live,uint64_t fence,int queued) {
    worker_on=queued; g_frame=live; advanced_live_frame=advanced;
    if (queued) vka_exec_sub_frame(origin);
    unsigned slot=g_slot_i%NSLOT;
    vka_rp_sub_begin();
    assert(g_frame==advanced); // actual callback advances the live frame after latch
    assert(g_sub_frame==origin && profiler_frame==origin);
    assert(g_slot_frame[slot]==origin);
    assert(vka_frame_start_ns(origin)>0);
    g_stat_draws++; g_frame=commit_live;
    assert(vka_rp_sub_commit(fence)==fence);
    assert(g_hrec[fence%NHREC].frame==origin && g_hrec[fence%NHREC].fence==fence);
    assert(callback_frame==origin && completed_post.kind==3);
    char expected[100]; snprintf(expected,sizeof expected,"sync sub: frame %llu fence %llu:",(unsigned long long)origin,(unsigned long long)fence);
    assert(strstr(last_log,expected));
    gpu_slot_done(slot);
    assert(mtl_gpu_ms(origin)>0);
}
int main(void) {
    for(unsigned i=0;i<NSLOT;i++)g_slot[i].cb=(VkCommandBuffer)(uintptr_t)(i+1);
    // Two already-queued frame7 submissions, replayed after live game advancement.
    record(7,40,41,99,21,1);
    record(7,99,100,777,22,1);
    assert(mtl_gpu_ms(7)==2.0); // both query intervals belong to frame7
    assert(mtl_gpu_ms(40)==-1 && mtl_gpu_ms(41)==-1 && mtl_gpu_ms(99)==-1);
    record(8,900,901,999,23,1);
    assert(mtl_gpu_ms(8)==1.0);
    // Worker disabled: ignore the stale queued frame8, latch live77 once.
    record(77,77,78,1000,24,0);
    assert(mtl_gpu_ms(77)==1.0 && mtl_gpu_ms(78)==-1);
    puts("ok: delayed queued identity, begin/commit delay, GPU bucket, profiler, hang/post/log and inline fallback");
    return 0;
}
'''


class SubmissionFrame(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_delayed_job_and_inline_fallback(self):
        source = (ROOT / "dm/gfx/vk_android/vka_exec.c").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / "frame.c"
            binary = Path(tmp) / "frame"
            c.write_text(fixture(source))
            command = ["cc", "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                       "-Wno-missing-field-initializers", "-Wno-unused-function", "-Wno-unused-variable",
                       "-fsanitize=address,undefined", "-pthread", "-I", str(ROOT / "dm/third_party/vulkan-headers/include"),
                       str(c), "-o", str(binary)]
            subprocess.run(command, check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("ok: delayed queued identity", result.stdout)
            # A live-frame re-latch is a regression even if begin is called late.
            stale = source.replace("g_sub_frame = vka_exec_frame();", "g_sub_frame = g_frame;")
            self.assertNotEqual(stale, source)
            c.write_text(fixture(stale))
            subprocess.run(command, check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("g_sub_frame==origin", result.stderr)


if __name__ == "__main__":
    unittest.main()
