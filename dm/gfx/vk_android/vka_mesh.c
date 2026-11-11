#if defined(__ANDROID__)
#include "vka.h"
#include "vka_mesh.h"
#include "vk_mesh_cache_gen.h"
#include "vka_spvpatch.h"
#include "../nvn/nvn_obj.h"
#include "../../host/plat/plat.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int vka_mesh_enabled(void) { const char *p = getenv("DM_VK_MESH_CACHE"); return p && *p; }
int vka_mesh_available(const vka_shlib *L, const dmsh_vk_entry *e) { vka_mesh_cache_head h; FILE *f = vka_mesh_cache_open(getenv("DM_VK_MESH_CACHE"), L, e, &h); if (f) fclose(f); return f != NULL; }
static VkShaderModule module(const uint32_t *w, size_t bytes) {
    VkShaderModuleCreateInfo ci = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, .codeSize = bytes, .pCode = w}; VkShaderModule m = VK_NULL_HANDLE;
    if (bytes < 20 || w[0] != 0x07230203 || vk.vkCreateShaderModule(V.dev, &ci, NULL, &m)) return VK_NULL_HANDLE; return m;
}
static uint32_t task_domain(const vka_mesh_cache_head *head, const uint32_t *code) {
    static const struct { uint64_t key; const char *digest; uint32_t limit; } domains[] = DM_GAME_MESH_TASK_DOMAIN;
    if (head->stage != 6 || head->vs_bytes || !head->task_limit) return 0;
    for (unsigned i = 0; i < DM_GAME_NMESH_TASK_DOMAIN; ++i) if (domains[i].key == head->key) {
        plat_sha256_t sha;
        uint8_t digest[32];
        plat_sha256_init(&sha);
        plat_sha256_update(&sha, head, sizeof *head);
        plat_sha256_update(&sha, code, head->cs_bytes);
        plat_sha256_final(digest, &sha);
        if (!memcmp(digest, domains[i].digest, sizeof digest)) return domains[i].limit;
    }
    return 0;
}
int vka_mesh_load(vka_shader *s, char *err, size_t errn) {
    vka_mesh_cache_head h; FILE *f = vka_mesh_cache_open(getenv("DM_VK_MESH_CACHE"), s->lib, s->e, &h);
    if (!f) { snprintf(err, errn, "missing or stale compute mesh cache"); return 0; }
    struct vka_mesh_data *m = calloc(1, sizeof *m); uint32_t *vs = h.vs_bytes ? malloc(h.vs_bytes) : NULL;
    if (!m) { free(vs); fclose(f); return 0; }
    m->code = malloc(h.cs_bytes); m->bytes = h.cs_bytes;
    int ok = m->code && (!h.vs_bytes || vs) && fread(m->code, 1, h.cs_bytes, f) == h.cs_bytes && (!h.vs_bytes || fread(vs, 1, h.vs_bytes, f) == h.vs_bytes) && fgetc(f) == EOF;
    fclose(f);
    if (!ok) { free(vs); free(m->code); free(m); snprintf(err, errn, "truncated compute mesh cache"); return 0; }
    m->words = h.words; m->payload = h.payload; m->primitives = h.primitives; m->corners = h.corners; m->task_limit = h.task_limit;
    s->mesh_task_domain = task_domain(&h, m->code);
    m->ce = *s->e; m->ce.stage = DMSH_VK_STAGE_CS;
    m->compute = *s; m->compute.e = &m->ce; m->compute.emulated = 1;
    if (h.vs_bytes) {
        m->ve.key = s->key ^ 0x434d565300000000ull; m->ve.stage = DMSH_VK_STAGE_VS;
        m->vertex.key = m->ve.key; m->vertex.e = &m->ve; m->vertex.lib = s->lib; m->vertex.emulated = 1;
        m->vertex.mods[0] = module(vs, h.vs_bytes);
        if (!m->vertex.mods[0]) { free(vs); free(m->code); free(m); snprintf(err, errn, "generated vertex module failed"); return 0; }
        m->program.magic = MAGIC('P','R','O','G'); m->program.serial = nvn_new_serial(); m->program.nstage = 1;
        vka_progsh ps = {0}; ps.sh[0] = &m->vertex; vka_prog_put(m->program.serial, &ps);
    }
    free(vs); s->mesh = m; s->emulated = 1;
    VLOG("compute mesh cache: %016llx, stage %u, output %u words, payload %u words", (unsigned long long)s->key, h.stage, h.words, h.payload);
    return 1;
}
VkDescriptorSetLayout vka_mesh_set_layout(void) {
    static VkDescriptorSetLayout dsl; if (dsl) return dsl;
    VkDescriptorSetLayoutBinding b[4]; for (int i = 0; i < 4; i++) b[i] = (VkDescriptorSetLayoutBinding){i, VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1, VK_SHADER_STAGE_COMPUTE_BIT | VK_SHADER_STAGE_VERTEX_BIT, NULL};
    VkDescriptorSetLayoutCreateInfo ci = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, .bindingCount = 4, .pBindings = b};
    if (vk.vkCreateDescriptorSetLayout(V.dev, &ci, NULL, &dsl)) return VK_NULL_HANDLE; return dsl;
}
void vka_mesh_pad_layouts(VkDescriptorSetLayout sets[4]) {
    static VkDescriptorSetLayout empty;
    if (!empty) { VkDescriptorSetLayoutCreateInfo ci = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO}; vk.vkCreateDescriptorSetLayout(V.dev, &ci, NULL, &empty); }
    if (!sets[1]) sets[1] = empty; if (!sets[2]) sets[2] = empty;
}
static VkPipeline mesh_aux_pipeline(const char *name, uint64_t key, VkPipelineLayout *layout) {
    const char *root = getenv("DM_VK_MESH_CACHE");
    char path[1400];
    if (!root || snprintf(path, sizeof path, "%s/%s", root, name) >= sizeof path) return VK_NULL_HANDLE;
    FILE *f = fopen(path, "rb");
    if (!f) return VK_NULL_HANDLE;
    uint32_t w[4096]; size_t bytes = fread(w, 1, sizeof w, f); int valid = feof(f) && !(bytes & 3); fclose(f);
    VkShaderModule mod = valid ? module(w, bytes) : VK_NULL_HANDLE;
    VkDescriptorSetLayout dsl = vka_mesh_set_layout();
    VkPipelineLayoutCreateInfo li = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, .setLayoutCount = 1, .pSetLayouts = &dsl};
    VkPipeline pipe = VK_NULL_HANDLE;
    if (mod && dsl && !vk.vkCreatePipelineLayout(V.dev, &li, NULL, layout)) {
        VkComputePipelineCreateInfo ci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, .layout = *layout,
            .stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = mod, .pName = "main"}};
        vka_mesh_create_pipeline(&ci, key, &pipe);
    }
    if (mod) vk.vkDestroyShaderModule(V.dev, mod, NULL);
    return pipe;
}
VkPipeline vka_mesh_schedule_pipeline(VkPipelineLayout *layout) {
    static VkPipeline pipe; static VkPipelineLayout lay; static int tried;
    if (!tried) { tried = 1; pipe = mesh_aux_pipeline("schedule-" VKA_MESH_CACHE_CHAIN ".spv", 0x434d534348454401ull, &lay); }
    *layout = lay; return pipe;
}
VkPipeline vka_mesh_indirect_pipeline(VkPipelineLayout *layout) {
    static VkPipeline pipe; static VkPipelineLayout lay; static int tried;
    if (!tried) { tried = 1; pipe = mesh_aux_pipeline("indirect-v1.spv", 0x434d494e44495201ull, &lay); }
    *layout = lay; return pipe;
}
VkPipeline vka_mesh_bounded_pipeline(int schedule, VkPipelineLayout *layout) {
    static VkPipeline pipes[2];
    static VkPipelineLayout layouts[2];
    static int tried[2];
    if (!tried[schedule]) {
        tried[schedule] = 1;
        const char *file = schedule ? "bounded-schedule-v1.spv" : "bounded-prepare-v1.spv";
        pipes[schedule] = mesh_aux_pipeline(file, 0x434d424f554e4400ull + (unsigned)schedule, &layouts[schedule]);
    }
    *layout = layouts[schedule];
    return pipes[schedule];
}
VkPipeline vka_mesh_pipeline(vka_shader *s, const vka_layout *lay, int gl) {
    struct vka_mesh_data *m = s->mesh; if (!m) return VK_NULL_HANDLE; gl = gl != 0;
    if (m->pipe[gl]) return m->pipe[gl];
    size_t n = 0; uint32_t *fold = vka_spv_spec_freeze(m->code, m->bytes / 4, DMSH_VK_SPEC_GL_DEPTH, gl, &n);
    m->folded[gl] = module(fold ? fold : m->code, fold ? 4 * n : m->bytes); free(fold);
    if (!m->folded[gl]) return VK_NULL_HANDLE;
    VkBool32 value = gl; VkSpecializationMapEntry me = {DMSH_VK_SPEC_GL_DEPTH, 0, sizeof value}; VkSpecializationInfo spec = {1, &me, sizeof value, &value};
    uint32_t pin = (s->e->flags & DMSH_VK_F_SUBGROUP) ? vka_shader_sg_pin(DMSH_VK_STAGE_CS) : 0;
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo sg = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO, .requiredSubgroupSize = pin};
    VkComputePipelineCreateInfo ci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, .layout = lay->layout,
        .stage = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, .pNext = pin ? &sg : NULL, .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = m->folded[gl], .pName = "main", .pSpecializationInfo = &spec}};
    VkResult r = vka_mesh_create_pipeline(&ci, s->key, &m->pipe[gl]);
    if (r) VRED("compute mesh pipeline %016llx: %d", (unsigned long long)s->key, r);
    return m->pipe[gl];
}
// Flat per-primitive values are duplicated on every generated primitive corner.
// This applies to fragment variants too, including integer-output and texture retypes.
void vka_mesh_flat(uint32_t *w, size_t *bytes) {
    if (!vka_mesh_enabled() || *bytes < 20) return;
    size_t nw = *bytes / 4; int fragment = 0, per = 0;
    for (size_t i = 5; i < nw;) { uint32_t n = w[i] >> 16, op = w[i] & 65535; if (!n || n > nw - i) return;
        if (op == 15 && n >= 3 && w[i + 1] == 4) fragment = 1;
        if ((op == 71 && n >= 3 && w[i + 2] == 5271) || (op == 72 && n >= 4 && w[i + 3] == 5271)) per = 1; i += n; }
    if (!fragment || !per) return;
    size_t o = 5;
    for (size_t i = 5; i < nw;) { uint32_t n = w[i] >> 16, op = w[i] & 65535;
        int drop = op == 17 && n == 2 && w[i + 1] == 5283;
        if (op == 10 && n >= 2 && !strcmp((const char *)(w + i + 1), "SPV_EXT_mesh_shader")) drop = 1;
        if (op == 71 && n >= 3 && w[i + 2] == 5271) w[i + 2] = 14;
        if (op == 72 && n >= 4 && w[i + 3] == 5271) w[i + 3] = 14;
        if (!drop) { memmove(w + o, w + i, 4 * n); o += n; } i += n;
    }
    *bytes = 4 * o;
}
#endif
