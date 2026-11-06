// Guest thread environment: gate slot = this thread's gate entry (guest_gate_stack); x18 slot cleared; tpidr_el0 slot = ELF TLS block; tpidrro_el0 slot = Horizon ThreadLocalRegion (0x200).
// The kernel owns two fields of the ThreadLocalRegion that the SDK reads: the current thread object at TLR[0x1f8] and, since SDK 22, the thread's ID at TLR[0x110] (nn::os 22's internal critical
// section stores it as a mutex's owner and compares it in IsLockedByCurrentThread, where SDK 16 compared the object pointer). Here each thread gets a zero-initialized object allocated and
// written in, and its ID once the thread table has one (guest_thread_set_id)
#include "dm.h"
#include <sys/mman.h>
#include <stdatomic.h>
#define TLS_SIZE 8192
#define TLR_SIZE 0x200
#define THREAD_OBJ_SIZE 0x400
static pthread_key_t g_keyown;   // raw pointers of the TLS block / thread-local region this thread allocated (see the end of guest_thread_init)
static pthread_once_t g_keyown_once = PTHREAD_ONCE_INIT;
static void own_free(void *p) { void **own = p; free(own[0]); free(own[1]); free(own); }
static void own_key_init(void) { if (pthread_key_create(&g_keyown, own_free)) die("cannot create the free key for thread-local blocks"); }
void guest_thread_init(void) {
    dm_slot_set(18, 0);
    // The TLS block and the thread-local region are freed when the thread exits. The guest overwrites the slots of the TLS / thread-local-region keys (it sets its own thread pointer), so they cannot be freed by the current value in the key:
    // the raw pointers that were allocated are recorded separately on this file's own key g_keyown and freed by its destructor (key destructors run at pthread_exit, like crash.c's signal alternate stack; the destructor only calls free, which is safe on a guest thread's small stack too).
    // They used not to be freed: every short-lived thread the game started leaked 8192 + 0x200 + 0x400 = 9728 bytes. The thread-local region and the current thread object are one block (the SDK may also overwrite the TLR[0x1f8] slot)
    pthread_once(&g_keyown_once, own_key_init);
    void **own = calloc(2, sizeof *own); uint8_t *tlr = calloc(1, TLR_SIZE + THREAD_OBJ_SIZE);
    if (!own || !tlr) die("guest thread-local block allocation failed");
    own[0] = calloc(1, TLS_SIZE); own[1] = tlr;
    if (!own[0]) die("guest thread TLS block allocation failed");
    *(void **)(tlr + 0x1f8) = tlr + TLR_SIZE;               // current thread object
    dm_slot_set(tls, own[0]); dm_slot_set(tlr, tlr);
    void **prev = pthread_getspecific(g_keyown); if (prev) own_free(prev);   // re-initialised on the same thread: free the previous set first (the new one is what the slots now point to)
    pthread_setspecific(g_keyown, own);
}
// Pointer authentication keys of guest threads (Android on arm64 with PAuth). Horizon gives a process one set of keys; the guest relies on it across threads: SDK 22's fibers
// (nn::os::detail::FiberImplByHorizon::Initialize, sdk+0x16d0d0 in 22.2.8) sign a new fiber's entry with pacia on the creating thread, and DispatchFiberContext (sdk+0x16d214)
// authenticates it with autiasp, and a suspended fiber's return with autib, on whichever thread switches to it. On the phone that check failed (SIGILL at autiasp, sdk+0x16d40c,
// Ocarina of Time's nn.npln.Worker) although the context's own pacga signature matched: the A key the signing thread used was not the switching thread's. The host cannot
// give its threads one key (Linux keeps them per thread and can only reset them to random values), so guest threads run with the instruction keys A and B switched off
// (PR_PAC_SET_ENABLED_KEYS): pacia / pacib / autia / autib / retab then leave pointers as they are on every guest thread, consistently. The generic key (pacga) is not affected
// and is the same on every thread. dm_pac_guest(1) on a guest thread's own start (its earlier frames never return: thread_main, or guest_main which calls dm_pac_guest(0) before it
// returns); DM_PAC_KEYS=1 keeps the keys on. Each thread's key fingerprint is compared with the first guest thread's, and a difference is logged (the evidence for the above).
#if defined(__aarch64__) && defined(__linux__)
#include <sys/auxv.h>
#include <sys/prctl.h>
#ifndef PR_PAC_SET_ENABLED_KEYS
#define PR_PAC_SET_ENABLED_KEYS 60
#endif
#define PAC_IA (1ul << 0)
#define PAC_IB (1ul << 1)
static void pac_fingerprint(uint64_t fp[3]) {   // pacia1716 / pacib1716 (hints: no-ops with the key off) and pacga x16, x16, x17 on fixed inputs
    register uint64_t x16 __asm__("x16"), x17 __asm__("x17");
    x16 = 0x55; x17 = 0x1000; __asm__ volatile(".inst 0xd503211f" : "+r"(x17) : "r"(x16)); fp[0] = x17;   // pacia1716: x17 signed with modifier x16, key IA
    x16 = 0x55; x17 = 0x1000; __asm__ volatile(".inst 0xd503215f" : "+r"(x17) : "r"(x16)); fp[1] = x17;   // pacib1716
    x16 = 0x1000; x17 = 0x55; __asm__ volatile(".inst 0x9ad13210" : "+r"(x16) : "r"(x17)); fp[2] = x16;   // pacga x16, x16, x17
}
void dm_pac_guest(int on) {
    static int has = -1, keep;
    if (has < 0) { unsigned long hw = getauxval(AT_HWCAP); has = (hw >> 30 & 1) && (hw >> 31 & 1); const char *e = getenv("DM_PAC_KEYS"); keep = e && atoi(e); }   // HWCAP_PACA, HWCAP_PACG
    if (!has) return;
    if (on) {
        static uint64_t first[3]; static _Atomic int set, nlog;
        uint64_t fp[3]; pac_fingerprint(fp);
        if (!atomic_exchange(&set, 1)) { memcpy(first, fp, sizeof fp); LOG("PAC: guest threads run with the instruction keys A and B %s (DM_PAC_KEYS=1 keeps them)", keep ? "on" : "off"); }
        else if (memcmp(first, fp, sizeof fp) && atomic_fetch_add(&nlog, 1) < 8)
            LOG("[RED] PAC: this guest thread's keys differ from the first guest thread's (A %s, B %s, generic %s)", fp[0] == first[0] ? "same" : "different",
                fp[1] == first[1] ? "same" : "different", fp[2] == first[2] ? "same" : "different");
    }
    if (keep) return;
    if (prctl(PR_PAC_SET_ENABLED_KEYS, PAC_IA | PAC_IB, on ? 0 : PAC_IA | PAC_IB, 0, 0)) { static _Atomic int once; if (!atomic_exchange(&once, 1)) LOG("[RED] PAC: PR_PAC_SET_ENABLED_KEYS failed (errno %d): guest threads keep their own keys", errno); }
}
#else
void dm_pac_guest(int on) { (void)on; }
#endif
// The thread's kernel ID at TLR[0x110], the one svcGetThreadId answers (hle_thread.c's thr_t id, never 0: 0 is the owner word of an unlocked mutex). Without it every
// unlocked SDK 22 mutex reads as the caller's and the first heap lock aborts (docs/reference/threads.md)
void guest_thread_set_id(uint64_t id) { *(uint32_t *)((uint8_t *)dm_slot_get(tlr) + 0x110) = (uint32_t)id; }

// Gate stacks (entry.S dm_gate): the SVC gate's register frame and the host's SVC handlers run on a stack of the thread's own, never below the guest's sp, as on Horizon, whose kernel leaves a
// thread's stack untouched during an SVC. Each thread holds a gate slot k: its KGATE slot points to entry.S stub k, which loads dm_gate_top[k]. A guest thread (hle_thread.c thread_main) runs
// its guest code on the guest stack the SDK gave it, so its gate stack is the rest of its own pthread stack (1 MB), below thread_main's frame: it lives exactly as long as the thread, which
// exits from inside an SVC (ExitThread → pthread_exit on the gate stack). The guest main thread runs its guest code on this pthread's stack, so it gets a mapped stack, never freed
// (the process ends with it). The slot is released when the thread exits, after which another thread may take it with its own stack.
#define GATE_SLOTS 1024                 // = entry.S .rept count and dm_gate_top size
#define GATE_STACK_SIZE (1 << 20)       // = entry.S nesting check
extern uint64_t dm_gate_top[GATE_SLOTS];
extern const uint8_t dm_gate_stubs[];
static pthread_mutex_t g_gate_lock = PTHREAD_MUTEX_INITIALIZER;
static uint16_t g_gate_free[GATE_SLOTS]; static int g_gate_nfree = -1;
static pthread_key_t g_keygslot; static pthread_once_t g_gslot_once = PTHREAD_ONCE_INIT;
static void gslot_release(void *p) {   // key destructor (pthread_exit): slot + 1
    pthread_mutex_lock(&g_gate_lock); g_gate_free[g_gate_nfree++] = (uint16_t)((uintptr_t)p - 1); pthread_mutex_unlock(&g_gate_lock);
}
static void gslot_key_init(void) {
    if (pthread_key_create(&g_keygslot, gslot_release)) die("cannot create the gate slot key");
    if ((uintptr_t)dm_gate_top & 0xfff) die("gate stack table %p is not page-aligned (entry.S stubs address it with adrp alone)", (void *)dm_gate_top);
}
void dm_gate_nested(uint64_t sp) { die("SVC gate entered on its own gate stack (sp %#llx): a gate handler ran guest code", (unsigned long long)sp); }
__attribute__((noinline)) void guest_gate_stack(int own_stack) {
    pthread_once(&g_gslot_once, gslot_key_init);
    uint8_t *top;
    if (own_stack) top = (uint8_t *)__builtin_frame_address(0);   // this function's frame record: everything above it is the caller's frame, everything below is free once the guest runs elsewhere
    else {
        uint8_t *m = mmap(NULL, GATE_STACK_SIZE + 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (m == MAP_FAILED || mprotect(m, 16384, PROT_NONE)) die("cannot map a gate stack (errno %d)", errno);   // guard page at the bottom
        top = m + 16384 + GATE_STACK_SIZE;
    }
    uintptr_t prev = (uintptr_t)pthread_getspecific(g_keygslot);
    int k;
    if (prev) k = (int)prev - 1;   // set again on the same thread: keep its slot
    else {
        pthread_mutex_lock(&g_gate_lock);
        if (g_gate_nfree < 0) { g_gate_nfree = 0; for (int i = GATE_SLOTS - 1; i >= 0; i--) g_gate_free[g_gate_nfree++] = (uint16_t)i; }
        if (!g_gate_nfree) die("more than %d threads with a gate stack", GATE_SLOTS);
        k = g_gate_free[--g_gate_nfree];
        pthread_mutex_unlock(&g_gate_lock);
        pthread_setspecific(g_keygslot, (void *)(uintptr_t)(k + 1));
    }
    dm_gate_top[k] = (uint64_t)(uintptr_t)top & ~15ull;
    dm_slot_set(gate, (void *)(dm_gate_stubs + 16 * k));
}
