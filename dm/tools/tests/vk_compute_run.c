// Runs one compute SPIR-V module once (one workgroup) on a Vulkan device (the tests use Mesa's lavapipe: VK_ICD_FILENAMES=.../lvp_icd.json), for the translator's numeric tests
// (test_ampere_lowerings.py). No game data: the test writes the module and the buffers.
// Usage: vk_compute_run <module.spv> <resource>...   resource = set,binding,kind,file[,w,h,layers,array]
//   kind u  a uniform buffer holding the file's bytes
//   kind s  a storage buffer holding the file's bytes, written back to the file after the dispatch
//   kind i  a storage image R32_UINT, (file bytes / 4) x 1 texels from the file, written back after the dispatch
//   kind t  a combined image sampler: an R8G8B8A8_UNORM image w x h with `layers` layers, viewed as a 2D array when array is 1, else 2D (the file is not read)
// Exit 0 when the dispatch ran; a Vulkan error prints its step and result.
#include <vulkan/vulkan_core.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CK(x) do { VkResult r_ = (x); if (r_ != VK_SUCCESS) { fprintf(stderr, "%s: %d\n", #x, r_); exit(2); } } while (0)

typedef struct { int set, binding; char kind; const char *file; int w, h, layers, array; VkBuffer buf; VkImage img; VkImageView view; VkSampler smp; VkDeviceMemory mem; size_t size; } res_t;

static VkDevice dev; static VkPhysicalDevice phys;

static void *slurp(const char *p, size_t *n) {
    FILE *f = fopen(p, "rb"); if (!f) { perror(p); exit(2); }
    fseek(f, 0, SEEK_END); *n = (size_t)ftell(f); fseek(f, 0, SEEK_SET);
    void *d = malloc(*n ? *n : 1); if (*n && fread(d, 1, *n, f) != *n) { perror(p); exit(2); }
    fclose(f); return d;
}

static VkDeviceMemory alloc_for(VkMemoryRequirements rq) {
    VkPhysicalDeviceMemoryProperties mp; vkGetPhysicalDeviceMemoryProperties(phys, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++) {
        VkMemoryPropertyFlags want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
        if ((rq.memoryTypeBits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & want) == want) {
            VkMemoryAllocateInfo ai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, rq.size, i};
            VkDeviceMemory m; CK(vkAllocateMemory(dev, &ai, NULL, &m)); return m;
        }
    }
    fprintf(stderr, "no host-visible memory\n"); exit(2);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: vk_compute_run <module.spv> <set,binding,kind,file[,w,h,layers,array]>...\n"); return 2; }
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "vk_compute_run", 1, NULL, 0, VK_API_VERSION_1_3};
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &app, 0, NULL, 0, NULL};
    VkInstance inst; CK(vkCreateInstance(&ici, NULL, &inst));
    uint32_t n = 1; VkResult er = vkEnumeratePhysicalDevices(inst, &n, &phys); if (er != VK_SUCCESS && er != VK_INCOMPLETE) { fprintf(stderr, "no device\n"); return 2; }
    if (!n) { fprintf(stderr, "no device\n"); return 2; }
    VkPhysicalDeviceVulkan13Features f13 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
    VkPhysicalDeviceVulkan12Features f12 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, &f13};
    VkPhysicalDeviceVulkan11Features f11 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, &f12};
    VkPhysicalDeviceFeatures2 f2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &f11};
    vkGetPhysicalDeviceFeatures2(phys, &f2);   // every feature the device has, on
    float prio = 1; VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, 0, 1, &prio};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &f2, 0, 1, &qci, 0, NULL, 0, NULL, NULL};
    CK(vkCreateDevice(phys, &dci, NULL, &dev));
    VkQueue q; vkGetDeviceQueue(dev, 0, 0, &q);

    int nr = argc - 2; res_t *rs = calloc((size_t)(nr ? nr : 1), sizeof *rs); int maxset = -1;
    for (int i = 0; i < nr; i++) {
        res_t *r = &rs[i]; char *s = strdup(argv[i + 2]), *t;
        r->set = atoi(strtok(s, ",")); r->binding = atoi(strtok(NULL, ",")); r->kind = *strtok(NULL, ","); r->file = strtok(NULL, ",");
        r->w = (t = strtok(NULL, ",")) ? atoi(t) : 1; r->h = (t = strtok(NULL, ",")) ? atoi(t) : 1; r->layers = (t = strtok(NULL, ",")) ? atoi(t) : 1; r->array = (t = strtok(NULL, ",")) ? atoi(t) : 0;
        if (r->set > maxset) maxset = r->set;
    }
    if (maxset > 7) { fprintf(stderr, "set %d\n", maxset); return 2; }
    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, 0};
    VkCommandPool pool; CK(vkCreateCommandPool(dev, &cpi, NULL, &pool));
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool, VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cb; CK(vkAllocateCommandBuffers(dev, &cai, &cb));
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    CK(vkBeginCommandBuffer(cb, &bi));

    for (int i = 0; i < nr; i++) {
        res_t *r = &rs[i];
        if (r->kind == 'u' || r->kind == 's') {
            void *d = slurp(r->file, &r->size); size_t sz = (r->size + 255) & ~(size_t)255;
            VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, sz, r->kind == 'u' ? VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT : VK_BUFFER_USAGE_STORAGE_BUFFER_BIT};
            CK(vkCreateBuffer(dev, &bci, NULL, &r->buf));
            VkMemoryRequirements rq; vkGetBufferMemoryRequirements(dev, r->buf, &rq); r->mem = alloc_for(rq);
            CK(vkBindBufferMemory(dev, r->buf, r->mem, 0));
            void *m; CK(vkMapMemory(dev, r->mem, 0, VK_WHOLE_SIZE, 0, &m)); memset(m, 0, sz); memcpy(m, d, r->size); vkUnmapMemory(dev, r->mem); free(d);
        } else if (r->kind == 'i' || r->kind == 't') {
            int storage = r->kind == 'i';
            void *d = NULL; if (storage) { d = slurp(r->file, &r->size); r->w = (int)(r->size / 4); r->h = 1; r->layers = 1; }
            VkFormat fmt = storage ? VK_FORMAT_R32_UINT : VK_FORMAT_R8G8B8A8_UNORM;
            VkImageCreateInfo ii = {VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, NULL, 0, VK_IMAGE_TYPE_2D, fmt, {(uint32_t)r->w, (uint32_t)r->h, 1}, 1, (uint32_t)r->layers, VK_SAMPLE_COUNT_1_BIT,
                                    VK_IMAGE_TILING_LINEAR, storage ? VK_IMAGE_USAGE_STORAGE_BIT : VK_IMAGE_USAGE_SAMPLED_BIT, VK_SHARING_MODE_EXCLUSIVE, 0, NULL, VK_IMAGE_LAYOUT_PREINITIALIZED};
            CK(vkCreateImage(dev, &ii, NULL, &r->img));
            VkMemoryRequirements rq; vkGetImageMemoryRequirements(dev, r->img, &rq); r->mem = alloc_for(rq);
            CK(vkBindImageMemory(dev, r->img, r->mem, 0));
            if (storage) {
                VkImageSubresource sub = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0}; VkSubresourceLayout lay; vkGetImageSubresourceLayout(dev, r->img, &sub, &lay);
                char *m; CK(vkMapMemory(dev, r->mem, 0, VK_WHOLE_SIZE, 0, (void **)&m)); memcpy(m + lay.offset, d, r->size); vkUnmapMemory(dev, r->mem); free(d);
            }
            VkImageViewCreateInfo vi = {VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, NULL, 0, r->img, r->array ? VK_IMAGE_VIEW_TYPE_2D_ARRAY : VK_IMAGE_VIEW_TYPE_2D, fmt, {0},
                                        {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, r->array ? (uint32_t)r->layers : 1}};
            CK(vkCreateImageView(dev, &vi, NULL, &r->view));
            VkImageMemoryBarrier b = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, NULL, VK_ACCESS_HOST_WRITE_BIT, VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT, VK_IMAGE_LAYOUT_PREINITIALIZED,
                                      VK_IMAGE_LAYOUT_GENERAL, VK_QUEUE_FAMILY_IGNORED, VK_QUEUE_FAMILY_IGNORED, r->img, {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, (uint32_t)r->layers}};
            vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_HOST_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, NULL, 0, NULL, 1, &b);
            if (!storage) { VkSamplerCreateInfo si = {VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO}; CK(vkCreateSampler(dev, &si, NULL, &r->smp)); }
        } else { fprintf(stderr, "kind %c\n", r->kind); return 2; }
    }

    VkDescriptorSetLayout layouts[8]; VkDescriptorSet sets[8];
    VkDescriptorPoolSize ps[] = {{VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 64}, {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 64}, {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 64}, {VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 64}};
    VkDescriptorPoolCreateInfo dpi = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, NULL, 0, 8, 4, ps};
    VkDescriptorPool dp; CK(vkCreateDescriptorPool(dev, &dpi, NULL, &dp));
    for (int s = 0; s <= maxset; s++) {
        VkDescriptorSetLayoutBinding lb[64]; int nb = 0;
        for (int i = 0; i < nr; i++) if (rs[i].set == s) {
            VkDescriptorType ty = rs[i].kind == 'u' ? VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER : rs[i].kind == 's' ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : rs[i].kind == 'i' ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
            lb[nb++] = (VkDescriptorSetLayoutBinding){(uint32_t)rs[i].binding, ty, 1, VK_SHADER_STAGE_COMPUTE_BIT, NULL};
        }
        VkDescriptorSetLayoutCreateInfo li = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO, NULL, 0, (uint32_t)nb, lb};
        CK(vkCreateDescriptorSetLayout(dev, &li, NULL, &layouts[s]));
        VkDescriptorSetAllocateInfo dai = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, NULL, dp, 1, &layouts[s]};
        CK(vkAllocateDescriptorSets(dev, &dai, &sets[s]));
    }
    for (int i = 0; i < nr; i++) {
        res_t *r = &rs[i];
        VkDescriptorBufferInfo dbi = {r->buf, 0, VK_WHOLE_SIZE};
        VkDescriptorImageInfo dii = {r->smp, r->view, VK_IMAGE_LAYOUT_GENERAL};
        VkWriteDescriptorSet w = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, NULL, sets[r->set], (uint32_t)r->binding, 0, 1,
                                  r->kind == 'u' ? VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER : r->kind == 's' ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : r->kind == 'i' ? VK_DESCRIPTOR_TYPE_STORAGE_IMAGE : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,
                                  &dii, &dbi, NULL};
        vkUpdateDescriptorSets(dev, 1, &w, 0, NULL);
    }
    size_t cn; uint32_t *code = slurp(argv[1], &cn);
    VkShaderModuleCreateInfo smi = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO, NULL, 0, cn, code};
    VkShaderModule sm; CK(vkCreateShaderModule(dev, &smi, NULL, &sm));
    VkPushConstantRange pr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 256};
    VkPipelineLayoutCreateInfo pli = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO, NULL, 0, (uint32_t)(maxset + 1), layouts, 1, &pr};
    VkPipelineLayout pl; CK(vkCreatePipelineLayout(dev, &pli, NULL, &pl));
    VkComputePipelineCreateInfo cpci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO, NULL, 0, {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, NULL, 0, VK_SHADER_STAGE_COMPUTE_BIT, sm, "main", NULL}, pl, VK_NULL_HANDLE, 0};
    VkPipeline pipe; CK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &pipe));
    vkCmdBindPipeline(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    if (maxset >= 0) vkCmdBindDescriptorSets(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, (uint32_t)(maxset + 1), sets, 0, NULL);
    vkCmdDispatch(cb, 1, 1, 1);
    VkMemoryBarrier mb = {VK_STRUCTURE_TYPE_MEMORY_BARRIER, NULL, VK_ACCESS_SHADER_WRITE_BIT, VK_ACCESS_HOST_READ_BIT};
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &mb, 0, NULL, 0, NULL);
    CK(vkEndCommandBuffer(cb));
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL};
    CK(vkQueueSubmit(q, 1, &si, VK_NULL_HANDLE)); CK(vkQueueWaitIdle(q));
    for (int i = 0; i < nr; i++) {
        res_t *r = &rs[i]; if (r->kind != 's' && r->kind != 'i') continue;
        char *m; CK(vkMapMemory(dev, r->mem, 0, VK_WHOLE_SIZE, 0, (void **)&m));
        size_t off = 0;
        if (r->kind == 'i') { VkImageSubresource sub = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0}; VkSubresourceLayout lay; vkGetImageSubresourceLayout(dev, r->img, &sub, &lay); off = lay.offset; }
        FILE *f = fopen(r->file, "wb"); fwrite(m + off, 1, r->size, f); fclose(f); vkUnmapMemory(dev, r->mem);
    }
    return 0;
}
