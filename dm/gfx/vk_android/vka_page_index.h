// Optional page-to-pool candidates, inspired by Anka Native's MIT page index.
// Unlike a direct-address table, every candidate is range-checked in the shader;
// partial pages, holes and addresses outside the bounded window use full search.
#ifndef VKA_PAGE_INDEX_H
#define VKA_PAGE_INDEX_H
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#define VKA_PIDX_WORD (8192u * 8u)
#define VKA_PIDX_SHIFT 12u
#define VKA_PIDX_BUFFERS 4
static inline uint32_t vka_pidx_window(const uint64_t *lo, const uint64_t *hi, int n, uint32_t limit, uint32_t *base) {
    uint64_t first = UINT64_MAX, last = 0;
    for (int i = 0; i < n; i++) {
        if (hi[i] <= lo[i] || lo[i] >= (1ull << 44)) continue;
        uint64_t a = (lo[i] + 4095) >> 12, b = hi[i] >> 12;
        if (b > (1ull << 32)) b = 1ull << 32;
        if (a >= b) continue;
        if (a < first) first = a;
        if (b > last) last = b;
    }
    *base = 0;
    if (first == UINT64_MAX || !limit) return 0;
    *base = (uint32_t)first;
    return last - first > limit ? limit : (uint32_t)(last - first);
}
static inline void vka_pidx_build(uint32_t *out, uint32_t base, uint32_t count, const uint64_t *lo, const uint64_t *hi, int n) {
    memset(out, 0, ((size_t)count + 1) * sizeof *out); // final record is the out-of-window sentinel
    // A candidate must not hide a later overlapping interval that the ordered
    // full search would select. Reject unordered/overlapping tables entirely.
    for (int i = 0; i < n; i++) if (hi[i] <= lo[i] || (i && lo[i] < hi[i - 1])) return;
    uint64_t end = (uint64_t)base + count;
    for (int i = 0; i < n && i < 8191; i++) {
        if (hi[i] <= lo[i] || lo[i] >= (1ull << 44)) continue;
        uint64_t a = (lo[i] + 4095) >> 12, b = hi[i] >> 12;
        if (a < base) a = base;
        if (b > end) b = end;
        for (; a < b; a++) out[a - base] = (uint32_t)i + 1;
    }
}
// A recording submission may already have sent parts to the GPU: even an open
// buffer whose old fence completed must never be rewritten in place.
static inline int vka_pidx_writable(int open, uint64_t fence, uint64_t done) { return !open && fence <= done; }
#endif
