"""Guard-page coverage of the production capture run enumeration and memory diffs."""
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
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


class CaptureMappedMemory(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_mapped_runs_and_retired_fragments(self):
        source = (ROOT / "dm/gfx/vk_android/vka_capture.c").read_text()
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#define MIN(a,b) ((a)<(b)?(a):(b))
typedef struct {uint64_t lo,hi;} pent;
typedef struct {uint64_t lo,size;uint32_t kind,pending;uint64_t *hash;uint8_t *hash_valid;} cap_region;
typedef struct {uint8_t *p;size_t n;} cap_buf;
static uint8_t *arena;static size_t page;static unsigned mapped=63;static int g_cap_w;
static cap_region *g_cap_reg;static uint32_t g_cap_nreg,*g_cap_rid;
static cap_buf saved;
#define CAP_PDIF 1
static void die(const char*s){(void)s;abort();}
static void cap_fail(const char*s){(void)s;abort();}
static int dm_guest_unmapped(uint64_t a,uint64_t n){
if(!n)return 0;assert(a>=(uintptr_t)arena&&a+n<=(uintptr_t)arena+6*page);
unsigned first=(a-(uintptr_t)arena)/page,last=(a+n-1-(uintptr_t)arena)/page;
for(unsigned i=first;i<=last;i++)if(!(mapped&(1u<<i)))return 1;return 0;}
static void cap_buf_put(cap_buf*b,const void*p,size_t n){b->p=realloc(b->p,b->n+n);assert(b->p);memcpy(b->p+b->n,p,n);b->n+=n;}
static void cap_buf_free(cap_buf*b){free(b->p);*b=(cap_buf){0};}
static void cap_put(int*w,int tag,unsigned submit,const void*p,size_t n,int codec){(void)w;(void)submit;(void)codec;assert(tag==CAP_PDIF);cap_buf_put(&saved,p,n);}
static void cap_reg_write(unsigned submit){(void)submit;} // fixture's region is already written
'''
        for name in ("cap_hash_block(", "cap_reg_hash(", "cap_mapped_runs(", "cap_mem("):
            code += function(source, name)
        code += r'''
static void check_saved(void){
size_t pos=0,bytes=0;
while(pos<saved.n){uint32_t id;uint64_t off,n;memcpy(&id,saved.p+pos,4);memcpy(&off,saved.p+pos+8,8);memcpy(&n,saved.p+pos+16,8);pos+=24;
assert(id==7&&n&&pos+n<=saved.n);
assert(!dm_guest_unmapped(g_cap_reg[0].lo+off,n));
assert(!memcmp(saved.p+pos,(void*)(uintptr_t)(g_cap_reg[0].lo+off),n));pos+=n;bytes+=n;}
assert(bytes);cap_buf_free(&saved);}
int main(void){
page=(size_t)sysconf(_SC_PAGESIZE);assert(page&&!(page%4096));
arena=mmap(NULL,6*page,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(arena!=MAP_FAILED);memset(arena,0x5a,6*page);
cap_region region={(uintptr_t)arena+13,6*page-26,0,0,NULL,NULL};uint32_t id=7;g_cap_reg=&region;g_cap_nreg=1;g_cap_rid=&id;
cap_reg_hash(&region,arena+13);
assert(!mprotect(arena+page,page,PROT_NONE));assert(!mprotect(arena+3*page,page,PROT_NONE));mapped&=~((1u<<1)|(1u<<3));
pent *runs=NULL;int n=0,cap=0;cap_mapped_runs(region.lo,region.size,&runs,&n,&cap);
assert(n==3);assert(runs[0].lo==(uintptr_t)arena+13&&runs[0].hi==(uintptr_t)arena+page);
assert(runs[1].lo==(uintptr_t)arena+2*page&&runs[1].hi==(uintptr_t)arena+3*page);
assert(runs[2].lo==(uintptr_t)arena+4*page&&runs[2].hi==(uintptr_t)arena+6*page-13);
size_t total=0;for(int i=0;i<n;i++){assert(!dm_guest_unmapped(runs[i].lo,runs[i].hi-runs[i].lo));cap_hash_block((void*)(uintptr_t)runs[i].lo,runs[i].hi-runs[i].lo);total+=runs[i].hi-runs[i].lo;}
assert(total==4*page-26);free(runs);
memset(arena,0x22,page);memset(arena+2*page,0x33,page);memset(arena+4*page,0x77,page);
cap_mem(2);assert(!region.hash_valid[0]);check_saved(); // relative hash blocks straddle holes: preserve readable fragments
// A -> B while a neighboring page is absent, then remap all bytes back to A.
// The whole-block hash equals its initial value, but replay must overwrite B.
assert(!mprotect(arena,6*page,PROT_READ|PROT_WRITE));mapped=63;memset(arena,0x5a,6*page);
cap_mem(3);assert(saved.n>=24);uint64_t first_off,first_len;memcpy(&first_off,saved.p+8,8);memcpy(&first_len,saved.p+16,8);
assert(first_off==0&&first_len>=4096&&region.hash_valid[0]);check_saved();
mapped=0;assert(!mprotect(arena,6*page,PROT_NONE));
runs=NULL;n=cap=0;cap_mapped_runs((uintptr_t)arena,6*page,&runs,&n,&cap);assert(!n&&!runs);
cap_mem(3);assert(!saved.n); // fully retired backing is never hashed or copied
free(region.hash);free(region.hash_valid);assert(!munmap(arena,6*page));puts("capture mapped runs and retired-page diffs: passed");}
'''
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "capture.c"
            binary = Path(tmp) / "capture"
            fixture.write_text(code)
            subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-Wno-misleading-indentation", "-fsanitize=address,undefined", str(fixture), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_tracked_texture_padding_and_field_extent(self):
        source = (ROOT / "dm/gfx/vk_android/vka_capture.c").read_text()
        header = (ROOT / "dm/gfx/vk_android/vka_capture.h").read_text()
        code = r"""
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "nvn_tex.h"
typedef struct {nvn_texdesc d;nvn_texlayout L;uint8_t *mem;void *pool;} tex_t;
typedef struct {uint64_t lo,size;uint32_t kind;} cap_region;
typedef struct {int(*addr)(void*,uint64_t*,uint64_t,int);int(*obj)(void*,int,uint64_t*,uint8_t*,uint32_t);} cap_ops;
#define CAP_K_POOL 7
static cap_region *g_cap_reg;static uint32_t g_cap_nreg;static int live=1;
static uint64_t nvn_pool_live(const void*p,void*a,void*b){(void)a;(void)b;return p&&live;}
static uint64_t source_hole;
static uint64_t dm_guest_unmapped(uint64_t a,uint64_t n){return source_hole&&a<source_hole+0x4000&&a+n>source_hole?1:0;}
static uint64_t encoded_size;static int encoded_pool;
static int addr(void*c,uint64_t*f,uint64_t n,int w){(void)c;(void)w;assert(*f==g_cap_reg[0].lo&&n<=g_cap_reg[0].size);encoded_size=n;return 1;}
static int obj(void*c,int k,uint64_t*f,uint8_t*p,uint32_t n){(void)c;(void)p;(void)n;assert(k==CAP_K_POOL&&*f==1);encoded_pool++;return 1;}
"""
        code += function(source, "cap_reg_find(")
        code += function(source, "cap_in_pool(")
        code += function(header, "vcap_tex_fields(")
        code += r"""
int main(void){
uint8_t *mem=mmap(NULL,0x30000,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(mem!=MAP_FAILED);
assert(!mprotect(mem+0x2c000,0x4000,PROT_NONE));
cap_region region={(uintptr_t)mem,0x2c000,0};g_cap_reg=&region;g_cap_nreg=1;
tex_t t={.d={.target=1,.format=0x49,.flags=0x24,.width=512,.height=512,.depth=1,.levels=10,.pkg_block_h=4},.mem=mem,.pool=(void*)1};
nvn_tex_layout(&t.d,&t.L);assert(t.L.size==0x30000);assert(nvn_tex_data_size(&t.d)==0x2b400);
assert(cap_in_pool(&t)); // CPU-tracked image retains its source; no static TPIX substitution
cap_ops ops={addr,obj};assert(vcap_tex_fields(&t,&ops,NULL));
assert(encoded_size==0x2b400&&encoded_pool==1&&t.mem==mem&&t.pool==(void*)1&&t.L.size==0x30000);
region.size=0x2b000;assert(!cap_in_pool(&t)); // actual last mip unavailable: cannot claim a complete CPU source
region.size=0x2c000;source_hole=(uintptr_t)mem+0x28000;assert(!mprotect((void*)(uintptr_t)source_hole,0x4000,PROT_NONE));assert(!cap_in_pool(&t)); // recorded region survives a later source unmap
source_hole=0;live=0;assert(!cap_in_pool(&t)); // finalized pool is not dereferenced
assert(!munmap(mem,0x30000));puts("tracked texture padding/source encoding contract: passed");}
"""
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "extent.c"
            binary = Path(tmp) / "extent"
            fixture.write_text(code)
            subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-fsanitize=address,undefined", "-I" + str(ROOT / "dm/gfx/nvn"), str(fixture), str(ROOT / "dm/gfx/nvn/nvn_tex.c"), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
