// Exact page protections from successful memory syscalls since the initial maps snapshot.
// Writers can run in the dirty-page signal handler, so they neither allocate nor wait.
#pragma once
#include <stdatomic.h>
#include <stdint.h>

enum {
    VKMD_UNTOUCHED, VKMD_UNKNOWN, VKMD_UNMAPPED, VKMD_PROT,
    VKMD_VALUE_MASK = 15,
    VKMD_BUSY = 16,
};

static inline void vkmd_begin(_Atomic uint32_t *page) {
    uint32_t old = atomic_load(page);
    for (;;) {
        uint32_t next = (old & ~VKMD_VALUE_MASK) + VKMD_BUSY;
        if (old >= VKMD_BUSY) next |= VKMD_UNKNOWN;
        if (atomic_compare_exchange_weak(page, &old, next)) return;
    }
}

static inline void vkmd_end(_Atomic uint32_t *page, unsigned value) {
    uint32_t old = atomic_load(page);
    for (;;) {
        uint32_t next = old == VKMD_BUSY ? value : old - VKMD_BUSY;
        if (atomic_compare_exchange_weak(page, &old, next)) return;
    }
}
