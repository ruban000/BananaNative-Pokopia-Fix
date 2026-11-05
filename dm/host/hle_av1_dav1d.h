// AV1 decoding for the nn::codec video decoders (hle_codec_video.c) with dav1d (dm/third_party/dav1d): one temporal unit in, 8-bit I420 pictures out in display order.
// Built only with NX_DAV1D (android/host/CMakeLists.txt NX_AV1_DAV1D, on by default); without it hle_av1_dav1d.c is empty and AV1 Initialize fails. Used by hle_codec_video.c (cv_dec_*): docs/reference/movies.md
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct nx_av1 nx_av1;
typedef struct {
    const uint8_t *plane[3];   // Y, U, V
    ptrdiff_t stride[2];       // Y; U and V
    unsigned w, h;
    int64_t ts;                // the time given with the unit the picture came from
    int bpc, layout;           // 8 and 1 (I420) only: anything else is refused by nx_av1_take
    int full_range, matrix;    // sequence header: colour range flag and matrix_coefficients (1 = BT.709, 2 = unspecified)
    void *priv;                // for nx_av1_release
} nx_av1_pic;

// threads 0 = one a core (at most 8); frame_delay 0 = dav1d's choice (frame threads need delay > 1 to overlap frames, at the cost of that many pictures of latency)
nx_av1 *nx_av1_new(int threads, int frame_delay);
void nx_av1_free(nx_av1 *a);
// one temporal unit (OBUs, with or without the temporal delimiter: Matroska and the movie library's packets have none): 0 = taken, < 0 = error (negative errno of dav1d).
// Pictures that came out meanwhile wait in the decoder: with more than 8 waiting the call gives -EBUSY and takes nothing, so the unit is sent again after nx_av1_take
int nx_av1_decode(nx_av1 *a, const uint8_t *tu, size_t n, int64_t ts);
// the next picture: 1 = written to *p (give it back with nx_av1_release), 0 = none yet; drain = no more units follow, so the pictures held back by frame threads come out too.
// < 0 = a picture dav1d produced that the host cannot read (not 8-bit I420: the picture is dropped)
int nx_av1_take(nx_av1 *a, nx_av1_pic *p, int drain);
void nx_av1_release(nx_av1 *a, nx_av1_pic *p);
// a seek, Stop or Flush: units and pictures not delivered are dropped, decoding restarts at the next key frame that carries a sequence header
void nx_av1_flush(nx_av1 *a);
