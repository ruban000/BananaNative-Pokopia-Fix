// CPU texture reads exclude final allocation alignment, but retain mip padding
// and the full stride between layers. A guard immediately after the source span
// catches reads of the omitted tail while every mip/layer is deswizzled.
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "../tex/nvn_deswizzle.h"

static void check(nvn_texdesc d, uint64_t allocation, uint64_t data) {
    nvn_texlayout L; nvn_tex_layout(&d, &L);
    assert(L.size == allocation);
    assert(nvn_tex_data_size(&d) == data);
    assert(data && data <= L.size);
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    size_t mapped = ((size_t)data + page - 1) / page * page;
    uint8_t *map = mmap(NULL, mapped + page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(map != MAP_FAILED);
    assert(!mprotect(map + mapped, page, PROT_NONE));
    uint8_t *src = map + mapped - data, *full = malloc(L.size), *snapshot = malloc(data);
    assert(full && snapshot);
    for (uint64_t i = 0; i < data; i++) src[i] = (uint8_t)(i * 37 + (i >> 8));
    memcpy(full, src, data);
    memset(full + data, 0xa5, L.size - data);
    memcpy(snapshot, src, data); // the bounded upload snapshot
    // Whole pages of alignment between layers need not be mapped either.
    // The snapshot is prepared first; deswizzling the original must skip them.
    uint64_t layer_data = nvn_tex_level_offset(&d, d.levels ? d.levels : 1);
    for (unsigned ly = 0; ly + 1 < L.layers; ly++) {
        uintptr_t lo = (uintptr_t)src + ly * L.layer_size + layer_data;
        uintptr_t hi = (uintptr_t)src + (ly + 1) * L.layer_size;
        lo = (lo + page - 1) / page * page; hi = hi / page * page;
        if (hi > lo) assert(!mprotect((void *)lo, hi - lo, PROT_NONE));
    }
    for (int gob = 0; gob < 2; gob++) {
        nvn_gob_switch2 = gob;
        for (unsigned lv = 0; lv < (d.levels ? d.levels : 1); lv++) {
            for (unsigned ly = 0; ly < L.layers; ly++) {
                uint32_t row, rows, slices, bpb; uint64_t base; unsigned th, td;
                assert(!nvn_deswizzle_geom(&d, lv, ly, &row, &rows, &slices, &base, &th, &td, &bpb));
                size_t n = (size_t)row * rows * slices;
                uint8_t *a = malloc(n), *b = malloc(n); assert(a && b);
                assert(!nvn_deswizzle(&d, lv, ly, full, a, row));
                assert(!nvn_deswizzle(&d, lv, ly, src, b, row));
                assert(!memcmp(a, b, n));
                assert(!nvn_deswizzle(&d, lv, ly, snapshot, b, row));
                assert(!memcmp(a, b, n));
                free(a); free(b);
            }
        }
    }
    free(full); free(snapshot); assert(!munmap(map, mapped + page));
}

int main(void) {
    // Real packaged shapes whose pools end before sparse allocation alignment.
    nvn_texdesc d = {.target = 1, .format = 0x49, .flags = 0x24, .width = 512, .height = 512, .depth = 1, .levels = 10, .pkg_block_h = 4};
    check(d, 0x30000, 0x2b400);
    d.format = 0x12; d.pkg_block_h = 5; check(d, 0x160000, 0x155c00);
    d.format = 6; check(d, 0xb0000, 0xab400);
    // Array/cube stride must not become the unrounded data size.
    d.format = 0x49; d.pkg_block_h = 4; d.target = 4; d.depth = 3;
    check(d, 3 * 0x30000, 2 * 0x30000 + 0x2b400);
    d.target = 8; check(d, 6 * 0x30000, 5 * 0x30000 + 0x2b400);
    d.target = 9; d.depth = 12; check(d, 12 * 0x30000, 11 * 0x30000 + 0x2b400);
    // Non-sparse layer alignment, packaged depth, minimal and default mip count.
    d = (nvn_texdesc){.target = 4, .format = 0x25, .width = 64, .height = 64, .depth = 3, .levels = 7};
    check(d, 3 * 24576, 2 * 24576 + 23552);
    d = (nvn_texdesc){.target = 2, .format = 0x49, .width = 64, .height = 64, .depth = 64, .levels = 7, .pkg_block_h = 0x41};
    check(d, 163328, 163328);
    d = (nvn_texdesc){.target = 1, .format = 0x3c, .flags = 0x200, .width = 320, .height = 320, .depth = 1, .levels = 1};
    check(d, 204800, 204800);
    d = (nvn_texdesc){.target = 1, .format = 0x25, .width = 4, .height = 4, .depth = 1};
    check(d, 512, 512);
    // Linear and buffer APIs retain their existing complete byte extents.
    d = (nvn_texdesc){.target = 1, .format = 0x42, .flags = 0x10, .width = 64, .height = 64, .stride = 128};
    assert(nvn_tex_data_size(&d) == 8192);
    d = (nvn_texdesc){.target = 10, .format = 0x25, .width = 123};
    assert(nvn_tex_data_size(&d) == 492);
    d.format = 0; assert(nvn_tex_data_size(&d) == 0);
    puts("t_tex_data: guarded mip/layer reads and allocation boundaries passed");
    return 0;
}
