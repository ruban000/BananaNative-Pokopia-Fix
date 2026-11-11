// Portable reader for the optional compute-mesh sidecar; no Vulkan device needed.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include "vka_shlib.h"
#define VKA_MESH_CACHE_VERSION 2u
typedef struct {
    char magic[8]; uint64_t key, hash;
    uint32_t stage, words, payload, primitives, corners, cs_bytes, vs_bytes, version, task_limit;
    char chain[16];
} vka_mesh_cache_head;
_Static_assert(sizeof(vka_mesh_cache_head) == 80, "mesh cache header");
enum { VKA_MCR_INT64=1, VKA_MCR_FLOAT64=2, VKA_MCR_INT16=4, VKA_MCR_FLOAT16=8, VKA_MCR_STORAGE16=16, VKA_MCR_STORAGE8=32 };
typedef struct {
    uint32_t local[3], shared, subgroup_ops, features;
} vka_mesh_cache_req;
// A checked header, exact native-entry fingerprint, file length and module magics.
// Success leaves the file positioned at the compute module; caller closes it.
FILE *vka_mesh_cache_open(const char *root, const vka_shlib *L, const dmsh_vk_entry *e, vka_mesh_cache_head *h);
// Read the compute module and derive its actual workgroup storage and capabilities.
// Refuse malformed or unrecognized workgroup layouts instead of claiming support.
int vka_mesh_cache_requirements(FILE *f, const vka_mesh_cache_head *h, vka_mesh_cache_req *r);
