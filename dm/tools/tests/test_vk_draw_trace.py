"""Compile the production draw trace with descriptors and environment stubs."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class DrawTrace(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_filter_and_bounds(self):
        source = (ROOT / 'dm/gfx/vk_android/vka_draw.c').read_text()
        start = source.index('static int draw_trace_overlap(')
        end = source.index('void vka_draw(', start)
        program = r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#define VKA_NSTAGE 8
#define VKA_R_BUF 1
#define VKA_R_DRV 4
#define DMSH_VK_KIND_SBUF 5
#define VK_DESCRIPTOR_TYPE_STORAGE_BUFFER 7
static uint64_t frame;
static unsigned edges, buffers;
static uint64_t vka_exec_frame(void) { return frame; }
static long envval(const char *key, long def) { const char *v = getenv(key); return v ? strtol(v, NULL, 0) : def; }
#define VKA_ENV(k,d) envval(k,d)
static void logline(const char *fmt, ...) {
    if (!strncmp(fmt, "draw edge: frame", 16)) edges++;
    if (!strncmp(fmt, "draw buffer:", 12)) buffers++;
}
#define VLOG(...) logline(__VA_ARGS__)
#define VRED_ONCE(...) logline(__VA_ARGS__)
typedef struct { uint64_t addr,size; } vka_range;
typedef struct { struct { vka_range ssbo[8][16]; unsigned base_instance; } b; } vka_dstate;
typedef struct { uint16_t set,binding; uint8_t kind; int reg; } dmsh_vk_bind;
typedef struct { unsigned bind_count; } entry;
typedef struct { uint64_t key; entry *e; dmsh_vk_bind *b; } vka_shader;
typedef struct { int what,desc,flags,bank; unsigned binding,elem; uint64_t addr,size; } vka_res;
typedef struct { uintptr_t buffer; uint64_t offset,range; } VkDescriptorBufferInfo;
typedef struct { unsigned dstBinding,dstArrayElement; const VkDescriptorBufferInfo *pBufferInfo; } write;
static struct { int n[8],pool[8]; vka_res r[8][2]; } g_wk;
static struct { unsigned n; write w[2]; } g_w;
''' + source[start:end] + r'''
int main(void) {
    uint64_t a,n; int all;
    assert(draw_trace_range("0x4290c19000:26880", &a,&n,&all) && a==0x4290c19000 && n==26880 && !all);
    assert(draw_trace_range("4290c19000:0x6900", &a,&n,&all) && n==26880);
    assert(draw_trace_range("indirect", &a,&n,&all) && all);
    assert(!draw_trace_range("xyz:12", &a,&n,&all));
    assert(!draw_trace_range("100:-1", &a,&n,&all));
    assert(!draw_trace_range("ffffffffffffffff:1", &a,&n,&all));
    assert(!draw_trace_overlap(100,20,120,3));
    assert(draw_trace_overlap(100,20,119,3));
    assert(draw_trace_overlap(UINT64_MAX-10,10,UINT64_MAX-5,3));
    entry e={1}; dmsh_vk_bind binding={0,9,DMSH_VK_KIND_SBUF,5};
    vka_shader vs={123,&e,&binding}; vka_shader *sh[8]={&vs}; vka_dstate d={0};
    VkDescriptorBufferInfo bi={0xabc,80,160};
    g_wk.n[0]=1; g_wk.r[0][0]=(vka_res){VKA_R_BUF,7,0,0,9,0,0x2000,160};
    g_w.n=1; g_w.w[0]=(write){9,0,&bi};
    setenv("DM_VK_DRAW_TRACE_COUNT","2",1); setenv("DM_VK_DRAW_TRACE_FRAMES","2",1);
    setenv("DM_VK_DRAW_TRACE_RANGE","1000:32",1);
    frame=1; draw_resource_trace(&d,sh,0x1010,20,1,0,0,0,0,0);
    assert(edges==1 && buffers==1);
    draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0); assert(edges==1);
    /* Bound-buffer overlap selects an unrelated indirect command. */
    setenv("DM_VK_DRAW_TRACE_RANGE","2000:32",1); frame=2;
    draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0); assert(edges==2);
    draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0); assert(edges==3);
    draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0); assert(edges==3);
    /* Slot is metadata reg=5, not descriptor binding=9. Empty range still permits slot selection. */
    setenv("DM_VK_DRAW_TRACE_RANGE","",1); setenv("DM_VK_DRAW_TRACE_SLOT","5",1); frame=3;
    draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0); assert(edges==4);
    frame=5; draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0); assert(edges==4);
    setenv("DM_VK_DRAW_TRACE_RANGE","indirect",1); setenv("DM_VK_DRAW_TRACE_SLOT","-1",1); frame=6;
    draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0); assert(edges==4);
    draw_resource_trace(&d,sh,0x9000,20,1,0,0,0,0,0); assert(edges==5);
    puts("ok");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c = Path(directory) / 'trace.c'; exe = Path(directory) / 'trace'
            c.write_text(program)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
            result = subprocess.run([str(exe)], text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), 'ok')


if __name__ == '__main__':
    unittest.main()
