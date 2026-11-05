"""Bounded upload receipts: actual recorder, tracked/row/full/MT and cause paths."""
from pathlib import Path
import csv
import shutil
import subprocess
import tempfile
import unittest

from dm.tools.tests.test_vk_texture_activity import function

ROOT = Path(__file__).resolve().parents[3]

HARNESS = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <stdatomic.h>
#include <unistd.h>
#include <sched.h>
#include <stdarg.h>
enum { NVN_TEXTURE_FLAGS_LINEAR=1, NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET=2,
 NVN_TEXTURE_TARGET_3D=2, VK_IMAGE_LAYOUT_GENERAL=1, VK_IMAGE_LAYOUT_UNDEFINED=0,
 VK_IMAGE_ASPECT_COLOR_BIT=1,VK_REMAINING_MIP_LEVELS=~0u,VK_REMAINING_ARRAY_LAYERS=~0u };
typedef uintptr_t VkCommandBuffer,VkBuffer,VkImage;typedef uint64_t VkDeviceSize;typedef int VkImageLayout;
typedef struct { uint32_t flags,stride,width,height,depth,levels,target; int format; } nvn_texdesc;
typedef struct { uint64_t size; } nvn_texlayout;
typedef struct { uint32_t bw,bh,bpb,kind,vk; } vka_fmt;
typedef struct { nvn_texdesc d; uint8_t *mem; nvn_texlayout L; uint64_t serial; } tex_t;
typedef struct { uint64_t off; uint8_t *ptr; VkBuffer buf; } vka_span;
typedef struct { uint64_t bufferOffset; uint32_t bufferRowLength,bufferImageHeight;
 struct {uint32_t aspectMask,mipLevel,baseArrayLayer,layerCount;} imageSubresource;
 struct {int32_t x,y,z;} imageOffset; struct {uint32_t width,height,depth;} imageExtent; } VkBufferImageCopy;
typedef struct {unsigned aspectMask,baseMipLevel,levelCount,baseArrayLayer,layerCount;} VkImageSubresourceRange;
typedef struct {float depth;uint32_t stencil;} VkClearDepthStencilValue;
typedef union {float f[4];} VkClearColorValue;
typedef struct vka_img {vka_fmt f; nvn_texdesc d;nvn_texlayout L;uint32_t w,h,depth,levels,layers,base,aspect,samples,rs_tw;
 VkImage img;uint64_t serial,pgen,stamp,po_off,up_ns,gw_seen,hash,head,up_fence,vgen;
 const uint8_t *mem;const void *span;void *vpool,*rs;uint8_t *snap;
 int hash_valid,head_valid,track,vtrack,vbind,gpu_owned,inited,from_mem,mode,cpu_dirty;} vka_img;
static unsigned clocks,copies,protects,allocations;static _Atomic unsigned logs,ends,saves;
static _Atomic int writer_hold,writer_entered;
static FILE *trace_fopen(const char *path,const char *mode){atomic_store(&writer_entered,1);while(atomic_load(&writer_hold))sched_yield();return fopen(path,mode);}
#define fopen trace_fopen
static uint64_t fake_frame=10,fake_fence;static int token,fail_alloc,fail_desw,mode=2,dirty=1,tracked=1,snapshot;
static size_t dirty_lo=8,dirty_hi=16;static uint8_t storage[1024*1024],scratch[4*1024*1024];static size_t scratch_used;
static uint64_t now_ns(void){clocks++;return clocks*100;}
static uint64_t vka_exec_frame(void){return fake_frame;}
static uint64_t vka_exec_reserved_fence(void){return fake_fence;}
static int env(const char *name,int def){if(!strcmp(name,"DM_VK_UPLOAD_TRACE"))return token;return def;}
#define VKA_ENV(key,def) env(key,def)
static void log_line(const char *fmt,...){char b[1024];va_list a;va_start(a,fmt);vsnprintf(b,sizeof b,fmt,a);va_end(a);atomic_fetch_add(&logs,1);if(!strncmp(b,"upload trace ended",18))atomic_fetch_add(&ends,1);if(!strncmp(b,"upload trace saved",18))atomic_fetch_add(&saves,1);}
#define VLOG(...) log_line(__VA_ARGS__)
#define VRED(...) ((void)0)
#define VRED_ONCE(...) ((void)0)
static int vka_scratch(uint64_t n,uint64_t align,vka_span *s){assert(align==16);allocations++;if(fail_alloc)return 0;assert(scratch_used+n<=sizeof scratch);*s=(vka_span){scratch_used,scratch+scratch_used,99};scratch_used+=n;return 1;}
static void copy(VkCommandBuffer cb,VkBuffer b,VkImage im,int layout,uint32_t n,const VkBufferImageCopy *r){assert(cb==42&&b==99&&im==23&&layout==1);for(unsigned i=0;i<n;i++)assert(r[i].imageExtent.width&&r[i].imageExtent.height);copies+=n;}
static void clear_d(VkCommandBuffer cb,VkImage im,int l,const VkClearDepthStencilValue *v,unsigned n,const VkImageSubresourceRange *r){(void)cb;(void)im;(void)l;(void)v;(void)n;(void)r;}
static void clear_c(VkCommandBuffer cb,VkImage im,int l,const VkClearColorValue *v,unsigned n,const VkImageSubresourceRange *r){(void)cb;(void)im;(void)l;(void)v;(void)n;(void)r;}
static struct {void (*vkCmdCopyBufferToImage)(VkCommandBuffer,VkBuffer,VkImage,int,uint32_t,const VkBufferImageCopy*);void (*vkCmdClearDepthStencilImage)(VkCommandBuffer,VkImage,int,const VkClearDepthStencilValue*,unsigned,const VkImageSubresourceRange*);void (*vkCmdClearColorImage)(VkCommandBuffer,VkImage,int,const VkClearColorValue*,unsigned,const VkImageSubresourceRange*);} vk={copy,clear_d,clear_c};
'''
STUBS = r'''
static uint64_t g_rows_up,g_reup,g_gw_over;static _Atomic uint64_t g_up_ns,g_up_bytes;
enum {ST_ZERO,ST_FILL,ST_TRACK,ST_SPARSE};static uint64_t g_st_up[4];static int g_first_sampled;
static uint64_t nvn_dirty_now(void){return 77;}
static void nvn_dirty_protect(const void *p,uint64_t n){assert(p==storage&&n);protects++;}
static const void *nvn_dirty_span(const void *p,uint64_t n){(void)p;(void)n;return tracked?(void*)1:NULL;}
static int nvn_dirty_bounds_since(const void **span,const void *p,uint64_t n,uint64_t stamp,size_t *lo,size_t *hi){(void)p;(void)n;(void)stamp;*span=tracked?(void*)1:NULL;*lo=dirty_lo;*hi=dirty_hi;return dirty;}
static int img_pool_dead(vka_img *im){(void)im;return 0;}
static int vka_nan_on(void){return 0;}
static int mem_hash_mapped(const void *p,uint64_t n,uint64_t *h){(void)p;(void)n;(void)h;abort();}
static uint64_t nvn_tex_data_size(const nvn_texdesc *d){return (uint64_t)d->width*d->height;}
static int dm_guest_unmapped(uintptr_t p,size_t n){(void)p;(void)n;return 0;}
static uint8_t *gw_content(const uint8_t *p,uint64_t n,uint64_t *seen){*seen=1;if(!snapshot)return NULL;uint8_t *c=malloc(n);assert(c);memcpy(c,p,n);return c;}
static uint8_t *unmapped_content(const tex_t *t){(void)t;return NULL;}
static uint64_t vka_fence_last(void){return 999;}
static VkCommandBuffer vka_up_point(VkCommandBuffer cb){return cb;}
static VkCommandBuffer vka_up_cur(VkCommandBuffer cb){return cb;}
static void vka_barrier(VkCommandBuffer cb){(void)cb;}
static void vka_img_barrier(VkCommandBuffer cb,VkImage im,unsigned asp,int a,int b){(void)cb;(void)im;(void)asp;(void)a;(void)b;}
static void storage_read_touch(vka_img *im){im->up_ns=123;}
static int nvn_deswizzle_block_rows(const nvn_texdesc *d,unsigned lv){return d->height>>lv?d->height>>lv:1;}
static int nvn_deswizzle_rows(const nvn_texdesc *d,unsigned lv,unsigned ly,const void *src,void *dst,size_t pitch,uint32_t y0,uint32_t y1){(void)ly;if(fail_desw)return 1;unsigned w=d->width>>lv?d->width>>lv:1;for(unsigned y=y0;y<y1;y++)memcpy((uint8_t*)dst+y*pitch,(const uint8_t*)src+y*w,w);return 0;}
static int nvn_deswizzle(const nvn_texdesc *d,unsigned lv,unsigned ly,const void *src,void *dst,size_t pitch){return nvn_deswizzle_rows(d,lv,ly,src,dst,pitch,0,nvn_deswizzle_block_rows(d,lv));}
static int mem_any(const void *p,uint64_t n){(void)p;(void)n;return 1;}
static int storage_mode(const tex_t *t,const void *p,const vka_fmt *f,int cpu,int sampled,int lazy){(void)t;(void)p;(void)f;(void)cpu;(void)sampled;(void)lazy;return mode;}
static int sparse_gpu(const tex_t *t,const vka_fmt *f){(void)t;(void)f;return 0;}
static void sp_add(vka_img *im){(void)im;}
static int compressed(const vka_fmt *f){return f->bw>1;}
static void sparse_bind(vka_img *im,VkCommandBuffer cb,int all){(void)im;(void)cb;(void)all;}
static uint64_t nvn_sparse_gen(void){return 3;}
static int nvn_sparse_since(void *p,uint64_t mem,uint64_t n,uint64_t gen,uint64_t stamp){(void)p;(void)mem;(void)n;(void)gen;(void)stamp;return 1;}
static void nvn_sparse_protect(void *p,uint64_t mem,uint64_t n){(void)p;(void)mem;assert(n);protects++;}
static int mapped_pages=1,rebase;
static int nvn_sparse_any(void *p,uint64_t mem,uint64_t n){(void)p;(void)mem;(void)n;return mapped_pages;}
static uint64_t nvn_sparse_copy(void *p,void *out,uint64_t mem,uint64_t n){(void)p;memcpy(out,(const void*)(uintptr_t)mem,n);return 0;}
static int nvn_deswizzle_bands(const nvn_texdesc *d,unsigned lv,uint32_t *rows,uint64_t *bytes){(void)lv;*rows=4;*bytes=d->width*4;return 0;}
static void sub_range(const nvn_texdesc *d,const nvn_texlayout *l,unsigned lv,unsigned ly,uint64_t *off,uint64_t *len){(void)lv;(void)ly;*off=0;*len=l->size;assert(*len==d->width*d->height);}
static int res_level(vka_img *im,unsigned i,unsigned rows,uint64_t bytes){(void)im;(void)i;(void)rows;(void)bytes;return 0;}
static unsigned rs_ntx(vka_img *im,unsigned i){(void)i;return (im->w+im->rs_tw-1)/im->rs_tw;}
static int vks_tile(void *rs,unsigned i,unsigned ly,unsigned x,unsigned y,int bound){(void)rs;(void)i;(void)ly;(void)x;(void)y;return bound;}
static int vks_tile_bound(void *rs,unsigned i,unsigned ly,unsigned x,unsigned y){(void)rs;(void)i;(void)ly;(void)y;return x!=1;}
static int is_astc(unsigned format){(void)format;return 0;}
static void astc_zero_blocks(uint8_t *p,uint64_t n){(void)p;(void)n;}
static const uint8_t k_astc_zero[16]={0};
enum {ZHALF=16};static VkBuffer g_zbuf=99;static int zero_src(void){return 1;}
static void upload(vka_img*,const tex_t*,VkCommandBuffer);
static void upload_(vka_img*,const tex_t*,VkCommandBuffer,uint64_t*);
static void sparse_refresh(vka_img*,VkCommandBuffer);
'''
TEST = r'''
static void saved(void){for(unsigned i=0;i<10000000&&atomic_load_explicit(&UT.state,memory_order_acquire)!=UT_IDLE;i++)sched_yield();assert(atomic_load(&UT.state)==UT_IDLE);}
static vka_img make_image(unsigned w,unsigned h,unsigned levels){return (vka_img){.f={1,1,1,0,1},.d={.width=w,.height=h,.levels=levels,.format=1},.L={w*h},.w=w,.h=h,.depth=1,.levels=levels,.layers=1,.samples=1,.aspect=1,.img=23,.serial=17,.pgen=4,.mem=storage,.track=1,.span=(void*)1};}
static tex_t texture(vka_img *im){return (tex_t){im->d,storage,im->L,im->serial};}
int main(int argc,char **argv){assert(argc==2);setenv("DM_TEMP",argv[1],1);memset(storage,0xa5,sizeof storage);
 vka_img im=make_image(8,8,1);tex_t t=texture(&im);
 ut_tick();unsigned before=clocks;upload_tracked(&im,&t,42);assert(!UT.count&&!ut_current&&clocks==before+2); // only the existing upload() timer
 before=logs;ut_tick();assert(logs==before&&!UT.count);
 token=1;fake_fence=0;ut_tick();fake_frame=999; // simulate frontend advancement after submission latch
 im.d.flags=NVN_TEXTURE_FLAGS_LINEAR;im.d.stride=8;t=texture(&im);dirty_lo=17;dirty_hi=35;
 scan_img(&im,42,0);assert(UT.count==1&&!ut_current);ut_record r=UT.rows[0];
 assert(r.v[UT_FRAME]==10&&r.v[UT_SUB]==1&&r.v[UT_FENCE]==UINT64_MAX&&r.v[UT_COPIED]==24);
 assert(r.v[UT_LO]==17&&r.v[UT_HI]==35&&r.u[UT_REASON]==UT_DIRTY&&r.v[UT_DESWIZZLE]!=UINT64_MAX&&r.v[UT_PROTECT]!=UINT64_MAX);
 fake_frame=10;fake_fence=123;ut_tick();im.d.flags=0;im.levels=im.d.levels=2;t=texture(&im);snapshot=1;
 img_first_use(&im,&t,42,1);assert(UT.count==2&&!ut_current);r=UT.rows[1];
 assert(r.u[UT_REASON]==UT_FIRST&&r.v[UT_COPIED]==80&&r.v[UT_LOGICAL]==80&&r.v[UT_FENCE]==123&&r.v[UT_SUB]==2);
 assert(r.v[UT_SNAPSHOT_BYTES]==128&&r.v[UT_LO]==UINT64_MAX&&r.v[UT_MTWAIT]==UINT64_MAX);snapshot=0;
 fail_alloc=1;scan_img(&im,42,0);assert(UT.rows[2].v[UT_COPIED]==0&&UT.rows[2].v[UT_SCRATCH]!=UINT64_MAX);fail_alloc=0;
 fail_desw=1;scan_img(&im,42,0);assert(UT.rows[3].v[UT_COPIED]==0);fail_desw=0;
 tracked=0;scan_img(&im,42,0);assert(UT.rows[4].v[UT_LO]==UINT64_MAX);tracked=1;
 unsigned n=UT.count;mode=ST_ZERO;img_first_use(&im,&t,42,1);assert(UT.count==n&&!ut_current);mode=ST_TRACK;
 im=make_image(1024,512,1);t=texture(&im);scratch_used=0;img_first_use(&im,&t,42,1);r=UT.rows[UT.count-1];
 assert(r.v[UT_COPIED]==524288&&r.u[UT_TASKS]==8&&r.u[UT_PARALLEL]==1&&r.v[UT_MTWAIT]!=UINT64_MAX);assert(!memcmp(scratch,storage,524288));
 im=make_image(8,8,1);im.vtrack=1;im.vpool=(void*)1;scan_img(&im,42,1);assert(UT.rows[UT.count-1].u[UT_REASON]==UT_SPARSE&&UT.rows[UT.count-1].v[UT_COPIED]==64);
 mapped_pages=0;scan_img(&im,42,1);assert(UT.rows[UT.count-1].v[UT_COPIED]==64&&UT.rows[UT.count-1].v[UT_SCRATCH]==UINT64_MAX);mapped_pages=1;
 int owned=ut_begin(&im,UT_SPARSE,0,0,0);im.rs_tw=2;vka_span tile_span={0,scratch,99};copy_bound(&im,42,&tile_span,0,0,8,8,8,4,0,2,0);ut_end(owned,1);assert(UT.rows[UT.count-1].v[UT_COPIED]==48);
 im.vtrack=0;im.vpool=NULL;t=texture(&im);
 run_redo(&im);assert(UT.rows[UT.count-1].u[UT_REASON]==UT_WRITE);
 run_gpu(&im);assert(UT.rows[UT.count-1].u[UT_REASON]==UT_GPU);
 im.vtrack=1;im.vpool=(void*)1;rebase=1;run_redo(&im);r=UT.rows[UT.count-1];assert(r.u[UT_REASON]==UT_WRITE&&r.u[UT_BASE]==1&&r.u[UT_W]==4&&r.v[UT_LOGICAL]==16);rebase=0;im=make_image(8,8,1);
 token=0;ut_tick();saved();assert(ends==1&&saves==1);unsigned frozen=UT.count;
 scan_img(&im,42,0);assert(UT.count==frozen);
 token=2;fake_frame=20;ut_tick();scratch_used=0;
 for(unsigned i=0;i<UT_CAP+16;i++){int own=ut_begin(&im,UT_DIRTY,1,0,4);ut_end(own,1);}assert(UT.count==UT_CAP&&UT.stop&&!ut_current);
 ut_tick();saved();assert(UT.count==UT_CAP);frozen=clocks;assert(!ut_begin(&im,UT_FIRST,0,0,0)&&clocks==frozen);
 token=3;fake_frame=30;ut_tick();for(unsigned i=0;i<112;i++){fake_frame=30+i;ut_tick();int own=ut_begin(&im,UT_FIRST,0,0,0);ut_end(own,1);}assert(UT.count==112);fake_frame=142;ut_tick();saved();assert(UT.last==141&&UT.count==112);
 token=4;fake_frame=200;ut_tick();int own=ut_begin(&im,UT_FIRST,0,0,0);ut_end(own,1);
 atomic_store(&writer_entered,0);atomic_store(&writer_hold,1);token=0;ut_tick();
 for(unsigned i=0;i<1000000&&!atomic_load(&writer_entered);i++){sched_yield();}assert(atomic_load(&writer_entered));
 token=5;ut_tick();assert(atomic_load(&UT.state)==UT_READY&&UT.count==1&&!ut_enabled); // no producer wait or reuse while writer is blocked
 assert(!ut_begin(&im,UT_FIRST,0,0,0));atomic_store(&writer_hold,0);saved();
 token=6;ut_tick();assert(atomic_load(&UT.state)==UT_RECORDING&&UT.session==5);token=0;ut_tick();saved();
 unsigned old_saves=atomic_load(&saves);setenv("DM_TEMP","/does-not-exist-upload-fixture",1);token=7;ut_tick();token=0;ut_tick();saved();assert(saves==old_saves); // failed writer still retires buffer
 puts("actual upload causes/copy bytes/off/unknown/MT/frame/event bounds: passed");
}
'''


class UploadTrace(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_production_paths_and_bounds(self):
        tex = (ROOT / "dm/gfx/vk_android/vka_tex.c").read_text()
        header = (ROOT / "dm/gfx/vk_android/vka_upload_trace.h").read_text().replace("#pragma once", "")
        code = HARNESS + header + STUBS
        for name in ("static uint8_t *ut_gw_content(", "static uint8_t *ut_unmapped_content(",
                     "static void upload_tracked(", "static void upload_fill(",
                     "static int upload_rows_tracked(", "static int zero_rows(",
                     "static void copy_bound(", "static void upload_sparse_parts(", "static void upload_sparse("):
            code += function(tex, name)
        code += "static void sparse_refresh(vka_img *im,VkCommandBuffer cb){if(rebase){im->base=1;im->w=im->d.width=4;im->h=im->d.height=4;im->L.size=16;}upload_sparse(im,cb,0);}\n"
        code += tex[tex.index("typedef struct { const nvn_texdesc *d;"):tex.index("\nvoid mtl_tex_init(")]
        code += function(tex, "static void img_first_use(")
        code += function(tex, "static int scan_img(")
        # Execute unchanged production cause blocks, with loop-owned table plumbing stubbed.
        a = tex.index("        int receipt = ut_begin(im, UT_WRITE")
        b = tex.index("    g_nredo = 0;", a)
        redo = tex[a:b].rsplit("    }", 1)[0]
        code += "static void run_redo(vka_img *im){VkCommandBuffer cb=42;tex_t t={im->d,storage,im->L,im->serial};for(int once=0;once<1;once++){" + redo + "}}\n"
        a = tex.index("                int receipt = ut_begin(im, UT_GPU")
        b = tex.index("                g_gw_late++;", a)
        code += "static void run_gpu(vka_img *im){VkCommandBuffer cb=42;tex_t t={im->d,storage,im->L,im->serial};" + tex[a:b] + "}\n"
        code += TEST
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / "receipt.c", Path(tmp) / "receipt"
            source.write_text(code)
            subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-Wno-unused-function", "-Wno-missing-field-initializers", "-Werror",
                            "-fsanitize=address,undefined", "-pthread", str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary), tmp], check=True, timeout=30)
            broken = code.replace("ut_add(UT_COPIED, (uint64_t)", "ut_add(UT_COPIED, 0 * (uint64_t)")
            self.assertNotEqual(broken, code)
            source.write_text(broken)
            subprocess.run(["cc", "-O1", "-g", "-pthread", str(source), "-o", str(binary)], check=True)
            control = subprocess.run([str(binary), tmp], capture_output=True, timeout=30)
            self.assertNotEqual(control.returncode, 0, "missing-copy accounting must fail the oracle")
            paths = sorted(Path(tmp).glob("upload-trace-*-*.tsv"))
            self.assertEqual(len(paths), 5)
            event_file = next(p for p in paths if p.name.endswith("-2.tsv"))
            with event_file.open() as f:
                meta = f.readline()
                rows = list(csv.DictReader(f, delimiter="\t"))
            self.assertIn("count=2048 end=events", meta)
            self.assertEqual(len(rows), 2048)
            self.assertEqual([int(r["sequence"]) for r in rows], list(range(1, 2049)))
            self.assertTrue(all(r["dirty_lo"] == "0" and r["protect_ns"] == "NA" for r in rows))
