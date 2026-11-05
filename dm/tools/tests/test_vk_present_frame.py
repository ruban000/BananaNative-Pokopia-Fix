"""Queued presents retain the caller's frame after both asynchronous delays.

Compile the production enqueue/inline/fill functions with queue and GPU stubs.
The reservation stub advances the live frame, as a blocked reservation can do.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


def function(source, name):
    start = source.index(name)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class PresentFrame(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_frame_survives_reservation_and_queues(self):
        worker = (ROOT / "dm/gfx/vk_android/vka_worker.c").read_text()
        present = (ROOT / "dm/gfx/vk_android/vka_present.c").read_text()
        # The actual worker dispatch must forward the field that this fixture queues.
        self.assertIn("vka_present_fill(j.ticket, j.frame, &j.t,", worker)
        self.assertIn("vka_ctl_shot_record(cb, im, cx, cy, cw, ch, it->frame)", present)
        declarations = r'''
#include <assert.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>
#include <stdio.h>
#include <pthread.h>
typedef struct { int serial; } tex_t;
typedef struct { tex_t t; double period; int crop[4], has_crop; uint64_t ticket, fence, frame; void *src; double hold; } pq_item;
typedef struct { int kind; uint64_t frame, ticket; tex_t t; double period; int has_crop, crop[4]; } job_t;
enum { J_PRESENT = 2, PQ = 32 };
static pq_item g_pq[PQ];
static uint64_t live_frame, tickets, g_pq_fill;
static int worker_on, g_thread_up = 1;
static pthread_mutex_t g_pmu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_pcv = PTHREAD_COND_INITIALIZER;
static job_t jobs[12]; static int njobs;
static uint64_t vka_frame(void) { return live_frame; }
static int up(void) { return worker_on; }
static int vka_worker_on(void) { return worker_on; }
static uint64_t vka_fence_last(void) { return 0; }
static uint64_t vka_fence_queued(void) { return 0; }
static void vka_perf_frame(double period) { (void)period; }
static uint64_t vka_present_reserve(void) { live_frame += 1000; return ++tickets; }
static void push(const job_t *j) { jobs[njobs++] = *j; live_frame += 1000; }
#define VLOG(...) ((void)0)
'''
        source = declarations + function(present, "void vka_present_fill(") + "\n"
        source += function(present, "uint64_t vka_present_inline(") + "\n"
        source += function(worker, "uint64_t mtl_present(") + r'''
int main(void) {
    tex_t texture = {123}; int crop[4] = {2, 3, 640, 360};
    worker_on = 1;
    for (uint64_t i = 0; i < 12; ++i) {
        live_frame = 8107 + i;
        assert(mtl_present(&texture, 0.016, crop) == i + 1);
    }
    /* The caller can reuse its crop and advance to an unrelated frame. */
    crop[0] = 999; live_frame = 999999;
    for (int i = 0; i < njobs; ++i) {
        job_t j = jobs[i];
        vka_present_fill(j.ticket, j.frame, &j.t, j.period, j.has_crop ? j.crop : NULL);
    }
    /* The presentation queue is consumed later than the replay queue. */
    live_frame = 777777;
    for (int i = 0; i < 12; ++i) {
        assert(g_pq[i].frame == (uint64_t)(8107 + i));
        assert(g_pq[i].ticket == (uint64_t)i + 1);
        assert(g_pq[i].t.serial == 123 && g_pq[i].crop[0] == 2);
    }
    worker_on = 0; live_frame = 9000;
    uint64_t ticket = mtl_present(&texture, 0.016, NULL);
    assert(g_pq[(ticket - 1) % PQ].frame == 9000);
    assert(!g_pq[(ticket - 1) % PQ].has_crop);
    puts("ok");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            c = Path(directory) / "present.c"
            binary = Path(directory) / "present"
            c.write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-missing-field-initializers", "-pthread", str(c), "-o", str(binary)], check=True)
            result = subprocess.run([str(binary)], check=True, text=True, capture_output=True)
            self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
