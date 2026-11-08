// Actual vka_mem_pageout selection and placement serialization, without a GPU.
// Link with function/data sections and --gc-sections: the rest of vka_mem is unused.
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>
#include "../../gfx/vk_android/vka_mem.c"
static _Atomic int holes;
uint64_t dm_guest_unmapped(uintptr_t a, size_t n) { (void)a; (void)n; return atomic_load(&holes); }
static uintptr_t test_lo, test_hi;
static _Atomic int entered, done;
static int result;
static void *request(void *unused) {
    (void)unused; atomic_store(&entered, 1);
    result = vka_mem_pageout(test_lo, test_hi);
    atomic_store(&done, 1); return NULL;
}
int main(void) {
    size_t pg = (size_t)sysconf(_SC_PAGESIZE), n = 4 * pg;
    unsigned char *p = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);
    assert(p != MAP_FAILED); memset(p, 0x53, n);
    test_lo = (uintptr_t)p; test_hi = test_lo + n;
    assert(vka_mem_pageout(test_lo, test_hi) == 0); // unknown mappings excluded
    dm_gfx_guest_region(p, n, "fixture");
    atomic_store(&holes, 1); assert(vka_mem_pageout(test_lo, test_hi) == 0);
    atomic_store(&holes, 0);
    g_sg = calloc(1, sizeof *g_sg); assert(g_sg); g_nsg = 1;
    g_sg[0] = (seg_t){.lo = test_lo + pg, .hi = test_lo + 2 * pg};
    assert(vka_mem_pageout(test_lo, test_hi) == 0); // partial placed overlap
    assert(vka_mem_pageout(test_lo, test_lo + pg) == 1); // touching boundary allowed
    g_nsg = 0;
    g_pend = calloc(1, sizeof *g_pend); assert(g_pend); g_npend = 1;
    g_pend[0].lo = test_lo; g_pend[0].hi = test_hi;
    assert(vka_mem_pageout(test_lo, test_hi) == 0); // detached driver mapping excluded
    g_npend = 0;
    // Simulate a placement whose segment has not yet been published: holding
    // only g_rw would miss it. The request must wait for g_pl then see overlap.
    pthread_mutex_lock(&g_pl);
    pthread_t th; assert(!pthread_create(&th, NULL, request, NULL));
    while (!atomic_load(&entered)) sched_yield();
    usleep(20000); assert(!atomic_load(&done));
    g_sg[0] = (seg_t){.lo = test_lo, .hi = test_hi}; g_nsg = 1;
    pthread_mutex_unlock(&g_pl);
    assert(!pthread_join(th, NULL)); assert(result == 0);
    g_nsg = 0;
    assert(vka_mem_pageout(test_lo, test_hi) == 1);
    for (size_t i = 0; i < n; i++) assert(p[i] == 0x53);
    munmap(p, n); free(g_sg); free(g_pend);
    puts("t_android_pageout_mem: ok"); return 0;
}
