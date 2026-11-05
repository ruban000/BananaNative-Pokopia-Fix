"""Production sampler metadata follows emitted handles across registry reuse/cache growth."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def function(source, name):
    start = source.index(name)
    while source.index(';', start) < source.index('{', start):
        start = source.index(name, start + len(name))
    start = source.rfind('\n', 0, start) + 1
    brace = source.index('{', start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


class SamplerTrace(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'C compiler required')
    def test_emitted_handle_snapshot_unknown_and_bounds(self):
        source = (ROOT / 'dm/gfx/vk_android/vka_tex.c').read_text()
        code = r'''
#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan_core.h>
#include "nvn_obj.h"
#include <assert.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static pthread_mutex_t g_tmu=PTHREAD_MUTEX_INITIALIZER;
static _Thread_local int held;
static int relocate, relocations, creates;
typedef struct {samp_par p; VkSampler s;} samp_ent;
static samp_ent *g_samp;static int g_nsamp;static unsigned g_nsamp_slots=32;
static samp_par *g_sreg;static int g_nsreg;
static VkSampler *g_sres;static int g_nsres;
static int *g_sdirty,g_nsd,g_sdcap;static uint8_t *g_sdflag;
static _Atomic uint32_t *g_sver;
static struct {VkDevice dev;VkPhysicalDeviceProperties props;} V;
static int lock(pthread_mutex_t *m){assert(!held);int r=pthread_mutex_lock(m);assert(!r);held=1;return r;}
static int unlock(pthread_mutex_t *m){
 assert(held);
 if(relocate){samp_ent *next=malloc((size_t)(g_nsamp+1)*sizeof *next);assert(next);memcpy(next,g_samp,(size_t)g_nsamp*sizeof *next);free(g_samp);g_samp=next;relocate=0;relocations++;}
 held=0;return pthread_mutex_unlock(m);
}
static int format(char *out,size_t n,const char *fmt,...){
 assert(!held);va_list args;va_start(args,fmt);int r=vsnprintf(out,n,fmt,args);va_end(args);return r;
}
static VkResult create(VkDevice dev,const VkSamplerCreateInfo *ci,const VkAllocationCallbacks *a,VkSampler *s){
 (void)dev;(void)a;assert(held);assert(ci->mipLodBias<=4&&ci->maxAnisotropy<=8);*s=(VkSampler)(uintptr_t)(0x100+ ++creates);return VK_SUCCESS;
}
static struct {PFN_vkCreateSampler vkCreateSampler;} vk={create};
#define pthread_mutex_lock lock
#define pthread_mutex_unlock unlock
#define snprintf format
#define VRED_ONCE(...) ((void)0)
'''
        for name in ('static void dirty(', 'void mtl_reg_samp(', 'static VkSamplerAddressMode wrap(',
                     'static VkSampler sampler_of(', 'int vka_samp_desc('):
            code += function(source, name)
        code += r'''
static samp_par original,latest;static VkSampler old_handle,new_handle;
static VkSampler make(const samp_par *p){lock(&g_tmu);VkSampler s=sampler_of(p);unlock(&g_tmu);return s;}
static void check_old(void){
 char text[384];assert(vka_samp_desc(old_handle,text,sizeof text));
 assert(strstr(text,"min=5 mag=1 wrap=1,7,6")&&strstr(text,"lod=0.5..11 bias=-2 aniso=16"));
 assert(strstr(text,"compare=1,4 reduction=2 border=0.1,0.2,0.3,0.4"));
 assert(strstr(text,"device-max-bias=4 device-max-aniso=8"));
}
static void *writer(void *arg){(void)arg;for(int i=0;i<256;i++)mtl_reg_samp(258,i&1?&original:&latest);return NULL;}
int main(void){
 V.props.limits.maxSamplerLodBias=4;V.props.limits.maxSamplerAnisotropy=8;
 original=(samp_par){.minf=5,.magf=1,.ws=1,.wt=7,.wr=6,.cmp_mode=1,.cmp_func=4,.reduction=2,
                      .lod_min=.5,.lod_max=11,.lod_bias=-2,.aniso=16,.border={.1,.2,.3,.4}};
 latest=original;latest.lod_bias=3;latest.minf=0;latest.lod_min=0;latest.lod_max=0;latest.border[0]=1;
 mtl_reg_samp(258,&original);old_handle=make(&original);
 // The registry now describes another sampler while a descriptor retains the old one.
 mtl_reg_samp(258,&latest);new_handle=make(&latest);assert(old_handle!=new_handle&&g_sreg[258].minf==0);
 check_old();char text[384];assert(vka_samp_desc(new_handle,text,sizeof text)&&strstr(text,"min=0")&&strstr(text,"bias=3"));
 // Move/free the entire source cache exactly at unlock: formatting must use a value copy.
 relocate=1;check_old();assert(relocations==1);
 pthread_t thread;assert(!pthread_create(&thread,NULL,writer,NULL));for(int i=0;i<256;i++)check_old();assert(!pthread_join(thread,NULL));
 assert(creates==2); // diagnostic never calls the driver or changes sampler creation.
 assert(!vka_samp_desc((VkSampler)(uintptr_t)0x999,text,sizeof text)&&!strcmp(text,"unknown"));
 assert(!vka_samp_desc(VK_NULL_HANDLE,text,sizeof text)&&!strcmp(text,"unknown"));
 char short_text[32];assert(!vka_samp_desc(old_handle,short_text,sizeof short_text)&&!strcmp(short_text,"unknown(truncated)"));
 char guard[3]={'L','X','R'};assert(!vka_samp_desc(old_handle,guard+1,1)&&guard[0]=='L'&&guard[1]==0&&guard[2]=='R');
 guard[1]='X';assert(!vka_samp_desc(old_handle,guard+1,0)&&guard[1]=='X');assert(!vka_samp_desc(old_handle,NULL,100));
 check_old();assert(creates==2&&!held);
 free(g_samp);free(g_sreg);free(g_sres);free(g_sdirty);free(g_sdflag);free(g_sver);
 puts("immutable sampler-handle metadata, lock snapshot and bounds: passed");
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / 'sampler.c'
            binary = Path(tmp) / 'sampler'
            c.write_text(code)
            command = ['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                       '-fsanitize=address,undefined', '-fno-pie', '-no-pie', '-pthread',
                       '-I', str(ROOT / 'dm/gfx/nvn'), '-I', str(ROOT / 'dm/gfx/nvn/include'),
                       '-I', str(ROOT / 'dm/third_party/vulkan-headers/include'), str(c), '-o', str(binary)]
            subprocess.run(command, check=True)
            subprocess.run([str(binary)], check=True)
            # Negative control uses the newest ID registration instead of emitted identity.
            self.assertIn('p = g_samp[i].p;', code)
            c.write_text(code.replace('p = g_samp[i].p;', 'p = g_sreg[258];'))
            subprocess.run(command, check=True)
            failed = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('min=5 mag=1', failed.stderr)


if __name__ == '__main__':
    unittest.main()
