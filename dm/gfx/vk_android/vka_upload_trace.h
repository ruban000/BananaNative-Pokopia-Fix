// Bounded CPU upload receipts. Included only by vka_tex.c; no GPU or memory-policy changes.
// The replay owns the records until READY, then the writer owns them until IDLE.
#pragma once
#include <inttypes.h>
enum { UT_CAP = 2048, UT_FRAMES = 112 };
enum { UT_IDLE, UT_RECORDING, UT_READY };
enum { UT_FIRST = 1, UT_DIRTY, UT_WRITE, UT_GPU, UT_SPARSE, UT_OTHER };
enum { UT_FRAME, UT_SUB, UT_FENCE, UT_SERIAL, UT_PGEN, UT_STORAGE, UT_LOGICAL, UT_COPIED,
       UT_LO, UT_HI, UT_TOTAL, UT_PROTECT, UT_SNAPSHOT, UT_SCRATCH, UT_DESWIZZLE, UT_MTWAIT,
       UT_COPY, UT_SNAPSHOT_BYTES, UT_SCRATCH_BYTES, UT_N64 };
enum { UT_REASON, UT_FORMAT, UT_W, UT_H, UT_D, UT_LEVELS, UT_LAYERS, UT_BASE, UT_TASKS,
       UT_PARALLEL, UT_BW, UT_BH, UT_BPB, UT_N32 };
typedef struct { uint64_t v[UT_N64]; uint32_t u[UT_N32]; } ut_record;
static struct {
    ut_record rows[UT_CAP];
    _Atomic int state;
    pthread_mutex_t mu;
    pthread_cond_t cv;
    uint64_t session, first, last, sub;
    unsigned count;
    int seen, stop, writer, token;
    char path[512];
    const char *end;
} UT = {.mu = PTHREAD_MUTEX_INITIALIZER, .cv = PTHREAD_COND_INITIALIZER};
_Static_assert(sizeof UT <= 512u * 1024, "upload receipt fixed-storage budget");
static _Thread_local int ut_enabled;
static _Thread_local ut_record *ut_current;
static _Thread_local ut_record ut_event;
static const char *const ut_names[UT_N64] = {"frame", "submission", "reserved_fence", "serial", "pool_generation", "storage_bytes", "logical_bytes", "copy_bytes", "dirty_lo", "dirty_hi", "total_ns", "protect_ns", "snapshot_ns", "scratch_ns", "deswizzle_ns", "mt_wait_ns", "copy_record_ns", "snapshot_bytes", "scratch_requested_bytes"};
static const char *const ut_unames[UT_N32] = {"reason", "format", "width", "height", "depth", "levels", "layers", "base_mip", "tasks", "parallel", "block_width", "block_height", "block_bytes"};
static void *ut_writer(void *arg) {
    (void)arg;
    pthread_setname_np(pthread_self(), "vk-upload-trace");
    for (;;) {
        pthread_mutex_lock(&UT.mu);
        while (atomic_load_explicit(&UT.state, memory_order_acquire) != UT_READY) pthread_cond_wait(&UT.cv, &UT.mu);
        pthread_mutex_unlock(&UT.mu);
        FILE *f = fopen(UT.path, "we"); int ok = f != NULL;
        if (f) {
            fprintf(f, "# upload-trace-v1 session=%" PRIu64 " token=%d first=%" PRIu64 " last=%" PRIu64 " count=%u end=%s coverage=recorded-prefix unknown=NA reasons=1:first,2:dirty,3:write,4:gpu,5:sparse,6:other mt_wait_is_subset_of_deswizzle=1\n", UT.session, UT.token, UT.first, UT.last, UT.count, UT.end);
            fputs("sequence", f);
            for (unsigned j = 0; j < UT_N64; j++) fprintf(f, "\t%s", ut_names[j]);
            for (unsigned j = 0; j < UT_N32; j++) fprintf(f, "\t%s", ut_unames[j]);
            fputc('\n', f);
            for (unsigned i = 0; i < UT.count; i++) {
                const ut_record *r = &UT.rows[i]; fprintf(f, "%u", i + 1);
                for (unsigned j = 0; j < UT_N64; j++) { if (r->v[j] == UINT64_MAX) fputs("\tNA", f); else fprintf(f, "\t%" PRIu64, r->v[j]); }
                for (unsigned j = 0; j < UT_N32; j++) { if (r->u[j] == UINT32_MAX) fputs("\tNA", f); else fprintf(f, "\t%u", r->u[j]); }
                fputc('\n', f);
            }
            ok = !ferror(f); if (fclose(f)) ok = 0;
        }
        VLOG("upload trace %s session=%llu count=%u path=%s", ok ? "saved" : "save failed", (unsigned long long)UT.session, UT.count, UT.path);
        atomic_store_explicit(&UT.state, UT_IDLE, memory_order_release);
    }
    return NULL;
}
// Called once per submission before the texture/pool locks. Only here may a session
// be published to the writer, or its configuration/path be changed.
static void ut_tick(void) {
    int token = VKA_ENV("DM_VK_UPLOAD_TRACE", 0);
    int state = atomic_load_explicit(&UT.state, memory_order_acquire);
    uint64_t frame = 0;
    if (state == UT_RECORDING || token > 0) frame = vka_exec_frame();
    ut_enabled = 0;
    if (state == UT_RECORDING) {
        if (UT.stop || token <= 0 || token != UT.seen || frame < UT.first || frame - UT.first >= UT_FRAMES) {
            UT.end = UT.stop ? "events" : token <= 0 ? "disabled" : token != UT.seen ? "rearm" : "frames";
            VLOG("upload trace ended session=%llu first=%llu last=%llu count=%u reason=%s coverage=recorded-prefix", (unsigned long long)UT.session, (unsigned long long)UT.first, (unsigned long long)UT.last, UT.count, UT.end);
            pthread_mutex_lock(&UT.mu); atomic_store_explicit(&UT.state, UT_READY, memory_order_release); pthread_cond_signal(&UT.cv); pthread_mutex_unlock(&UT.mu);
            state = UT_READY;
        } else { UT.last = frame; UT.sub++; ut_enabled = 1; }
    }
    if (token == UT.seen) return;
    UT.seen = token;
    if (token <= 0) return;
    if (state != UT_IDLE) { VLOG("upload trace arm rejected token=%d reason=busy; use a new token after saved", token); return; }
    if (!UT.writer) { pthread_t th; if (pthread_create(&th, NULL, ut_writer, NULL)) { VLOG("upload trace arm rejected token=%d reason=writer", token); return; } pthread_detach(th); UT.writer = 1; }
    const char *dir = getenv("DM_TEMP"); if (!dir || !*dir) { VLOG("upload trace arm rejected token=%d reason=no-DM_TEMP", token); return; }
    int n = snprintf(UT.path, sizeof UT.path, "%s/upload-trace-%d-%llu.tsv", dir, (int)getpid(), (unsigned long long)(UT.session + 1));
    if (n < 0 || (size_t)n >= sizeof UT.path) { VLOG("upload trace arm rejected token=%d reason=path", token); return; }
    UT.session++; UT.token = token; UT.first = UT.last = frame; UT.sub = 1; UT.count = 0; UT.stop = 0;
    atomic_store_explicit(&UT.state, UT_RECORDING, memory_order_release); ut_enabled = 1;
    VLOG("upload trace begin session=%llu frame=%llu frames=%u events=%u bytes=%zu path=%s", (unsigned long long)UT.session, (unsigned long long)frame, UT_FRAMES, UT_CAP, sizeof UT, UT.path);
}
// Sparse refresh can rebase the physical image after the outer cause starts.
// Refresh its actual shape at upload entry without losing that cause or timing.
static void ut_image(vka_img *im) {
    if (!ut_current) return;
    ut_record *r = ut_current;
    r->v[UT_SERIAL] = im->serial; r->v[UT_PGEN] = im->pgen; r->v[UT_STORAGE] = im->L.size;
    r->u[UT_FORMAT] = im->d.format;
    r->u[UT_W] = im->w; r->u[UT_H] = im->h; r->u[UT_D] = im->depth;
    r->u[UT_LEVELS] = im->levels; r->u[UT_LAYERS] = im->layers; r->u[UT_BASE] = im->base;
    r->u[UT_BW] = im->f.bw; r->u[UT_BH] = im->f.bh; r->u[UT_BPB] = im->f.bpb;
    uint64_t logical = 0;
    for (uint32_t lv = 0; lv < im->levels; lv++) {
        uint32_t w = im->w >> lv ? im->w >> lv : 1, h = im->h >> lv ? im->h >> lv : 1;
        uint32_t d = im->d.target == NVN_TEXTURE_TARGET_3D ? (im->depth >> lv ? im->depth >> lv : 1) : 1;
        logical += (uint64_t)((w + im->f.bw - 1) / im->f.bw) * ((h + im->f.bh - 1) / im->f.bh) * d * im->layers * im->f.bpb;
    }
    r->v[UT_LOGICAL] = logical;
}
static int ut_begin(vka_img *im, unsigned reason, int bounds, uint64_t lo, uint64_t hi) {
    if (!ut_enabled || ut_current || UT.stop) return 0;
    ut_record *r = &ut_event; memset(r, 0xff, sizeof *r);
    r->v[UT_FRAME] = UT.last; r->v[UT_SUB] = UT.sub;
    uint64_t fence = vka_exec_reserved_fence(); r->v[UT_FENCE] = fence ? fence : UINT64_MAX;
    r->v[UT_COPIED] = 0; r->v[UT_TOTAL] = now_ns();
    if (bounds) { r->v[UT_LO] = lo; r->v[UT_HI] = hi; }
    r->u[UT_REASON] = reason; ut_current = r; ut_image(im); return 1;
}
static void ut_end(int owned, int keep) {
    if (!owned) return;
    ut_record *r = ut_current; r->v[UT_TOTAL] = now_ns() - r->v[UT_TOTAL]; ut_current = NULL;
    if (!keep) return;
    UT.rows[UT.count++] = *r;
    if (UT.count == UT_CAP) UT.stop = 1;
}
static void ut_add(unsigned field, uint64_t n) {
    if (!ut_current) return;
    uint64_t *v = &ut_current->v[field]; if (*v == UINT64_MAX) *v = 0; *v += n;
}
#define UT_TIME(field, expr) ({ uint64_t ut_t_ = ut_current ? now_ns() : 0; __auto_type ut_r_ = (expr); if (ut_current) ut_add(field, now_ns() - ut_t_); ut_r_; })
#define UT_VOID(field, expr) do { uint64_t ut_t_ = ut_current ? now_ns() : 0; expr; if (ut_current) ut_add(field, now_ns() - ut_t_); } while (0)
static int ut_scratch(VkDeviceSize n, VkDeviceSize align, vka_span *s) {
    int ok = UT_TIME(UT_SCRATCH, vka_scratch(n, align, s)); if (ok) ut_add(UT_SCRATCH_BYTES, n); return ok;
}
static void ut_copy(VkCommandBuffer cb, VkBuffer b, VkImage im, VkImageLayout layout, uint32_t n, const VkBufferImageCopy *regions) {
    UT_VOID(UT_COPY, vk.vkCmdCopyBufferToImage(cb, b, im, layout, n, regions));
    if (!ut_current) return;
    const ut_record *r = ut_current; uint32_t bw = r->u[UT_BW], bh = r->u[UT_BH];
    for (uint32_t i = 0; i < n; i++) { const VkBufferImageCopy *c = &regions[i];
        ut_add(UT_COPIED, (uint64_t)((c->imageExtent.width + bw - 1) / bw) * ((c->imageExtent.height + bh - 1) / bh) * c->imageExtent.depth * c->imageSubresource.layerCount * r->u[UT_BPB]); }
}
