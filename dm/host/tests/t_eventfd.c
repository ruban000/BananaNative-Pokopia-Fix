// The bsd service's event descriptors as the host keeps them (hle_eventfd.c): gRPC's use in Zelda: Ocarina of Time (EventFd(0, 0) then Close, EventFd(0, 6) non-blocking,
// reads until EAGAIN, writes to wake a poller), semaphore mode, a blocking read woken by another thread, Poll's events, timeout and a descriptor closed under it.
// Compiled with the production source behind a POSIX shim (dm/tools/tests/test_eventfd.py), AddressSanitizer and UBSan
#include EFD_SOURCE
#include <assert.h>
#include <unistd.h>

static int waits;
void dm_thread_native_wait(int entering) { if (entering) waits++; }
static int poller_fd;
static void *writer(void *a) { (void)a; usleep(20000); assert(dm_efd_write(poller_fd, 3) == 0); return NULL; }

int main(void) {
    int probe = dm_efd_create(0, 0); assert(probe >= 0 && dm_efd_is(probe)); assert(dm_efd_close(probe) == 0 && !dm_efd_is(probe));
    assert(dm_efd_close(probe) == -EFD_EBADF);
    int fd = dm_efd_create(0, 6); assert(fd >= 0);   // gRPC's wakeup descriptor: EFD_NONBLOCK | EFD_CLOEXEC
    uint64_t v = 0; assert(dm_efd_read(fd, &v) == -EFD_EAGAIN);
    efd_pollfd p[2] = {{fd, EFD_POLLIN, 0}, {-1, EFD_POLLIN, 0}};
    assert(dm_efd_poll(p, 2, 0) == 0 && p[0].revents == 0);
    assert(dm_efd_write(fd, 1) == 0 && dm_efd_write(fd, 2) == 0);
    assert(dm_efd_poll(p, 2, 50) == 1 && p[0].revents == EFD_POLLIN && p[1].revents == 0);
    assert(dm_efd_read(fd, &v) == 0 && v == 3);
    assert(dm_efd_read(fd, &v) == -EFD_EAGAIN);
    p[0].events = EFD_POLLOUT; assert(dm_efd_poll(p, 1, 0) == 1 && p[0].revents == EFD_POLLOUT);
    assert(dm_efd_write(fd, 0xfffffffffffffffeull) == 0 && dm_efd_write(fd, 1) == -EFD_EAGAIN && dm_efd_write(fd, ~0ull) == -EFD_EINVAL);
    assert(dm_efd_read(fd, &v) == 0 && v == 0xfffffffffffffffeull);
    // a poller waits until another thread writes; a blocking reader in semaphore mode takes one at a time
    poller_fd = dm_efd_create(0, EFD_SEMAPHORE); assert(poller_fd >= 0);
    pthread_t t; pthread_create(&t, NULL, writer, NULL);
    p[0] = (efd_pollfd){poller_fd, EFD_POLLIN, 0};
    assert(dm_efd_poll(p, 1, 2000) == 1 && p[0].revents == EFD_POLLIN && waits > 0);
    pthread_join(t, NULL);
    for (int k = 0; k < 3; k++) { assert(dm_efd_read(poller_fd, &v) == 0 && v == 1); }
    pthread_create(&t, NULL, writer, NULL);
    assert(dm_efd_read(poller_fd, &v) == 0 && v == 1);   // blocks until the writer's 3
    pthread_join(t, NULL);
    // a poll that times out; a descriptor that is not an event descriptor
    p[0] = (efd_pollfd){poller_fd, EFD_POLLIN, 0};
    assert(dm_efd_read(poller_fd, &v) == 0 && dm_efd_read(poller_fd, &v) == 0);
    assert(dm_efd_poll(p, 1, 30) == 0 && p[0].revents == 0);
    efd_pollfd other = {5, EFD_POLLIN, 0}; assert(dm_efd_poll(&other, 1, 0) == -1);
    assert(dm_efd_close(poller_fd) == 0 && dm_efd_close(fd) == 0);
    for (int k = 0; k < EFD_MAX; k++) assert(dm_efd_create(0, 0) >= 0);
    assert(dm_efd_create(0, 0) == -24);
    printf("t_eventfd: passed\n");
    return 0;
}
