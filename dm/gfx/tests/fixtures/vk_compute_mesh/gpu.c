#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#define CHECK(x) do {VkResult r=(x);if(r){fprintf(stderr,"%s = %d at %d\n",#x,r,__LINE__);exit(2);}}while(0)
static VkDevice dev;static VkQueue queue;static VkCommandPool cp;static uint32_t qf;
static VkBuffer buf[4];static VkDeviceMemory mem[4];static uint32_t *map[4];static VkPipelineLayout pl;static VkDescriptorSet sets[4];
static VkPipeline pipeline_for(const char *path,VkPipelineLayout layout){
 FILE *f=fopen(path,"rb");if(!f){perror(path);exit(2);}fseek(f,0,SEEK_END);size_t n=ftell(f);rewind(f);uint32_t *w=malloc(n);if(fread(w,1,n,f)!=n)exit(2);fclose(f);
 VkShaderModuleCreateInfo mi={.sType=VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,.codeSize=n,.pCode=w};VkShaderModule sm;CHECK(vkCreateShaderModule(dev,&mi,0,&sm));free(w);
 VkComputePipelineCreateInfo pi={.sType=VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,.layout=layout,.stage={.sType=VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,.stage=VK_SHADER_STAGE_COMPUTE_BIT,.module=sm,.pName="main"}};
 VkPipeline p;CHECK(vkCreateComputePipelines(dev,0,1,&pi,0,&p));vkDestroyShaderModule(dev,sm,0);return p;
}
static VkPipeline pipeline(const char *path){return pipeline_for(path,pl);}
static void dispatch(VkPipeline p,uint32_t n){
 VkCommandBufferAllocateInfo ai={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=cp,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};VkCommandBuffer cb;CHECK(vkAllocateCommandBuffers(dev,&ai,&cb));
 VkCommandBufferBeginInfo bi={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};CHECK(vkBeginCommandBuffer(cb,&bi));
 vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_COMPUTE,p);vkCmdBindDescriptorSets(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pl,0,4,sets,0,0);vkCmdDispatch(cb,n,1,1);
 VkMemoryBarrier mb={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT,.dstAccessMask=VK_ACCESS_HOST_READ_BIT|VK_ACCESS_SHADER_READ_BIT};vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&mb,0,0,0,0);
 CHECK(vkEndCommandBuffer(cb));VkSubmitInfo si={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cb};CHECK(vkQueueSubmit(queue,1,&si,0));CHECK(vkQueueWaitIdle(queue));vkFreeCommandBuffers(dev,cp,1,&cb);
}
static int failures;
static void eq(const char *what,uint32_t got,uint32_t want){if(got!=want){if(failures<10)fprintf(stderr,"%s: got %u expected %u\n",what,got,want);failures++;}}
static uint32_t bits(float v){uint32_t x;memcpy(&x,&v,4);return x;}
static void dispatch_prepared(VkPipeline prepare,VkPipelineLayout layout,VkDescriptorSet prep_set,VkPipeline consumer){
 VkCommandBufferAllocateInfo ai={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,.commandPool=cp,.level=VK_COMMAND_BUFFER_LEVEL_PRIMARY,.commandBufferCount=1};VkCommandBuffer cb;CHECK(vkAllocateCommandBuffers(dev,&ai,&cb));
 VkCommandBufferBeginInfo bi={.sType=VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,.flags=VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT};CHECK(vkBeginCommandBuffer(cb,&bi));
 vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_COMPUTE,prepare);vkCmdBindDescriptorSets(cb,VK_PIPELINE_BIND_POINT_COMPUTE,layout,0,1,&prep_set,0,0);vkCmdDispatch(cb,1,1,1);
 VkMemoryBarrier mb={.sType=VK_STRUCTURE_TYPE_MEMORY_BARRIER,.srcAccessMask=VK_ACCESS_SHADER_WRITE_BIT,.dstAccessMask=VK_ACCESS_INDIRECT_COMMAND_READ_BIT|VK_ACCESS_SHADER_READ_BIT};
 vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_DRAW_INDIRECT_BIT|VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,0,1,&mb,0,0,0,0);
 if (consumer) {
  vkCmdBindPipeline(cb,VK_PIPELINE_BIND_POINT_COMPUTE,consumer);
  vkCmdBindDescriptorSets(cb,VK_PIPELINE_BIND_POINT_COMPUTE,pl,0,4,sets,0,0);
  vkCmdDispatchIndirect(cb,buf[2],0);
 }
 mb.dstAccessMask=VK_ACCESS_HOST_READ_BIT;vkCmdPipelineBarrier(cb,VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,VK_PIPELINE_STAGE_HOST_BIT,0,1,&mb,0,0,0,0);
 CHECK(vkEndCommandBuffer(cb));VkSubmitInfo si={.sType=VK_STRUCTURE_TYPE_SUBMIT_INFO,.commandBufferCount=1,.pCommandBuffers=&cb};CHECK(vkQueueSubmit(queue,1,&si,0));CHECK(vkQueueWaitIdle(queue));vkFreeCommandBuffers(dev,cp,1,&cb);
}
static void check_indirect(const char *path, VkPipelineLayout layout, VkDescriptorSetLayout dsl, VkPipeline task) {
 VkDescriptorPoolSize size = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 4};
 VkDescriptorPoolCreateInfo pool_info = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1,
                                       .poolSizeCount = 1, .pPoolSizes = &size};
 VkDescriptorPool pool;
 CHECK(vkCreateDescriptorPool(dev, &pool_info, NULL, &pool));
 VkDescriptorSetAllocateInfo alloc = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO, .descriptorPool = pool,
                                      .descriptorSetCount = 1, .pSetLayouts = &dsl};
 VkDescriptorSet prep_set;
 CHECK(vkAllocateDescriptorSets(dev, &alloc, &prep_set));
 VkDescriptorBufferInfo buffers[4] = {{buf[1], 0, 65536}, {buf[0], 0, 65536}, {buf[2], 0, 32}, {buf[3], 0, 32}};
 VkWriteDescriptorSet writes[4];
 for (uint32_t i = 0; i < 4; ++i)
  writes[i] = (VkWriteDescriptorSet){.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = prep_set, .dstBinding = i,
                                   .descriptorCount = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &buffers[i]};
 vkUpdateDescriptorSets(dev, 4, writes, 0, NULL);
 VkPipeline prep = pipeline_for(path, layout);
 const uint32_t cap = 65, stride = 68, first = UINT32_MAX - 1;
 const uint32_t counts[] = {0, 1, 64, 65, 66, UINT32_MAX};
 for (uint32_t with_task = 0; with_task <= 1; ++with_task) {
  for (size_t i = 0; i < sizeof counts / sizeof counts[0]; ++i) {
   uint32_t count = counts[i], groups = count <= cap ? count : 0;
   uint32_t ctl[8] = {with_task, stride, 0, 3, 7, 0, 0, cap};
   memcpy(map[3], ctl, sizeof ctl);
   map[0][7] = count;
   map[0][8] = first;
   memset(map[1], 0xa5, 65536);
   memset(map[2], 0xa5, 65536);
   dispatch_prepared(prep, layout, prep_set, with_task ? task : VK_NULL_HANDLE);
   uint32_t expected[] = {groups, 1, 1, count, with_task ? 0 : groups * 3, 1, 0, 0};
   for (size_t word = 0; word < sizeof expected / sizeof expected[0]; ++word)
    eq("prepared indirect word", map[2][word], expected[word]);
   eq("original first workgroup", map[3][4], first);
   eq("direct mesh first workgroup", map[3][2], with_task ? 0 : first);
   eq("exact task dispatch size", map[3][6], groups);
   eq("original argument count", map[0][7], count);
   eq("argument guard", map[2][8], 0xa5a5a5a5);
   if (with_task) for (uint32_t g = 0; g < groups; ++g) {
    eq("task emitted once per input", map[1][g * stride], 3);
    eq("task payload includes original workgroup ID", map[1][g * stride + 4], 1000u * (first + g));
   }
   eq("undispatched task record guard", map[1][(with_task ? groups : 0) * stride], 0xa5a5a5a5);
  }
 }
 // Run preparation alone to check unwritten task headers across both lanes
 // of the strided loop and ensure the payload words are not overwritten.
 uint32_t ctl[8] = {1, stride, 0, 3, 7, 0, 0, cap};
 memcpy(map[3], ctl, sizeof ctl);
 map[0][7] = cap;
 memset(map[1], 0xa5, 65536);
 dispatch_prepared(prep, layout, prep_set, VK_NULL_HANDLE);
 for (uint32_t g = 0; g < cap; ++g) {
  const uint32_t *record = &map[1][g * stride];
  eq("empty task x", record[0], 0);
  eq("empty task y", record[1], 1);
  eq("empty task z", record[2], 1);
  eq("untouched task payload", record[4], 0xa5a5a5a5);
 }
 eq("initialized task records fit the arena", map[1][cap * stride], 0xa5a5a5a5);
 vkDestroyPipeline(dev, prep, NULL);
 vkDestroyDescriptorPool(dev, pool, NULL);
}
static void check_bounded(const char *prepare_path, const char *schedule_path, VkPipelineLayout layout) {
 VkPipeline prepare = pipeline_for(prepare_path, layout);
 VkPipeline scheduler = pipeline_for(schedule_path, layout);
 // The preparer uses binding 0 for initialized task headers and binding 1
 // for the input arguments. The scheduler uses them in the opposite roles.
 const struct { uint32_t first, count, head, tail; } cases[] = {
  {0, 0, 0, 0}, {0, 65, 65, 0}, {0, UINT32_MAX, 65, 0},
  {64, 2, 1, 0}, {65, 100, 0, 0}, {UINT32_MAX - 1, 5, 0, 3},
  {2, UINT32_MAX, 63, 1}, {UINT32_MAX - 1, 2, 0, 0},
 };
 for (size_t i = 0; i < sizeof cases / sizeof cases[0]; ++i) {
  uint32_t ctl[16] = {1, 68, 0, 8, 7, 0, 0, 65, 1, 68};
  memcpy(map[3], ctl, sizeof ctl);
  memset(map[0], 0xa5, 65536);
  map[1][7] = cases[i].count;
  map[1][8] = cases[i].first;
  dispatch_prepared(prepare, layout, sets[3], VK_NULL_HANDLE);
  uint32_t head = cases[i].head, tail = cases[i].tail, total = head + tail;
  uint32_t expected[] = {head, 1, 1, total, tail, 1, 1, 0};
  for (size_t w = 0; w < sizeof expected / sizeof expected[0]; ++w)
   eq("bounded input arguments", map[2][w], expected[w]);
  eq("bounded head starts at original first", map[3][4], cases[i].first);
  eq("bounded tail starts at zero", map[3][12], 0);
  eq("bounded tail follows head records", map[3][13], head);
  eq("bounded task initialization guard", map[0][total * 68], 0xa5a5a5a5);
  for (uint32_t g = 0; g < total; ++g) {
   eq("bounded task initialized x", map[0][g * 68], 0);
   eq("bounded task initialized y", map[0][g * 68 + 1], 1);
   eq("bounded task initialized z", map[0][g * 68 + 2], 1);
  }
 }

 uint32_t ctl[8] = {1, 68, 0, 3, 2, 3, 3, 2};
 memcpy(map[3], ctl, sizeof ctl);
 map[0][3] = 3;
 for (uint32_t t = 0; t < 3; ++t) {
  map[1][t * 68] = t < 2 ? 2 : 1;
  map[1][t * 68 + 1] = map[1][t * 68 + 2] = 1;
 }
 memset(map[2], 0xa5, 65536);
 dispatch_prepared(scheduler, layout, sets[3], VK_NULL_HANDLE);
 for (uint32_t c = 0; c < 3; ++c) {
  uint32_t groups = c < 2 ? 2 : 1;
  eq("bounded chunk dispatch groups", map[2][c * 8], groups);
  eq("bounded chunk draw vertices", map[2][c * 8 + 4], groups * 3);
  eq("bounded chunk scheduler status", map[2][c * 8 + 3], 0);
 }
 for (uint32_t g = 0; g < 5; ++g) {
  eq("bounded mesh map ordered task", map[2][24 + g * 4], g / 2);
  eq("bounded mesh map ordered local group", map[2][25 + g * 4], g % 2);
 }
 eq("bounded mesh map guard", map[2][44], 0xa5a5a5a5);
 map[1][2 * 68] = 3;
 dispatch_prepared(scheduler, layout, sets[3], VK_NULL_HANDLE);
 eq("bounded scheduler detects incompatible task metadata", map[2][3], 1);
 for (uint32_t c = 0; c < 3; ++c) eq("bounded scheduler faults before dispatch", map[2][c * 8], 0);
 map[0][3] = 4;
 dispatch_prepared(scheduler, layout, sets[3], VK_NULL_HANDLE);
 eq("bounded scheduler checks its task arena", map[2][3], 1);
 eq("bounded scheduler leaves draw disabled", map[2][4], 0);
 vkDestroyPipeline(dev, scheduler, NULL);
 vkDestroyPipeline(dev, prepare, NULL);
}
int main(int argc,char **argv){
 if(argc<4 || argc==8 || argc>9)return 2;
 VkApplicationInfo app={.sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,.pApplicationName="Banana compute mesh semantics",.apiVersion=VK_API_VERSION_1_3};VkInstanceCreateInfo ii={.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,.pApplicationInfo=&app};VkInstance inst;CHECK(vkCreateInstance(&ii,0,&inst));
 uint32_t np=1;VkPhysicalDevice pd;CHECK(vkEnumeratePhysicalDevices(inst,&np,&pd));VkPhysicalDeviceProperties props;vkGetPhysicalDeviceProperties(pd,&props);printf("device %s, mesh extension not enabled\n",props.deviceName);
 uint32_t nq=0;vkGetPhysicalDeviceQueueFamilyProperties(pd,&nq,0);VkQueueFamilyProperties *qp=calloc(nq,sizeof *qp);vkGetPhysicalDeviceQueueFamilyProperties(pd,&nq,qp);for(qf=0;qf<nq;qf++)if(qp[qf].queueFlags&VK_QUEUE_COMPUTE_BIT)break;free(qp);
 float priority=1;VkDeviceQueueCreateInfo qi={.sType=VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,.queueFamilyIndex=qf,.queueCount=1,.pQueuePriorities=&priority};VkDeviceCreateInfo di={.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,.queueCreateInfoCount=1,.pQueueCreateInfos=&qi};CHECK(vkCreateDevice(pd,&di,0,&dev));vkGetDeviceQueue(dev,qf,0,&queue);
 VkCommandPoolCreateInfo ci={.sType=VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,.queueFamilyIndex=qf};CHECK(vkCreateCommandPool(dev,&ci,0,&cp));
 VkPhysicalDeviceMemoryProperties mp;vkGetPhysicalDeviceMemoryProperties(pd,&mp);
 for(int i=0;i<4;i++){
  VkBufferCreateInfo b={.sType=VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,.size=65536,.usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT|VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT};CHECK(vkCreateBuffer(dev,&b,0,&buf[i]));VkMemoryRequirements rq;vkGetBufferMemoryRequirements(dev,buf[i],&rq);
  uint32_t ty;for(ty=0;ty<mp.memoryTypeCount;ty++)if((rq.memoryTypeBits>>ty&1)&&(mp.memoryTypes[ty].propertyFlags&6)==6)break;if(ty==mp.memoryTypeCount)return 2;
  VkMemoryAllocateInfo a={.sType=VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,.allocationSize=rq.size,.memoryTypeIndex=ty};CHECK(vkAllocateMemory(dev,&a,0,&mem[i]));CHECK(vkBindBufferMemory(dev,buf[i],mem[i],0));CHECK(vkMapMemory(dev,mem[i],0,VK_WHOLE_SIZE,0,(void**)&map[i]));memset(map[i],0xa5,65536);
 }
 VkDescriptorSetLayoutBinding bindings[4];for(int i=0;i<4;i++)bindings[i]=(VkDescriptorSetLayoutBinding){.binding=i,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.descriptorCount=1,.stageFlags=VK_SHADER_STAGE_COMPUTE_BIT};
 VkDescriptorSetLayout layouts[4];for(int i=0;i<4;i++){VkDescriptorSetLayoutCreateInfo l={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,.bindingCount=i==3?4:0,.pBindings=i==3?bindings:0};CHECK(vkCreateDescriptorSetLayout(dev,&l,0,&layouts[i]));}
 VkPipelineLayoutCreateInfo li={.sType=VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,.setLayoutCount=4,.pSetLayouts=layouts};CHECK(vkCreatePipelineLayout(dev,&li,0,&pl));
 VkDescriptorPoolSize ps={VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,4};VkDescriptorPoolCreateInfo dpi={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,.maxSets=4,.poolSizeCount=1,.pPoolSizes=&ps};VkDescriptorPool dp;CHECK(vkCreateDescriptorPool(dev,&dpi,0,&dp));VkDescriptorSetAllocateInfo dai={.sType=VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,.descriptorPool=dp,.descriptorSetCount=4,.pSetLayouts=layouts};CHECK(vkAllocateDescriptorSets(dev,&dai,sets));
 VkWriteDescriptorSet wr[4];VkDescriptorBufferInfo db[4];for(int i=0;i<4;i++){db[i]=(VkDescriptorBufferInfo){buf[i],0,65536};wr[i]=(VkWriteDescriptorSet){.sType=VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET,.dstSet=sets[3],.dstBinding=i,.descriptorCount=1,.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER,.pBufferInfo=&db[i]};}vkUpdateDescriptorSets(dev,4,wr,0,0);
 memset(map[3],0,64);map[3][1]=68;map[3][4]=5;map[3][5]=5;
 VkPipeline task=pipeline(argv[1]),mesh=pipeline(argv[2]);dispatch(task,2);
 for(uint32_t t=5;t<7;t++){eq("emit x",map[1][t*68],3);eq("emit y",map[1][t*68+1],1);eq("emit z",map[1][t*68+2],1);for(uint32_t w=0;w<64;w++)eq("task payload",map[1][t*68+4+w],t*1000+w);}
 eq("task prefix guard",map[1][5*68-1],0xa5a5a5a5);eq("task suffix guard",map[1][7*68],0xa5a5a5a5);
 map[3][0]=1;map[2][0]=6;map[2][1]=2;map[2][2]=0;map[2][3]=0;map[2][4]=5;map[2][5]=0;map[2][6]=0;map[2][7]=0;
 dispatch(mesh,2);
 for(uint32_t g=0;g<2;g++){uint32_t b=g*36,t=g?5:6,wg=g?0:2;eq("vertices",map[0][b],3);eq("primitives",map[0][b+1],1);for(uint32_t v=0;v<3;v++){uint32_t p=b+4+8*v;eq("position x",map[0][p],bits(t*1000+v));eq("position y",map[0][p+1],bits(wg));eq("position z",map[0][p+2],bits(v));eq("position w",map[0][p+3],bits(1));}for(uint32_t i=0;i<3;i++)eq("triangle index",map[0][b+28+i],i);eq("perprimitive",map[0][b+32],bits(t*1000+63));float x=(float)(t*1000+63);volatile float rounded=x*1.0000001192092896f;eq("separate float rounding",map[0][b+33],bits(rounded-x));}
 eq("mesh suffix guard",map[0][72],0xa5a5a5a5);
 memset(map[0],0xa5,65536);VkPipeline points=pipeline(argv[3]);dispatch(points,2);
 for(uint32_t g=0;g<2;g++){
  uint32_t b=g*32,t=g?5:6,wg=g?0:2;eq("point vertices",map[0][b],2);eq("point primitives",map[0][b+1],2);
  for(uint32_t v=0;v<2;v++){uint32_t p=b+4+8*v;eq("point position",map[0][p],bits(t*1000+v));eq("point workgroup",map[0][p+1],bits(wg));eq("point size",map[0][p+4],bits(v+2));eq("point index",map[0][b+20+v],1-v);eq("point cull",map[0][b+22+v],v==1);eq("point primitive attr",map[0][b+24+4*v],bits(t*1000+63));}
 }
 eq("point suffix guard",map[0][64],0xa5a5a5a5);
 if(argc>=5){
  memset(map[0],0xa5,65536);VkPipeline predicates=pipeline(argv[4]);dispatch(predicates,2);
  for(uint32_t g=0;g<2;g++){
   uint32_t b=g*292;eq("predicate vertices",map[0][b],32);eq("predicate primitives",map[0][b+1],32);
   for(uint32_t lane=0;lane<32;lane++){
    eq("predicate point index",map[0][b+132+lane],lane);
    for(uint32_t c=0;c<4;c++)eq("accepted predicate",map[0][b+164+4*lane+c],bits(1));
   }
  }
  eq("predicate suffix guard",map[0][584],0xa5a5a5a5);
 }
 if(argc>=6){
  VkPipelineLayout layout;li.setLayoutCount=1;li.pSetLayouts=&layouts[3];CHECK(vkCreatePipelineLayout(dev,&li,0,&layout));VkPipeline scheduler=pipeline_for(argv[5],layout);
  for(uint32_t t=0;t<3;t++){memcpy(map[1]+t*68,map[1]+(5+(t==2))*68,68*4);map[1][t*68]=t==0?3:t==1?0:2;}
  uint32_t ctl[8]={1,68,2,3,0,0,3,5};memcpy(map[3],ctl,sizeof ctl);memset(map[0],0xa5,65536);memset(map[2],0xa5,65536);
  dispatch_prepared(scheduler,layout,sets[3],mesh);
  const uint32_t args[8]={5,1,1,0,15,1,0,0};for(int i=0;i<8;i++)eq("scheduler indirect args",map[2][i],args[i]);
  for(uint32_t g=0;g<5;g++){
   uint32_t t=g<3?0:2,x=g<3?g:g-3,b=g*36;
   eq("scheduled task",map[2][8+g*4],t);eq("scheduled mesh group",map[2][9+g*4],x);
   eq("indirect mesh vertices",map[0][b],3);eq("indirect mesh position x",map[0][b+4],bits(t?6000:5000));eq("indirect mesh position y",map[0][b+5],bits(x));
  }
  eq("scheduler map guard",map[2][28],0xa5a5a5a5);eq("indirect mesh guard",map[0][180],0xa5a5a5a5);
  map[3][7]=4;memset(map[0],0xa5,65536);dispatch_prepared(scheduler,layout,sets[3],mesh);
  eq("scheduler detects insufficient arena",map[2][3],1);eq("scheduler overflow dispatch disabled",map[2][0],0);eq("scheduler overflow draw disabled",map[2][4],0);eq("scheduler overflow guard",map[0][0],0xa5a5a5a5);
  if(argc>=7) check_indirect(argv[6],layout,layouts[3],task);
  if(argc==9) check_bounded(argv[7],argv[8],layout);
 }
 printf("compute mesh semantics: %s (%d failures)\n",failures?"FAIL":"PASS",failures);return failures?1:0;
}
