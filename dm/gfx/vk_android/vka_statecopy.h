// Exact filtering of inline vertex-state records. No guest pointer or resource
// lifetime is cached: compare the bytes already copied into the queue's state.
#ifndef VKA_STATECOPY_H
#define VKA_STATECOPY_H
#include <stddef.h>
#include <string.h>

static inline int vka_state_copy(void *dst, const void *src, size_t stride,
                                 int capacity, int first, int count,
                                 int replace, int *used, int filter) {
    if (first < 0 || count < 0 || first > capacity || count > capacity - first) return 1;
    unsigned char *bytes = dst;
    size_t offset = (size_t)first * stride, length = (size_t)count * stride;
    int end = first + count;
    int next = used ? (replace || end > *used ? end : *used) : 0;
    int changed = !filter || (used && next != *used) || memcmp(bytes + offset, src, length);
    if (filter && replace) {
        for (size_t i = 0; i < offset; i++) changed |= bytes[i] != 0;
        for (size_t i = offset + length; i < (size_t)capacity * stride; i++) changed |= bytes[i] != 0;
    }
    if (changed) {
        if (replace) memset(dst, 0, (size_t)capacity * stride);
        memcpy(bytes + offset, src, length);
        if (used) *used = next;
    }
    return changed != 0;
}
#endif
