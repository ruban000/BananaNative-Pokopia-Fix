"""Production burst recording and RGB conversion with deferred image copies."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class BurstCrop(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_recorded_region_and_conversion(self):
        source = (ROOT / 'dm/gfx/vk_android/vka_ctl.c').read_text()
        bpp = source[source.index('static int shot_bpp(VkFormat f) {'):source.index('// "burst <n>"')]
        definitions = source[source.index('enum { BURST_MAX'):source.index('static int burst_buf(')]
        operations = source[source.index('static int shot_bpp(VkFormat f);'):source.index('void vka_ctl_shot_record(')]
        harness = r'''
#define _POSIX_C_SOURCE 200809L
#define VK_NO_PROTOTYPES
#include <vulkan/vulkan_core.h>
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <limits.h>
static char logs[32768];static size_t logn;
static void logline(const char *fmt,...) {
 va_list a;va_start(a,fmt);int n=vsnprintf(logs+logn,sizeof logs-logn,fmt,a);va_end(a);
 assert(n>=0&&(size_t)n+logn+2<sizeof logs);logn+=n;logs[logn++]='\n';logs[logn]=0;
}
#define VLOG(...) logline(__VA_ARGS__)
#define VRED(...) logline(__VA_ARGS__)
static int origin,env_reads,barriers,copies,waits,jobs;
static int vka_origin_ll(void) { return origin; }
static int vka_env_str(const char *k,char *s,size_t n) { env_reads++;const char *v=getenv(k);if(v)snprintf(s,n,"%s",v);return !!v; }
static struct { VkDevice dev; } V;
typedef struct { struct { VkFormat vk; } f;uint32_t w,h,samples;VkImageAspectFlags aspect;VkImage img; } vka_img;
static char g_ctl_dir[64]="private";
static void ctl_dir(void) {}
static uint8_t pixels[12*10*4];static uint8_t *mapped;static size_t mapped_size;
static struct { VkBufferImageCopy r; } queued[32];static unsigned nq;
static void vka_barrier(VkCommandBuffer cb) { (void)cb;barriers++; }
static void copy_image(VkCommandBuffer cb,VkImage image,VkImageLayout layout,VkBuffer dst,uint32_t n,const VkBufferImageCopy *r) {
 (void)cb;(void)image;(void)dst;assert(layout==VK_IMAGE_LAYOUT_GENERAL&&n==1&&nq<32);
 assert(r->imageOffset.x>=0&&r->imageOffset.y>=0&&r->imageOffset.x+r->imageExtent.width<=12&&r->imageOffset.y+r->imageExtent.height<=10);
 assert(!r->bufferRowLength&&!r->bufferImageHeight&&r->imageSubresource.layerCount==1&&r->imageExtent.depth==1);
 assert(r->bufferOffset+r->imageExtent.width*r->imageExtent.height*4<=mapped_size);
 queued[nq++].r=*r;copies++;
}
static void barrier(VkCommandBuffer cb,VkPipelineStageFlags src,VkPipelineStageFlags dst,VkDependencyFlags flags,uint32_t nm,const VkMemoryBarrier *m,uint32_t nb,const VkBufferMemoryBarrier *b,uint32_t ni,const VkImageMemoryBarrier *im) {
 (void)cb;(void)flags;(void)b;(void)im;assert(src==VK_PIPELINE_STAGE_TRANSFER_BIT&&dst==VK_PIPELINE_STAGE_HOST_BIT&&nm==1&&!nb&&!ni);
 assert(m->srcAccessMask==VK_ACCESS_TRANSFER_WRITE_BIT&&m->dstAccessMask==VK_ACCESS_HOST_READ_BIT);barriers++;
}
static VkResult wait_fence(VkDevice d,uint32_t n,const VkFence *f,VkBool32 all,uint64_t timeout) {
 (void)d;assert(n==1&&*f==(VkFence)7&&all&&timeout==5000000000ull);waits++;
 for(unsigned i=0;i<nq;i++) { const VkBufferImageCopy *r=&queued[i].r;
  for(unsigned y=0;y<r->imageExtent.height;y++)memcpy(mapped+r->bufferOffset+y*r->imageExtent.width*4,pixels+((y+r->imageOffset.y)*12+r->imageOffset.x)*4,r->imageExtent.width*4);
 }nq=0;return VK_SUCCESS;
}
static struct { PFN_vkCmdCopyImageToBuffer vkCmdCopyImageToBuffer;PFN_vkCmdPipelineBarrier vkCmdPipelineBarrier;PFN_vkWaitForFences vkWaitForFences; } vk={copy_image,barrier,wait_fence};
'''
        allocation = r'''
static int burst_buf(VkDeviceSize n) {
 assert(!nq);if(n>mapped_size) { free(mapped);mapped=calloc(1,n);mapped_size=n; }
 g_bmap=mapped;g_bsize=mapped_size;g_bbuf=(VkBuffer)1;return mapped!=NULL;
}
'''
        tests = r'''
static int expected_x,expected_y,expected_w,expected_h,window_y,window_h;
static uint64_t expected_frame;
static void *shot_main(void *arg) {
 shot_job *j=arg;assert(waits>0&&!nq);assert(j->w==expected_w&&j->h==expected_h&&j->frame==expected_frame++);
 for(int y=0;y<j->h;y++)for(int x=0;x<j->w;x++) {
  int sx=expected_x+x,sy=window_y+(g_burst_flip?window_h-1-expected_y-y:expected_y+y);
  const uint8_t *p=j->rgb+(y*j->w+x)*3;
  assert(p[0]==sx+16*sy&&p[1]==3*sx+5*sy&&p[2]==200-sy);
 }
 jobs++;free(j->rgb);free(j);return NULL;
}
static void fill(VkFormat fmt) {
 for(int y=0;y<10;y++)for(int x=0;x<12;x++) {
  uint8_t r=x+16*y,g=3*x+5*y,b=200-y;uint8_t *p=pixels+(y*12+x)*4;
  if(fmt==VK_FORMAT_A2B10G10R10_UNORM_PACK32) { uint32_t v=(r*4u)|((g*4u)<<10)|((b*4u)<<20)|(3u<<30);memcpy(p,&v,4); }
  else if(fmt==VK_FORMAT_B8G8R8A8_UNORM||fmt==VK_FORMAT_B8G8R8A8_SRGB) { p[0]=b;p[1]=g;p[2]=r;p[3]=255; }
  else { p[0]=r;p[1]=g;p[2]=b;p[3]=255; }
 }
}
static void arm(const char *s,int n) { setenv("DM_VK_BURST_CROP",s,1);atomic_store(&g_burst_want,n); }
int main(void) {
 int rect[4];assert(burst_crop_parse("",8,6,rect)&&rect[2]==8&&rect[3]==6);
 const char *bad[]={"1,1,0,2","1,1,3,0","-1,1,3,2","+1,1,3,2","1,1,3","1,1,3,2,0","1,1,3,2x"," 1,1,3,2","2147483648,0,1,1","999999999999999999999,1,1,1","8,0,1,1","0,6,1,1","7,5,2,1","7,5,1,2"};
 vka_img im={{VK_FORMAT_R8G8B8A8_UNORM},12,10,1,VK_IMAGE_ASPECT_COLOR_BIT,(VkImage)9};
 for(unsigned i=0;i<sizeof bad/sizeof *bad;i++) {
  assert(!burst_crop_parse(bad[i],8,6,rect));arm(bad[i],2);burst_record((VkCommandBuffer)1,&im,2,3,8,6,100);
  assert(!g_burst_n&&!g_burst_done&&!copies&&!waits&&!jobs);
 }
 arm("1,1,3,2",2);burst_record((VkCommandBuffer)1,&im,11,3,8,6,100);assert(!copies&&!g_burst_n);
 char too_long[256];memset(too_long,'0',sizeof too_long);too_long[255]=0;
 arm(too_long,2);burst_record((VkCommandBuffer)1,&im,2,3,8,6,100);assert(!copies&&!g_burst_n);
 VkFormat formats[]={VK_FORMAT_R8G8B8A8_UNORM,VK_FORMAT_R8G8B8A8_SRGB,VK_FORMAT_B8G8R8A8_UNORM,VK_FORMAT_B8G8R8A8_SRGB,VK_FORMAT_A2B10G10R10_UNORM_PACK32};
 for(unsigned f=0;f<sizeof formats/sizeof *formats;f++)for(origin=0;origin<2;origin++)for(int crop=0;crop<2;crop++) {
  im.f.vk=formats[f];fill(im.f.vk);int c0=copies,w0=waits,j0=jobs,e0=env_reads;
  expected_x=2+(crop?1:0);expected_y=crop?1:0;expected_w=crop?3:8;expected_h=crop?2:6;window_y=3;window_h=6;expected_frame=200;
  arm(crop?"1,1,3,2":"",2);burst_record((VkCommandBuffer)1,&im,2,3,8,6,200);
  assert(copies==c0+1&&waits==w0&&jobs==j0&&env_reads==e0+1);
  assert(queued[0].r.imageOffset.x==expected_x&&queued[0].r.imageOffset.y==3+(origin?6-expected_y-expected_h:expected_y));
  burst_done((VkFence)7);assert(waits==w0&&jobs==j0);
  setenv("DM_VK_BURST_CROP","bad live edit",1); // latched, not reparsed mid-burst
  burst_record((VkCommandBuffer)1,&im,2,3,8,6,201);
  assert(env_reads==e0+1&&copies==c0+2&&waits==w0&&jobs==j0&&g_burst_done);
  burst_done((VkFence)7);assert(jobs==j0+2&&waits==w0+1&&!g_burst_n);
 }
 origin=0;im.f.vk=VK_FORMAT_R8G8B8A8_UNORM;fill(im.f.vk);
 // Mid-burst image size, present rectangle, origin and format changes stop before
 // another copy and retain the original recorded region for partial conversion.
 for(int mutation=0;mutation<4;mutation++) {
  expected_x=3;expected_y=1;expected_w=3;expected_h=2;window_y=3;window_h=6;expected_frame=300;
  arm("1,1,3,2",3);burst_record((VkCommandBuffer)1,&im,2,3,8,6,300);int c0=copies,j0=jobs;
  int cx=2;if(mutation==0)im.w=11;if(mutation==1)cx=1;if(mutation==2)origin=1;if(mutation==3)im.f.vk=VK_FORMAT_B8G8R8A8_UNORM;
  burst_record((VkCommandBuffer)1,&im,cx,3,8,6,301);assert(copies==c0&&g_burst_done&&g_burst_n==1);
  burst_done((VkFence)7);assert(jobs==j0+1);im.w=12;origin=0;im.f.vk=VK_FORMAT_R8G8B8A8_UNORM;
 }
 assert(strstr(logs,"frame 200 output 1,1,3,2 window 2,3,8,6 source 3,6 origin-ll 1"));
 assert(strstr(logs,"frame 201 output 1,1,3,2 window 2,3,8,6 source 3,4 origin-ll 0"));
 assert(barriers==2*copies&&BURST_MAX==32);free(mapped);puts("production burst crop recording, deferred fence and RGB conversion: passed");return 0;
}
'''
        program = harness + bpp + definitions + allocation + operations + tests
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / 'burst.c'
            binary = Path(tmp) / 'burst'
            c.write_text(program)
            args = ['cc', '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-I', str(ROOT / 'dm/third_party/vulkan-headers/include'), str(c), '-o', str(binary)]
            subprocess.run(args, check=True)
            subprocess.run([str(binary)], check=True)
            # A missing lower-left translation must fail the coordinate/pixel oracle.
            c.write_text(program.replace('g_burst_crop.ch - g_burst_crop.y - g_burst_crop.h', 'g_burst_crop.y'))
            subprocess.run(args, check=True)
            result = subprocess.run([str(binary)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'queued[0].r.imageOffset', result.stderr)


if __name__ == '__main__':
    unittest.main()
