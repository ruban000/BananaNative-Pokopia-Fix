"""Production dispatch selection, deferred GPU snapshots and exact-fence retirement."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class BufferTrace(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_gpu_boundaries_and_fence_retirement(self):
        source = (ROOT / "dm/gfx/vk_android/vka_buftrace.c").read_text()
        source = source.replace('#include "vka_capture.h"', '')
        header = (ROOT / "dm/gfx/vk_android/vka.h").read_text()
        sample = header[header.index("typedef struct {\n    uint64_t frame, key, ordinal, guest"):]
        sample = sample[:sample.index("uint64_t vka_buffer_trace_mask")]
        draw = (ROOT / "dm/gfx/vk_android/vka_draw.c").read_text()
        dispatch = draw[draw.index("static void buffer_trace_dispatch("):draw.index("int dm_sdk_ounce")]
        harness = r'''
#define __ANDROID__ 1
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan_core.h>
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
#define VLOG(...) ((void)0)
#define VRED(...) ((void)0)
#define VRED_ONCE(...) ((void)0)
#define VKA_ENV(k,d) (getenv(k) ? atoi(getenv(k)) : (d))
static int vka_env_str(const char *k,char *s,size_t n) { const char *v=getenv(k); if(v) snprintf(s,n,"%s",v);return !!v; }
static int hung;
static int vka_hung(void) { return hung; }
typedef struct { VkBuffer b; VkDeviceMemory m; uint8_t *p; VkDeviceSize n; } vcap_hbuf;
typedef struct { uint8_t *data; size_t size; int staging; } mock_buffer;
static unsigned allocations, frees, destroys, threads, barrier_count;
static uint64_t allocated_bytes, peak_bytes;
static size_t extra_requirement;
static mock_buffer *buffer(VkBuffer b) { return (mock_buffer *)(uintptr_t)b; }
static VkResult create_buffer(VkDevice d,const VkBufferCreateInfo *bi,const VkAllocationCallbacks *a,VkBuffer *out) {
 (void)d;(void)a;assert(bi->usage==VK_BUFFER_USAGE_TRANSFER_DST_BIT);
 mock_buffer *b=calloc(1,sizeof *b);assert(b);b->size=bi->size;b->staging=1;*out=(VkBuffer)(uintptr_t)b;return VK_SUCCESS;
}
static void requirements(VkDevice d,VkBuffer b,VkMemoryRequirements *r) { (void)d;*r=(VkMemoryRequirements){buffer(b)->size+extra_requirement,16,1}; }
static VkResult alloc_memory(VkDevice d,const VkMemoryAllocateInfo *a,const VkAllocationCallbacks *cb,VkDeviceMemory *m) {
 (void)d;(void)cb;uint8_t *p=calloc(1,a->allocationSize);assert(p);*m=(VkDeviceMemory)(uintptr_t)p;allocations++;
 allocated_bytes+=a->allocationSize;if(allocated_bytes>peak_bytes)peak_bytes=allocated_bytes;return VK_SUCCESS;
}
static VkResult bind_memory(VkDevice d,VkBuffer b,VkDeviceMemory m,VkDeviceSize o) { (void)d;assert(!o);buffer(b)->data=(uint8_t *)(uintptr_t)m;return VK_SUCCESS; }
static VkResult map_memory(VkDevice d,VkDeviceMemory m,VkDeviceSize o,VkDeviceSize n,VkMemoryMapFlags f,void **p) { (void)d;(void)n;(void)f;assert(!o);*p=(void *)(uintptr_t)m;return VK_SUCCESS; }
static void vcap_hbuf_free(vcap_hbuf *h) {
 if(h->b) { mock_buffer *b=buffer(h->b); if(h->m) { free((void *)(uintptr_t)h->m);allocated_bytes-=h->n;frees++; }free(b);destroys++; } memset(h,0,sizeof *h);
}
static uint32_t vka_mem_type(uint32_t bits,VkMemoryPropertyFlags need,VkMemoryPropertyFlags avoid) { (void)need;(void)avoid;assert(bits==1);return 0; }
static struct { VkDevice dev; } V;
static struct { void (*fn)(void *); void *arg; uint64_t fence; } completions[1024];
static unsigned ncompletions;
static void vka_after(uint64_t f,void (*fn)(void *),void *a) { assert(ncompletions<1024);completions[ncompletions++]=(typeof(completions[0])){fn,a,f}; }
static int fake_create(pthread_t *t,const pthread_attr_t *a,void *(*fn)(void *),void *v) { (void)t;(void)a;(void)fn;(void)v;threads++;return 0; }
static int fake_detach(pthread_t t) { (void)t;return 0; }
#define pthread_create fake_create
#define pthread_detach fake_detach
static struct { mock_buffer *s,*d;size_t so,off,n;int fill; } commands[8192];
static unsigned ncommands;
static int awaiting_copy, awaiting_war;
static void copy_cmd(VkCommandBuffer cb,VkBuffer s,VkBuffer d,uint32_t n,const VkBufferCopy *r) {
 (void)cb;assert(n==1&&awaiting_copy&&!awaiting_war);assert(r->size&&!(r->size&3));
 assert(r->srcOffset+r->size<=buffer(s)->size&&r->dstOffset+r->size<=buffer(d)->size);
 commands[ncommands++]=(typeof(commands[0])){buffer(s),buffer(d),r->srcOffset,r->dstOffset,r->size,0};awaiting_copy=0;awaiting_war=1;
}
static void barrier_cmd(VkCommandBuffer cb,VkPipelineStageFlags s,VkPipelineStageFlags d,VkDependencyFlags dep,uint32_t nm,const VkMemoryBarrier *m,uint32_t nb,const VkBufferMemoryBarrier *b,uint32_t ni,const VkImageMemoryBarrier *im) {
 (void)cb;(void)dep;(void)m;(void)im;assert(!nm&&!ni&&nb==1);barrier_count++;
 if(b->dstAccessMask==VK_ACCESS_TRANSFER_READ_BIT) {
  assert(!awaiting_copy&&!awaiting_war);assert(s==VK_PIPELINE_STAGE_ALL_COMMANDS_BIT&&d==VK_PIPELINE_STAGE_TRANSFER_BIT);
  assert(b->srcAccessMask==VK_ACCESS_MEMORY_WRITE_BIT);awaiting_copy=1;
 } else {
  assert(awaiting_war&&!awaiting_copy);assert(b->dstAccessMask==VK_ACCESS_HOST_READ_BIT&&b->srcAccessMask==VK_ACCESS_TRANSFER_WRITE_BIT);
  assert(s==VK_PIPELINE_STAGE_TRANSFER_BIT&&(d&VK_PIPELINE_STAGE_ALL_COMMANDS_BIT)&&(d&VK_PIPELINE_STAGE_HOST_BIT));awaiting_war=0;
 }
}
static struct {
 PFN_vkCreateBuffer vkCreateBuffer;PFN_vkGetBufferMemoryRequirements vkGetBufferMemoryRequirements;
 PFN_vkAllocateMemory vkAllocateMemory;PFN_vkBindBufferMemory vkBindBufferMemory;PFN_vkMapMemory vkMapMemory;
 PFN_vkCmdCopyBuffer vkCmdCopyBuffer;PFN_vkCmdPipelineBarrier vkCmdPipelineBarrier;
} vk={create_buffer,requirements,alloc_memory,bind_memory,map_memory,copy_cmd,barrier_cmd};
static void execute(void) {
 assert(!awaiting_copy&&!awaiting_war);
 for(unsigned i=0;i<ncommands;i++) {
  if(commands[i].fill) memset(commands[i].d->data+commands[i].off,commands[i].fill,commands[i].n);
  else memcpy(commands[i].d->data+commands[i].off,commands[i].s->data+commands[i].so,commands[i].n);
 } ncommands=0;
}
static void producer(mock_buffer *b,int value) { assert(!awaiting_copy&&!awaiting_war);commands[ncommands++]=(typeof(commands[0])){NULL,b,0,0,b->size,value}; }
'''
        selection_types = r'''
enum { VKA_R_BUF=1,DMSH_VK_KIND_SBUF=1,DMSH_VK_KIND_CBUF=2,DMSH_VK_KIND_UBO_PTR=3 };
typedef struct { uint64_t addr,size; } vka_range;
typedef struct { struct { vka_range ssbo[8][16],ubo[8][16]; } b; } vka_dstate;
typedef struct { int set,binding,kind,reg; } dmsh_vk_bind;
typedef struct { uint32_t bind_count; } mock_entry;
typedef struct { uint64_t key;mock_entry *e;dmsh_vk_bind *b; } vka_shader;
typedef struct { int what,desc;uint32_t binding,elem;uint64_t addr,size; } vka_res;
static struct { int n[8];vka_res r[8][8]; } g_wk;
static struct { uint32_t n;VkWriteDescriptorSet w[8]; } g_w;
static VkCommandBuffer g_cb=(VkCommandBuffer)1;
'''
        test = r'''
static void dispose(bt_packet *p) { uint64_t n=p->h.n;vcap_hbuf_free(&p->h);free(p);atomic_fetch_sub(&g_bt_live_bytes,n); }
static void fire_all(void) { for(unsigned i=0;i<ncompletions;i++) completions[i].fn(completions[i].arg);ncompletions=0; }
static void drain(void) { while(g_bt_ready) { bt_packet *p=g_bt_ready;g_bt_ready=p->next;dispose(p); }g_bt_ready_tail=NULL; }
int main(int argc,char **argv) {
 assert(argc>=2);const char *mode=argv[1];
 assert(!vka_buffer_trace_mask(1,0x301c,0));vka_buffer_trace_commit(1,1);
 assert(!allocations&&!threads&&!barrier_count&&!ncompletions); // default off is inert
 setenv("DM_VK_BUFFER_TRACE","301c:pre:ubo0:128,301c:post:ssbo0,8879:pre:ubo0:64,8879:post:ssbo0:64",1);
 setenv("DM_VK_BUFFER_TRACE_DIR",argv[2],1);
 setenv("DM_VK_BUFFER_TRACE_FRAMES","9999",1);
 assert(vka_buffer_trace_mask(2,0x301c,0)==(1ull<<16)&&g_bt_frames==120&&threads==1);
 uint8_t data[65536];memset(data,0x11,sizeof data);mock_buffer gpu={data,sizeof data,0};
 VkDescriptorBufferInfo ub={(VkBuffer)(uintptr_t)&gpu,256,128},sb={(VkBuffer)(uintptr_t)&gpu,512,57360};
 vka_dstate state={0};state.b.ubo[5][0]=(vka_range){0x1000,128};state.b.ssbo[5][0]=(vka_range){0x2010,57344};
 mock_entry entry={2};dmsh_vk_bind bindings[]={{0,5,DMSH_VK_KIND_CBUF,0},{0,4,DMSH_VK_KIND_SBUF,0}};
 vka_shader shader={0x301c,&entry,bindings};
 g_wk.n[5]=2;g_wk.r[5][0]=(vka_res){VKA_R_BUF,VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER,5,0,0x1000,128};
 g_wk.r[5][1]=(vka_res){VKA_R_BUF,VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,4,0,0x2000,57360};
 g_w.n=2;g_w.w[0]=(VkWriteDescriptorSet){.dstBinding=5,.pBufferInfo=&ub};g_w.w[1]=(VkWriteDescriptorSet){.dstBinding=4,.pBufferInfo=&sb};
 buffer_trace_dispatch(&state,&shader,2,7,0);assert(g_bt_pending->samples[0].source.descriptor.offset==256);
 assert(!ncompletions&&!frees&&g_bt_pending->h.p[0]==0); // GPU bytes untouched while recording
 producer(&gpu,0x22);buffer_trace_dispatch(&state,&shader,2,7,1);producer(&gpu,0x33);
 ub.offset=1024;sb.offset=4096;state.b.ubo[5][0].addr=0x9000;shader.key=0xdead;
 assert(g_bt_pending->samples[0].source.guest==0x1000&&g_bt_pending->samples[0].source.key==0x301c);
 bt_packet *first=g_bt_pending;assert(first->n==1&&first->next->samples[0].prefix==16);
 assert(first->next->samples[0].bytes==224&&first->next->samples[0].source.descriptor.range==57360);
 vka_buffer_trace_commit(123,1);assert(ncompletions==2&&completions[0].fence==123&&!g_bt_pending&&!g_bt_ready&&!frees);
 execute();assert(!g_bt_ready&&!frees); // actual GPU execution does not replace exact fence retirement
 if(!strcmp(mode,"failure")) vka_buffer_trace_commit(124,0);
 if(!strcmp(mode,"loss")) hung=1;
 fire_all();
 if(strcmp(mode,"normal")) { assert(!g_bt_ready&&!frees);dispose(first->next);dispose(first);return 0; }
 assert(g_bt_ready==first&&first->samples[0].fence==123);
 assert(first->h.p[0]==0x11&&first->next->h.p[0]==0x22&&data[0]==0x33);
 bt_write_packet(first); // actual file writer; checked by Python below
 drain();assert(atomic_load(&g_bt_live_bytes)==0&&frees==2);
 assert(vka_buffer_trace_mask(121,0x301c,0));assert(!vka_buffer_trace_mask(122,0x301c,0));
 // A new arming captures a complete SH output. Driver padding counts toward live cap.
 setenv("DM_VK_BUFFER_TRACE","301c:post:ssbo0:57344",1);
 assert(vka_buffer_trace_mask(123,0x301c,1)==1);
 vka_buffer_sample s={.frame=123,.key=0x301c,.ordinal=8,.guest=0x2000,.source_size=57344,.descriptor={(VkBuffer)(uintptr_t)&gpu,0,57344},.kind=0,.reg=0,.phase=1};
 extra_requirement=4096;
 for(unsigned i=0;i<1000;i++) vka_buffer_trace_copy(g_cb,&s,0);
 assert(atomic_load(&g_bt_live_bytes)<=BT_MAX_MEMORY&&peak_bytes<=BT_MAX_MEMORY&&g_bt_total<=BT_MAX_MEMORY);
 assert(g_bt_count>0&&g_bt_count<147);assert(g_bt_pending->samples[0].bytes==57344);execute();
 assert(!memcmp(g_bt_pending->h.p,data,57344));vka_buffer_trace_commit(124,1);fire_all();drain();extra_requirement=0;
 // Cumulative cap remains after previous packets are retired.
 g_bt_total=BT_MAX_MEMORY-57340;unsigned old=g_bt_count;vka_buffer_trace_copy(g_cb,&s,0);assert(g_bt_ended&&g_bt_count==old);
 unsetenv("DM_VK_BUFFER_TRACE");assert(!vka_buffer_trace_mask(124,0x301c,0));
 setenv("DM_VK_BUFFER_TRACE","301c:post:ssbo0:3",1);assert(!vka_buffer_trace_mask(125,0x301c,1));
 setenv("DM_VK_BUFFER_TRACE","301c:post:ssbo16",1);assert(!vka_buffer_trace_mask(126,0x301c,1));
 setenv("DM_VK_BUFFER_TRACE","301c:post:ssbo0,301c:post:ssbo0:64",1);assert(!vka_buffer_trace_mask(127,0x301c,1));
 setenv("DM_VK_BUFFER_TRACE","8879:post:ssbo0:64",1);assert(vka_buffer_trace_mask(128,0x8879,1));
 s.key=0x8879;s.frame=128;s.source_size=64;s.descriptor.range=64;vka_buffer_trace_copy(g_cb,&s,0);
 assert(g_bt_pending->samples[0].bytes==64);execute();vka_buffer_trace_commit(125,1);fire_all();drain();
 char truncated[300];memset(truncated,'a',sizeof truncated);truncated[sizeof truncated-1]=0;
 setenv("DM_VK_BUFFER_TRACE",truncated,1);assert(!vka_buffer_trace_mask(129,0xaaaa,0));
 assert(!allocated_bytes&&!atomic_load(&g_bt_live_bytes));puts("production GPU snapshots, descriptors, bounds and exact-fence lifetime: passed");return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / "buffer_trace.c"
            binary = Path(tmp) / "buffer_trace"
            out = Path(tmp) / "game-work"
            c.write_text(harness + sample + source + selection_types + dispatch + test)
            subprocess.run(["cc", "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "dm/third_party/vulkan-headers/include"), str(c), "-pthread", "-o", str(binary)], check=True)
            for mode in ("normal", "failure", "loss"):
                subprocess.run([str(binary), mode, str(out)], check=True)
            files = list(out.glob("*.bin"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), bytes([0x11]) * 128)
            metadata = Path(str(files[0]) + ".tsv").read_text().splitlines()
            row = dict(zip(metadata[0].split("\t"), metadata[1].split("\t")))
            self.assertEqual((row["frame"], row["ordinal"], row["descriptor_offset"], row["fence"]), ("2", "7", "256", "123"))
            self.assertEqual((row["guest"], row["kind"], row["bytes"]), ("0x1000", "ubo", "128"))


if __name__ == "__main__":
    unittest.main()
