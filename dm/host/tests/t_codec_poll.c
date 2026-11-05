// Compile the actual codec implementation with a minimal POSIX host shim on Linux.
// The shim substitutes platform declarations only; codec bodies are unchanged.
#include CODEC_SOURCE
#include <assert.h>

typedef struct { int bound, autoclear, signalled; } test_event;
static _Atomic unsigned polls;
void hle_bind_system_event(void *e, int autoclear) { *(test_event *)e = (test_event){1, autoclear, 0}; }
void hle_signal_system_event(void *e) { assert(((test_event *)e)->bound); ((test_event *)e)->signalled = 1; }
void hle_clear_system_event(void *e) { ((test_event *)e)->signalled = 0; }
static bool poll_event(void *e) {
    test_event *v = e; assert(v->bound); atomic_fetch_add(&polls, 1);
    bool set = v->signalled; if (v->autoclear) v->signalled = 0; return set;
}
static void destroy_event(void *e) { *(test_event *)e = (test_event){0}; }
void *guest_sym(const char *name) {
    if (!strcmp(name, "_ZN2nn2os18TryWaitSystemEventEPNS0_15SystemEventTypeE")) return poll_event;
    if (!strcmp(name, "_ZN2nn2os18DestroySystemEventEPNS0_15SystemEventTypeE")) return destroy_event;
    abort();
}
// Ensure the const event-pointer / bool return ABI does not drift.
_Static_assert(__builtin_types_compatible_p(__typeof__(&h_spt_trywait), bool (*)(const void *)), "poll ABI");
static void *concurrent_poll(void *e) { for (unsigned i = 0; i < 1000; i++) assert(!h_spt_trywait(e)); return NULL; }
int main(void) {
    _Alignas(16) unsigned char event[0x80];
    cv_dec decoder = {.magic = CV_MAGIC, .id = 1, .mu = PTHREAD_MUTEX_INITIALIZER};
    cv_dec *handle = &decoder; g_cv[0] = &decoder;
    decoder.buf[0].reg = 1;
    cv_sync sync = {CV_SYNC, 1, 0, 1};
    assert(!h_spt_create(event));
    assert(!((test_event *)event)->autoclear);
    // Concurrent first lookup and polling of an unsignalled manual event.
    pthread_t threads[8];
    for (unsigned i = 0; i < 8; i++) assert(!pthread_create(&threads[i], NULL, concurrent_poll, event));
    for (unsigned i = 0; i < 8; i++) assert(!pthread_join(threads[i], NULL));
    assert(atomic_load(&polls) == 8000);
    assert(!h_spt_request(event, &sync) && !h_spt_trywait(event));
    pthread_mutex_lock(&decoder.mu); decoder.buf[0].filled = 1; decoder.buf[0].filled_gen = 1; spt_fire(&decoder, 0); pthread_mutex_unlock(&decoder.mu);
    // Manual-clear: polling twice retains signal; cancellation explicitly clears.
    assert(h_spt_trywait(event) && h_spt_trywait(event));
    assert(!SPT(event)->armed);
    h_spt_cancel(event); assert(!h_spt_trywait(event));
    assert(!h_spt_request(event, &sync) && h_spt_trywait(event)); // already complete
    sync.gen++;
    assert(!h_spt_request(event, &sync) && !h_spt_trywait(event)); // rearm clears old signal
    h_spt_cancel(event);
    decoder.buf[0].filled_gen = 2; spt_fire(&decoder, 0);
    assert(!h_spt_trywait(event)); // cancelled request is no longer registered
    // SystemEvent owns clear policy: the wrapper also respects an auto-clear binding.
    ((test_event *)event)->autoclear = 1; hle_signal_system_event(event);
    assert(h_spt_trywait(event) && !h_spt_trywait(event));
    h_spt_destroy(event); assert(!((test_event *)event)->bound);

    decoder.buf[0].filled = 0;
    // Invalid decoder / index and not-yet-filled properties leave outputs untouched.
    uint16_t w = 1, h = 2, cw = 3, ch = 4; int64_t ts = 5; bool eos = true;
    assert(h_cv_vp9props(&w, &h, &cw, &ch, &ts, &eos, NULL, 0) == CV_R(2004));
    assert(h_cv_vp9props(&w, &h, &cw, &ch, &ts, &eos, &handle, CV_BUFS) == CV_R(2004));
    assert(h_cv_vp9props(&w, &h, &cw, &ch, &ts, &eos, &handle, 0) == CV_NONE);
    assert(w == 1 && h == 2 && cw == 3 && ch == 4 && ts == 5 && eos);
    decoder.buf[0].filled = 1; decoder.buf[0].w = 640; decoder.buf[0].h = 360; decoder.buf[0].ts = 123;
    assert(!h_cv_vp9props(&w, &h, &cw, &ch, &ts, &eos, &handle, 0));
    assert(w == 640 && h == 360 && cw == 640 && ch == 360 && ts == 123 && !eos);
    // SDK 19.3's overloads with an nn::movie::ColorSpace (x4) and the buffer index on the stack: the same answers through the host decoder; an error writes nothing,
    // success writes every output, the colour space included
    uint32_t cs = 9; w = 1; h = 2; cw = 3; ch = 4; ts = 5; eos = true;
    assert(h_cv_vp9props_cs(&w, &h, &cw, &ch, &cs, &ts, &eos, NULL, 0) == CV_R(2004));
    assert(h_cv_vp9props_cs(&w, &h, &cw, &ch, &cs, &ts, &eos, &handle, CV_BUFS) == CV_R(2004));
    decoder.buf[0].filled = 0;
    assert(h_cv_vp9props_cs(&w, &h, &cw, &ch, &cs, &ts, &eos, &handle, 0) == CV_NONE);
    assert(w == 1 && h == 2 && cw == 3 && ch == 4 && cs == 9 && ts == 5 && eos);
    // A picture in the buffer's pool image: the first success's red line reads its colour space under the decoder lock
    vpx_image_t picture = {.fmt = VPX_IMG_FMT_I420, .cs = VPX_CS_BT_709, .d_w = 16, .d_h = 16}, *pic = &picture;
    cv_pool pool = {.magic = CV_MAGIC, .w = 16, .h = 16, .count = 1, .img = &pic};
    decoder.buf[0].pool = &pool; decoder.buf[0].pidx = 0; decoder.buf[0].filled = 1;
    assert(!h_cv_vp9props_cs(&w, &h, &cw, &ch, &cs, &ts, &eos, &handle, 0));
    assert(w == 640 && h == 360 && cw == 640 && ch == 360 && cs == 0 && ts == 123 && !eos);
    cs = 9; assert(!h_cv_vp8props_cs(&w, &h, &cw, &ch, &cs, &ts, &eos, &handle, 0) && cs == 0);
    cs = 9; assert(!h_cv_h264props_cs(&w, &h, &cw, &ch, &cs, &ts, &eos, &handle, 0) && cs == 0);
    decoder.buf[0].pool = NULL;
    // The guest's ABI: nine arguments, the index a 32-bit value (the SDK's caller stores it with a 32-bit write at [sp])
    _Static_assert(__builtin_types_compatible_p(__typeof__(&h_cv_vp9props_cs), Result (*)(uint16_t *, uint16_t *, uint16_t *, uint16_t *, uint32_t *, int64_t *, bool *, void *, uint32_t)), "properties ABI");
    g_cv[0] = NULL; pthread_mutex_destroy(&decoder.mu);
    puts("t_codec_poll: passed (poll modes, cancel/rearm, concurrent lookup, properties errors, SDK 19.3 colour space overloads, API types)");
}
