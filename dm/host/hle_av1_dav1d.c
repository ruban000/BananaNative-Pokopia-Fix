// AV1 decoding with dav1d (hle_av1_dav1d.h). Only built with NX_DAV1D.
#ifdef NX_DAV1D
#include "hle_av1_dav1d.h"
#include <dav1d/dav1d.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#define NXAV1_QMAX 16
struct nx_av1 { Dav1dContext *c; Dav1dPicture q[NXAV1_QMAX]; int nq; };

nx_av1 *nx_av1_new(int threads, int frame_delay) {
    nx_av1 *a = calloc(1, sizeof *a); if (!a) return NULL;
    Dav1dSettings s; dav1d_default_settings(&s);
    s.n_threads = threads > 0 ? threads : 0; if (s.n_threads > 8) s.n_threads = 8;
    s.max_frame_delay = frame_delay > 0 ? frame_delay : 0;
    s.all_layers = 0;   // one picture a unit: the highest spatial layer
    s.logger.callback = NULL;
    if (dav1d_open(&a->c, &s) < 0) { free(a); return NULL; }
    return a;
}
static void drop_queue(nx_av1 *a) { for (int i = 0; i < a->nq; i++) dav1d_picture_unref(&a->q[i]); a->nq = 0; }
void nx_av1_free(nx_av1 *a) { if (!a) return; drop_queue(a); dav1d_close(&a->c); free(a); }
static int pull(nx_av1 *a) {   // every picture dav1d has ready into the queue; how many
    int got = 0;
    while (a->nq < NXAV1_QMAX) {
        if (dav1d_get_picture(a->c, &a->q[a->nq]) < 0) break;
        a->nq++; got++;
    }
    return got;
}
int nx_av1_decode(nx_av1 *a, const uint8_t *tu, size_t n, int64_t ts) {
    if (a->nq > NXAV1_QMAX / 2) return -EBUSY;   // a unit gives at most the pictures the frame threads hold (8), so the queue cannot overflow below this
    Dav1dData d; uint8_t *buf = dav1d_data_create(&d, n);   // a copy: dav1d keeps the data while a frame thread works on it
    if (!buf) return -ENOMEM;
    memcpy(buf, tu, n); d.m.timestamp = ts;
    int r = 0;
    while (d.sz > 0) {
        r = dav1d_send_data(a->c, &d);
        if (r < 0 && r != DAV1D_ERR(EAGAIN)) { dav1d_data_unref(&d); return r; }
        r = 0;
        pull(a);   // dav1d refuses more data (EAGAIN) while a picture waits to be taken
    }
    pull(a);
    return 0;
}
int nx_av1_take(nx_av1 *a, nx_av1_pic *p, int drain) {
    if (!a->nq && drain) { for (int again = 0; again < 2;) again = pull(a) ? 0 : again + 1; }   // dav1d starts to drain on the second call that finds nothing
    else if (!a->nq) pull(a);
    if (!a->nq) return 0;
    Dav1dPicture *q = &a->q[0];
    nx_av1_pic o = {{q->data[0], q->data[1], q->data[2]}, {q->stride[0], q->stride[1]}, (unsigned)q->p.w, (unsigned)q->p.h, q->m.timestamp, q->p.bpc, q->p.layout, 0, 2, NULL};
    if (q->seq_hdr) { o.full_range = q->seq_hdr->color_range; o.matrix = (int)q->seq_hdr->mtrx; }
    if (q->p.bpc != 8 || q->p.layout != DAV1D_PIXEL_LAYOUT_I420) {
        dav1d_picture_unref(q); memmove(a->q, a->q + 1, (size_t)--a->nq * sizeof a->q[0]); return -ENOTSUP;
    }
    Dav1dPicture *own = malloc(sizeof *own); if (!own) return -ENOMEM;
    *own = *q; memmove(a->q, a->q + 1, (size_t)--a->nq * sizeof a->q[0]);
    o.priv = own; *p = o;
    return 1;
}
void nx_av1_release(nx_av1 *a, nx_av1_pic *p) { (void)a; if (!p->priv) return; dav1d_picture_unref(p->priv); free(p->priv); p->priv = NULL; }
void nx_av1_flush(nx_av1 *a) { drop_queue(a); dav1d_flush(a->c); }
#endif
