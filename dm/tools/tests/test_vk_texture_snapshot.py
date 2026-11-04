"""Required texture snapshots must not fall back to direct reads on allocation failure."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def function(source, name):
    start = source.rfind("\n", 0, source.index(name)) + 1
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


class TextureSnapshot(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_required_allocation_failure(self):
        source = (ROOT / "dm/gfx/vk_android/vka_tex.c").read_text()
        code = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
typedef struct {uint64_t size;} desc;
typedef struct {desc d;const uint8_t *mem;uint64_t serial;} tex_t;
enum {GW_COPY,GW_FILL,GW_UPDATE,GW_OPAQUE};
typedef struct {uint64_t lo,hi,src,seq,fence;uint32_t kind,value;uint8_t *data;} gw_t;
static gw_t *g_gw;static int g_ngw;static uint64_t g_gwseq=2;
static int alloc_fail=1,unmapped=1,reads;static jmp_buf failure;
static uint64_t vka_done(void){return 0;}
static uint64_t nvn_tex_data_size(const desc *d){return d->size;}
static uint64_t dm_guest_unmapped(uintptr_t a,uint64_t n){(void)a;(void)n;return unmapped;}
static void dm_guest_read_mapped(void *d,uintptr_t a,uint64_t n){reads++;if(unmapped) memset(d,0,n);else memcpy(d,(void*)a,n);}
static void *test_malloc(size_t n){return alloc_fail?NULL:malloc(n);}
#define malloc test_malloc
#define die(...) longjmp(failure,1)
#define VRED(...) ((void)0)
'''
        code += function(source, "static uint64_t mem_hash(")
        code += function(source, "static int mem_hash_mapped(")
        code += function(source, "static uint8_t *gw_content(")
        code += function(source, "static uint8_t *unmapped_content(")
        code += r'''
int main(void){
size_t page=(size_t)sysconf(_SC_PAGESIZE);
uint8_t *guard=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(guard!=MAP_FAILED);
tex_t t={{page},guard,7};
uint64_t observed=123;assert(!mem_hash_mapped(guard,page,&observed)&&observed==123);
uint8_t readable[8]={1};unmapped=0;assert(mem_hash_mapped(readable,sizeof readable,&observed)&&observed==mem_hash(readable,sizeof readable));unmapped=1;
if(!setjmp(failure)){unmapped_content(&t);assert(!"required unmapped snapshot must fail explicitly");}
assert(!reads);
unmapped=0;assert(unmapped_content(&t)==NULL);assert(!reads); // no snapshot is needed
uint64_t seen=99;assert(gw_content(guard,page,&seen)==NULL&&seen==1); // no pending write
gw_t write={(uintptr_t)guard,(uintptr_t)guard+page,0,1,0,GW_FILL,0xabababab,NULL};g_gw=&write;g_ngw=1;
if(!setjmp(failure)){gw_content(guard,page,&seen);assert(!"required pending-write snapshot must fail explicitly");}
assert(!reads);
alloc_fail=0;unmapped=1;
uint8_t *snapshot=gw_content(guard,page,&seen);assert(snapshot&&seen==1&&reads==1);
for(size_t i=0;i<page;i++){assert(snapshot[i]==0xab);}free(snapshot);
assert(!munmap(guard,page));puts("required texture snapshot allocation contract: passed");
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            fixture, binary = Path(tmp) / "snapshot.c", Path(tmp) / "snapshot"
            fixture.write_text(code)
            subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-fsanitize=address,undefined", str(fixture), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
