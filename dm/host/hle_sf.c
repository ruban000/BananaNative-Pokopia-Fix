// Services whose client a game links into its own main (nn::sf::hipc): Animal Crossing: New Horizons 3.0.3 carries the clients of olsc:u (cloud save data backup settings,
// main+0x404bc6c) and npns (push notifications). The SDK's client code connects through sm (an abort in sm::Initialize under InitializeHipcServiceResolution, from main+0x5b8198) and talks
// HIPC through svcSendSyncRequest; the host has neither. It hands out sessions of its own instead and answers each request in the message buffer, for the services listed in sf_served:
// success, the output data zeroed, no objects or handles. For olsc that reads as "backup off for every user", so the game leaves the settings alone (main+0x2809c8c asks per user and
// switches backup off where it is on). Any other service name is refused with a red line, as sm refuses a service that is not registered.
// The SDK's own client functions are taken over where they reach the kernel (their calls inside sdk go through its PLT): the session manager's CreateNewSession runs natively on
// DuplicateSession, and ConvertToDomainImpl on QueryPointerBufferSize and ConvertSessionToDomain. DM_SF_TRACE=1 dumps every request.
// Message layout (Atmosphère / libnx): HIPC header {u16 type, counts of X / A / B / W descriptors; u32 data words, receive list, special header flag}, the special header and descriptors,
// then the data words, whose CMIF part starts 16-byte aligned to the buffer: in a domain session the domain header {u8 type, u8 objects, u16 size, u32 object, u32, u32 token} first,
// then {"SFCI", version, command, token} and the arguments. The response is the same with {u32 objects, 3 × u32} and {"SFCO", version, result, token}
// A service with a handler in sf_served (auddmg:u, hle_sf_auddmg.c) answers its commands itself (sf_call in hle.h): its output data, copy handles in the special header, and objects
// handed out as move handles, each a new host session named by its interface (a session that is not a domain; the SDK's Hipc2 proxies of SDK 22 use those, message type 6)
#include "hle.h"
#include <pthread.h>

#define SF_H0 0xd0000u   // session handles: outside the kernel handle table (0x1000 + slot, 4096 slots, hle_thread.c)
#define SF_MAX 64
static struct { char name[40]; int used, domain; unsigned seen; sf_handler fn; } g_sf[SF_MAX];
static pthread_mutex_t g_sf_mu = PTHREAD_MUTEX_INITIALIZER;
// The services the host serves: a handler answers its commands; none, every request succeeds with zeroed output
static const struct { const char *name; sf_handler fn; } sf_served[] = {{"olsc:u", NULL}, {"auddmg:u", dm_auddmg_call}};

static int sf_slot(uint32_t h) {
    unsigned i = h - SF_H0;
    return i < SF_MAX && g_sf[i].used ? (int)i : -1;
}
static uint32_t sf_new_fn(const char *name, int domain, sf_handler fn) {
    pthread_mutex_lock(&g_sf_mu);
    unsigned i = 0; while (i < SF_MAX && g_sf[i].used) i++;
    if (i == SF_MAX) die("sf: more than %d host service sessions", SF_MAX);
    snprintf(g_sf[i].name, sizeof g_sf[i].name, "%s", name); g_sf[i].used = 1; g_sf[i].domain = domain; g_sf[i].seen = ~0u; g_sf[i].fn = fn;
    pthread_mutex_unlock(&g_sf_mu);
    return SF_H0 + i;
}
static uint32_t sf_new(const char *name, int domain) { return sf_new_fn(name, domain, NULL); }

// A host session the host hands out itself, not through sm (the camera context, hle_misc.c): answered like the served services
uint32_t dm_sf_host_session(const char *name) { uint32_t h = sf_new(name, 0); LOG("sf: %s → host session %#x (every request succeeds with zeroed output)", name, h); return h; }

HLE_FORCE("_ZN2nn2sf4hipc31InitializeHipcServiceResolutionEv", "service name resolution (connects to sm); the host resolves the names it serves itself", Result, h_sf_InitRes, void) { return RES_OK; }
HLE_FORCE("_ZN2nn2sf4hipc29FinalizeHipcServiceResolutionEv", "service name resolution (sm)", void, h_sf_FinRes, void) {}
HLE_FORCE("_ZN2nn2sf4hipc20ConnectToHipcServiceEPNS_3svc6HandleEPKc", "connect to a service through sm → a host session for the services the host serves", Result, h_sf_Connect,
          uint32_t *out, const char *name) {
    for (size_t k = 0; k < sizeof sf_served / sizeof *sf_served; k++)
        if (!strncmp(name, sf_served[k].name, 8)) {
            *out = sf_new_fn(sf_served[k].name, 0, sf_served[k].fn);
            LOG("sf: %s → host session %#x (%s)", sf_served[k].name, *out, sf_served[k].fn ? "answered by the host's service" : "every request succeeds with zeroed output");
            return RES_OK;
        }
    LOG("[RED] sf: the game connects to service %.8s itself, which the host does not serve", name);
    return 0xe15u;   // sm ResultNotRegistered
}
HLE_FORCE("_ZN2nn2sf4hipc24CloseClientSessionHandleENS_3svc6HandleE", "close a session (kernel handle) → the host session", void, h_sf_Close, uint32_t h) {
    int i = sf_slot(h);
    if (i < 0) { LOG("[RED] sf: CloseClientSessionHandle on %#x, not a host session", h); return; }
    pthread_mutex_lock(&g_sf_mu); g_sf[i].used = 0; pthread_mutex_unlock(&g_sf_mu);
}
// Control requests: the pointer buffer is the server's receive buffer for X descriptors (none: the host reads them in place), the domain conversion makes the session's object 1, a duplicate
// is a new session to the same object
HLE_FORCE("_ZN2nn2sf4hipc6client22QueryPointerBufferSizeEPtNS_3svc6HandleE", "control request: pointer buffer size of a session → 0 for a host session", Result, h_sf_PtrBuf, uint16_t *out, uint32_t h) {
    if (sf_slot(h) < 0) { LOG("[RED] sf: QueryPointerBufferSize on %#x, not a host session", h); return RES_FAIL; }
    *out = 0; return RES_OK;
}
HLE_FORCE("_ZN2nn2sf4hipc6client22ConvertSessionToDomainEPNS0_4cmif18CmifDomainObjectIdENS_3svc6HandleE", "control request: convert a session to a domain → the host session's object 1", Result, h_sf_ToDomain,
          uint32_t *out, uint32_t h) {
    int i = sf_slot(h);
    if (i < 0) { LOG("[RED] sf: ConvertSessionToDomain on %#x, not a host session", h); return RES_FAIL; }
    g_sf[i].domain = 1; *out = 1; return RES_OK;
}
HLE_FORCE("_ZN2nn2sf4hipc6client16DuplicateSessionEPNS_3svc6HandleES4_", "control request: clone a session → a new host session to the same service", Result, h_sf_Dup, uint32_t *out, uint32_t h) {
    int i = sf_slot(h);
    if (i < 0) { LOG("[RED] sf: DuplicateSession on %#x, not a host session", h); return RES_FAIL; }
    *out = sf_new_fn(g_sf[i].name, g_sf[i].domain, g_sf[i].fn); return RES_OK;
}
HLE_FORCE("_ZN2nn2sf4hipc6client18DuplicateSessionExEPNS_3svc6HandleES4_j", "control request: clone a session (with a tag) → a new host session to the same service", Result, h_sf_DupEx,
          uint32_t *out, uint32_t h, unsigned tag) { (void)tag; return h_sf_Dup(out, h); }

// A buffer descriptor (A / B / W: 3 words) or a pointer descriptor (X: 2 words) → guest address and size (Atmosphère's HipcBufferDescriptor / HipcStaticDescriptor)
static sf_buf sf_desc_buf(const uint32_t *d) {
    uint64_t addr = d[1] | (uint64_t)(d[2] >> 28 & 15) << 32 | (uint64_t)(d[2] >> 2 & 7) << 36, size = d[0] | (uint64_t)(d[2] >> 24 & 15) << 32;
    return (sf_buf){addr, size};
}
static sf_buf sf_desc_static(const uint32_t *d) {
    uint64_t addr = d[1] | (uint64_t)(d[0] >> 12 & 15) << 32 | (uint64_t)(d[0] >> 6 & 63) << 36;
    return (sf_buf){addr, d[0] >> 16};
}
HLE_FORCE("_ZN2nn2sf4hipc15SendSyncRequestENS_3svc6HandleEPvm", "send a request on a session (svcSendSyncRequest) → answered by the host for its sessions", Result, h_sf_Send,
          uint32_t h, uint32_t *m, uint64_t size) {
    int i = sf_slot(h);
    if (i < 0 || size < 0x40) { LOG("[RED] sf: SendSyncRequest on %#x (%llu bytes), not a host session", h, (unsigned long long)size); return 0xe401u; }   // kernel ResultInvalidHandle
    uint8_t *base = (uint8_t *)m;
    unsigned type = m[0] & 0xffff, nx = m[0] >> 16 & 15, na = m[0] >> 20 & 15, nb = m[0] >> 24 & 15, nw = m[0] >> 28 & 15, ndw = m[1] & 0x3ff;
    sf_call c = {.iface = g_sf[i].name};
    uint32_t *p = m + 2;
    if (m[1] >> 31) {   // special header: process ID, copy and move handles
        uint32_t sh = *p++;
        if (sh & 1) { c.has_pid = 1; c.pid = p[0] | (uint64_t)p[1] << 32; p += 2; }
        p += (sh >> 1 & 15) + (sh >> 5 & 15);
    }
    for (unsigned k = 0; k < nx; k++, p += 2) if (k < 4) c.x[c.nx++] = sf_desc_static(p);
    for (unsigned k = 0; k < na; k++, p += 3) if (k < 4) c.a[c.na++] = sf_desc_buf(p);
    for (unsigned k = 0; k < nb; k++, p += 3) if (k < 4) c.b[c.nb++] = sf_desc_buf(p);
    p += nw * 3;
    size_t data = (size_t)((uint8_t *)p - base), cm = (data + 15) & ~(size_t)15;
    int dom = g_sf[i].domain && (type == 4 || type == 6);
    unsigned dtype = dom ? base[cm] : 1, obj = dom ? *(uint32_t *)(base + cm + 4) : 0;
    size_t hin = cm + (dom ? 16 : 0);
    if (hin + 16 > size || data + ndw * 4 > size) { LOG("[RED] sf: %s: request of %u data words does not fit %llu bytes", g_sf[i].name, ndw, (unsigned long long)size); return 0xe401u; }
    uint32_t magic = *(uint32_t *)(base + hin), ver = *(uint32_t *)(base + hin + 4), cmd = *(uint32_t *)(base + hin + 8);
    static int trace = -1; if (trace < 0) trace = getenv("DM_SF_TRACE") && atoi(getenv("DM_SF_TRACE"));
    if (trace) { char hx[3 * 0x80 + 1] = ""; for (unsigned k = 0; k < 0x80 && k < size; k++) snprintf(hx + 3 * k, 4, "%02x ", base[k]); LOG("sf: %s request: %s", g_sf[i].name, hx); }
    if (type != 4 && type != 6) { LOG("[RED] sf: %s: HIPC message type %u not served", g_sf[i].name, type); return 0xe401u; }
    if (dtype == 2) { memset(base, 0, 16); return RES_OK; }   // a domain object closed: nothing to answer
    if (magic != 0x49434653) { LOG("[RED] sf: %s: request without SFCI (%#x)", g_sf[i].name, magic); return 0xe401u; }
    c.cmd = cmd; c.in = base + hin + 16;
    size_t end_in = data + ndw * 4; c.in_len = end_in > hin + 16 ? end_in - (hin + 16) : 0;
    int served = g_sf[i].fn && !dom ? g_sf[i].fn(&c) : 0;
    unsigned key = (obj & 0xff) << 16 | (cmd & 0xffff);   // log each object / command once
    pthread_mutex_lock(&g_sf_mu); int first = g_sf[i].seen != key; g_sf[i].seen = key; pthread_mutex_unlock(&g_sf_mu);
    if (!served) {
        memset(&c.out, 0, sizeof c.out); c.out_len = 0; c.result = RES_OK; c.ncopy = c.nobj = 0;
        if (g_sf[i].fn) LOG("[RED] sf: %s command %u (%u data words in) is not implemented: success, zeroed output", g_sf[i].name, cmd, ndw);
        else if (first || trace) LOG("sf: %s object %u command %u (%u data words in): success, zeroed output", g_sf[i].name, obj, cmd, ndw);
    } else if (first || trace) LOG("sf: %s command %u: result %#x, %zu bytes out, %u handle(s), %u object(s)", g_sf[i].name, cmd, c.result, c.out_len, c.ncopy, c.nobj);
    // The response: a special header when it carries handles (copies, then the objects as move handles), the data words after it, the CMIF part 16-byte aligned; everything after
    // the headers up to the buffer's end reads as zero (zeroed output for a service without a handler)
    uint32_t moves[4]; unsigned nmove = 0;
    for (unsigned k = 0; k < c.nobj && k < 4; k++) moves[nmove++] = sf_new_fn(c.obj[k], 0, g_sf[i].fn);
    size_t end = size < 0x1000 ? size : 0x1000;
    memset(base, 0, end);
    unsigned nh = c.ncopy + nmove;
    size_t odata = nh ? 12 + 4 * (size_t)nh : 8, out = ((odata + 15) & ~(size_t)15) + (dom ? 16 : 0);
    if (out + 16 + c.out_len > end) { LOG("[RED] sf: %s command %u: %zu bytes of output do not fit the message", g_sf[i].name, cmd, c.out_len); return 0xe401u; }
    if (nh) {
        m[1] = 1u << 31; m[2] = c.ncopy << 1 | nmove << 5;
        for (unsigned k = 0; k < c.ncopy; k++) m[3 + k] = c.copy[k];
        for (unsigned k = 0; k < nmove; k++) m[3 + c.ncopy + k] = moves[k];
        m[1] |= (uint32_t)((out + 16 + c.out_len + 3 - odata) / 4 + 4);   // data words: the padding to the CMIF part, its header and the output, and the server's 16 bytes of slack
    } else m[1] = (uint32_t)((end - 8) / 4 < 0x3ff ? (end - 8) / 4 : 0x3ff);
    uint32_t *o = (uint32_t *)(base + out); o[0] = 0x4f434653; o[1] = ver; o[2] = c.result; o[3] = 0;   // "SFCO"; a domain's out-object count before it stays 0
    memcpy(base + out + 16, c.out, c.out_len);
    return RES_OK;
}
