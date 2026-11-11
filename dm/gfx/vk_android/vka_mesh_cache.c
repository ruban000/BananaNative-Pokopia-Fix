#include "vka_mesh_cache.h"
#include "vk_mesh_cache_gen.h"
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

FILE *vka_mesh_cache_open(const char *root, const vka_shlib *L, const dmsh_vk_entry *e, vka_mesh_cache_head *h) {
    if (!root || !*root) return NULL;
    char path[1200]; if (snprintf(path,sizeof path,"%s/%016llx.bnmesh",root,(unsigned long long)e->key)>=(int)sizeof path) return NULL;
    FILE *f=fopen(path,"rb"); if (!f) return NULL;
    const char chain[16]=VKA_MESH_CACHE_CHAIN; struct stat st; uint32_t magic;
    if (fread(h,sizeof *h,1,f)!=1 || memcmp(h->magic,"BNMESH2",8) || h->version!=VKA_MESH_CACHE_VERSION || memcmp(h->chain,chain,16)
        || h->key!=e->key || h->stage!=e->stage || h->hash!=vka_shlib_entry_hash(L,e)
        || !h->cs_bytes || (h->cs_bytes&3) || (h->vs_bytes&3) || h->cs_bytes>(16u<<20) || h->vs_bytes>(4u<<20)
        || h->payload>(1u<<20) || !h->words || h->words>(1u<<20)
        || h->task_limit>512 || (e->stage!=6 && h->task_limit)
        || (e->stage==7 && (!h->vs_bytes || !h->primitives || (h->corners!=1 && h->corners!=3)))
        || fstat(fileno(f),&st) || (uint64_t)st.st_size!=sizeof *h+(uint64_t)h->cs_bytes+h->vs_bytes
        || h->cs_bytes<20 || fread(&magic,4,1,f)!=1 || magic!=0x07230203) goto bad;
    if (h->vs_bytes && (h->vs_bytes<20 || fseek(f,(long)(sizeof *h+h->cs_bytes),SEEK_SET) || fread(&magic,4,1,f)!=1 || magic!=0x07230203)) goto bad;
    if (fseek(f,sizeof *h,SEEK_SET)) goto bad;
    return f;
bad: fclose(f); return NULL;
}

int vka_mesh_cache_requirements(FILE *f,const vka_mesh_cache_head *h,vka_mesh_cache_req *r) {
    memset(r,0,sizeof *r);
    size_t n=h->cs_bytes/4; uint32_t *w=malloc(h->cs_bytes), *sizes=NULL, *constants=NULL;
    int ok=0, entry=0, local=0; uint64_t shared=0;
    if (!w || fseek(f,sizeof *h,SEEK_SET) || fread(w,4,n,f)!=n || n<5 || w[0]!=0x07230203 || !w[3] || w[3]>(1u<<20)) goto done;
    uint32_t bound=w[3]; sizes=calloc(bound,sizeof *sizes); constants=calloc(bound,sizeof *constants);
    if (!sizes || !constants) goto done;
    for (size_t at=5;at<n;) {
        uint32_t len=w[at]>>16,op=w[at]&65535; const uint32_t *x=w+at;
        if (!len || len>n-at) goto done;
        if (op==15) { if (len<4 || x[1]!=5) goto done; entry++; }
        if (op==16 && len>=3 && x[2]==17) {
            if (len!=6) goto done;
            memcpy(r->local,x+3,12); local++;
        }
        if (op==17 && len==2) {
            uint32_t cap=x[1]; if (cap>=61 && cap<=68) r->subgroup_ops|=1u<<(cap-61);
            if (cap==4423) r->subgroup_ops|=8; if (cap==4431) r->subgroup_ops|=2;
            if (cap==11) r->features|=VKA_MCR_INT64; if (cap==10) r->features|=VKA_MCR_FLOAT64;
            if (cap==22) r->features|=VKA_MCR_INT16; if (cap==9) r->features|=VKA_MCR_FLOAT16;
            if (cap==4433) r->features|=VKA_MCR_STORAGE16;
            if (cap==4448) r->features|=VKA_MCR_STORAGE8;
        }
        if ((op==21 || op==22) && len>=3) { if (x[1]>=bound) goto done; sizes[x[1]]=x[2]==32?4:0; }
        if (op==23 && len==4) {
            if (x[1]>=bound || x[2]>=bound || x[3]>4) goto done;
            sizes[x[1]]=sizes[x[2]]*(x[3]==3?4:x[3]);
        }
        if (op==43 && len==4) { if (x[2]>=bound) goto done; constants[x[2]]=x[3]; }
        if (op==28 && len==4) {
            if (x[1]>=bound || x[2]>=bound || x[3]>=bound) goto done;
            uint64_t bytes=(uint64_t)sizes[x[2]]*constants[x[3]];
            if (bytes>UINT32_MAX) goto done; sizes[x[1]]=(uint32_t)bytes;
        }
        if (op==32 && len==4) { if (x[1]>=bound || x[3]>=bound) goto done; sizes[x[1]]=sizes[x[3]]; }
        if (op==59 && len>=4 && x[3]==4) {
            if (x[1]>=bound || !sizes[x[1]]) goto done;
            shared+=sizes[x[1]]; if (shared>UINT32_MAX) goto done;
        }
        at+=len;
    }
    if (entry!=1 || local!=1 || !r->local[0] || !r->local[1] || !r->local[2]) goto done;
    r->shared=(uint32_t)shared; ok=1;
done: free(constants); free(sizes); free(w); return ok;
}

#include "../nvn/dmsh_chain.h"
#include "../nvn/dmsh_chain_vk.h"
#define MMAX(a,b) do { if ((b)>(a)) (a)=(b); } while (0)
// File compatibility is separate from physical-device features. No process
// environment or Vulkan state changes, so the all-games report can call this.
__attribute__((visibility("default"))) int nxhost_vk_mesh_cache_report(const char *library,const char *root,const char *game,char *buf,int n) {
    if (!buf || n<64) return -1;
    if (!root || !*root) { snprintf(buf,n,"mesh.compute.selected=0\n"); return 0; }
    vka_shlib L; char error[256]=""; uint32_t meshes=0,tasks=0,invocations=0,shared=0,record=0,ops=0,features=0,storage=4,uniform=0,local[3]={0};
    if (!library || !*library || !vka_shlib_open(&L,library,DMSH_CHAIN,DMSH_VK_CHAIN,game,DMSH_VK_ABI,error,sizeof error)) {
        snprintf(buf,n,"mesh.compute.selected=1\nmesh.compute.cache=0\nmesh.compute.error=Shader library is unavailable or incompatible\n"); return 1;
    }
    for (uint32_t i=0;i<L.h->entry_count;i++) {
        const dmsh_vk_entry *e=L.ent+i; if (e->stage!=6 && e->stage!=7) continue;
        vka_mesh_cache_head h; vka_mesh_cache_req r;
        FILE *f=vka_mesh_cache_open(root,&L,e,&h);
        int valid=f && vka_mesh_cache_requirements(f,&h,&r); if (f) fclose(f);
        if (!valid) { snprintf(error,sizeof error,"Missing, stale or invalid sidecar %016llx",(unsigned long long)e->key); break; }
        if (r.local[0]!=e->local[0] || r.local[1]!=e->local[1] || r.local[2]!=e->local[2]) {
            snprintf(error,sizeof error,"Sidecar workgroup differs from the native entry %016llx",(unsigned long long)e->key); break;
        }
        uint64_t product=(uint64_t)r.local[0]*r.local[1]*r.local[2];
        if (product>UINT32_MAX) { snprintf(error,sizeof error,"Sidecar workgroup is too large"); break; }
        meshes+=e->stage==7; tasks+=e->stage==6; MMAX(invocations,(uint32_t)product); MMAX(shared,r.shared); MMAX(record,4*h.words);
        for (int k=0;k<3;k++) MMAX(local[k],r.local[k]);
        ops|=r.subgroup_ops; features|=r.features;
        uint32_t ns=4,nu=0; const dmsh_vk_bind *bind=vka_shlib_binds(&L,e);
        for (uint32_t k=0;k<e->bind_count;k++) if (bind[k].set==DMSH_VK_SET_DRAW) {
            if (bind[k].desc==7) ns+=bind[k].count;
            if (bind[k].desc==6) nu+=bind[k].count;
        }
        MMAX(storage,ns); MMAX(uniform,nu);
    }
    vka_shlib_close(&L);
    if (!*error && !meshes) snprintf(error,sizeof error,"This library has no mesh entries");
    snprintf(buf,n,"mesh.compute.selected=1\nmesh.compute.chain=" VKA_MESH_CACHE_CHAIN "\nmesh.compute.cache=%u\nmesh.compute.error=%s\nmesh.compute.meshEntries=%u\nmesh.compute.taskEntries=%u\n"
        "mesh.compute.invocations=%u\nmesh.compute.localX=%u\nmesh.compute.localY=%u\nmesh.compute.localZ=%u\nmesh.compute.shared=%u\nmesh.compute.record=%u\n"
        "mesh.compute.subgroupOps=%u\nmesh.compute.features=%u\nmesh.compute.storageDescriptors=%u\nmesh.compute.uniformDescriptors=%u\n",
        !*error,error,meshes,tasks,invocations,local[0],local[1],local[2],shared,record,ops,features,storage,uniform);
    return *error?1:0;
}
#undef MMAX
