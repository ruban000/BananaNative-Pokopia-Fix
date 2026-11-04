// NVN2 task and mesh programs on a device without VK_EXT_mesh_shader (plain C, no Vulkan: unit-tested by dm/host/tests/t_android_spvpatch.c with DM_VK_MESH_SPV).
//
// The library's task and mesh modules are TaskEXT / MeshEXT entry points (docs/reference/shaders.md "NVN2 task and mesh programs filed under their SPH's stage"). A device
// without mesh shading (the Adreno 740 on Qualcomm's driver and on Turnip T30) runs them as compute shaders instead, and draws what the mesh workgroups made with an ordinary
// vertex shader (vka_draw.c mesh_cs_draw):
//
// vka_spv_mesh_compute rewrites a module as a GLCompute entry point:
//  - Mesh: every Output variable (the gl_MeshPerVertexEXT position array, the per-vertex and per-primitive vec4 arrays, PrimitiveTriangleIndicesEXT) becomes a member of one
//    record per mesh workgroup in a storage buffer (set 0, out_binding): uint nprim at 0, then each array at 16-byte stride. OpSetMeshOutputsEXT stores the primitive count
//    into nprim. The workgroup's slot in that buffer is WorkgroupId.y * NumWorkgroups.x + WorkgroupId.x: the replay dispatches (mesh groups per task workgroup, task
//    workgroups) or (count, 1). The shader's own WorkgroupId becomes a Private variable holding (x, 0, 0), its index among the mesh workgroups its task workgroup launched,
//    which is what MeshEXT gives. A new entry function sets these and calls the original one; a workgroup past its task workgroup's count only writes nprim = 0.
//  - Task: the TaskPayloadWorkgroupEXT payload becomes a record per task workgroup in a storage buffer (set 0, pay_binding): uint count at 0, the payload words from 16.
//    OpEmitMeshTasksEXT x y z stores x * y * z into count and returns. A mesh module reads the same records (its task workgroup is WorkgroupId.y).
//  - Barriers get UniformMemory semantics too, since what was output / payload memory is now storage buffer memory.
// The layout is returned in vka_mesh_info, which the vertex shader of the draw (vka_spv_mesh_vs) and the replay's buffer sizes follow.
//
// The translator's mesh modules (dm/gfx/tools/... SpirvMeshEntry.cs) have this shape on all 26 Mario Kart World programs: outputs are arrays of vec4, of uvec3 or of a
// one-member struct of vec4 (Position); every access goes through an access chain to a vector or scalar. Anything else is refused with the reason.
#include "vka_spvpatch.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { OpName = 5, OpMemberName = 6, OpExtension = 10, OpMemoryModel = 14, OpEntryPoint = 15, OpExecutionMode = 16, OpCapability = 17, OpTypeVoid = 19, OpTypeBool = 20,
       OpTypeInt = 21, OpTypeFloat = 22, OpTypeVector = 23, OpTypeArray = 28, OpTypeRuntimeArray = 29, OpTypeStruct = 30, OpTypePointer = 32, OpTypeFunction = 33,
       OpConstant = 43, OpConstantComposite = 44, OpFunction = 54, OpFunctionEnd = 56, OpFunctionCall = 57, OpVariable = 59, OpLoad = 61, OpStore = 62, OpAccessChain = 65,
       OpInBoundsAccessChain = 66, OpDecorate = 71, OpMemberDecorate = 72, OpCompositeConstruct = 80, OpCompositeExtract = 81, OpBitcast = 124, OpIAdd = 128, OpISub = 130,
       OpIMul = 132, OpUDiv = 134, OpULessThan = 176, OpSelect = 169, OpSelectionMerge = 247, OpLabel = 248, OpBranch = 249, OpBranchConditional = 250, OpReturn = 253,
       OpControlBarrier = 224, OpMemoryBarrier = 225, OpDecorateId = 332, OpDecorateString = 5632, OpMemberDecorateString = 5633,
       OpEmitMeshTasksEXT = 5294, OpSetMeshOutputsEXT = 5295 };
enum { SCInput = 1, SCOutput = 3, SCPrivate = 6, SCStorageBuffer = 12, SCTaskPayload = 5402 };
enum { DecBlock = 2, DecArrayStride = 6, DecBuiltIn = 11, DecFlat = 14, DecNonWritable = 24, DecLocation = 30, DecBinding = 33, DecDescriptorSet = 34, DecOffset = 35, DecPerPrimitive = 5271 };
enum { BIPosition = 0, BINumWorkgroups = 24, BIWorkgroupId = 26, BIVertexIndex = 42, BIPrimIndices = 5296 };
enum { EMTask = 5364, EMMesh = 5365, EMGLCompute = 5, EMVertex = 0, ModeLocalSize = 17, ModeOutputVertices = 26, ModeOutputPrims = 5270, ModeOutputTriangles = 5298,
       CapMesh = 5283, MemUniform = 0x40 };

typedef struct { uint32_t *w; size_t n, cap; int oom; } vec_t;
static void put(vec_t *v, uint32_t x) {
    if (v->oom) return;
    if (v->n == v->cap) { size_t c = v->cap ? 2 * v->cap : 1024; uint32_t *p = realloc(v->w, c * 4); if (!p) { v->oom = 1; return; } v->w = p; v->cap = c; }
    v->w[v->n++] = x;
}
static void put_n(vec_t *v, const uint32_t *x, size_t n) { for (size_t i = 0; i < n; i++) put(v, x[i]); }
static void ins(vec_t *v, uint32_t op, int n, ...) {   // one instruction of n operand words
    __builtin_va_list ap; __builtin_va_start(ap, n);
    put(v, (uint32_t)(n + 1) << 16 | op);
    for (int i = 0; i < n; i++) put(v, __builtin_va_arg(ap, uint32_t));
    __builtin_va_end(ap);
}
static void put_str(vec_t *v, const char *s) {   // a literal string, NUL-padded words
    size_t l = strlen(s) + 1;
    for (size_t i = 0; i < l; i += 4) { uint32_t x = 0; for (size_t k = 0; k < 4 && i + k < l; k++) x |= (uint32_t)(uint8_t)s[i + k] << (8 * k); put(v, x); }
}
static int str_eq(const uint32_t *w, size_t nw, const char *s) { size_t l = strlen(s); return nw * 4 > l && !memcmp(w, s, l) && ((const char *)w)[l] == 0; }

// ---------- vka_spv_mesh_compute ----------
enum { OpExtInst = 12, OpVectorShuffle = 79, OpCompositeInsert = 82, OpLoopMerge = 246, OpSwitch = 251 };
static int lit_operand(uint32_t op, uint32_t k) {   // operand word k of op is a literal, not an id (for the check that moved variables are used only through access chains)
    switch (op) {
    case OpExtInst: return k == 4;
    case OpCompositeExtract: return k >= 4;
    case OpCompositeInsert: case OpVectorShuffle: return k >= 5;
    case OpSelectionMerge: return k >= 2;
    case OpLoopMerge: return k >= 3;
    case OpSwitch: return k >= 3 && (k - 3) % 2 == 0;
    case OpLoad: case OpStore: return k >= (op == OpLoad ? 4u : 3u);   // memory operands
    default: return 0;
    }
}
typedef struct {   // the module as read: types, constants and the variables the rewrite moves
    uint32_t bound, *op_of, *a1, *a2, *a3, *cval; uint8_t *is_const;   // per id: the defining opcode and its first operands (types), constant values
    uint32_t t_void, t_bool, t_u32, t_f32, t_v3u, t_v4f, t_fn;
    uint32_t model, main_fn, wg_var, nwg_var;
    uint32_t outs[24], nouts; uint32_t pay_var;
} rd_t;
// The pointer type (sc, pointee) the module declares, or a new one made in decl (cached)
typedef struct { uint32_t sc, t, id; } ptr_t;
typedef struct { ptr_t p[256]; int n; } ptab_t;
static uint32_t ptr_get(ptab_t *pt, vec_t *decl, uint32_t *nb, uint32_t sc, uint32_t t) {
    for (int i = 0; i < pt->n; i++) if (pt->p[i].sc == sc && pt->p[i].t == t) return pt->p[i].id;
    if (pt->n == 256) return 0;
    uint32_t id = (*nb)++; ins(decl, OpTypePointer, 3, id, sc, t);
    pt->p[pt->n++] = (ptr_t){sc, t, id};
    return id;
}
typedef struct { uint32_t v, id; } cst_t;
typedef struct { cst_t c[64]; int n; uint32_t t; } ctab_t;
static uint32_t cst_get(ctab_t *ct, vec_t *decl, uint32_t *nb, uint32_t v) {
    for (int i = 0; i < ct->n; i++) if (ct->c[i].v == v) return ct->c[i].id;
    if (ct->n == 64) return 0;
    uint32_t id = (*nb)++; ins(decl, OpConstant, 3, ct->t, id, v);
    ct->c[ct->n++] = (cst_t){v, id};
    return id;
}

uint32_t *vka_spv_mesh_compute(const uint32_t *w, size_t nw, uint32_t out_binding, uint32_t pay_binding, vka_mesh_info *mi, size_t *out_nw, char *err, size_t errn) {
#define FAIL(...) do { snprintf(err, errn, __VA_ARGS__); goto fail; } while (0)
    rd_t R; memset(&R, 0, sizeof R); memset(mi, 0, sizeof *mi);
    vec_t head = {0}, ann = {0}, decl = {0}, fn = {0}, o = {0};
    uint8_t *gone = NULL; uint32_t *memb = NULL, *ptee = NULL;   // gone[id]: a moved variable (1 output, 2 payload, 3 the mesh's WorkgroupId), memb[var]: its member in the record
    ptab_t *pt = calloc(1, sizeof *pt); ctab_t ct; memset(&ct, 0, sizeof ct);
    if (!pt) { snprintf(err, errn, "out of memory"); return NULL; }
    if (nw < 5 || w[0] != 0x07230203) FAIL("not SPIR-V");
    R.bound = w[3];
    R.op_of = calloc(R.bound, 4); R.a1 = calloc(R.bound, 4); R.a2 = calloc(R.bound, 4); R.a3 = calloc(R.bound, 4); R.cval = calloc(R.bound, 4); R.is_const = calloc(R.bound, 1);
    gone = calloc(R.bound, 1); memb = calloc(R.bound, 4); ptee = calloc(R.bound, 4);
    if (!R.op_of || !R.a1 || !R.a2 || !R.a3 || !R.cval || !R.is_const || !gone || !memb || !ptee) FAIL("out of memory");
    size_t first_fn = nw, first_type = nw;
    uint32_t local[3] = {1, 1, 1}, maxv = 0, maxp = 0;
    // Pass 1: what the module declares
    for (size_t i = 5; i < nw;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (!len || i + len > nw) FAIL("bad instruction at word %zu", i);
        const uint32_t *in = w + i;
        if (op >= OpTypeVoid && op <= OpTypeFunction && len >= 2 && in[1] < R.bound) {
            R.op_of[in[1]] = op; if (len > 2) R.a1[in[1]] = in[2]; if (len > 3) R.a2[in[1]] = in[3]; if (len > 4) R.a3[in[1]] = in[4];
            if (first_type == nw) first_type = i;
        }
        if (op == OpTypeVoid) R.t_void = in[1];
        else if (op == OpTypeBool) R.t_bool = in[1];
        else if (op == OpTypeInt && in[2] == 32 && !in[3]) R.t_u32 = in[1];
        else if (op == OpTypeFloat && in[2] == 32) R.t_f32 = in[1];
        else if (op == OpTypeFunction && len == 3 && in[2] == R.t_void && R.t_void) R.t_fn = in[1];
        else if (op == OpTypeVector && R.t_u32 && in[2] == R.t_u32 && in[3] == 3) R.t_v3u = in[1];
        else if (op == OpTypeVector && R.t_f32 && in[2] == R.t_f32 && in[3] == 4) R.t_v4f = in[1];
        else if (op == OpTypePointer) { if (pt->n < 256) pt->p[pt->n++] = (ptr_t){in[2], in[3], in[1]}; ptee[in[1]] = in[3]; }
        else if (op == OpConstant && len == 4 && in[2] < R.bound) { R.is_const[in[2]] = 1; R.cval[in[2]] = in[3]; if (in[1] == R.t_u32 && ct.n < 64) ct.c[ct.n++] = (cst_t){in[3], in[2]}; }
        else if (op == OpEntryPoint) { if (R.model) FAIL("more than one entry point"); R.model = in[1]; R.main_fn = in[2]; }
        else if (op == OpExecutionMode && len >= 3) {
            if (in[2] == ModeLocalSize && len >= 6) { local[0] = in[3]; local[1] = in[4]; local[2] = in[5]; }
            else if (in[2] == ModeOutputVertices && len >= 4) maxv = in[3];
            else if (in[2] == ModeOutputPrims && len >= 4) maxp = in[3];
        }
        else if (op == OpDecorate && len >= 4 && in[2] == DecBuiltIn && in[1] < R.bound) {
            if (in[3] == BIWorkgroupId) R.wg_var = in[1]; else if (in[3] == BINumWorkgroups) R.nwg_var = in[1];
        }
        else if (op == OpVariable && len >= 4 && in[2] < R.bound && i < first_fn) {
            if (in[3] == SCOutput) { if (R.nouts == 24) FAIL("too many outputs"); R.outs[R.nouts++] = in[2]; gone[in[2]] = 1; }
            else if (in[3] == SCTaskPayload) { if (R.pay_var) FAIL("two payload variables"); R.pay_var = in[2]; gone[in[2]] = 2; }
            if (in[3] == SCInput || in[3] == SCOutput || in[3] == SCTaskPayload) R.a3[in[2]] = in[1];   // (variables: their pointer type)
        }
        else if (op == OpFunction && first_fn == nw) first_fn = i;
        i += len;
    }
    if (R.model != EMMesh && R.model != EMTask) FAIL("not a task or mesh entry point (execution model %u)", R.model);
    if (!R.t_u32 || !R.t_fn || !R.t_void || first_fn == nw || first_type == nw) FAIL("no 32-bit unsigned type, void function type or function");
    const int mesh = R.model == EMMesh;
    if (mesh && (!maxv || !maxp || !R.nouts)) FAIL("a mesh entry point without its output counts or outputs");
    if (!mesh && R.nouts) FAIL("a task entry point with outputs");
    if (!mesh && !R.pay_var) FAIL("a task entry point without a payload");
    if (R.wg_var && (R.a3[R.wg_var] == 0 || ptee[R.a3[R.wg_var]] != R.t_v3u)) FAIL("a WorkgroupId that is not a uvec3 input");
    if (mesh && R.wg_var) gone[R.wg_var] = 3;
    uint32_t nb = R.bound; ct.t = R.t_u32;
    mi->role = mesh ? 1 : 2; memcpy(mi->local, local, sizeof local); mi->max_v = maxv; mi->max_p = maxp;
    // The output record: nprim, then each output array at 16-byte stride (pass 1 of the annotations gives the locations and the built-ins of each)
    uint32_t loc_of[24], prim_of[24], bi_of[24]; for (int k = 0; k < 24; k++) { loc_of[k] = ~0u; prim_of[k] = 0; bi_of[k] = ~0u; }
    for (size_t i = 5; i < first_type;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16; const uint32_t *in = w + i;
        if (op == OpDecorate && len >= 3) for (uint32_t k = 0; k < R.nouts; k++) if (R.outs[k] == in[1]) {
            if (in[2] == DecLocation && len >= 4) loc_of[k] = in[3]; else if (in[2] == DecPerPrimitive) prim_of[k] = 1; else if (in[2] == DecBuiltIn && len >= 4) bi_of[k] = in[3];
        }
        i += len;
    }
    uint32_t rec_types[25], off = 16, new_arr[24], new_struct_pos = 0;
    // New types (in decl; their decorations in ann): the record of the outputs, the payload record
    uint32_t c0 = 0, t_out_blk = 0, v_out = 0, t_pay_blk = 0, v_pay = 0, v_wg = 0, v_nwg = 0, v_slot = 0, v_task = 0;
    for (uint32_t k = 0; k < R.nouts; k++) {
        uint32_t pty = R.a3[R.outs[k]], arr = ptee[pty];
        if (!arr || R.op_of[arr] != OpTypeArray) FAIL("output %u is not an array", R.outs[k]);
        uint32_t el = R.a1[arr], lenc = R.a2[arr], n = R.is_const[lenc] ? R.cval[lenc] : 0;
        if (!n) FAIL("output %u: an array without a constant length", R.outs[k]);
        uint32_t nel = el;
        if (R.op_of[el] == OpTypeStruct) {   // the gl_MeshPerVertexEXT block: Position only
            if (R.a1[el] != R.t_v4f || R.a2[el]) FAIL("output %u: a per-vertex block other than { vec4 Position }", R.outs[k]);
            if (!new_struct_pos) { new_struct_pos = nb++; ins(&decl, OpTypeStruct, 2, new_struct_pos, R.t_v4f); ins(&ann, OpMemberDecorate, 4, new_struct_pos, 0, DecOffset, 0); }
            nel = new_struct_pos; mi->pos_off = off; bi_of[k] = BIPosition;
        } else if (el == R.t_v3u && bi_of[k] == BIPrimIndices) { mi->idx_off = off; if (n != maxp) FAIL("primitive indices for %u primitives, %u declared", n, maxp); }
        else if (el == R.t_v4f && loc_of[k] != ~0u) {
            if (mi->nloc == 16) FAIL("too many output locations");
            mi->locs[mi->nloc].loc = (uint8_t)loc_of[k]; mi->locs[mi->nloc].prim = (uint8_t)prim_of[k]; mi->locs[mi->nloc].off = off; mi->nloc++;
            if (n != (prim_of[k] ? maxp : maxv)) FAIL("output location %u holds %u, %u declared", loc_of[k], n, prim_of[k] ? maxp : maxv);
        } else FAIL("output %u: not a vec4 location, the primitive indices or the position", R.outs[k]);
        new_arr[k] = nb++; ins(&decl, OpTypeArray, 3, new_arr[k], nel, lenc); ins(&ann, OpDecorate, 3, new_arr[k], DecArrayStride, 16);
        rec_types[k + 1] = new_arr[k]; memb[R.outs[k]] = k + 1;
        ins(&ann, OpMemberDecorate, 4, 0, k + 1, DecOffset, off);   // (the record's id is filled below)
        off += 16 * n;
    }
    if (mesh && (!mi->pos_off || !mi->idx_off)) FAIL("a mesh entry point without position or primitive indices");
    if (mesh) {
        mi->rec_bytes = off;
        uint32_t rec = nb++, rta = nb++; t_out_blk = nb++; v_out = nb++;
        for (size_t i = 0; i < ann.n;) { uint32_t len = ann.w[i] >> 16; if ((ann.w[i] & 0xffff) == OpMemberDecorate && ann.w[i + 1] == 0) ann.w[i + 1] = rec; i += len; }
        put(&decl, (uint32_t)(R.nouts + 3) << 16 | OpTypeStruct); put(&decl, rec); put(&decl, R.t_u32); for (uint32_t k = 0; k < R.nouts; k++) put(&decl, new_arr[k]);
        ins(&decl, OpTypeRuntimeArray, 2, rta, rec); ins(&decl, OpTypeStruct, 2, t_out_blk, rta);
        ins(&ann, OpMemberDecorate, 4, rec, 0, DecOffset, 0); ins(&ann, OpDecorate, 3, rta, DecArrayStride, off);
        ins(&ann, OpDecorate, 2, t_out_blk, DecBlock); ins(&ann, OpMemberDecorate, 4, t_out_blk, 0, DecOffset, 0);
        ins(&ann, OpDecorate, 3, v_out, DecDescriptorSet, 0); ins(&ann, OpDecorate, 3, v_out, DecBinding, out_binding);
    }
    (void)rec_types;
    if (R.pay_var) {
        uint32_t arr = ptee[R.a3[R.pay_var]];
        if (!arr || R.op_of[arr] != OpTypeArray || R.a1[arr] != R.t_u32 || !R.is_const[R.a2[arr]]) FAIL("a payload that is not a uint array");
        uint32_t n = R.cval[R.a2[arr]]; mi->pay_words = n; mi->pay_rec_bytes = (16 + 4 * n + 15) & ~15u;
        uint32_t pa = nb++, rec = nb++, rta = nb++; t_pay_blk = nb++; v_pay = nb++; memb[R.pay_var] = 1;
        ins(&decl, OpTypeArray, 3, pa, R.t_u32, R.a2[arr]); ins(&decl, OpTypeStruct, 3, rec, R.t_u32, pa); ins(&decl, OpTypeRuntimeArray, 2, rta, rec); ins(&decl, OpTypeStruct, 2, t_pay_blk, rta);
        ins(&ann, OpDecorate, 3, pa, DecArrayStride, 4); ins(&ann, OpMemberDecorate, 4, rec, 0, DecOffset, 0); ins(&ann, OpMemberDecorate, 4, rec, 1, DecOffset, 16);
        ins(&ann, OpDecorate, 3, rta, DecArrayStride, mi->pay_rec_bytes); ins(&ann, OpDecorate, 2, t_pay_blk, DecBlock); ins(&ann, OpMemberDecorate, 4, t_pay_blk, 0, DecOffset, 0);
        ins(&ann, OpDecorate, 3, v_pay, DecDescriptorSet, 0); ins(&ann, OpDecorate, 3, v_pay, DecBinding, pay_binding);
        if (mesh) ins(&ann, OpDecorate, 2, v_pay, DecNonWritable);
    }
    if (!R.t_bool) { R.t_bool = nb++; ins(&decl, OpTypeBool, 1, R.t_bool); }
    if (!R.t_v3u) FAIL("no uvec3 type");
    c0 = cst_get(&ct, &decl, &nb, 0);
    uint32_t p_in_v3 = ptr_get(pt, &decl, &nb, SCInput, R.t_v3u), p_priv_u = ptr_get(pt, &decl, &nb, SCPrivate, R.t_u32), p_sb_u = ptr_get(pt, &decl, &nb, SCStorageBuffer, R.t_u32);
    if (mesh || !R.wg_var) { v_wg = nb++; ins(&decl, OpVariable, 3, p_in_v3, v_wg, SCInput); ins(&ann, OpDecorate, 3, v_wg, DecBuiltIn, BIWorkgroupId); }
    else v_wg = R.wg_var;
    if (mesh) {
        if (R.nwg_var) FAIL("a mesh entry point that reads NumWorkgroups");
        v_nwg = nb++; ins(&decl, OpVariable, 3, p_in_v3, v_nwg, SCInput); ins(&ann, OpDecorate, 3, v_nwg, DecBuiltIn, BINumWorkgroups);
        v_slot = nb++; v_task = nb++; ins(&decl, OpVariable, 3, p_priv_u, v_slot, SCPrivate); ins(&decl, OpVariable, 3, p_priv_u, v_task, SCPrivate);
        uint32_t p_out = ptr_get(pt, &decl, &nb, SCStorageBuffer, t_out_blk); ins(&decl, OpVariable, 3, p_out, v_out, SCStorageBuffer);
        if (R.wg_var) ins(&decl, OpVariable, 3, ptr_get(pt, &decl, &nb, SCPrivate, R.t_v3u), R.wg_var, SCPrivate);
    }
    if (R.pay_var) ins(&decl, OpVariable, 3, ptr_get(pt, &decl, &nb, SCStorageBuffer, t_pay_blk), v_pay, SCStorageBuffer);
    // Pass 2a: the functions, rewritten (into fn); new types they need go to decl
    {
        uint32_t cur_fn = 0;
        for (size_t i = first_fn; i < nw;) {
            uint32_t op = w[i] & 0xffff, len = w[i] >> 16; const uint32_t *in = w + i;
            if (op == OpFunction) cur_fn = in[2];
            (void)cur_fn;
            if ((op == OpAccessChain || op == OpInBoundsAccessChain) && len >= 4 && in[3] < R.bound && gone[in[3]]) {
                uint32_t g = gone[in[3]], rt = in[1], pe = rt < R.bound ? ptee[rt] : 0;
                if (!pe || (R.op_of[pe] != OpTypeVector && R.op_of[pe] != OpTypeInt && R.op_of[pe] != OpTypeFloat)) FAIL("an access chain to an aggregate of a moved variable (id %u)", in[3]);
                if (g == 3) {   // the mesh's WorkgroupId, now Private
                    put_n(&fn, in, len); fn.w[fn.n - len + 1] = ptr_get(pt, &decl, &nb, SCPrivate, pe);
                    i += len; continue;
                }
                uint32_t s = nb++;
                if (mesh) ins(&fn, OpLoad, 3, R.t_u32, s, g == 1 ? v_slot : v_task);
                else { uint32_t a = nb++; ins(&fn, OpLoad, 3, R.t_v3u, a, v_wg); ins(&fn, OpCompositeExtract, 4, R.t_u32, s, a, 0); }
                put(&fn, (len + 3) << 16 | op); put(&fn, ptr_get(pt, &decl, &nb, SCStorageBuffer, pe)); put(&fn, in[2]); put(&fn, g == 1 ? v_out : v_pay);
                put(&fn, c0); put(&fn, s); put(&fn, cst_get(&ct, &decl, &nb, memb[in[3]])); put_n(&fn, in + 4, len - 4);
                i += len; continue;
            }
            if (op == OpSetMeshOutputsEXT && len == 3) {
                uint32_t s = nb++, p = nb++;
                ins(&fn, OpLoad, 3, R.t_u32, s, v_slot); ins(&fn, OpAccessChain, 6, p_sb_u, p, v_out, c0, s, c0); ins(&fn, OpStore, 2, p, in[2]);
                i += len; continue;
            }
            if (op == OpEmitMeshTasksEXT && len >= 4) {
                uint32_t a = nb++, s = nb++, m1 = nb++, m2 = nb++, p = nb++;
                ins(&fn, OpIMul, 4, R.t_u32, m1, in[1], in[2]); ins(&fn, OpIMul, 4, R.t_u32, m2, m1, in[3]);
                ins(&fn, OpLoad, 3, R.t_v3u, a, v_wg); ins(&fn, OpCompositeExtract, 4, R.t_u32, s, a, 0);
                ins(&fn, OpAccessChain, 6, p_sb_u, p, v_pay, c0, s, c0); ins(&fn, OpStore, 2, p, m2); ins(&fn, OpReturn, 0);
                i += len; continue;
            }
            if (op == OpControlBarrier || op == OpMemoryBarrier) {
                uint32_t si = op == OpControlBarrier ? 3 : 2;
                if (len <= si || !R.is_const[in[si]]) FAIL("a barrier with non-constant semantics");
                put_n(&fn, in, len); fn.w[fn.n - len + si] = cst_get(&ct, &decl, &nb, R.cval[in[si]] | MemUniform);
                i += len; continue;
            }
            for (uint32_t k = 1; k < len; k++) if (in[k] < R.bound && gone[in[k]] && gone[in[k]] != 3 && !lit_operand(op, k)) FAIL("opcode %u uses a moved variable (id %u) other than through an access chain", op, in[k]);
            if (op == OpLoad && len >= 4 && gone[in[3]] == 3) { put_n(&fn, in, len); i += len; continue; }
            put_n(&fn, in, len);
            i += len;
        }
    }
    // The mesh entry: slot, task, own WorkgroupId, then the original entry point (or nprim = 0 for a workgroup past its task workgroup's count)
    uint32_t entry = R.main_fn;
    if (mesh) {
        entry = nb++;
        uint32_t L0 = nb++, a = nb++, x = nb++, y = nb++, n = nb++, nx = nb++, t = nb++, s = nb++, v = nb++, call = nb++;
        ins(&fn, OpFunction, 4, R.t_void, entry, 0, R.t_fn); ins(&fn, OpLabel, 1, L0);
        ins(&fn, OpLoad, 3, R.t_v3u, a, v_wg); ins(&fn, OpCompositeExtract, 4, R.t_u32, x, a, 0); ins(&fn, OpCompositeExtract, 4, R.t_u32, y, a, 1);
        ins(&fn, OpLoad, 3, R.t_v3u, n, v_nwg); ins(&fn, OpCompositeExtract, 4, R.t_u32, nx, n, 0);
        ins(&fn, OpIMul, 4, R.t_u32, t, y, nx); ins(&fn, OpIAdd, 4, R.t_u32, s, t, x);
        ins(&fn, OpStore, 2, v_slot, s); ins(&fn, OpStore, 2, v_task, y);
        if (R.wg_var) { ins(&fn, OpCompositeConstruct, 5, R.t_v3u, v, x, c0, c0); ins(&fn, OpStore, 2, R.wg_var, v); }
        if (R.pay_var) {
            uint32_t pc = nb++, cnt = nb++, ok = nb++, Lrun = nb++, Lskip = nb++, Lend = nb++, pz = nb++;
            ins(&fn, OpAccessChain, 6, p_sb_u, pc, v_pay, c0, y, c0); ins(&fn, OpLoad, 3, R.t_u32, cnt, pc); ins(&fn, OpULessThan, 4, R.t_bool, ok, x, cnt);
            ins(&fn, OpSelectionMerge, 2, Lend, 0); ins(&fn, OpBranchConditional, 3, ok, Lrun, Lskip);
            ins(&fn, OpLabel, 1, Lrun); ins(&fn, OpFunctionCall, 3, R.t_void, call, R.main_fn); ins(&fn, OpBranch, 1, Lend);
            ins(&fn, OpLabel, 1, Lskip); ins(&fn, OpAccessChain, 6, p_sb_u, pz, v_out, c0, s, c0); ins(&fn, OpStore, 2, pz, c0); ins(&fn, OpBranch, 1, Lend);
            ins(&fn, OpLabel, 1, Lend); ins(&fn, OpReturn, 0);
        } else { ins(&fn, OpFunctionCall, 3, R.t_void, call, R.main_fn); ins(&fn, OpReturn, 0); }
        ins(&fn, OpFunctionEnd, 0);
    }
    // Pass 2b: the module before its functions: capabilities and extension of mesh shading dropped, the entry point and its modes, names and annotations of moved variables dropped
    for (size_t i = 5; i < first_fn;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16; const uint32_t *in = w + i;
        if (i == first_type) put_n(&head, ann.w, ann.n);   // the new annotations, after the module's own
        if (op == OpCapability && in[1] == CapMesh) { i += len; continue; }
        if (op == OpExtension && str_eq(in + 1, len - 1, "SPV_EXT_mesh_shader")) { i += len; continue; }
        if (op == OpEntryPoint) {
            size_t nl = 0; while (3 + nl < len && !(((in[3 + nl] >> 24) & 0xff) == 0)) nl++;   // the name's words (ends in a word with a NUL top byte)
            nl++;
            vec_t ep = {0}; put(&ep, 0); put(&ep, EMGLCompute); put(&ep, entry); put_n(&ep, in + 3, nl);
            for (uint32_t k = 3 + (uint32_t)nl; k < len; k++) if (!(in[k] < R.bound && gone[in[k]] && gone[in[k]] != 3)) put(&ep, in[k]);
            if (v_wg != R.wg_var || mesh) put(&ep, v_wg);
            if (mesh) { put(&ep, v_nwg); put(&ep, v_slot); put(&ep, v_task); put(&ep, v_out); }
            if (R.pay_var) put(&ep, v_pay);
            ep.w[0] = (uint32_t)ep.n << 16 | OpEntryPoint; put_n(&head, ep.w, ep.n); int bad = ep.oom; free(ep.w);
            if (bad) FAIL("out of memory");
            i += len; continue;
        }
        if (op == OpExecutionMode) {
            if (in[2] == ModeOutputVertices || in[2] == ModeOutputPrims || in[2] == ModeOutputTriangles) { i += len; continue; }
            put_n(&head, in, len); head.w[head.n - len + 1] = entry; i += len; continue;
        }
        if ((op == OpName || op == OpDecorate || op == OpMemberDecorate || op == OpDecorateId || op == OpDecorateString || op == OpMemberDecorateString) && len >= 2 && in[1] < R.bound && gone[in[1]]) {
            if (!(gone[in[1]] == 3 && op == OpName)) { i += len; continue; }
        }
        if (op == OpMemberDecorate && len >= 4 && in[3] == DecBuiltIn) { i += len; continue; }   // the old per-vertex block's Position (its type stays, unused)
        if (op == OpDecorate && len >= 3 && in[2] == DecBlock && R.op_of[in[1]] == OpTypeStruct && R.a1[in[1]] == R.t_v4f && !R.a2[in[1]]) { i += len; continue; }
        if (op == OpVariable && len >= 4 && in[2] < R.bound && gone[in[2]]) { i += len; continue; }
        if (op == OpTypePointer && in[2] == SCTaskPayload) { i += len; continue; }   // (only the moved payload had them)
        put_n(&head, in, len);
        i += len;
    }
    put_n(&o, w, 5); put_n(&o, head.w, head.n); put_n(&o, decl.w, decl.n); put_n(&o, fn.w, fn.n);
    if (head.oom || ann.oom || decl.oom || fn.oom || o.oom) FAIL("out of memory");
    o.w[3] = nb;
    free(head.w); free(ann.w); free(decl.w); free(fn.w);
    free(R.op_of); free(R.a1); free(R.a2); free(R.a3); free(R.cval); free(R.is_const); free(gone); free(memb); free(ptee); free(pt);
    *out_nw = o.n; return o.w;
fail:
    free(head.w); free(ann.w); free(decl.w); free(fn.w); free(o.w);
    free(R.op_of); free(R.a1); free(R.a2); free(R.a3); free(R.cval); free(R.is_const); free(gone); free(memb); free(ptee); free(pt);
    return NULL;
#undef FAIL
}

// ---------- vka_spv_mesh_vs: the vertex shader that draws the records ----------
// Non-indexed triangles: vertex v is corner v % 3 of primitive t = v / 3, primitive t % max_p of the record t / max_p. A primitive at or past the record's nprim gets a zero
// position at every corner (no area: not drawn). Per-vertex locations read the vertex the corner's index names, per-primitive locations the primitive (Flat, so a pixel
// shader's flat input sees the same value on every corner)
uint32_t *vka_spv_mesh_vs(const vka_mesh_info *mi, uint32_t binding, size_t *out_nw) {
    if (mi->role != 1 || !mi->max_p || (mi->rec_bytes & 3)) return NULL;
    vec_t o = {0}; uint32_t nb = 1;
    #define ID() (nb++)
    uint32_t t_void = ID(), t_fn = ID(), t_u = ID(), t_i = ID(), t_f = ID(), t_v4 = ID(), t_b = ID(), t_bv4 = ID(), p_in_i = ID(), p_out_v4 = ID(), rta = ID(), blk = ID(), p_blk = ID(), p_sb_u = ID();
    uint32_t v_vid = ID(), v_pos = ID(), v_buf = ID(), v_out[16]; for (uint32_t k = 0; k < mi->nloc; k++) v_out[k] = ID();
    uint32_t main_fn = ID(), L0 = ID();
    uint32_t cv[32]; uint32_t cvals[32]; int ncv = 0;
    #define CST(val) ({ uint32_t v_ = (val), r_ = 0; for (int q_ = 0; q_ < ncv; q_++) if (cvals[q_] == v_) r_ = cv[q_]; if (!r_ && ncv < 32) { cvals[ncv] = v_; r_ = cv[ncv++] = ID(); } r_; })
    uint32_t c0 = CST(0), c1 = CST(1), c2 = CST(2), c3 = CST(3), c4 = CST(4), cP = CST(mi->max_p), cRW = CST(mi->rec_bytes / 4), cIdx = CST(mi->idx_off / 4), cPos = CST(mi->pos_off / 4),
             cV = CST(mi->max_v); uint32_t cLoc[16]; for (uint32_t k = 0; k < mi->nloc; k++) cLoc[k] = CST(mi->locs[k].off / 4);
    uint32_t f0 = ID(), z4 = ID();
    // Header, capability, memory model, entry point
    put(&o, 0x07230203); put(&o, 0x00010300); put(&o, 0); put(&o, 0); put(&o, 0);
    ins(&o, 17, 1, 1); ins(&o, OpMemoryModel, 2, 0, 1);
    { vec_t ep = {0}; put(&ep, 0); put(&ep, EMVertex); put(&ep, main_fn); put_str(&ep, "main"); put(&ep, v_vid); put(&ep, v_pos); for (uint32_t k = 0; k < mi->nloc; k++) put(&ep, v_out[k]);
      ep.w[0] = (uint32_t)ep.n << 16 | OpEntryPoint; put_n(&o, ep.w, ep.n); free(ep.w); }
    ins(&o, OpDecorate, 3, v_vid, DecBuiltIn, BIVertexIndex); ins(&o, OpDecorate, 3, v_pos, DecBuiltIn, BIPosition);
    for (uint32_t k = 0; k < mi->nloc; k++) { ins(&o, OpDecorate, 3, v_out[k], DecLocation, mi->locs[k].loc); if (mi->locs[k].prim) ins(&o, OpDecorate, 2, v_out[k], DecFlat); }
    ins(&o, OpDecorate, 3, rta, DecArrayStride, 4); ins(&o, OpDecorate, 2, blk, DecBlock); ins(&o, OpMemberDecorate, 4, blk, 0, DecOffset, 0); ins(&o, OpMemberDecorate, 3, blk, 0, DecNonWritable);
    ins(&o, OpDecorate, 3, v_buf, DecDescriptorSet, 0); ins(&o, OpDecorate, 3, v_buf, DecBinding, binding);
    ins(&o, OpTypeVoid, 1, t_void); ins(&o, OpTypeFunction, 2, t_fn, t_void); ins(&o, OpTypeInt, 3, t_u, 32, 0); ins(&o, OpTypeInt, 3, t_i, 32, 1); ins(&o, OpTypeFloat, 2, t_f, 32);
    ins(&o, OpTypeVector, 3, t_v4, t_f, 4); ins(&o, OpTypeBool, 1, t_b); ins(&o, OpTypeVector, 3, t_bv4, t_b, 4);
    ins(&o, OpTypePointer, 3, p_in_i, SCInput, t_i); ins(&o, OpTypePointer, 3, p_out_v4, SCOutput, t_v4);
    ins(&o, OpTypeRuntimeArray, 2, rta, t_u); ins(&o, OpTypeStruct, 2, blk, rta); ins(&o, OpTypePointer, 3, p_blk, SCStorageBuffer, blk); ins(&o, OpTypePointer, 3, p_sb_u, SCStorageBuffer, t_u);
    for (int q = 0; q < ncv; q++) ins(&o, OpConstant, 3, t_u, cv[q], cvals[q]);
    ins(&o, OpConstant, 3, t_f, f0, 0); ins(&o, OpConstantComposite, 6, t_v4, z4, f0, f0, f0, f0);
    ins(&o, OpVariable, 3, p_in_i, v_vid, SCInput); ins(&o, OpVariable, 3, p_out_v4, v_pos, SCOutput); ins(&o, OpVariable, 3, p_blk, v_buf, SCStorageBuffer);
    for (uint32_t k = 0; k < mi->nloc; k++) ins(&o, OpVariable, 3, p_out_v4, v_out[k], SCOutput);
    // The function
    ins(&o, OpFunction, 4, t_void, main_fn, 0, t_fn); ins(&o, OpLabel, 1, L0);
    #define LDW(dst, idx) do { uint32_t p_ = ID(); ins(&o, OpAccessChain, 5, p_sb_u, p_, v_buf, c0, (idx)); ins(&o, OpLoad, 3, t_u, (dst), p_); } while (0)
    #define BIN(opc, dst, a, b) ins(&o, (opc), 4, t_u, (dst), (a), (b))
    uint32_t vi = ID(), v = ID(), tri = ID(), t3 = ID(), c = ID(), slot = ID(), sp = ID(), p = ID(), base = ID(), np = ID(), ok = ID();
    ins(&o, OpLoad, 3, t_i, vi, v_vid); ins(&o, OpBitcast, 3, t_u, v, vi);
    BIN(OpUDiv, tri, v, c3); BIN(OpIMul, t3, tri, c3); BIN(OpISub, c, v, t3);
    BIN(OpUDiv, slot, tri, cP); BIN(OpIMul, sp, slot, cP); BIN(OpISub, p, tri, sp);
    BIN(OpIMul, base, slot, cRW); LDW(np, base); ins(&o, OpULessThan, 4, t_b, ok, p, np);
    uint32_t ia = ID(), p4 = ID(), ib = ID(), ic = ID(), ii0 = ID(), ii1 = ID(), iok = ID(), ii = ID(), i4 = ID();
    BIN(OpIAdd, ia, base, cIdx); BIN(OpIMul, p4, p, c4); BIN(OpIAdd, ib, ia, p4); BIN(OpIAdd, ic, ib, c); LDW(ii0, ic);
    ins(&o, OpSelect, 5, t_u, ii1, ok, ii0, c0); ins(&o, OpULessThan, 4, t_b, iok, ii1, cV); ins(&o, OpSelect, 5, t_u, ii, iok, ii1, c0); BIN(OpIMul, i4, ii, c4);
    // A vec4 of the four words from word `at`
    #define VEC4(dst, at) do { uint32_t w_[4], f_[4]; for (int q_ = 0; q_ < 4; q_++) { uint32_t a_ = ID(); w_[q_] = ID(); f_[q_] = ID(); BIN(OpIAdd, a_, (at), q_ == 0 ? c0 : q_ == 1 ? c1 : q_ == 2 ? c2 : c3); \
                                    LDW(w_[q_], a_); ins(&o, OpBitcast, 3, t_f, f_[q_], w_[q_]); } ins(&o, OpCompositeConstruct, 6, t_v4, (dst), f_[0], f_[1], f_[2], f_[3]); } while (0)
    uint32_t pb = ID(), pv = ID(), pos0 = ID(), okv = ID(), pos = ID();
    BIN(OpIAdd, pb, base, cPos); BIN(OpIAdd, pv, pb, i4); VEC4(pos0, pv);
    ins(&o, OpCompositeConstruct, 6, t_bv4, okv, ok, ok, ok, ok); ins(&o, OpSelect, 5, t_v4, pos, okv, pos0, z4); ins(&o, OpStore, 2, v_pos, pos);
    for (uint32_t k = 0; k < mi->nloc; k++) {
        uint32_t lb = ID(), lv = ID(), val = ID();
        BIN(OpIAdd, lb, base, cLoc[k]); BIN(OpIAdd, lv, lb, mi->locs[k].prim ? p4 : i4); VEC4(val, lv); ins(&o, OpStore, 2, v_out[k], val);
    }
    ins(&o, OpReturn, 0); ins(&o, OpFunctionEnd, 0);
    #undef VEC4
    #undef LDW
    #undef BIN
    #undef CST
    #undef ID
    if (o.oom) { free(o.w); return NULL; }
    o.w[3] = nb; *out_nw = o.n; return o.w;
}

// ---------- vka_spv_unprim: a mesh program's pixel shader for the vertex shader above ----------
// Its per-primitive inputs (PerPrimitiveEXT, valid only after a mesh stage) become Flat inputs, which vka_spv_mesh_vs writes the same on every corner; the mesh shading
// capability and extension go
uint32_t *vka_spv_unprim(const uint32_t *w, size_t nw, size_t *out_nw, char *err, size_t errn) {
    if (nw < 5 || w[0] != 0x07230203) { snprintf(err, errn, "not SPIR-V"); return NULL; }
    uint32_t bound = w[3]; uint8_t *flat = calloc(bound, 1), *prim = calloc(bound, 1); vec_t o = {0};
    if (!flat || !prim) { free(flat); free(prim); snprintf(err, errn, "out of memory"); return NULL; }
    size_t first_type = nw;
    for (size_t i = 5; i < nw;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16;
        if (!len || i + len > nw) { free(flat); free(prim); snprintf(err, errn, "bad instruction at word %zu", i); return NULL; }
        if (op == OpDecorate && len >= 3 && w[i + 1] < bound) { if (w[i + 2] == DecFlat) flat[w[i + 1]] = 1; if (w[i + 2] == DecPerPrimitive) prim[w[i + 1]] = 1; }
        if (op >= OpTypeVoid && op <= OpTypeFunction && first_type == nw) first_type = i;
        i += len;
    }
    put_n(&o, w, 5);
    for (size_t i = 5; i < nw;) {
        uint32_t op = w[i] & 0xffff, len = w[i] >> 16; const uint32_t *in = w + i;
        if (op == OpCapability && in[1] == CapMesh) { i += len; continue; }
        if (op == OpExtension && str_eq(in + 1, len - 1, "SPV_EXT_mesh_shader")) { i += len; continue; }
        if (op == OpDecorate && len >= 3 && in[2] == DecPerPrimitive) {
            if (!flat[in[1]]) ins(&o, OpDecorate, 2, in[1], DecFlat);
            i += len; continue;
        }
        put_n(&o, in, len);
        i += len;
    }
    free(flat); free(prim);
    if (o.oom) { free(o.w); snprintf(err, errn, "out of memory"); return NULL; }
    *out_nw = o.n; return o.w;
}
