// Conservative ordering and arena rules for prepared compute-mesh draws.
#pragma once
#include <stdint.h>
#include "../nvn/nvn_obj.h"

static inline int vka_mp_state(uint32_t op) {
    switch (op) {
    case OP_BEGIN: case OP_END: case OP_CALL: case OP_JUMP:
    case OP_BIND_PROGRAM: case OP_BIND_STATE: case OP_BIND_VATTR: case OP_BIND_VSTREAM:
    case OP_BIND_VBUF: case OP_BIND_VBUF1: case OP_BIND_UBO: case OP_BIND_UBO_SMALL:
    case OP_BIND_SSBO: case OP_BIND_TEX: case OP_BIND_TEX1: case OP_BIND_SAMP: case OP_BIND_IMG:
    case OP_VIEWPORTS: case OP_SCISSOR: case OP_DEPTH_RANGE: case OP_STENCIL:
    case OP_POLY_OFFSET: case OP_BLEND_COLOR: case OP_DEPTH_CLAMP: case OP_RAST_DISCARD:
    case OP_VP_SWIZZLE: case OP_PRIM_RESTART: case OP_ALPHA_REF: case OP_PUSH_CONST:
        return 1;
    default: return 0;
    }
}
// Unknown or wrapping ranges never prove independence. Whole storage ranges,
// rather than only view subresources, deliberately reject harmless aliases too.
static inline int vka_mp_disjoint(uint64_t a, uint64_t n, uint64_t b, uint64_t m) {
    return a && b && n && m && n <= UINT64_MAX - a && m <= UINT64_MAX - b &&
           (a + n <= b || b + m <= a);
}
enum { VKA_MP_RANGES = 64 };
typedef struct { uint64_t addr, size; } vka_mp_range;
typedef struct { vka_mp_range r[VKA_MP_RANGES]; unsigned n; int all; } vka_mp_access;
static inline int vka_mp_add(vka_mp_access *a, uint64_t addr, uint64_t size) {
    if (!addr || !size || size > UINT64_MAX - addr || a->n == VKA_MP_RANGES) return 0;
    a->r[a->n++] = (vka_mp_range){addr, size}; return 1;
}
static inline int vka_mp_independent(const vka_mp_access *writes, const vka_mp_access *reads) {
    if (writes->all || (writes->n && reads->all)) return 0;
    for (unsigned i = 0; i < writes->n; i++) for (unsigned j = 0; j < reads->n; j++)
        if (!vka_mp_disjoint(writes->r[i].addr, writes->r[i].size, reads->r[j].addr, reads->r[j].size)) return 0;
    return 1;
}
static inline int vka_mp_reserve(uint64_t used, uint64_t bytes, uint64_t align,
                                 uint64_t capacity, uint64_t *offset) {
    if (!align || !bytes || used > capacity || used > UINT64_MAX - (align - 1)) return 0;
    uint64_t at = (used + align - 1) / align * align;
    if (at > capacity || bytes > capacity - at) return 0;
    *offset = at; return 1;
}
