// Shared production helpers for bounded, byte-preserving MADV_PAGEOUT requests.
#pragma once
#include <stdint.h>
#include <sys/mman.h>
#ifndef MADV_PAGEOUT
#define MADV_PAGEOUT 21
#endif

// Whole pages inside the source allocation; shared boundary pages stay resident.
static inline int vkp_interior(uintptr_t a, uint64_t n, uintptr_t pm, uintptr_t *lo, uintptr_t *hi) {
    if (!n || n > UINTPTR_MAX - a || a > UINTPTR_MAX - pm) return 0;
    uintptr_t l = (a + pm) & ~pm, h = (a + (uintptr_t)n) & ~pm;
    if (h <= l) return 0;
    *lo = l; *hi = h; return 1;
}
// off advances only after success. Neither one image nor failed calls exceed budget.
static inline int vkp_chunk(uintptr_t a, uint64_t n, uintptr_t pm, uint64_t off, uint64_t budget, uintptr_t *lo, uintptr_t *hi) {
    uintptr_t l, h;
    if (!vkp_interior(a, n, pm, &l, &h) || off >= h - l || (off & pm)) return 0;
    uint64_t take = h - l - off, cap = (UINT64_C(1) << 20) & ~(uint64_t)pm;
    budget &= ~(uint64_t)pm;
    if (take > cap) take = cap;
    if (take > budget) take = budget;
    if (!take) return 0;
    *lo = l + off; *hi = *lo + take; return 1;
}
static inline int vkp_advise(uintptr_t lo, uintptr_t hi) {
    return madvise((void *)lo, hi - lo, MADV_PAGEOUT);
}
