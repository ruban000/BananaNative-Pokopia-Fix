// auddmg:u (hle_sf_auddmg.c) through the host sessions of hle_sf.c, with requests built as SDK 22.2.8's nn::audio::devmgr client builds them (Zelda: Ocarina of Time's
// sdk 1643a838…: InitializeImpl sdk+0x2ac7f0 and its sender +0x2ad8e0, the IUserSession senders +0x2adde0, +0x134360, +0x1a3090, +0x2adf70, +0x2ae100, +0x2ae280) and replies
// read as its parser reads them (the CMIF part 16-byte aligned after the data words' start, the move handle of the object in the special header).
// Compiled with the production sources behind a POSIX shim (dm/tools/tests/test_sf_auddmg.py), AddressSanitizer and UBSan
#include SF_SOURCE
#include AUDDMG_SOURCE
#include <assert.h>
#include <sys/mman.h>

static unsigned kevents, dups;
uint32_t hle_kevent_new(uint32_t *write_end) { kevents++; *write_end = 0x2001; return 0x2002; }
uint32_t hle_dup_readable_handle(uint32_t h) { assert(h == 0x2002); return 0x3000 + ++dups; }

static uint32_t msg[0x40];
// The request header and the CMIF part as the client lays them out: type 6, A / B descriptors, data words, an optional process ID
static uint32_t *request(unsigned na, unsigned nb, unsigned ndw, int pid, unsigned cmd) {
    memset(msg, 0, sizeof msg);
    msg[0] = 6 | na << 20 | nb << 24; msg[1] = ndw | (pid ? 1u << 31 : 0);
    unsigned w = 2;
    if (pid) { msg[w++] = 1; w += 2; }   // special header: send the process ID (the kernel fills it)
    return msg + w;   // descriptors from here
}
static uint8_t *cmif(uint32_t *after_desc, unsigned cmd) {
    uint8_t *c = (uint8_t *)(((uintptr_t)after_desc + 15) & ~(uintptr_t)15);
    uint32_t h[4] = {0x49434653, 1, cmd, 0}; memcpy(c, h, 16);
    return c + 16;
}
static void desc(uint32_t *d, const void *p, size_t n, unsigned mode) {
    uint64_t a = (uintptr_t)p;
    d[0] = (uint32_t)n; d[1] = (uint32_t)a; d[2] = mode | (uint32_t)(a >> 36 & 7) << 2 | (uint32_t)(n >> 32 & 15) << 24 | (uint32_t)(a >> 32 & 15) << 28;
}
// The reply as the client's parser reads it: → the result; the output data, the copy and move handles
static uint32_t reply(const uint8_t **out, uint32_t *copy, uint32_t *move) {
    unsigned w = 2, nc = 0, nm = 0;
    if (msg[1] >> 31) { uint32_t sh = msg[w++]; nc = sh >> 1 & 15; nm = sh >> 5 & 15; }
    for (unsigned k = 0; k < nc; k++) { if (copy) copy[k] = msg[w]; w++; }
    for (unsigned k = 0; k < nm; k++) { if (move) move[k] = msg[w]; w++; }
    const uint8_t *c = (const uint8_t *)(((uintptr_t)(msg + w) + 15) & ~(uintptr_t)15);
    uint32_t h[4]; memcpy(h, c, 16);
    assert(h[0] == 0x4f434653);
    assert((size_t)(c + 16 - (const uint8_t *)msg) <= (2 + (msg[1] >> 31) + nc + nm) * 4u + (msg[1] & 0x3ff) * 4u);   // the output lies inside the data words
    if (out) *out = c + 16;
    return h[2];
}
// HIPC descriptors carry 39-bit addresses (a Switch's address space; the guest's memory on the host lies below 2^39): the buffers live in a low mapping
static uint8_t *low; static size_t low_used;
static void *lowbuf(size_t n) { void *p = low + low_used; low_used += (n + 15) & ~(size_t)15; assert(low_used <= 0x100000); return p; }
static const char *lowstr(const char *s) { char *p = lowbuf(strlen(s) + 1); strcpy(p, s); return p; }
static uint32_t name_call(uint32_t s, unsigned cmd, const char *name, void *outbuf, size_t outlen, const void *in, size_t inlen, const uint8_t **out, uint32_t *copy) {
    uint32_t *d = request(1, outbuf ? 1 : 0, 12, 0, cmd);
    name = lowstr(name); desc(d, name, strlen(name), 0); d += 3;
    if (outbuf) { desc(d, outbuf, outlen, 0); d += 3; }
    memcpy(cmif(d, cmd), in, inlen);
    assert(h_sf_Send(s, msg, 0x100) == 0);
    return reply(out, copy, NULL);
}

int main(void) {
    low = mmap((void *)0x40000000, 0x100000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    assert(low == (void *)0x40000000);
    uint32_t root = 0;
    assert(h_sf_Connect(&root, "auddmg:u") == 0 && root);
    uint32_t refused = 0; assert(h_sf_Connect(&refused, "audsmx:u") == 0xe15u);
    // IUserSessionManager command 0: {u32 2, u64 ARUID, u64 0} and the process ID → an IUserSession as a move handle
    uint32_t *d = request(0, 0, 14, 1, 0);
    uint8_t *in = cmif(d, 0); uint64_t aruid = 0x77; uint32_t two = 2;
    memcpy(in, &two, 4); memcpy(in + 8, &aruid, 8);
    assert(h_sf_Send(root, msg, 0x100) == 0);
    uint32_t sess = 0; assert(reply(NULL, NULL, &sess) == 0 && sess && sess != root);
    // 0 ListVirtualDeviceNames: inputs → none, outputs → one
    char (*names)[0x100] = lowbuf(8 * 0x100); memset(names, 0x55, 8 * 0x100);
    for (unsigned f = 1; f <= 2; f++) {
        d = request(0, 1, 10, 0, 0); desc(d, names, 8 * 0x100, 0); in = cmif(d + 3, 0);
        in[0] = 0; memcpy(in + 4, &f, 4);
        assert(h_sf_Send(sess, msg, 0x100) == 0);
        const uint8_t *out; assert(reply(&out, NULL, NULL) == 0);
        uint32_t count; memcpy(&count, out, 4);
        assert(count == (f == 1 ? 1u : 0u));
    }
    assert(!strcmp(names[0], DEVMGR_NAME));
    // 100 GetDefaultVirtualDeviceName: output → the listed name; input → refused
    char *def = lowbuf(0x100); unsigned f = 1;
    d = request(0, 1, 9, 0, 100); desc(d, def, 0x100, 0); memcpy(cmif(d + 3, 100), &f, 4);
    assert(h_sf_Send(sess, msg, 0x100) == 0 && reply(NULL, NULL, NULL) == 0 && !strcmp(def, DEVMGR_NAME));
    f = 2; d = request(0, 1, 9, 0, 100); desc(d, def, 0x100, 0); memcpy(cmif(d + 3, 100), &f, 4);
    assert(h_sf_Send(sess, msg, 0x100) == 0 && reply(NULL, NULL, NULL) != 0);
    // 200 IsVirtualDeviceAvailable: the result is the answer
    f = 1; assert(name_call(sess, 200, DEVMGR_NAME, NULL, 0, &f, 4, NULL, NULL) == 0);
    assert(name_call(sess, 200, "AudioTvOutput", NULL, 0, &f, 4, NULL, NULL) != 0);
    // 300 IsVirtualDeviceStreamFormatSupported: {format, channels, rate, functionality}
    uint32_t fmt[4] = {2, 2, 48000, 1}; assert(name_call(sess, 300, DEVMGR_NAME, NULL, 0, fmt, 16, NULL, NULL) == 0);
    uint32_t bad[4] = {9, 2, 48000, 1}; assert(name_call(sess, 300, DEVMGR_NAME, NULL, 0, bad, 16, NULL, NULL) != 0);
    // 401 GetVirtualDeviceProfile: what GetVirtualOutputDeviceProperties and OutputStreamImpl::Open read
    uint8_t *prof = lowbuf(0x228); memset(prof, 0xaa, 0x228);
    assert(name_call(sess, 401, DEVMGR_NAME, prof, 0x228, &f, 4, NULL, NULL) == 0);
    uint32_t ch, id; uint64_t lat; memcpy(&ch, prof + 0x108, 4); memcpy(&lat, prof + 0x218, 8); memcpy(&id, prof + 0x220, 4);
    assert(!strcmp((char *)prof, DEVMGR_NAME) && ch == 2 && lat == DEVMGR_LATENCY_NS && id != 0);
    // 500 AcquireVirtualDeviceStateChangeEvent: a copy handle, one kernel event for every client
    uint32_t copy[2] = {0};
    assert(name_call(sess, 500, DEVMGR_NAME, NULL, 0, &f, 4, NULL, copy) == 0 && copy[0] == 0x3001);
    assert(name_call(sess, 500, DEVMGR_NAME, NULL, 0, &f, 4, NULL, copy) == 0 && copy[0] == 0x3002 && kevents == 1);
    // 1000 GetDeviceStreamFormat
    const uint8_t *out; assert(name_call(sess, 1000, DEVMGR_NAME, NULL, 0, &f, 4, &out, NULL) == 0);
    uint32_t sf[3]; memcpy(sf, out, 12); assert(sf[0] == 2 && sf[1] == 2 && sf[2] == 48000);
    // A command the service does not know: success with zeroed output (logged in red)
    d = request(0, 0, 9, 0, 1500); cmif(d, 1500);
    assert(h_sf_Send(sess, msg, 0x100) == 0 && reply(&out, NULL, NULL) == 0);
    memset(msg, 0, sizeof msg); msg[0] = 2; assert(h_sf_Send(sess, msg, 0x100) == 0);   // the Close message devmgr::Finalize sends before closing the handle
    h_sf_Close(sess); h_sf_Close(root);
    printf("t_sf_auddmg: passed\n");
    return 0;
}
