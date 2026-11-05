"""Exercise the production row upload's storage-read/pageout activity policy."""
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


class TextureActivity(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_row_copy_recency_and_allocation_failure(self):
        source = (ROOT / "dm/gfx/vk_android/vka_tex.c").read_text()
        code = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
enum { NVN_TEXTURE_FLAGS_LINEAR=1, NVN_TEXTURE_FLAGS_LINEAR_RENDER_TARGET=2,
       VK_IMAGE_LAYOUT_GENERAL=1, VK_IMAGE_ASPECT_COLOR_BIT=1 };
typedef uintptr_t VkCommandBuffer;
typedef struct { uint32_t flags,stride; } nvn_texdesc;
typedef struct { uint32_t bw,bh,bpb,kind; } vka_fmt;
typedef struct { nvn_texdesc d; const uint8_t *mem; struct {uint64_t size;} L; } tex_t;
typedef struct { vka_fmt f; uint32_t w,h,depth,levels,layers,aspect;
    uintptr_t img; uint64_t stamp,po_off,up_ns; } vka_img;
typedef struct { uint64_t off; uint8_t *ptr; uintptr_t buf; } vka_span;
typedef struct { uint64_t bufferOffset; uint32_t bufferRowLength,bufferImageHeight;
    struct {uint32_t aspectMask,mipLevel,baseArrayLayer,layerCount;} imageSubresource;
    struct {int32_t x,y,z;} imageOffset;
    struct {uint32_t width,height,depth;} imageExtent; } VkBufferImageCopy;
static uint8_t storage[64],scratch[64];
static int fail_alloc,allocs,protects,reads,touches,clock_reads,copies;
static uint64_t g_rows_up;
static vka_img *current;
static VkBufferImageCopy observed;
static uint64_t now_ns(void) { assert(reads>0);clock_reads++;return 900; }
static void pageout_start(void) { assert(reads>0);touches++; }
static uint64_t nvn_dirty_now(void) { return 77; }
static void nvn_dirty_protect(const void *p,uint64_t n) {
    assert(p==storage&&n==sizeof storage);assert(current->stamp==77);protects++;
}
static int vka_scratch(uint64_t n,uint32_t alignment,vka_span *s) {
    assert(alignment==16&&n<=sizeof scratch);allocs++;
    if(fail_alloc)return 0;
    *s=(vka_span){96,scratch,99};return 1;
}
static void *read_copy(void *dst,const void *src,size_t size) {
    assert(protects==1&&current->stamp==77&&!touches&&!clock_reads);
    assert(src>= (void *)storage && (const uint8_t *)src+size<=storage+sizeof storage);
    reads++;return memcpy(dst,src,size);
}
static void gpu_copy(VkCommandBuffer cb,uintptr_t buf,uintptr_t image,int layout,
                     uint32_t count,const VkBufferImageCopy *copy) {
    assert(cb==42&&buf==99&&image==23&&layout==VK_IMAGE_LAYOUT_GENERAL&&count==1);
    assert(touches==1&&current->up_ns==900&&current->po_off==0);
    observed=*copy;copies++;
}
static struct {void (*vkCmdCopyBufferToImage)(VkCommandBuffer,uintptr_t,uintptr_t,int,uint32_t,const VkBufferImageCopy *);} vk={gpu_copy};
#define memcpy read_copy
'''
        code += function(source, "static void storage_read_touch(")
        code += function(source, "static int upload_rows_tracked(")
        code += r'''
#undef memcpy
static void reset(vka_img *im) {
    allocs=protects=reads=touches=clock_reads=copies=0;
    memset(scratch,0xcc,sizeof scratch);current=im;
    im->stamp=11;im->up_ns=123;im->po_off=4096;
}
int main(void) {
    for(unsigned i=0;i<sizeof storage;i++)storage[i]=(uint8_t)(i+1);
    vka_img im={.f={1,1,1,0},.w=4,.h=8,.depth=1,.levels=1,.layers=1,
        .aspect=VK_IMAGE_ASPECT_COLOR_BIT,.img=23};
    tex_t t={.d={NVN_TEXTURE_FLAGS_LINEAR,8},.mem=storage,.L={sizeof storage}};
    reset(&im);
    assert(upload_rows_tracked(&im,&t,42,17,35)==1);
    assert(allocs==1&&protects==1&&reads==3&&touches==1&&clock_reads==1&&copies==1);
    assert(im.stamp==77&&im.up_ns==900&&im.po_off==0&&g_rows_up==12);
    for(unsigned y=0;y<3;y++)assert(!memcmp(scratch+4*y,storage+8*(y+2),4));
    for(unsigned i=12;i<sizeof scratch;i++)assert(scratch[i]==0xcc);
    assert(observed.bufferOffset==96&&observed.bufferRowLength==4&&observed.bufferImageHeight==3);
    assert(observed.imageSubresource.aspectMask==1&&observed.imageSubresource.mipLevel==0);
    assert(observed.imageSubresource.baseArrayLayer==0&&observed.imageSubresource.layerCount==1);
    assert(observed.imageOffset.x==0&&observed.imageOffset.y==2&&observed.imageOffset.z==0);
    assert(observed.imageExtent.width==4&&observed.imageExtent.height==3&&observed.imageExtent.depth==1);
    // Existing failure stamp behavior is intentionally preserved; no bytes were read.
    reset(&im);fail_alloc=1;
    assert(upload_rows_tracked(&im,&t,42,17,35)==1);
    assert(allocs==1&&protects==1&&!reads&&!touches&&!clock_reads&&!copies);
    assert(im.stamp==77&&im.up_ns==123&&im.po_off==4096&&g_rows_up==12);
    reset(&im);fail_alloc=0;t.d.flags=0;
    assert(upload_rows_tracked(&im,&t,42,17,35)==0);
    assert(!allocs&&!protects&&!reads&&!touches&&!clock_reads&&!copies);
    assert(im.stamp==11&&im.up_ns==123&&im.po_off==4096);
    puts("production partial row bytes/range/stamp/read-activity and failure policy: passed");
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            fixture, binary = Path(tmp) / "activity.c", Path(tmp) / "activity"
            fixture.write_text(code)
            subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", str(fixture), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
