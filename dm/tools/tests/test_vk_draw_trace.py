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
        end = source.index("// The pace line's view of the draw path", start)   # (the draw trace code ends where the pace line's view of the draw path begins)
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
#define DMSH_VK_KIND_CBUF 1
#define DMSH_VK_KIND_UBO_PTR 2
#define VK_DESCRIPTOR_TYPE_SAMPLER 0
#define VK_DESCRIPTOR_TYPE_STORAGE_BUFFER 7
static uint64_t frame;
static unsigned edges, buffers, images, targets, view_descriptions, sampler_descriptions;
static char logs[131072]; static size_t log_bytes;
static uint64_t vka_exec_frame(void) { return frame; }
static int vka_env_str(const char *key, char *s, size_t n) { const char *v=getenv(key); if(v) snprintf(s,n,"%s",v); return !!v; }
static long envval(const char *key, long def) { const char *v = getenv(key); return v ? strtol(v, NULL, 0) : def; }
#define VKA_ENV(k,d) envval(k,d)
static void logline(const char *fmt, ...) {
    if (!strncmp(fmt, "draw edge: frame", 16)) edges++;
    if (!strncmp(fmt, "draw buffer:", 12)) buffers++;
    if (!strncmp(fmt, "draw image:", 11)) images++;
    if (!strncmp(fmt, "draw target:", 12)) targets++;
    va_list args;va_start(args,fmt);int n=vsnprintf(logs+log_bytes,sizeof logs-log_bytes,fmt,args);va_end(args);
    assert(n>=0 && (size_t)n+log_bytes+1<sizeof logs);log_bytes+=(size_t)n;logs[log_bytes++]='\n';logs[log_bytes]=0;
}
#define VLOG(...) logline(__VA_ARGS__)
#define VRED_ONCE(...) logline(__VA_ARGS__)
typedef struct { uint64_t addr,size; } vka_range;
typedef struct { struct { vka_range ssbo[8][16],ubo[8][16]; unsigned base_instance; } b; } vka_dstate;
typedef struct { uint16_t set,binding; uint8_t kind; int reg; } dmsh_vk_bind;
typedef struct { unsigned bind_count; } entry;
typedef struct { uint64_t key; entry *e; dmsh_vk_bind *b; } vka_shader;
typedef struct { int what,desc,flags,bank; unsigned binding,elem; uint64_t addr,size; unsigned dim; int tid,sid; } vka_res;
typedef struct { uintptr_t buffer; uint64_t offset,range; } VkDescriptorBufferInfo;
typedef struct { uintptr_t sampler,imageView; unsigned imageLayout; } VkDescriptorImageInfo;
typedef struct { unsigned dstBinding,dstArrayElement; const VkDescriptorBufferInfo *pBufferInfo; const VkDescriptorImageInfo *pImageInfo; const uintptr_t *pTexelBufferView; } VkWriteDescriptorSet;
typedef VkWriteDescriptorSet write;
static void vka_tex_view_desc(int id,uintptr_t view,char *s,size_t n) {
 assert(id==7 && (view==0xbbb || view==0xbbc));view_descriptions++;
 snprintf(s,n,"serial=31403 actual-format=97 level=2+1 layer=3+1 actual-view=%#lx",(unsigned long)view);
}
static int vka_samp_desc(uintptr_t sampler,char *s,size_t n) {
 assert(sampler==0xaaa || sampler==0xaab);sampler_descriptions++;snprintf(s,n,"emitted-sampler=%#lx",(unsigned long)sampler);return 1;
}
typedef struct { uint64_t serial;uintptr_t img; } vka_img;
typedef struct { unsigned width,height; } extent;
static struct { uint64_t cs[8],ds;int nci,has_rt;vka_img *cim[8],*dim;unsigned cslot[8],clv[8],cl0[8],cnl[8],cf[8],df,sf,dlv,dl0,dnl;uintptr_t cv[8],dv;extent cext[8],dext;struct { const vka_img *color[8],*depth; } rt; } R;
static struct { int n[8],pool[8]; vka_res r[8][8]; } g_wk;
static struct { unsigned n; write w[8]; } g_w;
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
    g_wk.n[0]=1; g_wk.r[0][0]=(vka_res){VKA_R_BUF,7,0,0,9,0,0x2000,160,0,0,0};
    g_w.n=1; g_w.w[0]=(write){9,0,&bi,NULL,NULL};
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
    /* Empty selectors are inert; shader-only selects direct draws by VS OR PS. */
    setenv("DM_VK_DRAW_TRACE_RANGE","",1);frame=7;
    unsigned before=edges;draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0);assert(edges==before&&!images&&!targets&&!view_descriptions&&!sampler_descriptions);
    uint64_t keys[16];unsigned nk;
    assert(draw_trace_keys("7b,0x1C8",keys,&nk)&&nk==2&&keys[0]==123&&keys[1]==456);
    assert(!draw_trace_keys("7b,",keys,&nk));assert(!draw_trace_keys("7b,7b",keys,&nk));
    assert(!draw_trace_keys("10000000000000000",keys,&nk));assert(!draw_trace_keys("-1",keys,&nk));
    assert(!draw_trace_keys("bad!",keys,&nk));assert(!draw_trace_keys(",7b",keys,&nk));
    setenv("DM_VK_DRAW_TRACE_SHADERS","7b",1);frame=8;
    draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0);assert(edges==before+1);
    vs.key=124;draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0);assert(edges==before+1);
    /* Fragment match; resolved UBO, both array elements' actual view/sampler, targets. */
    entry pe={2};dmsh_vk_bind pb[]={{0,257,DMSH_VK_KIND_CBUF,0},{0,258,9,3}};
    vka_shader ps={456,&pe,pb};sh[1]=&ps;
    setenv("DM_VK_DRAW_TRACE_SHADERS","1c8",1);frame=9;
    g_wk.n[1]=3;g_wk.r[1][0]=(vka_res){VKA_R_BUF,6,0,0,257,0,0x3400,64,0,0,0};
    g_wk.r[1][1]=(vka_res){2,1,0,0,258,0,0,0,1,7,11};
    g_wk.r[1][2]=(vka_res){2,1,0,0,258,1,0,0,1,7,12};
    VkDescriptorBufferInfo ub={0xdef,128,64};VkDescriptorImageInfo ii={0xaaa,0xbbb,1},ii1={0xaab,0xbbc,1};
    d.b.ubo[1][0]=(vka_range){0x3400,64};
    g_w.n=4;g_w.w[1]=(write){257,0,&ub,NULL,NULL};g_w.w[2]=(write){258,1,NULL,&ii1,NULL};g_w.w[3]=(write){258,0,NULL,&ii,NULL};
    vka_img colour={100,0x900},depth={200,0xa00},logical={101,0};
    R.cs[0]=101;R.ds=200;R.has_rt=1;R.nci=1;R.cim[0]=&colour;R.cslot[0]=2;R.cv[0]=0xc00;R.cf[2]=97;R.clv[0]=2;R.cl0[0]=3;R.cnl[2]=1;R.cext[2]=(extent){64,32};R.rt.color[2]=&logical;
    R.dim=&depth;R.dv=0xd00;R.dlv=1;R.dl0=4;R.dnl=1;R.df=126;R.dext=(extent){128,64};R.rt.depth=&depth;
    draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0);
    assert(edges==before+2&&images==2&&view_descriptions==2&&sampler_descriptions==2&&targets==2);
    assert(strstr(logs,"key 00000000000001c8 binding 257[0] source 1 reg 0")&&strstr(logs,"source-guest 0x3400 source-size 64 guest-base 0x3400 size 64 buffer 0xdef offset 128 range 64"));
    assert(strstr(logs,"binding 258[0]")&&strstr(logs,"sampler-id 11 sampler 0xaaa view 0xbbb")&&strstr(logs,"binding 258[1]")&&strstr(logs,"sampler-id 12 sampler 0xaab view 0xbbc"));
    assert(strstr(logs,"colour 2 serial 101 image-serial 100 image 0x900 view 0xc00 format 97 level 2 layer 3+1 shape 64x32"));
    assert(strstr(logs,"depth-stencil serial 200 image-serial 200 image 0xa00 view 0xd00 depth-format 126 stencil-format 0 level 1 layer 4+1"));
    /* Range OR slot is intersected with shader list, never broadens it. */
    setenv("DM_VK_DRAW_TRACE_RANGE","5000:32",1);frame=10;
    draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0);assert(edges==before+2);
    draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0);assert(edges==before+3);
    ps.key=457;draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0);assert(edges==before+3);ps.key=456;
    /* No sampler inspection after the exact two-recording-frame window. */
    frame=12;unsigned descriptions=view_descriptions,samplers=sampler_descriptions;draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0);assert(edges==before+3&&view_descriptions==descriptions&&sampler_descriptions==samplers);
    setenv("DM_VK_DRAW_TRACE_SHADERS","1c8,",1);frame=13;draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0);assert(edges==before+3);
    char longkeys[300];memset(longkeys,'a',sizeof longkeys);longkeys[sizeof longkeys-1]=0;setenv("DM_VK_DRAW_TRACE_SHADERS",longkeys,1);frame=14;draw_resource_trace(&d,sh,0x5000,20,1,0,0,0,0,0);assert(edges==before+3);
    /* Empty selectors remain inert even with emitted image descriptors present. */
    setenv("DM_VK_DRAW_TRACE_SHADERS","",1);setenv("DM_VK_DRAW_TRACE_RANGE","",1);frame=15;
    samplers=sampler_descriptions;draw_resource_trace(&d,sh,0,0,0,0,0,0,0,0);assert(sampler_descriptions==samplers);
    puts("ok");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c = Path(directory) / 'trace.c'; exe = Path(directory) / 'trace'
            c.write_text(program)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', str(c), '-o', str(exe)], check=True)
            result = subprocess.run([str(exe)], text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), 'ok')


if __name__ == '__main__':
    unittest.main()
