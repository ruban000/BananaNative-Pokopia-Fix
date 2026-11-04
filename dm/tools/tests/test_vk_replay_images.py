"""Execute production restore/view logic with mocked GPU calls; no device required."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]

class ReplayImageState(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_realized_base_and_views(self):
        src=(ROOT / 'dm/gfx/vk_android/vka_tex.c').read_text()
        start=src.index('vka_img *vka_tex_restore('); end=src.index('\nvka_img *vka_tex_acquire',start)
        fn=src[start:end]
        vstart=src.index('    uint32_t tl = im->base + im->levels',src.index('static VkImageView view_of('));vend=src.index('    uint32_t a0',vstart)
        view=src[vstart:vend]
        pre=r'''
        #include <assert.h>
        #include <stdint.h>
        #include <stdlib.h>
        #include <stdatomic.h>
        #include <stdio.h>
        #include <string.h>
        typedef void *VkCommandBuffer;
        typedef struct {uint32_t width,height,depth,levels;int target,format;} desc;
        typedef struct {unsigned layers;} layout;
        typedef struct {uint64_t serial;desc d;layout L;void *mem,*pool;} tex_t;
        typedef struct {int vk;} vka_fmt;
        typedef struct vka_img {uint64_t serial;desc d;layout L;void *mem,*vpool;unsigned base,vmin,levels,w,h,aspect;int refs,inited,replay_vcut,track,vtrack,vbind,cpu_dirty,from_mem,gpu_owned;void *img;} vka_img;
        typedef struct {vka_img *im;} smap_ent;
        typedef struct {unsigned base_level,nlevels;int set_levels;} view_t;
        #define NVN_TEXTURE_TARGET_3D 2
        #define VK_IMAGE_LAYOUT_UNDEFINED 0
        #define VK_IMAGE_LAYOUT_GENERAL 1
        static int leases,g_tmu,barriers,creates,rebases,redirties,g_nlive,g_livecap;
        static _Atomic unsigned g_tgen;
        static vka_img **g_live; static smap_ent slot;
        static void nvn_pool_use(int on){leases+=on?1:-1;assert(leases>=0);}
        static void pthread_mutex_lock(int *p){(void)p;} static void pthread_mutex_unlock(int *p){(void)p;}
        static int vka_ok(void){return 1;} static smap_ent *map_get(uint64_t s){(void)s;return slot.im?&slot:NULL;}
        static int vka_fmt_of(int fmt,vka_fmt *f){f->vk=fmt;return fmt!=0;}
        static int same_shape(vka_img *im,const tex_t*t,const vka_fmt*f,int mode){(void)im;(void)t;(void)f;(void)mode;return 0;}
        static int img_pool_dead(vka_img *im){(void)im;return 0;}
        static vka_img *img_create_(const tex_t*t,const vka_fmt*f,unsigned base,int res,int*why,uint64_t*need){
        (void)f;(void)why;(void)need;assert(!res);creates++;
        vka_img *im=calloc(1,sizeof *im);im->serial=t->serial;im->d=t->d;im->L=t->L;im->base=base;im->levels=t->d.levels-base;im->w=t->d.width>>base;im->h=t->d.height>>base;im->refs=1;im->img=im;return im;}
        static void vka_img_barrier(VkCommandBuffer cb,void*im,unsigned aspect,int old,int new){(void)cb;(void)im;(void)aspect;assert(old==0&&new==1);barriers++;}
        static void lazy_drop(uint64_t s){(void)s;}
        static void map_put(uint64_t s,vka_img*im){(void)s;slot.im=im;}
        static int img_rebase(vka_img*im,unsigned base,VkCommandBuffer cb){(void)cb;assert(!im->vpool);rebases++;im->base=base;im->levels=im->d.levels-base;im->w=im->d.width>>base;im->h=im->d.height>>base;return 1;}
        static void redirty_ids(vka_img*im){(void)im;redirties++;}
        '''
        post=r'''
        static void levels(vka_img*im,const view_t*v,int storage,unsigned*out,unsigned*count){
        VIEW
        *out=l0;*count=nl;
        }
        int main(void){
        tex_t t={.serial=31488,.d={.width=6144,.height=6144,.depth=2,.levels=4,.target=4,.format=38},.L={2}};
        vka_img *im=vka_tex_restore(&t,NULL,1,2,1,0);
        assert(im&&im->w==3072&&im->h==3072&&im->levels==3&&im->L.layers==2&&im->d.width==6144);
        assert(creates==1&&barriers==1&&!leases&&im->inited&&!im->track&&!im->vtrack&&!im->gpu_owned);
        unsigned off,n;levels(im,NULL,0,&off,&n);assert(off==1&&n==2); // logical mip2 -> image mip1
        levels(im,NULL,1,&off,&n);assert(off==0&&n==3); // storage ignores sampled view clamp
        view_t v={.set_levels=1,.base_level=3,.nlevels=1};levels(im,&v,0,&off,&n);assert(off==2&&n==1);
        assert(vka_tex_restore(&t,NULL,1,2,1,1)==im&&creates==1&&barriers==1&&im->gpu_owned);
        assert(vka_tex_restore(&t,NULL,0,0,0,0)==im&&rebases==1&&im->w==6144&&im->levels==4&&!im->gpu_owned&&!im->replay_vcut);
        assert(!vka_tex_restore(&t,NULL,4,0,0,0)&&!leases);
        t.d.target=2;assert(!vka_tex_restore(&t,NULL,1,0,0,0)&&!leases);
        free(im);free(g_live);puts("actual restore and sampled/storage view paths: passed");}
        '''.replace('VIEW',view)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "restore.c"
            binary = Path(tmp) / "restore"
            source.write_text(pre + fn + post)
            subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-fsanitize=address,undefined", str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

if __name__ == "__main__":
    unittest.main()
