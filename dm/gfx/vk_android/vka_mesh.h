// Experimental task/mesh execution through compute plus a generated vertex shader.
// The optional Mac-built cache is fingerprinted against the unmodified native library.
#ifndef VKA_MESH_H
#define VKA_MESH_H
#include "vka_mesh_cache.h"
// Auxiliary set 3: 0 output words, 1 task records, 2 (task,x,y,z) mapping,
// 3 controls. Controls: task enabled, payload stride in words, map/no-task group
// base, vertices per group (scheduler), logical task group base, stored task
// record base, task count, output group capacity (scheduler). Each task record:
// emit x/y/z, reserved, payload words. Each mesh
// record: vertex count, primitive count, two reserved words, reflected outputs.
struct vka_mesh_data {
    uint32_t words, payload, primitives, corners, task_limit;
    uint32_t *code; size_t bytes;
    VkPipeline pipe[2]; VkShaderModule folded[2];
    dmsh_vk_entry ce, ve; vka_shader compute, vertex;
    prog_t program;
};
int vka_mesh_enabled(void);
int vka_mesh_available(const vka_shlib *L, const dmsh_vk_entry *e);
int vka_mesh_load(vka_shader *s, char *err, size_t errn);
VkDescriptorSetLayout vka_mesh_set_layout(void);
void vka_mesh_pad_layouts(VkDescriptorSetLayout sets[4]);
VkResult vka_mesh_create_pipeline(const VkComputePipelineCreateInfo *ci, uint64_t key, VkPipeline *out);
VkPipeline vka_mesh_pipeline(vka_shader *s, const vka_layout *lay, int gl);
VkPipeline vka_mesh_schedule_pipeline(VkPipelineLayout *layout);
void vka_mesh_flat(uint32_t *w, size_t *bytes);
int vka_exec_flush_wait(void);   // experimental count readback; preserves the submission's scratch lifetime
#endif
