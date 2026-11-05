// Bounded diagnostic GPU snapshots. Copies join the existing command stream; only its
// successful completion fence permits CPU reads or destruction of staging storage.
#if defined(__ANDROID__)
#include "vka_capture.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

enum { BT_SLOTS = 16, BT_BYTES = 65536, BT_MAX_MEMORY = 8 * 1024 * 1024 };
typedef struct {
    vka_buffer_sample source;
    uint64_t prefix, fence, session;
    uint32_t bytes, requested, sequence;
} bt_sample;
typedef struct bt_packet {
    struct bt_packet *next;
    vcap_hbuf h;
    unsigned n, stride;
    char dir[1024];
    bt_sample samples[BT_SLOTS];
} bt_packet;
static bt_packet *g_bt_pending, *g_bt_tail;
static _Atomic uint64_t g_bt_live_bytes;
static _Atomic int g_bt_failed;
void vka_buffer_trace_failed(void) { atomic_store(&g_bt_failed, 1); }
static pthread_mutex_t g_bt_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_bt_cv = PTHREAD_COND_INITIALIZER;
static bt_packet *g_bt_ready, *g_bt_ready_tail;
static int g_bt_writer;
static struct { uint64_t key; uint32_t phase, kind, reg, bytes; } g_bt_sel[8];
static unsigned g_bt_nsel, g_bt_count, g_bt_max_count, g_bt_frames;
static uint64_t g_bt_total, g_bt_first = UINT64_MAX, g_bt_checked = UINT64_MAX, g_bt_session;
static char g_bt_config[256], g_bt_dir[1024];
static int g_bt_ended;
static const char *bt_phase(uint32_t phase) { return phase == 2 ? "post_draw" : phase ? "post" : "pre"; }

// Account the driver's actual allocation size, not just the requested buffer size.
// Recording is single-threaded; the writer only subtracts live allocations.
static int bt_staging_make(vcap_hbuf *h, VkDeviceSize n) {
    memset(h, 0, sizeof *h);
    VkBufferCreateInfo bi = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = n, .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT};
    if (vk.vkCreateBuffer(V.dev, &bi, NULL, &h->b)) return 0;
    VkMemoryRequirements mr; vk.vkGetBufferMemoryRequirements(V.dev, h->b, &mr);
    if (mr.size > BT_MAX_MEMORY || atomic_load(&g_bt_live_bytes) > BT_MAX_MEMORY - mr.size) { vcap_hbuf_free(h); return 0; }
    uint32_t t = vka_mem_type(mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT, 0);
    if (t == UINT32_MAX) t = vka_mem_type(mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT, 0);
    VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = mr.size, .memoryTypeIndex = t};
    void *mapped = NULL;
    if (t == UINT32_MAX || vk.vkAllocateMemory(V.dev, &ai, NULL, &h->m) || vk.vkBindBufferMemory(V.dev, h->b, h->m, 0) ||
        vk.vkMapMemory(V.dev, h->m, 0, VK_WHOLE_SIZE, 0, &mapped)) { vcap_hbuf_free(h); return 0; }
    h->p = mapped; h->n = mr.size;
    atomic_fetch_add(&g_bt_live_bytes, h->n);
    return 1;
}
static int bt_directory(const char *path) {
    char buf[1024]; snprintf(buf, sizeof buf, "%s", path);
    for (char *p = buf + 1; *p; p++) if (*p == '/') { *p = 0; if (mkdir(buf, 0700) && errno != EEXIST) return 0; *p = '/'; }
    return !mkdir(buf, 0700) || errno == EEXIST;
}
static void bt_write_packet(bt_packet *p) {
    int directory = bt_directory(p->dir);
    for (unsigned i = 0; i < p->n; i++) {
        const bt_sample *s = &p->samples[i]; const vka_buffer_sample *r = &s->source;
        const uint8_t *bytes = p->h.p + (size_t)i * p->stride;
        char path[1280]; snprintf(path, sizeof path, "%s/f%llu-k%016llx-d%llu-%s%u-%s-a%llx-s%llu-n%u.bin", p->dir,
            (unsigned long long)r->frame, (unsigned long long)r->key, (unsigned long long)r->ordinal, r->kind ? "ubo" : "ssbo", r->reg,
            bt_phase(r->phase), (unsigned long long)r->guest, (unsigned long long)s->session, s->sequence);
        uint64_t hash = 0xcbf29ce484222325ull; for (unsigned j = 0; j < s->bytes; j++) hash = (hash ^ bytes[j]) * 0x100000001b3ull;
        FILE *f = directory ? fopen(path, "wb") : NULL; int ok = f && fwrite(bytes, 1, s->bytes, f) == s->bytes; if (f && fclose(f)) ok = 0;
        char meta[1290]; snprintf(meta, sizeof meta, "%s.tsv", path); f = directory ? fopen(meta, "w") : NULL;
        if (f) {
            if (fprintf(f, "frame\tkey\tordinal\tphase\tkind\treg\tbinding\telement\tguest\tsource_size\tresolved_guest\tresolved_size\tbuffer\tdescriptor_offset\tdescriptor_range\tprefix\trequested\tbytes\tfence\thash\tsession\tsequence\tstage\tvertex_key\tvariant\n"
                "%llu\t%016llx\t%llu\t%s\t%s\t%u\t%u\t%u\t%#llx\t%llu\t%#llx\t%llu\t%#llx\t%llu\t%llu\t%llu\t%u\t%u\t%llu\t%016llx\t%llu\t%u\t%u\t%016llx\t%u\n",
                (unsigned long long)r->frame, (unsigned long long)r->key, (unsigned long long)r->ordinal, bt_phase(r->phase), r->kind ? "ubo" : "ssbo", r->reg, r->binding, r->element,
                (unsigned long long)r->guest, (unsigned long long)r->source_size, (unsigned long long)r->resolved_guest, (unsigned long long)r->resolved_size,
                (unsigned long long)(uintptr_t)r->descriptor.buffer, (unsigned long long)r->descriptor.offset, (unsigned long long)r->descriptor.range,
                (unsigned long long)s->prefix, s->requested, s->bytes, (unsigned long long)s->fence, (unsigned long long)hash, (unsigned long long)s->session, s->sequence, r->stage, (unsigned long long)r->vertex_key, r->variant) < 0) ok = 0;
            if (fclose(f)) ok = 0;
        } else ok = 0;
        if (ok) VLOG("buffer trace saved frame=%llu key=%016llx %s=%llu phase=%s %s%u bytes=%u/%u fence=%llu hash=%016llx path=%s",
            (unsigned long long)r->frame, (unsigned long long)r->key, r->phase == 2 ? "draw" : "dispatch", (unsigned long long)r->ordinal, bt_phase(r->phase), r->kind ? "ubo" : "ssbo", r->reg,
            s->bytes, s->requested, (unsigned long long)s->fence, (unsigned long long)hash, path);
        else VRED("buffer trace: could not write %s and its metadata", path);
    }
}
static void *bt_writer(void *unused) {
    (void)unused;
    for (;;) {
        pthread_mutex_lock(&g_bt_mu);
        while (!g_bt_ready) pthread_cond_wait(&g_bt_cv, &g_bt_mu);
        bt_packet *p = g_bt_ready; g_bt_ready = p->next; if (!g_bt_ready) g_bt_ready_tail = NULL;
        pthread_mutex_unlock(&g_bt_mu);
        // bt_complete only queues storage whose exact fence completed successfully.
        bt_write_packet(p);
        uint64_t n = p->h.n; vcap_hbuf_free(&p->h); free(p); atomic_fetch_sub(&g_bt_live_bytes, n);
    }
    return NULL;
}
static void bt_complete(void *arg) {
    bt_packet *p = arg;
    // Device loss publishes CPU fences without GPU completion. Retain bounded storage:
    // neither mapping reads nor destruction is safe while its copies may still run.
    if (vka_hung() || atomic_load(&g_bt_failed)) { VRED("buffer trace: GPU completion invalid; %u snapshots discarded, staging retained", p->n); return; }
    p->next = NULL;
    pthread_mutex_lock(&g_bt_mu); if (g_bt_ready_tail) g_bt_ready_tail->next = p; else g_bt_ready = p; g_bt_ready_tail = p;
    pthread_cond_signal(&g_bt_cv); pthread_mutex_unlock(&g_bt_mu);
}
void vka_buffer_trace_commit(uint64_t fence, int submitted) {
    if (!submitted) vka_buffer_trace_failed();
    bt_packet *p = g_bt_pending; g_bt_pending = g_bt_tail = NULL;
    while (p) {
        bt_packet *next = p->next;
        for (unsigned i = 0; i < p->n; i++) p->samples[i].fence = fence;
        if (submitted > 0) vka_after(fence, bt_complete, p);
        else VRED("buffer trace: submission %llu did not complete on GPU; %u snapshots discarded, staging retained", (unsigned long long)fence, p->n);
        p = next;
    }
}
static void bt_configure(const char *config) {
    snprintf(g_bt_config, sizeof g_bt_config, "%s", config);
    g_bt_nsel = g_bt_count = 0; g_bt_total = 0; g_bt_first = UINT64_MAX; g_bt_ended = 0; g_bt_session++;
    const char *s = config; int bad = strlen(config) == sizeof g_bt_config - 1;
    if (bad) s = "";   // a truncated live setting must never arm a partial selection
    while (*s) {
        if (g_bt_nsel == 8) { bad = 1; break; }
        char *end; errno = 0; uint64_t key = strtoull(s, &end, 16);
        if (errno || end == s || *end != ':' || *s == '-' || *s == '+') { bad = 1; break; } s = end + 1;
        unsigned phase, kind;
        if (!strncmp(s, "pre:", 4)) { phase = 0; s += 4; } else if (!strncmp(s, "post:", 5)) { phase = 1; s += 5; } else if (!strncmp(s, "draw:", 5)) { phase = 2; s += 5; } else { bad = 1; break; }
        if (!strncmp(s, "ssbo", 4)) { kind = 0; s += 4; } else if (!strncmp(s, "ubo", 3)) { kind = 1; s += 3; } else { bad = 1; break; }
        unsigned long reg = strtoul(s, &end, 10); if (end == s || reg > 15 || *s < '0' || *s > '9') { bad = 1; break; } s = end;
        unsigned long bytes = phase == 2 ? 512 : 224;
        if (*s == ':') { s++; errno = 0; bytes = strtoul(s, &end, 10); if (errno || end == s || *s < '0' || *s > '9') { bad = 1; break; } s = end; }
        if (bytes < 4 || bytes > BT_BYTES || (bytes & 3) || (*s && *s != ',')) { bad = 1; break; }
        if (phase == 2 && (kind != 1 || bytes > 512)) { bad = 1; break; }
        // Reject duplicate selectors instead of silently producing ambiguous byte lengths.
        for (unsigned i = 0; i < g_bt_nsel; i++) if (g_bt_sel[i].key == key && g_bt_sel[i].phase == phase && g_bt_sel[i].kind == kind && g_bt_sel[i].reg == reg) bad = 1;
        if (bad) break;
        g_bt_sel[g_bt_nsel++] = (typeof(g_bt_sel[0])){key, phase, kind, (uint32_t)reg, (uint32_t)bytes};
        if (*s == ',' && !*++s) { bad = 1; break; }
    }
    if (bad) { g_bt_nsel = 0; VRED("buffer trace: expected key:pre|post:ubo|ssbo<0..15>[:bytes4..65536] or key:draw:ubo<0..15>[:bytes4..512], at most 8 selectors"); }
    int n = VKA_ENV("DM_VK_BUFFER_TRACE_FRAMES", 120); g_bt_frames = n < 1 ? 1u : n > 120 ? 120u : (unsigned)n;
    for (unsigned i = 0; i < g_bt_nsel; i++) if (g_bt_sel[i].phase == 2 && g_bt_frames > 64) g_bt_frames = 64;
    n = VKA_ENV("DM_VK_BUFFER_TRACE_COUNT", 1024); g_bt_max_count = n < 1 ? 1u : n > 1024 ? 1024u : (unsigned)n;
    g_bt_dir[0] = 0; vka_env_str("DM_VK_BUFFER_TRACE_DIR", g_bt_dir, sizeof g_bt_dir);
    if (g_bt_nsel && (!g_bt_dir[0] || g_bt_dir[0] != '/')) { g_bt_nsel = 0; VRED("buffer trace: set DIR to an absolute private game-work folder before arming"); }
    if (g_bt_nsel && !g_bt_writer) { pthread_t t; if (!pthread_create(&t, NULL, bt_writer, NULL)) { pthread_detach(t); g_bt_writer = 1; } else { g_bt_nsel = 0; VRED("buffer trace: writer thread not started"); } }
    if (g_bt_nsel) VLOG("buffer trace armed session=%llu selectors=%u frames=%u count=%u byte-limit=%u dir=%s", (unsigned long long)g_bt_session,
        g_bt_nsel, g_bt_frames, g_bt_max_count, BT_MAX_MEMORY, g_bt_dir);
}
uint64_t vka_buffer_trace_mask(uint64_t frame, uint64_t key, int phase) {
    if (g_bt_checked != frame) {
        g_bt_checked = frame; char config[256] = {0}; vka_env_str("DM_VK_BUFFER_TRACE", config, sizeof config);
        if (strcmp(config, g_bt_config)) bt_configure(config);
    }
    if (!g_bt_nsel || g_bt_ended || atomic_load(&g_bt_failed) || vka_hung()) return 0;
    if (g_bt_first != UINT64_MAX && (frame < g_bt_first || frame - g_bt_first >= g_bt_frames || g_bt_count >= g_bt_max_count || g_bt_total >= BT_MAX_MEMORY)) {
        g_bt_ended = 1; VLOG("buffer trace ended session=%llu snapshots=%u bytes=%llu first=%llu end=%llu", (unsigned long long)g_bt_session, g_bt_count,
            (unsigned long long)g_bt_total, (unsigned long long)g_bt_first, (unsigned long long)frame); return 0;
    }
    uint64_t mask = 0;
    for (unsigned i = 0; i < g_bt_nsel; i++) if (g_bt_sel[i].key == key && g_bt_sel[i].phase == (uint32_t)phase) mask |= 1ull << (g_bt_sel[i].kind * 16 + g_bt_sel[i].reg);
    if (mask && g_bt_first == UINT64_MAX) g_bt_first = frame;
    return mask;
}
void vka_buffer_trace_copy(VkCommandBuffer cb, const vka_buffer_sample *s, uint64_t prefix) {
    if (!cb || !s->descriptor.buffer || s->descriptor.range == VK_WHOLE_SIZE || g_bt_count >= g_bt_max_count || g_bt_ended || vka_hung() || atomic_load(&g_bt_failed)) return;
    unsigned requested = 0;
    for (unsigned i = 0; i < g_bt_nsel; i++) if (g_bt_sel[i].key == s->key && g_bt_sel[i].phase == s->phase && g_bt_sel[i].kind == s->kind && g_bt_sel[i].reg == s->reg) requested = g_bt_sel[i].bytes;
    if (!requested || prefix >= s->descriptor.range || s->descriptor.offset > UINT64_MAX - prefix || ((s->descriptor.offset + prefix) & 3)) return;
    uint64_t size = s->descriptor.range - prefix;
    if (s->source_size && size > s->source_size) size = s->source_size;
    unsigned n = size < requested ? (unsigned)size & ~3u : requested;
    if (!n) return;
    // Cumulative data as well as live driver allocations have an 8MiB bound. Do not
    // shorten a requested snapshot at this boundary: an omitted record is unambiguous.
    if (g_bt_total > BT_MAX_MEMORY - n) { g_bt_ended = 1; VLOG("buffer trace ended: 8MiB capture byte limit"); return; }
    bt_packet *p = g_bt_tail;
    if (!p || p->n == BT_SLOTS || p->stride < n || p->samples[0].session != g_bt_session) {
        p = calloc(1, sizeof *p); if (!p) return;
        if (!bt_staging_make(&p->h, (uint64_t)BT_SLOTS * n)) { free(p); VRED_ONCE("buffer trace: staging unavailable or 8MiB live bound reached; snapshot omitted without waiting"); return; }
        p->stride = n; snprintf(p->dir, sizeof p->dir, "%s", g_bt_dir);
        if (g_bt_tail) g_bt_tail->next = p; else g_bt_pending = p; g_bt_tail = p;
    }
    VkBufferMemoryBarrier src = {VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT, .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .buffer = s->descriptor.buffer, .offset = s->descriptor.offset + prefix, .size = n};
    vk.vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 1, &src, 0, NULL);
    VkBufferCopy copy = {s->descriptor.offset + prefix, (VkDeviceSize)p->n * p->stride, n}; vk.vkCmdCopyBuffer(cb, s->descriptor.buffer, p->h.b, 1, &copy);
    VkBufferMemoryBarrier dst = {VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .buffer = p->h.b, .offset = copy.dstOffset, .size = n};
    // TRANSFER -> ALL_COMMANDS is also the execution dependency protecting source
    // reads against subsequent shader/transfer overwrites (WAR), including across parts.
    vk.vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT | VK_PIPELINE_STAGE_ALL_COMMANDS_BIT, 0, 0, NULL, 1, &dst, 0, NULL);
    p->samples[p->n++] = (bt_sample){*s, prefix, 0, g_bt_session, n, requested, g_bt_count++};
    g_bt_total += n;
}
#endif
