// The nn::codec AV1 decoder (hle_codec_video.c with NX_DAV1D, hle_av1_dav1d.c, dav1d) driven through its SDK take-overs as Zelda: Ocarina of Time's movie library calls them
// (SDK 22.2.8): ImagePool → Initialize…ByTransferMemory / …ByRemoteMemory → Register → Acquire / Release / TryWaitNativeSync → Submit (temporal units, end of stream on the last)
// → GetAv1DecoderOutputBufferProperties (3604 until the buffer holds a picture) → ReadDecoderOutputImageNv12 → Finalize. The pictures are compared with ffmpeg's dav1d bit for bit.
// argv: an IVF file of AV1 (its temporal delimiters are removed, as Matroska does), its I420 frames decoded by ffmpeg's libdav1d, width, height.
// Compiled with the production codec source behind a POSIX shim (dm/tools/tests/test_codec_av1.py), AddressSanitizer and UBSan; libvpx's decoders and the H.264 path are
// stubbed (never reached by AV1), libvpx's vpx_image.c and hle_movie_yuv.c (the NV12 converter) are the real ones
#include CODEC_SOURCE
#include <assert.h>

// ---- the shim's platform functions: a SystemEvent double and the two nn::os calls the codec looks up
typedef struct { int bound, autoclear, signalled; } test_event;
void hle_bind_system_event(void *e, int autoclear) { *(test_event *)e = (test_event){1, autoclear, 0}; }
void hle_signal_system_event(void *e) { assert(((test_event *)e)->bound); ((test_event *)e)->signalled = 1; }
void hle_clear_system_event(void *e) { ((test_event *)e)->signalled = 0; }
static bool poll_event(void *e) { test_event *v = e; assert(v->bound); bool set = v->signalled; if (v->autoclear) v->signalled = 0; return set; }
static void destroy_event(void *e) { *(test_event *)e = (test_event){0}; }
void *guest_sym(const char *name) {
    if (!strcmp(name, "_ZN2nn2os18TryWaitSystemEventEPNS0_15SystemEventTypeE")) return poll_event;
    if (!strcmp(name, "_ZN2nn2os18DestroySystemEventEPNS0_15SystemEventTypeE")) return destroy_event;
    abort();
}
// ---- never reached by AV1: libvpx's decoders and the H.264 decoder
vpx_codec_err_t vpx_codec_dec_init_ver(vpx_codec_ctx_t *c, vpx_codec_iface_t *i, const vpx_codec_dec_cfg_t *cfg, vpx_codec_flags_t f, int v) { (void)c; (void)i; (void)cfg; (void)f; (void)v; abort(); }
vpx_codec_iface_t *vpx_codec_vp8_dx(void) { abort(); }
vpx_codec_iface_t *vpx_codec_vp9_dx(void) { abort(); }
vpx_codec_err_t vpx_codec_decode(vpx_codec_ctx_t *c, const uint8_t *d, unsigned int n, void *u, long dl) { (void)c; (void)d; (void)n; (void)u; (void)dl; abort(); }
vpx_image_t *vpx_codec_get_frame(vpx_codec_ctx_t *c, vpx_codec_iter_t *it) { (void)c; (void)it; abort(); }
vpx_codec_err_t vpx_codec_destroy(vpx_codec_ctx_t *c) { (void)c; abort(); }
const char *vpx_codec_error(const vpx_codec_ctx_t *c) { (void)c; abort(); }
const char *vpx_codec_error_detail(const vpx_codec_ctx_t *c) { (void)c; abort(); }
void *vpx_memalign(size_t align, size_t size) { void *p = NULL; return posix_memalign(&p, align < sizeof(void *) ? sizeof(void *) : align, size ? size : 1) ? NULL : p; }
void vpx_free(void *p) { free(p); }
mavc *mavc_new(void) { abort(); }
void mavc_free(mavc *a) { (void)a; abort(); }
void mavc_param_sets(mavc *a, const uint8_t *p, size_t n) { (void)a; (void)p; (void)n; abort(); }
int mavc_decode(mavc *a, const uint8_t *p, int n, int64_t t) { (void)a; (void)p; (void)n; (void)t; abort(); }
int mavc_take(mavc *a, int f, uint8_t *o, int w, int h, int64_t *t) { (void)a; (void)f; (void)o; (void)w; (void)h; (void)t; abort(); }
void mavc_drop(mavc *a) { (void)a; abort(); }

// ---- the stream
static uint8_t *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb"); assert(f); fseek(f, 0, SEEK_END); *n = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(*n + 1); assert(b && fread(b, 1, *n, f) == *n); fclose(f); return b;
}
static size_t leb(const uint8_t *p, size_t n, size_t *len) {
    size_t v = 0; *len = 0;
    for (int i = 0; i < 8 && (size_t)i < n; i++) { v |= (size_t)(p[i] & 0x7f) << (7 * i); (*len)++; if (!(p[i] & 0x80)) break; }
    return v;
}
static size_t strip_td(const uint8_t *in, size_t n, uint8_t *out) {   // the unit without its temporal delimiter OBUs (type 2)
    size_t o = 0, i = 0;
    while (i < n) {
        uint8_t h = in[i]; int type = (h >> 3) & 15, ext = (h >> 2) & 1; assert(h & 2);
        size_t hl = 1 + (size_t)ext, ll, sz = leb(in + i + hl, n - i - hl, &ll), tot = hl + ll + sz;
        assert(i + tot <= n);
        if (type != 2) { memcpy(out + o, in + i, tot); o += tot; }
        i += tot;
    }
    return o;
}
typedef struct { uint8_t *d; size_t n; } unit;
static unit *units; static int nunits;
static const uint8_t *ref; static size_t nref; static unsigned W, H;
#define STEP 16666667ll                         // 60 fps, in ns (TimeSpan)
#define TS(pass, k) ((int64_t)(pass) * 1000000000000ll + (int64_t)(k) * STEP + 7)

// ---- the guest's objects (as the SDK's types: the decoder 0x260 bytes with the host's event at +0x100, the pools 0x40)
#define NBUF 4
typedef struct { _Alignas(16) uint8_t t[0x260]; _Alignas(16) uint8_t pool[NBUF][0x40]; uint8_t *mem[NBUF]; uint8_t *y, *uv, *nv12; int contiguous; } guest;
static void pools_make(guest *g) {
    cv_pref pref = h_cv_pref21((uint16_t)W, (uint16_t)H, 3, 0); uint64_t size, align;
    assert(!h_cv_poolsize(&size, &align, &pref, 1) && size >= (uint64_t)W * H * 3 / 2 && align == 0x1000);
    for (int i = 0; i < NBUF; i++) { g->mem[i] = malloc(size); assert(!h_cv_pool21(g->pool[i], g->mem[i], size, &pref, 1) && pool_of(g->pool[i])); }
    g->nv12 = malloc((size_t)W * H * 3 / 2); g->y = malloc((size_t)W * H); g->uv = malloc((size_t)W * H / 2);
}
static void pools_free(guest *g) { for (int i = 0; i < NBUF; i++) { h_cv_poolfin(g->pool[i]); assert(!pool_of(g->pool[i])); free(g->mem[i]); } free(g->nv12); free(g->y); free(g->uv); }
static void init(guest *g, int variant, const char *threads) {
    if (threads) setenv("DM_AV1_THREADS", threads, 1); else unsetenv("DM_AV1_THREADS");
    memset(g->t, 0, sizeof g->t);   // the SDK's Initialize clears the type before it calls the service
    static uint8_t work[0x1000];
    Result r = variant ? h_cv_av1init21r(g->t, 4u << 20, (uint16_t)W, (uint16_t)H, 8, 0, 3, 1) : h_cv_av1init21(g->t, work, sizeof work, 4u << 20, (uint16_t)W, (uint16_t)H, 8, 0, 3, 1);
    assert(!r);
    cv_dec *d = cv_of(g->t); assert(d && d->codec == CV_AV1 && d->av1 && d->w == W && d->h == H);
    assert(d->av1_threads == (threads ? atoi(threads) : 2));
    void *ev = h_cv_av1event21(g->t); assert(ev == g->t + CV_EVENT_OFF && poll_event(ev));   // signalled: the decoder takes packets
    void *ev2 = NULL; assert(!h_cv_av1event(&ev2, g->t) && ev2 == ev);
    for (uint32_t i = 0; i < NBUF; i++) assert(!h_cv_av1reg(g->t, i, g->pool[i], 0));
    assert(!h_cv_av1start(g->t));
}
// one picture against reference frame k: properties, then the NV12 read (into one buffer, or two apart, the player's layout)
static void check_picture(guest *g, uint32_t idx, int64_t ts, bool eos, int k) {
    assert(k >= 0 && (size_t)k < nref);
    const uint8_t *f = ref + (size_t)k * W * H * 3 / 2, *fu = f + (size_t)W * H, *fv = fu + (size_t)(W / 2) * (H / 2);
    uint8_t *y = g->contiguous ? g->nv12 : g->y, *uv = g->contiguous ? g->nv12 + (size_t)W * H : g->uv;
    memset(y, 0xee, (size_t)W * H); memset(uv, 0xee, (size_t)W * H / 2);
    assert(!h_cv_nv12(g->pool[idx], 0, 0, 0, (uint16_t)W, (uint16_t)H, y, W, (uint64_t)W * H, uv, W, (uint64_t)W * H / 2));
    assert(!memcmp(y, f, (size_t)W * H));
    for (unsigned r = 0; r < H / 2; r++) for (unsigned x = 0; x < W / 2; x++) {
        size_t o = (size_t)r * W + 2 * x, s = (size_t)r * (W / 2) + x;
        if (uv[o] != fu[s] || uv[o + 1] != fv[s]) { fprintf(stderr, "picture %d: UV differs at %u,%u\n", k, x, r); abort(); }
    }
    g->contiguous ^= 1;
    // the pool keeps the stream's colour space (BT.709, limited range: the sequence header's); the colour-space overload answers the same, with 0 for the colour space
    const vpx_image_t *im = pool_of(g->pool[idx])->img[0]; assert(im && im->cs == VPX_CS_BT_709 && im->range == VPX_CR_STUDIO_RANGE);
    uint16_t w = 0, h = 0, cw = 0, ch = 0; uint32_t cs = 9; int64_t t2 = 0; bool e2 = !eos;
    assert(!h_cv_av1props_cs(&w, &h, &cw, &ch, &cs, &t2, &e2, g->t, idx) && w == W && h == H && cw == W && ch == H && cs == 0 && t2 == ts && e2 == eos);
}
// The player: one buffer acquired at a time; once its sync handle is reached its properties tell whether it holds a picture (3604 until then, outputs untouched), it is
// read and released, and packets are submitted while the awaited buffer is not ready. stop_after >= 0: stops after that many pictures of this pass (a seek), without the
// end of stream. Returns the pictures of this pass that were checked
static int play(guest *g, int pass, int stop_after, int *busy) {
    int next = 0, seen = 0, iters = 0; bool done = false, have = false; uint32_t idx = 0; cv_sync sync;
    while (!done) {
        assert(++iters < 100000);
        if (!have) { assert(!h_cv_av1acq(&idx, &sync, g->t)); have = true; }
        if (h_gs_trywait(&sync)) {
            uint16_t w = 1, h = 2, cw = 3, ch = 4; int64_t ts = 5; bool eos = false;
            Result r = h_cv_av1props(&w, &h, &cw, &ch, &ts, &eos, g->t, idx);
            if (!r) {
                assert(w == W && h == H && cw == W && ch == H);
                if (ts / 1000000000000ll == pass) {   // a picture of an earlier pass (decoded before the seek, given to a buffer before it) is skipped, as a player does by its time
                    int k = (int)((ts % 1000000000000ll - 7) / STEP);
                    assert(k == seen && ts == TS(pass, k));   // in display order, each with the time of the unit that showed it (dav1d's, not the call's)
                    check_picture(g, idx, ts, eos, k); seen++;
                    if (eos) { assert((size_t)seen == nref); done = true; }
                    if (stop_after >= 0 && seen == stop_after) done = true;
                } else assert(ts / 1000000000000ll < pass && !eos);
            } else { assert(r == CV_NONE && w == 1 && h == 2 && cw == 3 && ch == 4 && ts == 5 && !eos); }
            h_cv_av1rel(g->t, idx, sync); have = false;
            continue;
        }
        assert(next < nunits);   // the awaited buffer is not filled while packets remain: never stalls
        uint64_t id = 0; Result r = h_cv_av1submit(&id, g->t, units[next].d, units[next].n, TS(pass, next), next == nunits - 1);
        if (r == CV_BUSY) { (*busy)++; assert(!poll_event(g->t + CV_EVENT_OFF)); continue; }
        assert(!r && id > 0); next++;
    }
    if (have) h_cv_av1rel(g->t, idx, sync);
    return seen;
}
static void run(int variant, const char *threads) {
    guest *g = calloc(1, sizeof *g); pools_make(g);
    init(g, variant, threads);
    int busy = 0;
    // before any packet: the registered buffers are acquirable at once, none holds a picture
    { uint16_t w = 1, h = 2, cw = 3, ch = 4; int64_t ts = 5; bool eos = true;
      assert(h_cv_av1props(&w, &h, &cw, &ch, &ts, &eos, g->t, 0) == CV_NONE && w == 1 && h == 2 && cw == 3 && ch == 4 && ts == 5 && eos);
      assert(h_cv_av1props(&w, &h, &cw, &ch, &ts, &eos, NULL, 0) == CV_R(2004) && h_cv_av1props(&w, &h, &cw, &ch, &ts, &eos, g->t, CV_BUFS) == CV_R(2004)); }
    assert(play(g, 1, -1, &busy) == (int)nref);                  // the whole stream, end of stream on the last picture
    // a seek: Flush in the middle of the stream, then the stream again from its first key frame (the title screen's loop)
    assert(!h_cv_av1flush(g->t));
    assert(play(g, 2, (int)nref / 2, &busy) == (int)nref / 2);
    assert(!h_cv_av1flush(g->t));
    assert(play(g, 3, -1, &busy) == (int)nref);
    assert(!h_cv_av1stop(g->t));
    assert(play(g, 4, -1, &busy) == (int)nref);
    // garbage: a red line, the decoder goes on
    { uint8_t junk[64]; memset(junk, 0xff, sizeof junk); uint64_t id; assert(!h_cv_av1submit(&id, g->t, junk, sizeof junk, 0, false)); }
    assert(!h_cv_av1unreg(g->t, 3) && h_cv_av1unreg(g->t, CV_BUFS) == CV_R(2004) && h_cv_av1reg(g->t, CV_BUFS, g->pool[0], 0) == CV_R(2004));
    assert(h_cv_av1enc() == CV_R(2004));
    assert(!h_cv_av1fin(g->t) && !cv_of(g->t) && h_cv_av1event21(g->t) == NULL && !h_cv_av1fin(g->t));
    printf("variant %s, DM_AV1_THREADS %s: %zu pictures x 4 passes identical, %d busy answers\n", variant ? "ByRemoteMemory" : "ByTransferMemory", threads ? threads : "unset (2)", nref, busy);
    // packets refused while 4 pictures wait for a buffer: Submit answers 3603 without taking the packet, the submission-ready event is cleared; Finalize frees what waits
    init(g, variant, threads);
    for (uint32_t i = 0; i < NBUF; i++) assert(!h_cv_av1unreg(g->t, i));
    int taken = 0; Result r = 0; uint64_t id;
    for (int i = 0; i < nunits && (r = h_cv_av1submit(&id, g->t, units[i].d, units[i].n, TS(5, i), false)) == 0; i++) taken++;
    assert(r == CV_BUSY && taken >= CV_HOLD && cv_of(g->t)->nheld == CV_HOLD && !poll_event(g->t + CV_EVENT_OFF));
    assert(!h_cv_av1fin(g->t) && !cv_of(g->t));
    pools_free(g); free(g);
}
int main(int argc, char **argv) {
    assert(argc == 5);
    size_t n, rn; uint8_t *ivf = slurp(argv[1], &n), *r = slurp(argv[2], &rn);
    W = (unsigned)atoi(argv[3]); H = (unsigned)atoi(argv[4]); size_t frame = (size_t)W * H * 3 / 2;
    assert(n > 32 && !memcmp(ivf, "DKIF", 4) && rn % frame == 0);
    ref = r; nref = rn / frame;
    units = calloc(4096, sizeof *units);
    for (size_t i = 32; i + 12 <= n;) {
        size_t sz = ivf[i] | ivf[i + 1] << 8 | ivf[i + 2] << 16 | (size_t)ivf[i + 3] << 24; i += 12; assert(i + sz <= n);
        units[nunits].d = malloc(sz); units[nunits].n = strip_td(ivf + i, sz, units[nunits].d); nunits++; i += sz;
    }
    assert((size_t)nunits == nref);   // one shown picture a unit (hidden frames ride in the unit of the picture that shows them)
    printf("%d units, %zu reference pictures %ux%u\n", nunits, nref, W, H);
    run(0, NULL);   // the default: 2 threads
    run(1, "1");    // one thread: dav1d decodes on the caller's thread
    if (W * H <= 640 * 360) { run(0, "4"); run(1, "0"); }
    for (int i = 0; i < nunits; i++) free(units[i].d);
    free(units); free(ivf); free(r);
    puts("t_codec_av1: passed");
    return 0;
}
