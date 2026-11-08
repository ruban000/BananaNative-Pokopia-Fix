"""Actual selected-view validation, post-draw copy, limits and deferred writer."""
import csv
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from dm.tools.tests.test_vk_buffer_trace import ROOT, TRACE_HARNESS, TRACE_SELECTION_TYPES


class ImageTrace(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_selected_view_and_retirement(self):
        header = (ROOT / "dm/gfx/vk_android/vka.h").read_text()
        view = header[header.index("typedef struct {\n    vka_img *hold;"):header.index("const char *vka_tex_snapshot_view")]
        sample = header[header.index("typedef struct {\n    uint64_t frame, key, ordinal, guest"):header.index("uint64_t vka_buffer_trace_mask")]
        image_sample = header[header.index("typedef struct {\n    vka_buffer_sample source;"):header.index("uint64_t vka_exec_reserved_fence")]
        tex = (ROOT / "dm/gfx/vk_android/vka_tex.c").read_text()
        exact_view = tex[tex.index("static unsigned snapshot_bpp("):tex.index("void vka_tex_view_desc(")]
        draw = (ROOT / "dm/gfx/vk_android/vka_draw.c").read_text()
        fragment = draw[draw.index("static void image_trace_refuse_mask("):draw.index("static int draw_trace_overlap(")]
        writes = draw[draw.index("static int draw_writes("):draw.index("// After a game barrier")]
        source = (ROOT / "dm/gfx/vk_android/vka_buftrace.c").read_text().replace('#include "vka_capture.h"', '')
        executor = (ROOT / "dm/gfx/vk_android/vka_exec.c").read_text()
        getter = executor[executor.index("uint64_t vka_exec_reserved_fence("):executor.index("uint64_t vka_rp_sub_commit(")]
        self.assertIn("if (fence) g_cur_fence = fence", executor)
        worker = (ROOT / "dm/gfx/vk_android/vka_worker.c").read_text()
        reservation = worker[worker.index("    j.fence = vka_fence_next();"):worker.index("uint64_t vka_fence_last(void);")]
        self.assertLess(reservation.index("vka_fence_next()"), reservation.index("push(&j)"))
        harness = TRACE_HARNESS.replace("static uint64_t vka_exec_reserved_fence(void) { return current_reserved; }", "#define g_cur_fence current_reserved\n" + getter)
        harness = harness.replace("assert(!nm&&!ni&&nb==1);barrier_count++;", "assert(!nm);barrier_count++; if(ni) { assert(ni==1&&!nb&&im->oldLayout==VK_IMAGE_LAYOUT_GENERAL&&im->newLayout==VK_IMAGE_LAYOUT_GENERAL); assert(im->subresourceRange.levelCount==1&&im->subresourceRange.layerCount==1); assert(s==VK_PIPELINE_STAGE_ALL_COMMANDS_BIT&&d==VK_PIPELINE_STAGE_TRANSFER_BIT); assert(im->srcAccessMask==VK_ACCESS_MEMORY_WRITE_BIT&&im->dstAccessMask==VK_ACCESS_TRANSFER_READ_BIT);assert(!awaiting_copy&&!awaiting_war);awaiting_copy=1;return; } assert(nb==1);")
        harness = harness.replace("#define VLOG(...) ((void)0)", 'static unsigned image_end_lines;\nstatic void trace_log(const char *fmt,...) {if(!strncmp(fmt,"image trace ended ",18))image_end_lines++;}\n#define VLOG(...) trace_log(__VA_ARGS__)')
        harness += r'''
static uint32_t expected_level,expected_layer;
static void image_copy(VkCommandBuffer cb,VkImage im,VkImageLayout layout,VkBuffer dst,uint32_t count,const VkBufferImageCopy *c) {
 (void)cb;assert(!in_rendering);assert(count==1&&awaiting_copy&&!awaiting_war&&layout==VK_IMAGE_LAYOUT_GENERAL);
 assert(c->bufferOffset==0&&!c->bufferRowLength&&!c->bufferImageHeight);
 assert(c->imageSubresource.aspectMask==VK_IMAGE_ASPECT_COLOR_BIT&&c->imageSubresource.mipLevel==expected_level&&c->imageSubresource.baseArrayLayer==expected_layer&&c->imageSubresource.layerCount==1);
 assert(!c->imageOffset.x&&!c->imageOffset.y&&!c->imageOffset.z&&c->imageExtent.depth==1);
 mock_buffer *s=(mock_buffer *)(uintptr_t)im,*d=buffer(dst);assert(s->size==d->size);
 commands[ncommands++]=(typeof(commands[0])){s,d,0,0,d->size,0};awaiting_copy=0;awaiting_war=1;
}
'''
        texture_state = r'''
enum { NVN_TEXTURE_TARGET_3D=7 };
typedef struct view_ent { struct view_ent *next;VkImageView v;VkFormat f;VkImageViewType t;VkImageAspectFlags asp;uint32_t l0,nl,a0,na;VkComponentMapping swz; } view_ent;
struct vka_img { VkImage img;int inited;uint32_t samples,depth,base,vmin,w,h,levels,layers;struct {int target;} d;void *rs,*vpool;int vtrack,vbind,dcap;VkImageUsageFlags usage;VkImageAspectFlags aspect;struct {VkFormat vk;unsigned bw,bh,bpb;} f;void *views;uint64_t serial;int refs,holds; };
static pthread_mutex_t g_tmu=PTHREAD_MUTEX_INITIALIZER;
static struct {struct {uint64_t serial;} t;} g_treg[2];static int g_ntreg=2;
typedef struct {vka_img *im;} smap_ent;static smap_ent mapping;
static smap_ent *map_get(uint64_t serial) {return serial==g_treg[1].t.serial?&mapping:NULL;}
static uint64_t last_reserved=123;static unsigned retired;static vka_img *release_owner_on_end;
static void retire_image(void *v) {(void)v;retired++;}
static void vka_tex_drop(vka_img *im) {pthread_mutex_lock(&g_tmu);assert(im->holds>0&&im->refs>0);im->holds--;if(!--im->refs)vka_after(last_reserved,retire_image,im);pthread_mutex_unlock(&g_tmu);}
'''
        selection = TRACE_SELECTION_TYPES[:TRACE_SELECTION_TYPES.index("static unsigned image_trace_collect(")]
        selection = selection.replace("VKA_R_BUF=1,", "VKA_R_BUF=1,VKA_R_TEX=5,DMSH_VK_KIND_TEX=8,")
        selection = selection.replace("int set,binding,kind,reg;", "int set,binding,kind,reg;unsigned count;")
        selection = selection.replace("pass_ends++;", "pass_ends++;if(release_owner_on_end){assert(release_owner_on_end->refs==2);release_owner_on_end->refs--;mapping.im=NULL;release_owner_on_end=NULL;}")
        selection = selection.replace("unsigned flags; } vka_res;", "unsigned flags;int tid,sid; } vka_res;")
        selection += r'''
static struct {int nci;vka_img *cim[8];uint32_t clv[8],cl0[8],cnl[8],cslot[8];} R;
'''
        test = r'''
static void dispose(bt_packet *p) {uint64_t n=p->h.n;vcap_hbuf_free(&p->h);free(p);atomic_fetch_sub(&g_bt_live_bytes,n);}
static void fire_until(uint64_t f) {unsigned n=0;for(unsigned i=0;i<ncompletions;i++){if(completions[i].fence<=f)completions[i].fn(completions[i].arg);else completions[n++]=completions[i];}ncompletions=n;}
static void fire_all(void) {fire_until(UINT64_MAX);}
static void drain(int write) {while(g_bt_ready){bt_packet *p=g_bt_ready;g_bt_ready=p->next;if(write)bt_write_packet(p);dispose(p);}g_bt_ready_tail=NULL;}
static void finish(void) {execute();vka_buffer_trace_commit(current_reserved,1);fire_all();drain(0);}
static unsigned next_frame=1;
static void arm(const char *s) {setenv("DM_VK_BUFFER_TRACE","",1);vka_buffer_trace_mask(next_frame++,0x1234,2);setenv("DM_VK_BUFFER_TRACE",s,1);recording_frame=next_frame++;vka_buffer_trace_mask(recording_frame,0x1234,2);}
static vka_img im;static view_ent ve;static mock_buffer raw;
static vka_dstate state;static mock_entry entry={1};static dmsh_vk_bind bindings[2]={{0,263,DMSH_VK_KIND_TEX,5,1}};
static vka_shader vs={.key=0x5678},fs={.key=0x1234,.e=&entry,.b=bindings};static vka_shader *sh[VKA_NSTAGE]={&vs,&fs};
static VkDescriptorImageInfo ii;
static void bind_draw(void) {
 fs.key=0x1234;state.rast_discard=0;g_wk.n[1]=1;g_wk.r[1][0]=(vka_res){.what=VKA_R_TEX,.desc=VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,.binding=263,.tid=1,.sid=7};
 ii=(VkDescriptorImageInfo){(VkSampler)99,ve.v,VK_IMAGE_LAYOUT_GENERAL};g_w.n=1;g_w.w[0]=(VkWriteDescriptorSet){.dstBinding=263,.descriptorCount=1,.descriptorType=VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER,.pImageInfo=&ii};
 in_rendering=draw_recorded=1;
}
static void make_image(unsigned w,unsigned h,VkFormat fmt,unsigned bpp) {
 raw.size=(size_t)w*h*bpp;raw.data=malloc(raw.size);assert(raw.data);for(size_t i=0;i<raw.size;i++)raw.data[i]=(uint8_t)(i*13+7);
 ve=(view_ent){.v=(VkImageView)19,.f=fmt,.t=VK_IMAGE_VIEW_TYPE_2D,.asp=VK_IMAGE_ASPECT_COLOR_BIT,.nl=1,.na=1,.swz={VK_COMPONENT_SWIZZLE_B,VK_COMPONENT_SWIZZLE_G,VK_COMPONENT_SWIZZLE_R,VK_COMPONENT_SWIZZLE_ONE}};
 im=(vka_img){.img=(VkImage)(uintptr_t)&raw,.inited=1,.samples=1,.depth=1,.w=w,.h=h,.levels=1,.layers=1,.usage=VK_IMAGE_USAGE_TRANSFER_SRC_BIT,.aspect=VK_IMAGE_ASPECT_COLOR_BIT,.f={fmt,1,1,bpp},.views=&ve,.serial=44,.refs=1};
 mapping.im=&im;g_treg[1].t.serial=55;
}
static void rejected_view(void) {vka_image_view v;int h=im.holds,r=im.refs;assert(vka_tex_snapshot_view(1,ve.v,&v));assert(!v.hold&&im.holds==h&&im.refs==r);}
int main(int argc,char **argv) {
 assert(argc==3);vk.vkCmdCopyImageToBuffer=image_copy;setenv("DM_VK_BUFFER_TRACE_DIR",argv[2],1);setenv("DM_VK_BUFFER_TRACE_FRAMES","999",1);
 make_image(4,3,VK_FORMAT_R16G16B16A16_SFLOAT,8);bind_draw();buffer_trace_draw(&state,sh,10,0);assert(!g_bt_pending&&!pass_ends&&!im.holds); // off
 arm("1234:draw:tex5");assert(g_bt_frames==32&&g_bt_sel[0].kind==2&&g_bt_sel[0].bytes==0);
 current_reserved=0;bind_draw();buffer_trace_draw(&state,sh,11,0);assert(!g_bt_pending&&!pass_ends&&!im.holds&&g_bt_omitted==1);
 current_reserved=done_fence=123;bind_draw();buffer_trace_draw(&state,sh,12,0);assert(!g_bt_pending&&!im.holds&&g_bt_omitted==2);done_fence=0;
 // Identity and eligibility are checked by the actual texture helper, under its mutex.
 vka_image_view snapshot;assert(vka_tex_snapshot_view(1,(VkImageView)20,&snapshot));assert(!im.holds);
 ve.f=VK_FORMAT_R8_UNORM;rejected_view();ve.f=im.f.vk;
 im.samples=4;rejected_view();im.samples=1;im.vpool=&im;rejected_view();im.vpool=NULL;im.base=1;rejected_view();im.base=0;
 im.inited=0;rejected_view();im.inited=1;im.usage=0;rejected_view();im.usage=VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
 im.f.bw=4;rejected_view();im.f.bw=1;im.f.vk=VK_FORMAT_G8_B8_R8_3PLANE_420_UNORM;rejected_view();im.f.vk=ve.f;
 ve.t=VK_IMAGE_VIEW_TYPE_3D;rejected_view();ve.t=VK_IMAGE_VIEW_TYPE_2D;ve.nl=2;rejected_view();ve.nl=1;
 ve.asp=VK_IMAGE_ASPECT_DEPTH_BIT;rejected_view();ve.asp=VK_IMAGE_ASPECT_COLOR_BIT;ve.a0=1;rejected_view();ve.a0=0;
 im.w=UINT32_MAX;rejected_view();im.w=4;
 R.nci=1;R.cim[0]=&im;R.cnl[0]=1;bind_draw();buffer_trace_draw(&state,sh,13,0);assert(!im.holds&&!g_bt_pending&&g_bt_omitted==3);R.nci=0;
 bind_draw();ii.imageLayout=VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;buffer_trace_draw(&state,sh,14,0);assert(!im.holds&&!g_bt_pending);
 // Invalid/duplicate/unsupported selectors never arm.
 const char *bad[]={"1234:pre:tex5","1234:draw:tex16","1234:draw:tex-1","1234:draw:tex5:8","1234:draw:tex5,1234:draw:tex5"};
 for(unsigned i=0;i<sizeof bad/sizeof *bad;i++){arm(bad[i]);assert(!g_bt_nsel&&!im.holds);}
 arm("1234:draw:tex5");bind_draw();clobber_shader=&fs;clobber_state=&state;
 buffer_trace_draw(&state,sh,21,3);assert(pass_ends==1&&!in_rendering&&!im.holds&&im.refs==1&&g_bt_pending&&g_bt_count==1);
 bt_packet *p=g_bt_pending;assert(p->capacity==1&&p->n==1&&p->h.n==96&&!p->samples[0].image.view.hold);
 assert(p->samples[0].source.key==0x1234&&p->samples[0].source.variant==3&&p->samples[0].image.view.serial==55&&p->samples[0].image.view.image_serial==44);
 // No short hold survives: another storage upgrade can proceed before completion.
 assert(!im.holds);ii.imageView=(VkImageView)999;ve.v=(VkImageView)999;
 assert(p->samples[0].image.view.view==(VkImageView)19&&!g_bt_ready&&!frees);
 vka_buffer_trace_commit(123,1);assert(completions[0].fence==123&&!g_bt_pending&&!g_bt_ready);execute();assert(!g_bt_ready&&!memcmp(p->h.p,raw.data,96));
 if(!strcmp(argv[1],"loss"))hung=1;if(!strcmp(argv[1],"failure"))vka_buffer_trace_commit(124,0);
 fire_all();if(strcmp(argv[1],"normal")){assert(!g_bt_ready);dispose(p);free(raw.data);return 0;}
 assert(g_bt_ready==p);bt_write_packet(p);drain(0);clobber_shader=NULL;clobber_state=NULL;free(raw.data);
 // Exact nonzero physical mip/layer metadata and raw 1/2/4/8-byte formats.
 const VkFormat formats[]={VK_FORMAT_R8_UNORM,VK_FORMAT_R8G8_UNORM,VK_FORMAT_R8G8B8A8_UNORM,VK_FORMAT_B8G8R8A8_UNORM,VK_FORMAT_R16G16B16A16_SFLOAT};const unsigned bpps[]={1,2,4,4,8};
 for(unsigned i=0;i<5;i++) {make_image(7,3,formats[i],bpps[i]);im.w*=2;im.h*=2;im.levels=2;im.layers=3;ve.l0=expected_level=1;ve.a0=expected_layer=2;arm("1234:draw:tex5");bind_draw();buffer_trace_draw(&state,sh,30+i,0);assert(g_bt_pending&&!im.holds);execute();vka_buffer_trace_commit(123,1);fire_all();assert(!memcmp(g_bt_ready->h.p,raw.data,raw.size));drain(1);free(raw.data);}
 expected_level=expected_layer=0;
 // Large images do not allocate sixteen slots; memory padding counts toward8MiB.
 make_image(768,432,VK_FORMAT_R16G16B16A16_SFLOAT,8);arm("1234:draw:tex5");extra_requirement=4096;
 for(unsigned i=0;i<4;i++){bind_draw();buffer_trace_draw(&state,sh,40+i,0);assert(!im.holds);}
 assert(g_bt_image_count==3&&g_bt_omitted==1&&g_bt_count==3&&atomic_load(&g_bt_live_bytes)<=BT_MAX_MEMORY);finish();assert(!allocated_bytes);extra_requirement=0;
 // The32-image ceiling holds within ONE frame with COUNT1024, not only across frames.
 arm("");arm("1234:draw:tex5");for(unsigned i=0;i<33;i++){bind_draw();buffer_trace_draw(&state,sh,60+i,0);if(g_bt_pending)finish();assert(!im.holds);}
 assert(g_bt_image_count==32&&g_bt_count==32&&g_bt_image_total==32ull*768*432*8&&g_bt_ended);assert(g_bt_total==0);free(raw.data);
 // Separate cumulative image budget, omissions never consume a sequence.
 make_image(2,2,VK_FORMAT_R8G8B8A8_UNORM,4);arm("");arm("1234:draw:tex5");g_bt_image_total=BT_MAX_IMAGE_DATA-15;bind_draw();buffer_trace_draw(&state,sh,99,0);assert(g_bt_ended&&g_bt_count==0&&g_bt_omitted==1&&!im.holds&&!g_bt_pending);
 arm("");arm("1234:draw:tex5");extra_requirement=BT_MAX_MEMORY;bind_draw();buffer_trace_draw(&state,sh,100,0);assert(!g_bt_pending&&g_bt_omitted==1&&!g_bt_count&&!im.holds);extra_requirement=0;
 // Mixed selections share one end; the32nd image can be reached between two slots.
 arm("");arm("1234:draw:tex5,1234:draw:tex6");g_bt_image_count=g_bt_count=31;bind_draw();entry.bind_count=2;bindings[1]=(dmsh_vk_bind){0,264,DMSH_VK_KIND_TEX,6,1};
 g_wk.n[1]=2;g_wk.r[1][1]=g_wk.r[1][0];g_wk.r[1][1].binding=264;g_w.n=2;g_w.w[1]=g_w.w[0];g_w.w[1].dstBinding=264;
 unsigned before_ends=pass_ends,before_image_ends=image_end_lines;buffer_trace_draw(&state,sh,100,0);assert(pass_ends==before_ends+1&&g_bt_image_count==32&&g_bt_count==32&&g_bt_omitted==1&&!im.holds);
 for(unsigned i=0;i<16;i++)vka_image_trace_copy(g_cb,&g_bt_pending->samples[0].image);assert(image_end_lines==before_image_ends+1&&g_bt_image_count==32&&g_bt_omitted==1);
 finish();entry.bind_count=1;
 // New sessions keep pending metadata and allocations immutable until retirement.
 arm("");arm("1234:draw:tex5");bind_draw();buffer_trace_draw(&state,sh,100,0);uint64_t previous_session=g_bt_pending->samples[0].session;bt_packet *previous=g_bt_pending;
 arm("");arm("1234:draw:tex5");bind_draw();buffer_trace_draw(&state,sh,100,0);assert(previous->next&&previous->next->samples[0].session!=previous_session&&!im.holds);finish();
 // Retiring the last short-held owner must target a reservation covering this copy.
 bind_draw();release_owner_on_end=&im;last_reserved=124;buffer_trace_draw(&state,sh,101,0);assert(!im.refs&&!im.holds&&ncompletions==1&&completions[0].fence==124&&!retired);execute();vka_buffer_trace_commit(123,1);assert(!retired);fire_until(122);assert(!retired&&!g_bt_ready);fire_until(123);assert(!retired&&g_bt_ready);drain(0);fire_until(124);assert(retired==1);free(raw.data);
 assert(!atomic_load(&g_bt_live_bytes)&&!allocated_bytes);puts("production image view/copy/writer/bounds passed");return 0;
}
'''
        code = harness + view + sample + image_sample + texture_state + exact_view + source + selection + writes + fragment + test
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / "image_trace.c"
            binary = Path(tmp) / "image_trace"
            out = Path(tmp) / "game-work"
            c.write_text(code)
            command = ["cc", "-std=gnu11", "-O1", "-g", "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-I", str(ROOT / "dm/third_party/vulkan-headers/include"), str(c), "-pthread", "-o", str(binary)]
            subprocess.run(command, check=True)
            for mode in ("normal", "failure", "loss"):
                subprocess.run([str(binary), mode, str(out)], check=True)
            files = list(out.glob("*.bin"))
            self.assertEqual(len(files), 6)
            for path in files:
                with Path(str(path) + ".tsv").open() as f:
                    rows = list(csv.DictReader(f, delimiter="\t"))
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertNotIn(None, row)
                data = path.read_bytes()
                self.assertEqual(data, bytes((i * 13 + 7) & 255 for i in range(len(data))))
                self.assertEqual((row["row_order"], row["kind"], row["phase"], row["reg"], row["binding"], row["fence"]), ("vulkan_y_increasing", "tex", "post_draw", "5", "263", "123"))
                self.assertEqual(len(data), int(row["bytes"]))
                h = 0xCBF29CE484222325
                for b in data:
                    h = ((h ^ b) * 0x100000001B3) & ((1 << 64) - 1)
                self.assertEqual(row["hash"], f"{h:016x}")
            # Negative control: actual copy/barrier path must reject rendering still open.
            for broken, expected in (
                (code.replace("    vka_rp_end();", ""), b"!in_rendering"),
                (code.replace("if (view && v->v == view)", "if (view)"), b"vka_tex_snapshot_view"),
                (code.replace("vka_tex_drop(images[i].view.hold);", "(void)0;"), b"!im.holds"),
            ):
                c.write_text(broken)
                subprocess.run(command, check=True)
                result = subprocess.run([str(binary), "normal", str(out)], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
