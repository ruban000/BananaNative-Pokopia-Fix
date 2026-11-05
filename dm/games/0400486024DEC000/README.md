# The Legend of Zelda: Ocarina of Time (`0400486024DEC000`)

A Switch 2 game: update v131072 (the dump's content version; no `control.nacp`, so no display version), SDK 22.2.8 built for the Switch 2 (`spec: Ounce`), NVN2 API 231.40. Nintendo EPD's engine (sead / agl / gsys, Havok, CRIWARE), modules `main`, `subsdk0` (NintendoSDK movie: the AV1 movies) and `sdk`; every NSO segment is ZBIC-compressed. **In bring-up: packed for Android (2026-11-05), never run** on any host. Everything below about memory, movies and rendering is read from the files, not measured.

## What it needs

| Setting | Where | Why |
|---|---|---|
| `modules main subsdk0 sdk` | [`game.tsv`](game.tsv) | `subsdk0` is NintendoSDK movie (824 exports), which `main` imports; `rtld` is replaced by the host |
| `sys_resource 0x3000000` | `game.tsv` | the update's NPDM declares 48 MB of system resource (the base's: 24 MB): virtual address memory on, as Mario Kart World |
| `app_pool 0x240000000` | `game.tsv` | the Switch 2's 9 GB, Donkey Kong Bananza's and Mario Kart World's figure; nothing in this game's files gives the pool ([Memory](#memory)) |
| `mem_reserve 0x340000000`, `mem_alias 0x140000000`, `mem_stack 0x10000000` | `game.tsv` | 256 MB in front, a 5 GB alias region, 256 MB of stacks and a 7.5 GB heap region, sized from `nninitStartup`'s heap formula and this SDK's 1 GB node area ([Memory](#memory)); not measured |
| `render_cond on` | `game.tsv` | taken over from Donkey Kong Bananza (agl's render buffer binding wraps its clears in render conditions); not checked on this game |
| `shader_groups /Shader/=shader /Effect/=effect /UI/=layout /Layout2Shader/=layout /Lib/=lib` | `game.tsv` | every folder that holds shaders ([Files](#files)); `Layout2Shader/` (the layout shader pack) is not under `UI/`, and `Lib/` holds agl's, gsys's and the terrain's BAHS packs |
| no `window_fn` | | the game's window size constant is not looked for yet (open) |

## Builds

| Version | Display | Modules (build IDs) | Status |
|---|---|---|---|
| `v131072` | not known (no control data) | main `e0f3ccfaf50065ae4ea01eb5aeb13b1a`, subsdk0 `b0110087a7b339ef74588d3bd0b4dd5d` (movie), sdk `1643a838814f9c10c696f930ed681e7f`, SDK 22.2.8 | **registered**, the packed build: the update tree has its own ExeFS and a nearly complete RomFS (186,583 files, 26.2 GB), packed alone (`pack_android.py --version 131072`). No build-keyed cuts, gates or host fixes |
| `v0` | not known | main `33fa6c2c23848282a77e8b88a0a93f48`, subsdk0 `ecf61f74a892d5b55be2ad09888179ed`, sdk `5fd6d40b36fb19c82ef24a03dbcb474c`, SDK 22.2.5 | registered so that its modules are recognised; never packed. The tables are the update's |

## Files

- The dump folder: `base/` and `updates/131072/`, each with `exefs/` (main, main.npdm, rtld, sdk, subsdk0) and `romfs/`. The phone gets the update alone as a one-version folder (`update-131072/exefs`, `update-131072/romfs`): the pack's `romfs` row names `updates/131072/romfs`, which that folder lacks, and the launcher takes the folder's own `romfs/` and `exefs/` (`Pack.romfs`, `PackCheck.module`; checked by `LauncherJvmTest` `layouts`). Select the `update-131072` folder itself as the game folder; its pack is then looked for at `<parent>/0400486024DEC000-work/android-pack`.
- The NSOs are ZBIC (system 22.0.0 on): `dm/tools/nso.py` needs the `zbic` package ([android/README.md](../../../android/README.md#the-environment)). The tools on this machine take it from a private folder through `PYTHONPATH` (the toolchain environment does not have it).
- RomFS files are zstd with dictionary 1 from `Pack/ZsDic.pack.zs`, as Tears of the Kingdom's.
- `romfs_marker`: `System/Resource/ResourceSizeTable.Product.111.Nin_Ounce_NVN2.rsizetable.zs` (the update's; the base has `Product.100`).
- Movies: 26 `Event/Movie/*.webm`, each one AV1 1920x1080 yuv420p video stream ([Movies](#movies)).

## Movies

- **What they are** (`ffprobe` and `trace_headers` over all 26): AV1 Main 8-bit 4:2:0, 1920x1080 at 60 fps, BT.709 matrix and primaries, sRGB transfer, limited range, no film grain, no audio track; encoded with SVT-AV1 and remuxed by QuickTime into Matroska. 1,394 s and 83,630 pictures in all, 1.64 GB, 2.8 to 21.5 Mbit/s. Each packet is a temporal unit without a temporal delimiter; every key frame packet repeats the sequence header (a key frame about every 5 s). From 9 s (the 8 `Dm_MelodyGet_*` jingles) to 311 s (`Dm_StaffCredit_0000`); `Dm_Title_*` are the title screen's background loops (99 s, then 30 s loops), on screen for as long as the player idles.
- **Who plays them:** the pack's movie module is `subsdk0` (NintendoSDK movie 22.2.8). The game's player (main +0x3b5f08c…) asks `movie::VideoDecoder` for NV12 pictures in plain buffers (output format 3, pixel storage type 1, w*h*3/2 bytes a frame, one image pool per `VideoFrame`); the movie library's wrapper (subsdk0 +0xf6d0…) picks the codec by the decoder kind at +0x400 (3 = AV1) and calls the 14 `nn::codec` AV1 exports of the sdk module, then `ReadDecoderOutputImageNv12`. It aborts on any error of the AV1 Initialize (subsdk0 +0xf900 / +0xf9c0), so without an AV1 decoder the first movie would end the game.
- **Status: implemented, unverified on a device (2026-11-05).** The Android host decodes them with dav1d ([AV1 movies on SDK 22.2](../../../docs/reference/movies.md#av1-movies-on-sdk-222-implemented-unverified-on-a-device)): all 14 entry points of this sdk build are taken over, pictures are bit-exact with ffmpeg's dav1d in the Linux test (and on 90 packets each of `Dm_MelodyGet_Sun_05`, `Dm_Title_0005` and `Dm_StaffCredit_0000`, decoded by the wrapper by hand). `DM_AV1_THREADS` sets dav1d's threads (default 2). Decode cost on one Ryzen 9 7950X core (ffmpeg's libdav1d, x86 assembly): 3.1 ms a picture (`Dm_StaffCredit_0000`), 4.4 ms (`Dm_Title_0075`), 6.4 ms (`Dm_Title_0065`, the hardest); on the phone not measured (estimated 8 to 22 ms a picture on one core).
- **What the first boot should log:** `[codec] image pool 1920x1080 format 3, 1 picture(s)`, then `[codec] AV1 decoder 1: 1920x1080, input format N, output format 3, by transfer memory` (or `by remote memory`: which one the game takes is not known from the disassembly) `(dav1d, 2 thread(s) on the host)`, `[codec] AV1 decoder 1: first picture 1920x1080 into buffer 0`, and at the end of a movie `end of stream after N pictures` with N = 60 a second (570 for a 9.5 s jingle). No `[RED] nn::codec` line. A title loop shows a Flush or Stop and a new first picture, or a Finalize and an Initialize in the same memory.

## Shaders

Current (2026-11-05, Linux, `shader_split.py` with `J=12` on the update's `romfs` and `main`, about a minute): **42,829 shaders** (compute 4,101, mesh 500, pixel 22,356, task 20, vertex 15,852; sources: effects 22,403, `Shader` 14,712, `Lib` 5,569, layouts 145, none in `main`), 4,621 compute answer rows, 26,894 variation pairs. `runtime_shaders.py --map` finds the same 42,829 in 1,441 files, all in the map.

- The first split had only 9 mesh and 3 task programs: the task and mesh stages of NVN2 BNSH programs are in slots 7 and 8, which the reader did not take ([NVN2 task and mesh stages in a BNSH program's slots 7 and 8](../../../docs/reference/shaders.md#nvn2-task-and-mesh-stages-in-a-bnsh-programs-slots-7-and-8); resolved). All 520 task / mesh programs now have their pairs.
- Open: 5 mesh programs have outputs besides position and the generic vectors in their SPH output map, whose planes are not known (`[RED] ms program …` at the split): they have no mesh layout in `shader_facts.tsv`.
- Open: 3 NVN2 control sections no reader pairs, all compute in agl's VR packs (`agl_ndsr_vr.pck`, `agl_ndsr_vr_light.pck`); 1 program the split files as pixel whose control section says vertex and 2 the other way (`runtime_shaders.py`). Whether the game binds any of them is not known.

## Rewrite (v131072)

Current (2026-11-05): `scripts/pack_android.py` on the Linux machine (`patch_nso.py --target android`, under a minute for the three modules), no `[RED]` line, no cut in any module:

| Module | Rewritten sites | Gates | Observations |
|---|---|---|---|
| main | plain 48,889, conditional 861, pc-relative 541, `tpidr_el0` reads 1,264, `blr` 5, `br` 94, spin 66 | 66 | 0 |
| sdk | plain 25,136, conditional 252, pc-relative 47, `tpidrro_el0` reads 441, `tpidr_el0` 2 reads / 3 writes, `ctr_el0` 2, `cntpct_el0` 45, `svc` 57, spin 7 | 114 | 5 |
| subsdk0 (movie) | plain 3,346, conditional 21, pc-relative 2, spin 2 | 2 | 0 |

The sdk's 5 observation gates are the general ones (VammManager::AllocateAddressRegion entry and result, FreeAddressRegion, StandardAllocator::Initialize's two forms). The SDK-wide function-matched cuts of `dm/tools/sdk_patches.py` find no matching function in this `sdk` and are skipped, and none is needed ([Memory](#memory)).

## Android shader library

Current (2026-11-05, Linux x86_64): `scripts/pack_android.py -J 16` built it from the split above (`vk_android_build.py --target vulkan`, about 40 minutes): library version `v-06c4db29ef11` (chain `812184d12231`, profile inputs `c6746b0b98cc`), `dmsh_vk.lib` 1,235.9 MB (6,292.8 MB of SPIR-V), SHA-256 `2d5eee49bae0545f2d3e6ce23fbe70e65dc1ede767cb2fcfe2a327a10057c3bb`; the pack is 1,280 MiB.

- **42,724 of 42,829 translate**: compute 4,084 / 4,101, mesh 487 / 500, pixel 22,350 / 22,356, task 19 / 20, vertex 15,784 / 15,852.
- 31 fail in the translator (open): Ampere opcode `0x243` in 10 compute and 8 mesh programs, `0x3a9` in 6 compute, `0x481` in 1 compute; a stage memory access of mode `0xb800` in 1 task program; and the 5 mesh programs without a layout ([Shaders](#shaders)).
- 74 are refused by `spirv-val` (open): 68 vertex and 6 pixel shaders, all of `Effect/`, "Vector access is out of bounds, vector size is 2, but access index is 2": a translator bug on a 2-component value, not yet looked at.
- 52,467 modules (base 42,724, `uint` 9,258, `uint_mixed` 485), every one valid for `spirv-val --target-env vulkan1.3`, again with `--check` (74 s).
- Flags: `alpha_test` 22,243, `gl_depth` 16,271, `int_out` 8,994, `subgroup` 3,485, `hmma` 52, `mesh_prim` 6. Capabilities: `DrawParameters` 15,784, `Int64` / `PhysicalStorageBufferAddresses` 2,207 (`gmem_map`), `MeshShadingEXT` 509, `StorageImageWriteWithoutFormat` 307, `StorageImageReadWithoutFormat` 56; no 64-bit float. Bindings: no undecoded slot, no unknown kind. These are the rows of [`readiness.tsv`](readiness.tsv).

## Memory

Read from the code, none of it measured:

- **The heap.** `nninitStartup` (`main+0x260bb10` in v131072, `+0x25bbbf0` in the base) sets the heap to `align_down(total − used, 2 MB) − 0xaf200000` (`0xb0a00000` in the base) from `QueryMemoryInfo`, hands the whole heap to `AllocateMemoryBlock`, and takes 36 MB of it for an allocator of its own. With the 9 GB pool and the host's 64 MB of used memory the request is 0x18ce00000 (6.20 GB). The heap region is 7.5 GB, so the request fits with 1.3 GB to spare; the rewritten modules (about 105 MB) leave `SetHeapSize`'s check against the pool far from its limit, so no `mem_used`.
- **The alias region.** The 2.74 GB `nninitStartup` keeps out of the heap is the game's memory beside it; with virtual address memory on, regions it maps in the alias region are its likely user, as on Mario Kart World (not traced). The SDK's AddressRegionManager constructor (`sdk+0x16ba30` in both builds) keeps the 1 GB node area (`mov w8, #0x3fe00000` at `+0x16ba48`, the occupied length at `+0x16bad4`) at the start of the alias region. 5 GB holds the node area, the 2.74 GB and 1.26 GB for fragmentation.
- **No SDK cut.** VammManager::AllocateAddressRegion (`sdk+0x16bb80`) aligns a region to 64 KB under 2 MB and to 2 MB above, with no 1 GB class, so the "2 MB alignment of large regions" cut has nothing to do; the node area cut ("1 GB → 62 MB") would only matter if 5 GB proved short, which is not shown. The shared rules' fingerprints do not match this implementation, as expected.
- **The pool.** 9 GB is not read from the game: the NPDM's system resource is address-space bookkeeping, not a pool size. Whether the game's own heaps fit in 6.2 GB and its regions in the alias region is open until a run (`[mem]` lines, `DM_REGIONS`, `SetHeapSize OOM`).

## Open

- **First boot (Android):** heap request and `SetHeapSize` result, the alias region's high-water mark, the first frame. Nothing has run yet.
- **AV1 movies** ([Movies](#movies)): decoded with dav1d, not run yet. To see on the first boot: which Initialize overload the game takes, the decode time and the CPU threads' load at 1080p60 on the phone (`[longframe]`, per-thread CPU), the heat over the title loop, whether the title loop plays and loops, and whether the player keeps 60 fps. Then the hardware decoder (MediaCodec `video/av01`, if the phone has `c2.qti.av1.decoder`) for heat. The movie library's other machinery (its event manager thread, sync point events, `movie::VideoFrame` sync handles) has only run with Bananza's VP9 player.
- **Mesh and task programs:** 500 mesh and 20 task programs; on a phone without `VK_EXT_mesh_shader` the replay runs them as compute (`DM_VK_MESH_CS`), checked only on Mario Kart World.
- Audio, the save path and input are untested.
- The window size constant (`window_fn`) and the render-scale stub are not looked for.

## Found on this game

- [AV1 movies on SDK 22.2](../../../docs/reference/movies.md#av1-movies-on-sdk-222-implemented-unverified-on-a-device): the host's AV1 decoder (dav1d) was made for its 26 movies.
- [NVN2 task and mesh stages in a BNSH program's slots 7 and 8](../../../docs/reference/shaders.md#nvn2-task-and-mesh-stages-in-a-bnsh-programs-slots-7-and-8): 491 mesh and 17 task programs the split did not take; `runtime_shaders.py` found nothing in a RomFS that uses zstd dictionaries.
