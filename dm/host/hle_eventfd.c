// Event file descriptors of the bsd socket service (nn::socket::EventFd, EventFdRead, EventFdWrite, and Close / Poll on them), kept by the host.
// The host cuts the network (hle_net.c): every socket call that would reach the network fails with ENETDOWN. An eventfd is not the network: it is a counter the bsd service
// keeps, and it works on an offline console. gRPC's poll engine (NPLN's client in Zelda: Ocarina of Time, main+0x21f1280) takes one as its wakeup descriptor: its
// availability check (main+0x21f3bd0) creates EventFd(0, 0) and closes it, and when that fails no poll engine initializes and gRPC calls abort() (main+0x21f0c7c, the
// "grpc_poll_strategy" loop). The engine then creates EventFd(0, 6) and polls on it beside its sockets.
// Semantics (Linux / FreeBSD eventfd(2)): a 64-bit counter; a write adds to it (EAGAIN on a non-blocking descriptor when it would pass 2^64 - 2, else it waits); a read takes the
// whole count (1 in semaphore mode) and waits while it is 0, or fails with EAGAIN when non-blocking; Poll reports POLLIN while the count is above 0 and POLLOUT while a write of 1
// fits. Flags as FreeBSD numbers them (Horizon's bsd is FreeBSD's): 1 semaphore, 4 non-blocking; gRPC's EFD_NONBLOCK | EFD_CLOEXEC is 6, so the 2 is taken for close-on-exec
// (inferred, not read from the service). Errors are the C errno values the game's own code compares against (gRPC: 4 EINTR, 11 EAGAIN, main+0x21f3af8).
#include "hle.h"
#include <pthread.h>

#define EFD_FIRST 0x40   // descriptors the host hands out: the host makes no other socket (Socket fails), so any number is free; above the usual 0..2
#define EFD_MAX 64
#define EFD_SEMAPHORE 1u
#define EFD_NONBLOCK 4u
#define EFD_EAGAIN 11
#define EFD_EBADF 9
#define EFD_EINVAL 22
static struct { int used; unsigned flags; uint64_t count; } g_efd[EFD_MAX];
static pthread_mutex_t g_efd_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_efd_cv = PTHREAD_COND_INITIALIZER;   // any count changed or a descriptor closed

static int efd_slot(int fd) { int i = fd - EFD_FIRST; return i >= 0 && i < EFD_MAX && g_efd[i].used ? i : -1; }
int dm_efd_is(int fd) { pthread_mutex_lock(&g_efd_mu); int r = efd_slot(fd) >= 0; pthread_mutex_unlock(&g_efd_mu); return r; }
// A wait for the counter: the guest core is released while the host thread blocks; at most until the deadline (0 = none)
static void efd_wait(uint64_t deadline_ns) {
    dm_thread_native_wait(1);
    if (deadline_ns) { struct timespec ts = {(time_t)(deadline_ns / 1000000000u), (long)(deadline_ns % 1000000000u)}; pthread_cond_timedwait(&g_efd_cv, &g_efd_mu, &ts); }
    else pthread_cond_wait(&g_efd_cv, &g_efd_mu);
    dm_thread_native_wait(0);
}
static uint64_t realtime_ns(void) { struct timespec ts; clock_gettime(CLOCK_REALTIME, &ts); return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec; }

int dm_efd_create(uint64_t init, unsigned flags) {   // → the descriptor, or -errno
    if (init > 0xfffffffffffffffeull) return -EFD_EINVAL;
    pthread_mutex_lock(&g_efd_mu);
    int i = 0; while (i < EFD_MAX && g_efd[i].used) i++;
    if (i < EFD_MAX) { g_efd[i].used = 1; g_efd[i].flags = flags; g_efd[i].count = init; }
    pthread_mutex_unlock(&g_efd_mu);
    if (i == EFD_MAX) { LOG("[RED] socket: more than %d event descriptors", EFD_MAX); return -24; }   // EMFILE
    return EFD_FIRST + i;
}
int dm_efd_read(int fd, uint64_t *v) {
    pthread_mutex_lock(&g_efd_mu);
    int i, r = 0;
    for (;;) {
        if ((i = efd_slot(fd)) < 0) { r = -EFD_EBADF; break; }
        if (g_efd[i].count) {
            if (g_efd[i].flags & EFD_SEMAPHORE) { *v = 1; g_efd[i].count--; } else { *v = g_efd[i].count; g_efd[i].count = 0; }
            pthread_cond_broadcast(&g_efd_cv); break;
        }
        if (g_efd[i].flags & EFD_NONBLOCK) { r = -EFD_EAGAIN; break; }
        efd_wait(0);
    }
    pthread_mutex_unlock(&g_efd_mu);
    return r;
}
int dm_efd_write(int fd, uint64_t v) {
    if (v == ~0ull) return -EFD_EINVAL;
    pthread_mutex_lock(&g_efd_mu);
    int i, r = 0;
    for (;;) {
        if ((i = efd_slot(fd)) < 0) { r = -EFD_EBADF; break; }
        if (g_efd[i].count <= 0xfffffffffffffffeull - v) { g_efd[i].count += v; pthread_cond_broadcast(&g_efd_cv); break; }
        if (g_efd[i].flags & EFD_NONBLOCK) { r = -EFD_EAGAIN; break; }
        efd_wait(0);
    }
    pthread_mutex_unlock(&g_efd_mu);
    return r;
}
int dm_efd_close(int fd) {
    pthread_mutex_lock(&g_efd_mu);
    int i = efd_slot(fd); if (i >= 0) { g_efd[i].used = 0; pthread_cond_broadcast(&g_efd_cv); }
    pthread_mutex_unlock(&g_efd_mu);
    return i >= 0 ? 0 : -EFD_EBADF;
}
// Poll on descriptors that are all event descriptors or negative (ignored): → how many have events, waiting up to ms (< 0: no limit; the caller bounds it). -1 when another
// descriptor is among them (the caller answers those as the network cut does)
typedef struct { int32_t fd; int16_t events, revents; } efd_pollfd;
#define EFD_POLLIN 1
#define EFD_POLLOUT 4
#define EFD_POLLNVAL 0x20
int dm_efd_poll(void *fds, uint64_t n, int64_t ms) {
    efd_pollfd *p = fds;
    pthread_mutex_lock(&g_efd_mu);
    for (uint64_t k = 0; k < n; k++) if (p[k].fd >= 0 && efd_slot(p[k].fd) < 0) { pthread_mutex_unlock(&g_efd_mu); return -1; }
    uint64_t deadline = ms >= 0 ? realtime_ns() + (uint64_t)ms * 1000000u : 0;
    int ready;
    for (;;) {
        ready = 0;
        for (uint64_t k = 0; k < n; k++) {
            p[k].revents = 0;
            if (p[k].fd < 0) continue;
            int i = efd_slot(p[k].fd);
            if (i < 0) p[k].revents = EFD_POLLNVAL;   // closed while we waited
            else {
                if ((p[k].events & EFD_POLLIN) && g_efd[i].count) p[k].revents |= EFD_POLLIN;
                if ((p[k].events & EFD_POLLOUT) && g_efd[i].count < 0xfffffffffffffffeull) p[k].revents |= EFD_POLLOUT;
            }
            if (p[k].revents) ready++;
        }
        if (ready || ms == 0 || (deadline && realtime_ns() >= deadline)) break;
        efd_wait(deadline);
    }
    pthread_mutex_unlock(&g_efd_mu);
    return ready;
}
