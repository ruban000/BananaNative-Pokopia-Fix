// Host sessions of hle_sf.c: the decoded request a service answers. Plain C, no host headers: the Linux tests compile it beside the services behind a POSIX shim
#pragma once
#include <stddef.h>
#include <stdint.h>
// hle_sf.c: one request on a host session, decoded, and its answer. A service that answers its commands itself (sf_served in hle_sf.c) gets the request's raw data, its
// buffers (A: in, B: out, X: in by pointer; guest addresses are host addresses), the process ID if sent, and fills the output data, the result, copy handles (kernel objects
// it hands over, e.g. an event's read end) and objects (each a new host session named by its interface, handed out as a move handle)
typedef struct { uint64_t addr, size; } sf_buf;
typedef struct {
    const char *iface;            // the session's interface name: the service name for the session sm hands out, the name given to an object otherwise
    unsigned cmd;
    const uint8_t *in; size_t in_len;   // the raw arguments after the CMIF header (data words, aligned as the client put them)
    uint64_t pid; int has_pid;
    sf_buf a[4], b[4], x[4]; unsigned na, nb, nx;
    uint8_t out[0x100]; size_t out_len;
    uint32_t result;
    uint32_t copy[4]; unsigned ncopy;
    const char *obj[4]; unsigned nobj;  // objects to hand out, by interface name (hle_sf.c makes their sessions)
} sf_call;
// Answers a request; returns 0 when the command is not one it knows (hle_sf.c logs a red line and answers success with zeroed output)
typedef int (*sf_handler)(sf_call *c);
int dm_auddmg_call(sf_call *c);   // hle_sf_auddmg.c: the Switch 2 audio device manager (auddmg:u)
