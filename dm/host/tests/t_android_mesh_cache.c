// The real file reader and requirement reflection, using only self-authored SPIR-V.
#include "../../gfx/vk_android/vka_mesh_cache.h"
#include "vk_mesh_cache_gen.h"
#include "../../gfx/nvn/dmsh_chain.h"
#include "../../gfx/nvn/dmsh_chain_vk.h"
int nxhost_vk_mesh_cache_report(const char*,const char*,const char*,char*,int);
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define I(n,op) ((n)<<16|(op))
static const uint32_t cs[]={
 0x07230203,0x00010500,0,16,0,
 I(2,17),1,I(2,17),61,I(2,17),64,I(2,17),11,
 I(3,14),0,1,I(6,15),5,1,0x6e69616d,0,8,I(6,16),1,17,32,1,1,
 I(2,19),2,I(3,33),3,2,I(4,21),4,32,0,I(4,43),4,5,64,
 I(4,28),6,4,5,I(4,32),7,4,6,I(4,59),7,8,4,
 I(5,54),2,1,0,3,I(2,248),9,I(1,253),I(1,56)
};
static void write_cache(const char *path,const vka_mesh_cache_head *h,const uint32_t *code){
 FILE *f=fopen(path,"wb");assert(f);assert(fwrite(h,sizeof *h,1,f)==1);assert(fwrite(code,h->cs_bytes,1,f)==1);assert(fwrite(cs,h->vs_bytes,1,f)==1);assert(!fclose(f));
}
int main(void){
 char dir[]="/tmp/nx-mesh-cache-XXXXXX";assert(mkdtemp(dir));char path[512];snprintf(path,sizeof path,"%s/%016llx.bnmesh",dir,0x12345ull);
 dmsh_vk_header native={.magic=DMSH_VK_LIB_MAGIC,.version=DMSH_VK_LIB_VERSION,.entry_count=1,.entry_off=128,.bind_off=192,.mod_count=1,.mod_off=192,.blob_off=208,.blob_size=sizeof cs};
 memcpy(native.chain,DMSH_CHAIN,strlen(DMSH_CHAIN));memcpy(native.vk_chain,DMSH_VK_CHAIN,strlen(DMSH_VK_CHAIN));memcpy(native.abi,DMSH_VK_ABI,12);memcpy(native.game,"FFFFFFFFFFFFFFFF",16);
 dmsh_vk_entry entry={.key=0x12345,.stage=7,.local={32,1,1},.mod_count=1};dmsh_vk_bind bind={0};dmsh_vk_module mod={.size=sizeof cs,.stored=sizeof cs,.codec=DMSH_VK_CODEC_RAW};
 vka_shlib L={.h=&native,.ent=&entry,.bind=&bind,.mod=&mod,.blob=(const uint8_t *)cs};
 vka_mesh_cache_head h={.magic="BNMESH2",.key=0x12345,.hash=vka_shlib_entry_hash(&L,&entry),.stage=7,.words=292,.primitives=32,.corners=1,.cs_bytes=sizeof cs,.vs_bytes=20,.version=VKA_MESH_CACHE_VERSION};
 char libpath[512];snprintf(libpath,sizeof libpath,"%s/native.lib",dir);FILE *lf=fopen(libpath,"wb");assert(lf);assert(fwrite(&native,sizeof native,1,lf)==1);assert(fwrite(&entry,sizeof entry,1,lf)==1);assert(fwrite(&mod,sizeof mod,1,lf)==1);assert(fwrite(cs,sizeof cs,1,lf)==1);assert(!fclose(lf));
 memcpy(h.chain,VKA_MESH_CACHE_CHAIN,16);write_cache(path,&h,cs);
 char report[4096];assert(!nxhost_vk_mesh_cache_report(libpath,dir,"FFFFFFFFFFFFFFFF",report,sizeof report));assert(strstr(report,"mesh.compute.cache=1\n"));assert(strstr(report,"mesh.compute.meshEntries=1\n"));assert(strstr(report,"mesh.compute.shared=256\n"));
 assert(nxhost_vk_mesh_cache_report(libpath,dir,"EEEEEEEEEEEEEEEE",report,sizeof report)==1);assert(strstr(report,"mesh.compute.cache=0\n"));
vka_mesh_cache_head got;FILE *f=vka_mesh_cache_open(dir,&L,&entry,&got);assert(f);
 vka_mesh_cache_req r;assert(vka_mesh_cache_requirements(f,&got,&r));assert(r.local[0]==32 && r.local[1]==1 && r.local[2]==1);assert(r.shared==256 && r.subgroup_ops==9 && r.features==VKA_MCR_INT64);fclose(f);
 vka_mesh_cache_head bad=h;bad.hash^=1;write_cache(path,&bad,cs);assert(!vka_mesh_cache_open(dir,&L,&entry,&got));
 bad=h;bad.chain[0]^=1;write_cache(path,&bad,cs);assert(!vka_mesh_cache_open(dir,&L,&entry,&got));
 bad=h;bad.version++;write_cache(path,&bad,cs);assert(!vka_mesh_cache_open(dir,&L,&entry,&got));
 bad=h;bad.key++;write_cache(path,&bad,cs);assert(!vka_mesh_cache_open(dir,&L,&entry,&got));
 bad=h;bad.stage=6;write_cache(path,&bad,cs);assert(!vka_mesh_cache_open(dir,&L,&entry,&got));
 write_cache(path,&h,cs);assert(!truncate(path,sizeof h+sizeof cs-4));assert(!vka_mesh_cache_open(dir,&L,&entry,&got));
 uint32_t corrupt[sizeof cs/4];memcpy(corrupt,cs,sizeof cs);corrupt[5]=I(0,17);write_cache(path,&h,corrupt);f=vka_mesh_cache_open(dir,&L,&entry,&got);assert(f);assert(!vka_mesh_cache_requirements(f,&got,&r));fclose(f);
 memcpy(corrupt,cs,sizeof cs);corrupt[0]=0;write_cache(path,&h,corrupt);assert(!vka_mesh_cache_open(dir,&L,&entry,&got));
 assert(!unlink(path));assert(!unlink(libpath));assert(!rmdir(dir));puts("t_android_mesh_cache: compatible, stale, truncated, malformed and requirement checks passed");return 0;
}
