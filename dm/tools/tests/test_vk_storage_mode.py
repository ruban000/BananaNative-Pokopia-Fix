"""Actual storage/lazy decisions versus the pre-change scan, with a read oracle."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from dm.tools.tests.test_vk_texture_activity import function

ROOT = Path(__file__).resolve().parents[3]
# Frozen storage_mode from f1244b6d; surrounding predicates below use production code.
OLD_MODE = r'''static int storage_mode(const tex_t *t, const uint8_t *c, const vka_fmt *f, int cpuw, int sampled) {   // c: the storage's content as the GPU will see it (t->mem or gw_content's copy)
    const nvn_texdesc *d = &t->d;
    if (!t->mem || d->samples > 1 || f->kind >= 3 || d->target == NVN_TEXTURE_TARGET_BUFFER) return ST_ZERO;
    // A sparse virtual pool's texture (nvn_mtl.m tex_from_storage): a sampled one holds what the CPU wrote into the physical pages mapped behind it (streamed textures), read
    // through the page table and uploaded again when its mappings or those pages change (ST_SPARSE); render targets and images are GPU content. Its address is reserved, inaccessible
    // memory where nothing is mapped (no host alias on Android), so mem_any and the other checks below must not read it
    int sp = 0; unsigned pf = 0; if (t->pool && !nvn_pool_live(t->pool, &pf, &sp)) return ST_ZERO;   // a finalized pool: its memory may be unmapped (treg_dead)
    if (sp) return sparse_sampled(t, f) ? ST_SPARSE : ST_ZERO;
    int linear = (d->flags & (NVN_TEXTURE_FLAGS_LINEAR | NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET)) != 0, comp = compressed(f);
    int want = ST_ZERO;
    if (cpuw || t->has_layout) want = ST_TRACK;
    else if (linear && t->pool) want = ST_TRACK;
    else if (comp && !(d->flags & (NVN_TEXTURE_FLAGS_IMAGE | NVN_TEXTURE_FLAGS_COMPRESSIBLE | NVN_TEXTURE_FLAGS_SPARSE)) && mem_any_mapped(c, t)) want = ST_TRACK;
    else if (nvn2_sampled_content(t, f, pf, sampled)) {
        int any = mem_any_mapped(c, t); uint64_t k = g_nvn2_first[!any]++; want = ST_TRACK; if (!any) g_nvn2_zero_bytes += t->L.size;
        if (any && k < 4) VLOG("texture serial %llu %ux%ux%u format %#x pool flags %#x: written by the CPU before its first use, a sample (NVN2): uploaded from its storage, re-uploaded when it changes (%llu so far, %llu more with empty storage)",
                               (unsigned long long)t->serial, d->width, d->height, d->depth, d->format, pf, (unsigned long long)k + 1, (unsigned long long)g_nvn2_first[1]);
    }
    else if (!(d->flags & NVN_TEXTURE_FLAGS_COMPRESSIBLE) && t->pool && ((pf & (NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS | NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED)) || nvn_flushed(t->mem, t->L.size))
             && mem_any_mapped(c, t)) want = ST_FILL;
    // No later CPU writes to follow: a pool the CPU cannot access, a virtual pool (its pages are not the storage's), storage outside every tracked pool, or tracking off
    int trk = VKA_ENV("DM_VK_TEX_TRACK", 1) != 0;   // live: textures made from now on
    if (want == ST_TRACK && (!trk || (pf & (NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS | NVN_MEMORY_POOL_FLAGS_VIRTUAL)) || !nvn_dirty_span(t->mem, t->L.size))) want = ST_FILL;
    // Diagnostic DM_VK_ZEROLOG=<n>: the first n textures made as zero images whose storage holds data (virtual pools left out: their range may be reserved, inaccessible memory) (what they are, and whether their first use was a sample)
    { int zl = VKA_ENV("DM_VK_ZEROLOG", 0); static _Atomic int nz;
      if (zl > 0 && want == ST_ZERO && !(pf & NVN_MEMORY_POOL_FLAGS_VIRTUAL) && atomic_load(&nz) < zl && mem_any_mapped(c, t) && atomic_fetch_add(&nz, 1) < zl)
          VLOG("zero image over data: serial %llu %ux%ux%u levels %u format %#x target %d flags %#x pool flags %#x layout %d sampled %d cpuw %d gpu over %d",
               (unsigned long long)t->serial, d->width, d->height, d->depth, d->levels, d->format, d->target, d->flags, pf, t->has_layout, sampled, cpuw, gpu_image_over(t->mem)); }
    return want;
}
'''

HARNESS = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdatomic.h>
#include <sys/mman.h>
#include "dm/gfx/nvn/nvn_obj.h"
typedef struct { unsigned bw,bh,kind; } vka_fmt;
typedef struct { int gpu_owned,dead; } vka_img;
static struct { uint64_t lo; vka_img *im; } g_bymem[1];
static int g_nbymem;
static int bymem_first(uint64_t p){(void)p;return 0;}
static int img_pool_dead(vka_img *im){return im->dead;}
static int first_on=1,nvn2=1,track_on=1,span=1,live=1,sparse,flushed,cpuw,lazy_on=1,zerolog;
static unsigned poolflags=NVN_MEMORY_POOL_FLAGS_CPU_CACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED;
static uintptr_t hole;static size_t hole_len;
static uint64_t read_bytes;
static unsigned logs,snapshots;static int overlay;
static struct { int null_desc; } V={1};
static int env(const char *key,int def){if(!strcmp(key,"DM_VK_STORAGE_FIRST_USE"))return first_on;if(!strcmp(key,"DM_VK_TEX_TRACK"))return track_on;if(!strcmp(key,"DM_VK_ZEROLOG"))return zerolog;if(!strcmp(key,"DM_VK_LAZY"))return lazy_on;return def;}
#define VKA_ENV(key,def) env(key,def)
#define VKA_ENVF(key,def) (def)
static void log_line(const char *fmt,...) __attribute__((format(printf,1,2)));
static void log_line(const char *fmt,...){(void)fmt;logs++;}
#define VLOG(...) log_line(__VA_ARGS__)
static int vka_nvn2(void){return nvn2;}
static uint64_t nvn_pool_live(const void *pool,unsigned *flags,int *sp){assert(pool);if(flags)*flags=poolflags;if(sp)*sp=sparse;return live;}
static int pool_sparse(const void *pool){return pool&&live&&sparse;}
static int nvn_flushed(const void *p,uint64_t n){(void)p;(void)n;return flushed;}
static const void *nvn_dirty_span(const void *p,uint64_t n){(void)p;(void)n;return span?(void*)1:NULL;}
static int dm_guest_unmapped(uintptr_t p,uint64_t n){return hole_len&&p<hole+hole_len&&hole<p+n;}
static int cpuw_has(uint64_t serial){(void)serial;return cpuw;}
static uint8_t *ut_gw_content(const uint8_t *p,uint64_t n,uint64_t *seen){*seen=1;snapshots++;if(!overlay)return NULL;uint8_t *c=malloc(n);assert(c);memcpy(c,p,n);c[n-1]=1;return c;}
static void *read_memcpy(void *dst,const void *src,size_t n){read_bytes+=n;return memcpy(dst,src,n);}
enum { ST_ZERO,ST_FILL,ST_TRACK,ST_SPARSE };
static uint64_t g_nvn2_mode[2],g_nvn2_first[2],g_nvn2_zero_bytes;
'''
TEST = r'''
static unsigned cases;
static void compare(tex_t *t,vka_fmt *f,int written,int sampled){
    uint64_t seen;uint8_t *copy=ut_gw_content(t->mem,nvn_tex_data_size(&t->d),&seen);
    const uint8_t *src=copy?copy:t->mem;
    read_bytes=0;unsigned a=old_storage_mode(t,src,f,written,sampled);uint64_t oldreads=read_bytes;
    read_bytes=0;unsigned b=storage_mode(t,src,f,written,sampled,0);uint64_t newreads=read_bytes;
    assert(a==b);assert(newreads<=oldreads);free(copy);cases++;
    unsigned eligible=live&&!sparse&&!written&&!t->has_layout&&!(t->d.flags&(NVN_TEXTURE_FLAGS_LINEAR|NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET))&&t->mem&&t->d.samples<=1&&f->kind<3&&t->d.target!=NVN_TEXTURE_TARGET_BUFFER&&nvn2_sampled_content(t,f,poolflags,sampled);
    if(eligible){assert(newreads==0);assert(a==(track_on&&span?ST_TRACK:ST_FILL));}
    read_bytes=0;unsigned la=old_lazy_ok(t,f);uint64_t lazyreads=read_bytes;
    read_bytes=0;unsigned lb=lazy_ok(t,f);assert(la==lb);assert(read_bytes<=lazyreads);
}
int main(int argc,char **argv){
    assert(argc==4);first_on=atoi(argv[1]);nvn2=atoi(argv[2]);lazy_on=atoi(argv[3]);
    uint8_t *mem=mmap(NULL,4*1024*1024,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(mem!=MAP_FAILED);
    pool_t pool={0};tex_t t={.d={.target=NVN_TEXTURE_TARGET_2D,.format=0x25,.width=64,.height=32,.depth=1,.levels=1},.mem=mem,.pool=&pool,.serial=7};
    vka_fmt f={1,1,0};vka_img owner={1,0};g_bymem[0].lo=(uintptr_t)mem;g_bymem[0].im=&owner;
    int flags[]={0,NVN_TEXTURE_FLAGS_IMAGE,NVN_TEXTURE_FLAGS_COMPRESSIBLE,NVN_TEXTURE_FLAGS_LINEAR,NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET,NVN_TEXTURE_FLAGS_SPARSE};
    unsigned pools[]={NVN_MEMORY_POOL_FLAGS_CPU_CACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED,NVN_MEMORY_POOL_FLAGS_CPU_UNCACHED,NVN_MEMORY_POOL_FLAGS_CPU_NO_ACCESS,NVN_MEMORY_POOL_FLAGS_VIRTUAL};
    for(unsigned pattern=0;pattern<3;pattern++)for(unsigned flag=0;flag<sizeof(flags)/sizeof(*flags);flag++)for(unsigned pf=0;pf<sizeof(pools)/sizeof(*pools);pf++)for(unsigned control=0;control<12;control++){
        t.d.flags=flags[flag];t.d.samples=control==6?4:1;t.d.target=control==7?NVN_TEXTURE_TARGET_BUFFER:NVN_TEXTURE_TARGET_2D;
        t.has_layout=control==5;f.kind=control==8?3:0;f.bw=control==9?4:1;f.bh=f.bw;
        track_on=control!=1;span=control!=2;live=control!=3;sparse=control==4;flushed=control==10;g_nbymem=control==11;owner.dead=0;
        poolflags=pools[pf];nvn_tex_layout(&t.d,&t.L);uint64_t n=nvn_tex_data_size(&t.d);assert(n&&n<4*1024*1024);
        memset(mem,0,n);if(pattern==1)mem[0]=1;if(pattern==2)mem[n-1]=1;
        for(int sampled=0;sampled<2;sampled++)for(int written=0;written<2;written++)compare(&t,&f,written,sampled);
    }
    t.d=(nvn_texdesc){.target=NVN_TEXTURE_TARGET_2D,.format=0x25,.width=512,.height=512,.depth=1,.levels=1};nvn_tex_layout(&t.d,&t.L);f=(vka_fmt){1,1,0};
    poolflags=NVN_MEMORY_POOL_FLAGS_CPU_CACHED|NVN_MEMORY_POOL_FLAGS_GPU_CACHED;track_on=span=live=1;sparse=flushed=cpuw=g_nbymem=0;
    t.has_layout=0;memset(mem,0,t.L.size);uint64_t n=nvn_tex_data_size(&t.d);
    for(int variant=0;variant<6;variant++){
        overlay=variant==1;hole= (uintptr_t)mem+4096;hole_len=variant==2?4096:0;g_nbymem=variant==3;owner.dead=1;cpuw=variant==4;
        t.pool=variant==5?NULL:&pool;compare(&t,&f,0,1);
    }
    t.pool=&pool;overlay=cpuw=g_nbymem=0;hole_len=0;owner.dead=0;
    if(first_on&&nvn2){
        // Same mode for zero and later nonzero bytes; no zero-content classification or data read.
        for(int fill=0;fill<2;fill++){
            memset(mem,fill?0x5a:0,n);read_bytes=0;assert(old_storage_mode(&t,mem,&f,0,1)==ST_TRACK);assert(read_bytes==(fill?8:n));
            read_bytes=0;assert(storage_mode(&t,mem,&f,0,1,0)==ST_TRACK);assert(read_bytes==0);
        }
        // A protected source proves the candidate branch does not dereference storage.
        assert(!mprotect(mem,4*1024*1024,PROT_NONE));read_bytes=0;
        assert(storage_mode(&t,mem,&f,0,1,0)==ST_TRACK);assert(!lazy_ok(&t,&f));assert(read_bytes==0);
        assert(!mprotect(mem,4*1024*1024,PROT_READ|PROT_WRITE));
        uint64_t a=g_nvn2_mode[0],b=g_nvn2_mode[1];
        assert(storage_mode(&t,mem,&f,0,1,0)==ST_TRACK);assert(g_nvn2_mode[0]==a+1&&g_nvn2_mode[1]==b);
        assert(!lazy_ok(&t,&f));assert(g_nvn2_mode[0]==a+1&&g_nvn2_mode[1]==b+(unsigned)lazy_on);
    }
    munmap(mem,4*1024*1024);printf("%u actual mode/lazy cases passed\n",cases);return 0;
}
'''


class StorageMode(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_mode_lazy_equivalence_and_no_diagnostic_reads(self):
        source = (ROOT/'dm/gfx/vk_android/vka_tex.c').read_text()
        code = HARNESS
        for name in ('compressed(', 'sparse_sampled(', 'gpu_image_over(', 'nvn2_sampled_content('):
            code += function(source, name)
        code += '#define memcpy read_memcpy\n'+function(source, 'mem_any(')+'\n#undef memcpy\n'
        code += function(source, 'mem_any_mapped(')
        code += OLD_MODE.replace('storage_mode(', 'old_storage_mode(')
        code += function(source, 'storage_mode(')
        lazy = function(source, 'lazy_ok(')
        code += lazy.replace('lazy_ok(', 'old_lazy_ok(').replace('storage_mode(', 'old_storage_mode(').replace('f, 0, 1, 1)', 'f, 0, 1)')
        code += lazy+TEST
        with tempfile.TemporaryDirectory() as tmp:
            c, exe = Path(tmp)/'mode.c', Path(tmp)/'mode'
            c.write_text(code)
            flags = ['cc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-I',str(ROOT),'-I',str(ROOT/'dm/gfx/nvn/include')]
            subprocess.run([*flags,'-fsanitize=address,undefined',str(c),str(ROOT/'dm/gfx/nvn/nvn_tex.c'),'-o',str(exe)],check=True)
            for first, nvn2, lazy in ((1,1,1),(0,1,1),(1,0,1),(1,1,0)):
                subprocess.run([str(exe),str(first),str(nvn2),str(lazy)],check=True,timeout=30)
            # Reinsert the old redundant read: the read-count assertion must reject it.
            changed = code.replace('uint64_t k = g_nvn2_mode[lazy != 0]++;', '(void)mem_any_mapped(c, t); uint64_t k = g_nvn2_mode[lazy != 0]++;')
            self.assertNotEqual(changed, code)
            c.write_text(changed)
            subprocess.run([*flags,str(c),str(ROOT/'dm/gfx/nvn/nvn_tex.c'),'-o',str(exe)],check=True)
            result = subprocess.run([str(exe),'1','1','1'],capture_output=True,timeout=30)
            self.assertNotEqual(result.returncode,0,'baseline diagnostic read must fail the read oracle')
