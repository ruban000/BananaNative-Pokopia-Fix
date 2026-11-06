// auddmg:u, the Switch 2 audio device manager (nn::audio::devmgr), answered as a host session of hle_sf.c. The SDK's devmgr::Initialize connects to it through sm
// (ConnectToHipcService) and aborts on a refusal (Result 0xa99: Zelda: Ocarina of Time's first boot, from nn::audio::ListVirtualInputDevices). The SDK's public calls
// (ListVirtualOutputDevices / ListVirtualInputDevices, GetDefaultVirtualOutputDevice, GetVirtualOutputDeviceProperties, Is…StreamFormatSupported, and OpenOutputStream /
// OpenInputStream, which take the device's profile before they create a sound mixer stream through audsmx:u) all come here.
// Read from SDK 22.2.8's client (Ocarina of Time's sdk 1643a838…; nn::sf Hipc2 proxies, message type 6, not domains):
//   IUserSessionManager (the session sm hands out), command 0: in {u32 2, u64 applet resource user ID, u64 0} and the process ID (InitializeImpl, sdk+0x2ac97c) → an
//   IUserSession object (a move handle; CreateProxyContext asks for another one for an applet).
//   IUserSession commands (CmifProxy vtable order = the devmgr::detail::*Impl calls): 0 ListVirtualDeviceNames (B: names, 0x100 bytes each; in {u8 flag, u32 functionality}
//   → u32 count), 100 GetDefaultVirtualDeviceName (B: name; in u32 functionality), 200 IsVirtualDeviceAvailable (A: name; in u32 functionality; the result is the answer),
//   300 IsVirtualDeviceStreamFormatSupported (A: name; in {StreamFormat, u32 functionality}), 401 GetVirtualDeviceProfile (A: name, B: 0x228-byte profile; in u32
//   functionality), 500 / 600 Acquire / ReleaseVirtualDeviceStateChangeEvent (A: name; in u32 functionality; 500 → a copy handle), 700 AttachStream, 800 DetachStream (in u32
//   stream ID), 900 GetPhysicalDeviceName (A: name, B: name), 1000 GetDeviceStreamFormat (A: name; in u32 functionality → StreamFormat), 1500 GetMixCount, 1550
//   GetMixTopology, 1600 GetMixNode, 2000 IsCaptureDeviceStreamFormatSupported, 2100 AttachCaptureStream, 2301 GetCaptureDeviceProfile, 3000 AttachInjectionStream.
//   Device functionality: 1 output (GetDefaultVirtualOutputDevice), 2 input (ListVirtualInputDevices). StreamFormat: {u32 sample format (1..5: PCM 8 / 16 / 24 / 32 bit,
//   float), s32 channels, s32 sample rate} (sysmix OpenStreamImpl). Profile: the device name at +0, the channel count at +0x108 (GetVirtualOutputDeviceProperties copies it
//   and the u64 at +0x218), the latency in ns at +0x218 (OutputStreamImpl::Open refuses a stream shorter than latency × sample rate frames), the device ID at +0x220 (the
//   first argument of sysmix CreateStream).
// The host has one output, the one its audio renderer plays through (stereo, 48 kHz), and no microphone: one output device, no input device. The device never changes, so
// its state change event is never signalled. The fields of the profile at +0x100, +0x104, +0x10c and +0x110 are not read by the SDK's paths above: the host fills the
// sample rate at +0x10c and the physical device's name at +0x110, as a reading of another emulator (Eglantine's nx2_audio_device_manager.h) suggests; not verified.
// Streams (700 / 800 and audsmx:u) are not served yet: those commands log a red line.
#include "hle.h"
#include <pthread.h>

#define DEVMGR_SESSION "auddmg:u IUserSession"
#define DEVMGR_NAME "AudioVirtualOutput"   // the host's own name for its one virtual output device: the game passes it back, it never shows it
#define DEVMGR_PHYSICAL "AudioBuiltInSpeakerOutput"   // hle_audren.c's output device name
#define DEVMGR_FUNC_OUTPUT 1u
#define DEVMGR_LATENCY_NS 10000000ull     // the output's latency the profile reports (the AAudio / Core Audio buffer is shorter); bounds the shortest output stream
#define RES_AUD_NO_DEVICE 0x1499u         // nn::audio (module 153) description 10: what sysmix OpenStreamImpl answers for a stream it cannot open; here: no such device

static uint32_t u32_at(const sf_call *c, size_t off) { uint32_t v = 0; if (off + 4 <= c->in_len) memcpy(&v, c->in + off, 4); return v; }
static void put32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
// The device name in an A buffer (not terminated: the client sends its length)
static int devmgr_is_ours(const sf_call *c, unsigned functionality) {
    if (functionality != DEVMGR_FUNC_OUTPUT || c->na < 1) return 0;
    size_t n = strlen(DEVMGR_NAME);
    const char *s = (const char *)(uintptr_t)c->a[0].addr;
    size_t len = c->a[0].size; while (len && !s[len - 1]) len--;
    return s && len == n && !memcmp(s, DEVMGR_NAME, n);
}
static void devmgr_name_out(const sf_buf *b, const char *name) {
    if (!b->addr || !b->size) return;
    char *o = (char *)(uintptr_t)b->addr; size_t n = b->size < 0x100 ? b->size : 0x100;
    memset(o, 0, n); memcpy(o, name, strlen(name) < n - 1 ? strlen(name) : n - 1);
}

int dm_auddmg_call(sf_call *c) {
    if (!strcmp(c->iface, "auddmg:u")) {
        if (c->cmd != 0) return 0;
        LOG("audio devmgr: user session opened (argument %u, applet resource user ID %#llx, process ID %s)", u32_at(c, 0),
            (unsigned long long)(c->in_len >= 16 ? *(const uint64_t *)(c->in + 8) : 0), c->has_pid ? "sent" : "not sent");
        c->obj[c->nobj++] = DEVMGR_SESSION; return 1;
    }
    if (strcmp(c->iface, DEVMGR_SESSION)) return 0;
    switch (c->cmd) {
    case 0: {   // ListVirtualDeviceNames
        unsigned f = u32_at(c, 4), count = 0;
        if (f == DEVMGR_FUNC_OUTPUT && c->nb >= 1 && c->b[0].size >= 0x100) { devmgr_name_out(&c->b[0], DEVMGR_NAME); count = 1; }
        LOG("audio devmgr: %s devices listed: %u", f == DEVMGR_FUNC_OUTPUT ? "output" : f == 2 ? "input" : "other", count);
        put32(c->out, count); c->out_len = 4; return 1;
    }
    case 100: {   // GetDefaultVirtualDeviceName
        unsigned f = u32_at(c, 0);
        if (f != DEVMGR_FUNC_OUTPUT) { LOG("audio devmgr: default device of functionality %u asked: the host has none", f); c->result = RES_AUD_NO_DEVICE; return 1; }
        if (c->nb >= 1) devmgr_name_out(&c->b[0], DEVMGR_NAME);
        return 1;
    }
    case 200:   // IsVirtualDeviceAvailable
        if (!devmgr_is_ours(c, u32_at(c, 0))) c->result = RES_AUD_NO_DEVICE;
        return 1;
    case 300: {   // IsVirtualDeviceStreamFormatSupported: what the host's mixer can take (it converts sample formats and rates, and mixes down to stereo)
        uint32_t fmt = u32_at(c, 0), ch = u32_at(c, 4), rate = u32_at(c, 8);
        int ok = devmgr_is_ours(c, u32_at(c, 12)) && fmt >= 1 && fmt <= 5 && ch >= 1 && ch <= 6 && rate >= 8000 && rate <= 48000;
        if (!ok) { LOG("audio devmgr: stream format %u, %u channels, %u Hz not supported", fmt, ch, rate); c->result = RES_AUD_NO_DEVICE; }
        return 1;
    }
    case 401: {   // GetVirtualDeviceProfile
        if (!devmgr_is_ours(c, u32_at(c, 0)) || c->nb < 1 || c->b[0].size < 0x228) { c->result = RES_AUD_NO_DEVICE; return 1; }
        uint8_t *pr = (uint8_t *)(uintptr_t)c->b[0].addr;
        memset(pr, 0, 0x228);
        memcpy(pr, DEVMGR_NAME, strlen(DEVMGR_NAME));
        put32(pr + 0x108, 2); put32(pr + 0x10c, 48000);
        memcpy(pr + 0x110, DEVMGR_PHYSICAL, strlen(DEVMGR_PHYSICAL));
        uint64_t lat = DEVMGR_LATENCY_NS; memcpy(pr + 0x218, &lat, 8);
        put32(pr + 0x220, 1);
        return 1;
    }
    case 500: {   // AcquireVirtualDeviceStateChangeEvent: one kernel event for the device, never signalled (the device never changes); each client gets its own read end
        if (!devmgr_is_ours(c, u32_at(c, 0))) { c->result = RES_AUD_NO_DEVICE; return 1; }
        static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER; static uint32_t read_end, write_end;
        pthread_mutex_lock(&mu);
        if (!read_end) read_end = hle_kevent_new(&write_end);
        uint32_t h = hle_dup_readable_handle(read_end);
        pthread_mutex_unlock(&mu);
        if (!h) { LOG("[RED] audio devmgr: no handle for the device's state change event"); c->result = RES_FAIL; return 1; }
        c->copy[c->ncopy++] = h; return 1;
    }
    case 600: return 1;   // ReleaseVirtualDeviceStateChangeEvent: the client closes its handle itself
    case 900:   // GetPhysicalDeviceName
        if (!devmgr_is_ours(c, u32_at(c, 0))) { c->result = RES_AUD_NO_DEVICE; return 1; }
        if (c->nb >= 1) devmgr_name_out(&c->b[0], DEVMGR_PHYSICAL);
        return 1;
    case 1000:   // GetDeviceStreamFormat: the host's output (PCM 16-bit, stereo, 48 kHz)
        if (!devmgr_is_ours(c, u32_at(c, 0))) { c->result = RES_AUD_NO_DEVICE; return 1; }
        put32(c->out, 2); put32(c->out + 4, 2); put32(c->out + 8, 48000); c->out_len = 12; return 1;
    }
    return 0;
}
