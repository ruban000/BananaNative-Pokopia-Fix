// The storage page-out helpers (dm/gfx/vk_android/vka_pageout.h) and what the page-out relies on from the kernel: madvise(MADV_PAGEOUT) keeps every byte of private
// anonymous pages, read-only (the dirty tracker's write protection) or not, and a write after it still faults on a read-only page (the protection stays)
#include <errno.h>
#include <signal.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include "../../gfx/vk_android/vka_pageout.h"
#ifndef MADV_PAGEOUT
#define MADV_PAGEOUT 21
#endif

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)
static sigjmp_buf g_jb; static volatile sig_atomic_t g_faults;
static void on_segv(int s) { (void)s; g_faults++; siglongjmp(g_jb, 1); }

int main(void) {
    uintptr_t lo = 0, hi = 0, pm = 4095;
    CHECK(vkp_interior(0x1000, 0x3000, pm, &lo, &hi) && lo == 0x1000 && hi == 0x4000, "aligned range: all its pages (%#lx-%#lx)", (unsigned long)lo, (unsigned long)hi);
    CHECK(vkp_interior(0x1010, 0x3000, pm, &lo, &hi) && lo == 0x2000 && hi == 0x4000, "unaligned start: the shared first page stays (%#lx-%#lx)", (unsigned long)lo, (unsigned long)hi);
    CHECK(vkp_interior(0x1000, 0x2ff0, pm, &lo, &hi) && lo == 0x1000 && hi == 0x3000, "unaligned end: the shared last page stays (%#lx-%#lx)", (unsigned long)lo, (unsigned long)hi);
    CHECK(vkp_interior(0x1010, 0x1ff0, pm, &lo, &hi) && lo == 0x2000 && hi == 0x3000, "one whole page at exact end");
    CHECK(!vkp_interior(0x1010, 0x1fef, pm, &lo, &hi), "no whole page inside: nothing");
    CHECK(!vkp_interior(0x1000, 0, pm, &lo, &hi), "empty range: nothing");
    CHECK(!vkp_interior(UINTPTR_MAX - 10, 32, pm, &lo, &hi), "end overflow rejected");
    CHECK(!vkp_interior(UINTPTR_MAX - 10, 1, pm, &lo, &hi), "alignment overflow rejected");
    CHECK(vkp_chunk(0x1010, 0x400000, pm, 0, 0x200000, &lo, &hi) && lo == 0x2000 && hi == 0x102000, "one image bounded to 1 MiB");
    CHECK(vkp_chunk(0x1010, 0x400000, pm, 0x100000, 0x3000, &lo, &hi) && lo == 0x102000 && hi == 0x105000, "remaining budget bounds next chunk");
    CHECK(!vkp_chunk(0x1010, 0x400000, pm, 0, 4095, &lo, &hi), "less than one page: no request");
    CHECK(!vkp_chunk(0x1000, 0x1000, pm, 0x1000, 0x1000, &lo, &hi), "completed image: no request");
    // The kernel: content and protection survive MADV_PAGEOUT
    long pg = sysconf(_SC_PAGESIZE); size_t n = 64 * (size_t)pg;
    unsigned char *p = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    CHECK(p != MAP_FAILED, "mmap");
    if (p != MAP_FAILED) {
        for (size_t i = 0; i < n; i++) p[i] = (unsigned char)(i * 131 + (i >> 12));
        memset(p + 8 * pg, 0, 8 * (size_t)pg);   // zero pages too (block-linear padding)
        CHECK(!mprotect(p, n, PROT_READ), "read-only protection established");
        int r = vkp_advise((uintptr_t)p, (uintptr_t)p + n);
        if (r && errno == EINVAL) printf("note: this kernel has no MADV_PAGEOUT (the checks of its effect are skipped)\n");
        else {
            CHECK(r == 0, "madvise(MADV_PAGEOUT) on read-only private pages: %d (errno %d)", r, errno);
            size_t bad = 0; for (size_t i = 0; i < n; i++) { unsigned char want = i >= 8 * (size_t)pg && i < 16 * (size_t)pg ? 0 : (unsigned char)(i * 131 + (i >> 12)); bad += p[i] != want; }
            CHECK(bad == 0, "every byte read back after the page-out (%zu differ)", bad);
            signal(SIGSEGV, on_segv);
            if (!sigsetjmp(g_jb, 1)) { p[5] = 1; }
            CHECK(g_faults == 1, "a write to a paged-out read-only page still faults (%d)", (int)g_faults);
            signal(SIGSEGV, SIG_DFL);
            CHECK(!mprotect(p, n, PROT_READ | PROT_WRITE), "write protection lifted"); p[5] = 7;
            CHECK(p[5] == 7 && p[6] == (unsigned char)(6 * 131), "writable again after the protection is lifted");
        }
        munmap(p, n);
        // Refused requests do not poison a cursor: the same chunk remains retryable.
        uint64_t off = 0; uintptr_t retry_lo, retry_hi;
        CHECK(vkp_chunk((uintptr_t)p, n, (uintptr_t)pg - 1, off, n, &lo, &hi), "failed request chunk");
        int refused = vkp_advise(lo, hi);
        if (!refused) off += hi - lo;
        CHECK(refused && errno == ENOMEM && off == 0, "unmapped request refused and cursor unchanged");
        CHECK(vkp_chunk((uintptr_t)p, n, (uintptr_t)pg - 1, off, n, &retry_lo, &retry_hi) && retry_lo == lo && retry_hi == hi, "refused chunk retryable");
    }
    if (!fails) printf("t_android_pageout: ok\n");
    return fails != 0;
}
