"""Decoded replay command storage outlives queued work without additional waits."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from dm.tools.tests.test_vk_sync_publication import fixture, function

ROOT = Path(__file__).resolve().parents[3]


def lifetime_fixture(replay_source):
    execution = (ROOT / "dm/gfx/vk_android/vka_exec.c").read_text()
    # Reuse full production exec, submit and publication functions with deterministic GPU stubs.
    code = fixture(execution).split("typedef struct {op_hdr h;uint64_t arg;} rec;")[0]
    code += r'''
static void *segments[64]; static int segment_count, segment_frees, next_job, finish_calls;
static int fail_allocation;
static void *segment_alloc(size_t n) { void *p=calloc(1,n); assert(p); segments[segment_count++]=p; return p; }
static void tracked_free(void *p) {
    for(int i=0;i<segment_count;i++) if(p && segments[i]==p) {segments[i]=NULL;segment_frees++;break;}
    free(p);
}
static void *batch_alloc(size_t n) { return fail_allocation ? NULL : malloc(n); }
static uint64_t vka_done(void) { return done_fence; }
static void fail_replay(void) { assert(segment_frees==0); exit(2); }
#define RFAIL(...) fail_replay()
static uint8_t **rp_seg; static uint32_t rp_nseg;
typedef struct rp_segments { struct rp_segments *next; uint8_t **p; uint32_t n; } rp_segments;
static rp_segments *rp_retired;
#define free tracked_free
#define malloc batch_alloc
'''
    code += function(replay_source, "static void retire_segments(")
    code += function(replay_source, "static void release_segments(")
    code += r'''
#undef malloc
#undef free
static void finish(void) {
    finish_calls++;
    while(next_job<nqueued) {
        job_t *j=&queued[next_job++]; vka_rp_sub_begin();
        for(int i=0;i<j->nh;i++) vka_rp_exec(j->h[i],j->fence);
        vka_rp_sub_commit(j->fence); free(j->h);
    }
    done_fence=last_fence;
}
typedef struct {op_hdr h;uint64_t target;} link_record;
typedef struct {op_hdr h;p_scissor sc;op_hdr end;} state_record;
static void make_submission(int marker,int sync) {
    // Sparse segment IDs, nested CALL, JUMP, and inline state payloads survive table reuse.
    rp_nseg=4;rp_seg=calloc(rp_nseg,sizeof *rp_seg);assert(rp_seg);
    rp_seg[0]=segment_alloc(2*sizeof(link_record));
    rp_seg[2]=segment_alloc(sizeof(state_record));
    rp_seg[3]=segment_alloc(sizeof(state_record));
    link_record *head=(void*)rp_seg[0];
    head[0]=(link_record){{OP_CALL,16},(uintptr_t)rp_seg[2]};
    head[1]=(link_record){{OP_JUMP,16},(uintptr_t)rp_seg[3]};
    state_record *call=(void*)rp_seg[2],*jump=(void*)rp_seg[3];
    *call=(state_record){{OP_SCISSOR,24},{marker,1,2,3},{OP_END,8}};
    *jump=(state_record){{OP_SCISSOR,24},{marker+1,4,5,6},{OP_END,8}};
    uint64_t *hs=malloc(sizeof *hs);assert(hs);*hs=(uintptr_t)rp_seg[0];
    mtl_sub_begin();mtl_exec((const uint8_t*)(uintptr_t)*hs);mtl_sub_commit();
    if(sync) finish();
    free(hs);retire_segments();
    assert(!rp_seg&&!rp_nseg);
}
int main(int argc,char **argv) {
    (void)batch_alloc;
    int fail=argc>1 ? atoi(argv[1]) : 0;
    if(fail) {
        fail_allocation=fail==2;
        make_submission(10,0);
        // A failed finish must terminate before releasing any worker-owned segment.
        assert(done_fence<last_fence); release_segments(); assert(0);
    }
    for(int mode=0;mode<3;mode++) {
        worker_on=mode!=2;
        for(int warm=0;warm<3;warm++) {
            int before=segment_frees,waits=finish_calls;
            make_submission(100+warm*10,mode==1);
            make_submission(200+warm*10,mode==1);
            // No per-submit release, even if an existing state/sync wait already finished.
            assert(segment_frees==before);
            finish(); assert(S.has_sc&&S.sc.x==201+warm*10&&S.sc.h==6);
            release_segments();
            assert(segment_frees==before+6&&!rp_retired&&!rp_seg);
            assert(finish_calls==waits+(mode==1?3:1));
        }
    }
    assert(segment_count==segment_frees);
    free(g_cur.h);puts("replay segments: delayed execution, inline, sync and warm ownership passed");
}
'''
    return code


class ReplayLifetime(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_segments_survive_delayed_replay_and_release_once(self):
        source = (ROOT / "dm/gfx/vk_android/vka_replay.c").read_text()
        self.assertIn("free(hs); retire_segments();", source)
        final_finish = source.index("mtl_set_frame(fno + 1);")
        self.assertLess(source.index("mtl_finish();", final_finish), source.index("release_segments();", final_finish))
        self.assertEqual(source.count("mtl_finish();"), 3)  # unchanged existing wait sites
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp) / "lifetime.c"
            binary = Path(tmp) / "lifetime"
            command = ["cc", "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers",
                       "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                       "-I", str(ROOT / "dm/gfx/nvn"), "-I", str(ROOT / "dm/gfx/nvn/include"),
                       "-I", str(ROOT / "dm/third_party/vulkan-headers/include"), str(c), "-o", str(binary)]
            c.write_text(lifetime_fixture(source))
            subprocess.run(command, check=True)
            subprocess.run([str(binary)], check=True)
            for failure in ("1", "2"):
                result = subprocess.run([str(binary), failure], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
            # Old immediate reclamation fails the lifetime assertion before any deferred read.
            old = function(source, "static void retire_segments(")
            early = "static void retire_segments(void) { for(uint32_t i=0;i<rp_nseg;i++)free(rp_seg[i]);free(rp_seg);rp_seg=NULL;rp_nseg=0; }\n"
            c.write_text(lifetime_fixture(source.replace(old, early)))
            subprocess.run(command, check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("segment_frees==before", result.stderr)


if __name__ == "__main__":
    unittest.main()
