// nx_av1 (hle_av1_dav1d.c) against ffmpeg's own dav1d: argv[1] = an IVF file of AV1 (aom or svt, hidden frames included), argv[2] = its decoded I420 frames (ffmpeg -pix_fmt yuv420p -f rawvideo),
// argv[3] = width, argv[4] = height. The IVF's temporal delimiters are removed, as Matroska does, before the units go to the decoder. Run by dm/tools/tests/test_av1_dav1d.py
#include "../hle_av1_dav1d.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb"); assert(f); fseek(f, 0, SEEK_END); *n = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(*n + 1); assert(b && fread(b, 1, *n, f) == *n); fclose(f); return b;
}
static size_t leb(const uint8_t *p, size_t n, size_t *len) {   // OBU size field
    size_t v = 0; *len = 0;
    for (int i = 0; i < 8 && (size_t)i < n; i++) { v |= (size_t)(p[i] & 0x7f) << (7 * i); (*len)++; if (!(p[i] & 0x80)) break; }
    return v;
}
static size_t strip_td(const uint8_t *in, size_t n, uint8_t *out) {   // the unit without its temporal delimiter OBUs
    size_t o = 0, i = 0;
    while (i < n) {
        uint8_t h = in[i]; int type = (h >> 3) & 15, ext = (h >> 2) & 1; assert(h & 2);   // every OBU carries its size
        size_t hl = 1 + (size_t)ext, ll, sz = leb(in + i + hl, n - i - hl, &ll), tot = hl + ll + sz;
        assert(i + tot <= n);
        if (type != 2) { memcpy(out + o, in + i, tot); o += tot; }
        i += tot;
    }
    return o;
}
typedef struct { uint8_t *d; size_t n; } unit;
static unit *units; static int nunits;
static int compare(nx_av1 *a, const uint8_t *ref, size_t frame, unsigned w, unsigned h, int drain, int *count) {
    nx_av1_pic p; int r, bad = 0;
    while ((r = nx_av1_take(a, &p, drain)) == 1) {
        assert(p.w == w && p.h == h && p.bpc == 8 && p.layout == 1);
        const uint8_t *f = ref + (size_t)(*count) * frame;
        for (unsigned y = 0; y < h; y++) bad |= memcmp(p.plane[0] + (size_t)y * p.stride[0], f + (size_t)y * w, w) != 0;
        for (int c = 1; c < 3; c++) for (unsigned y = 0; y < h / 2; y++)
            bad |= memcmp(p.plane[c] + (size_t)y * p.stride[1], f + (size_t)w * h + (size_t)(c - 1) * (w / 2) * (h / 2) + (size_t)y * (w / 2), w / 2) != 0;
        nx_av1_release(a, &p); (*count)++;
    }
    assert(r == 0);
    return bad;
}
static void run(int threads, int delay, const uint8_t *ref, size_t nframes, unsigned w, unsigned h) {
    size_t frame = (size_t)w * h * 3 / 2; int count = 0, bad = 0;
    nx_av1 *a = nx_av1_new(threads, delay); assert(a);
    for (int i = 0; i < nunits; i++) {
        int r; while ((r = nx_av1_decode(a, units[i].d, units[i].n, i * 1000 + 7)) == -EBUSY) bad |= compare(a, ref, frame, w, h, 0, &count);
        assert(r == 0);
        bad |= compare(a, ref, frame, w, h, 0, &count);
    }
    bad |= compare(a, ref, frame, w, h, 1, &count);   // end of stream: what frame threads held back
    printf("threads %d delay %d: %d pictures of %zu, %s\n", threads, delay, count, nframes, bad ? "MISMATCH" : "identical");
    assert(!bad && (size_t)count == nframes);
    // a flush starts over: the same units give the same pictures again (the first unit is a key frame with its sequence header)
    nx_av1_flush(a); count = 0;
    for (int i = 0; i < nunits; i++) { assert(nx_av1_decode(a, units[i].d, units[i].n, i) == 0); bad |= compare(a, ref, frame, w, h, 0, &count); }
    bad |= compare(a, ref, frame, w, h, 1, &count);
    assert(!bad && (size_t)count == nframes);
    // garbage is an error and the decoder goes on
    uint8_t junk[64]; memset(junk, 0xff, sizeof junk);
    int r = nx_av1_decode(a, junk, sizeof junk, 0); assert(r <= 0);
    nx_av1_free(a);
}
int main(int argc, char **argv) {
    assert(argc == 5);
    size_t n, rn; uint8_t *ivf = slurp(argv[1], &n), *ref = slurp(argv[2], &rn);
    unsigned w = (unsigned)atoi(argv[3]), h = (unsigned)atoi(argv[4]); size_t frame = (size_t)w * h * 3 / 2;
    assert(n > 32 && !memcmp(ivf, "DKIF", 4) && rn % frame == 0);
    units = calloc(4096, sizeof *units);
    for (size_t i = 32; i + 12 <= n;) {
        size_t sz = ivf[i] | ivf[i + 1] << 8 | ivf[i + 2] << 16 | (size_t)ivf[i + 3] << 24; i += 12; assert(i + sz <= n);
        units[nunits].d = malloc(sz); units[nunits].n = strip_td(ivf + i, sz, units[nunits].d); nunits++; i += sz;
    }
    printf("%d units, %zu reference pictures %ux%u\n", nunits, rn / frame, w, h);
    run(1, 0, ref, rn / frame, w, h);
    run(4, 2, ref, rn / frame, w, h);
    for (int i = 0; i < nunits; i++) free(units[i].d);
    free(units); free(ivf); free(ref);
    puts("t_av1_dav1d: passed");
    return 0;
}
