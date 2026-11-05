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
- Movies: 26 `Event/Movie/*.webm`, each one AV1 1920x1080 yuv420p video stream ([Open](#open)).

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

## Memory

Read from the code, none of it measured:

- **The heap.** `nninitStartup` (`main+0x260bb10` in v131072, `+0x25bbbf0` in the base) sets the heap to `align_down(total − used, 2 MB) − 0xaf200000` (`0xb0a00000` in the base) from `QueryMemoryInfo`, hands the whole heap to `AllocateMemoryBlock`, and takes 36 MB of it for an allocator of its own. With the 9 GB pool and the host's 64 MB of used memory the request is 0x18ce00000 (6.20 GB). The heap region is 7.5 GB, so the request fits with 1.3 GB to spare; the rewritten modules (about 105 MB) leave `SetHeapSize`'s check against the pool far from its limit, so no `mem_used`.
- **The alias region.** The 2.74 GB `nninitStartup` keeps out of the heap is the game's memory beside it; with virtual address memory on, regions it maps in the alias region are its likely user, as on Mario Kart World (not traced). The SDK's AddressRegionManager constructor (`sdk+0x16ba30` in both builds) keeps the 1 GB node area (`mov w8, #0x3fe00000` at `+0x16ba48`, the occupied length at `+0x16bad4`) at the start of the alias region. 5 GB holds the node area, the 2.74 GB and 1.26 GB for fragmentation.
- **No SDK cut.** VammManager::AllocateAddressRegion (`sdk+0x16bb80`) aligns a region to 64 KB under 2 MB and to 2 MB above, with no 1 GB class, so the "2 MB alignment of large regions" cut has nothing to do; the node area cut ("1 GB → 62 MB") would only matter if 5 GB proved short, which is not shown. The shared rules' fingerprints do not match this implementation, as expected.
- **The pool.** 9 GB is not read from the game: the NPDM's system resource is address-space bookkeeping, not a pool size. Whether the game's own heaps fit in 6.2 GB and its regions in the alias region is open until a run (`[mem]` lines, `DM_REGIONS`, `SetHeapSize OOM`).

## Open

- **First boot (Android):** heap request and `SetHeapSize` result, the alias region's high-water mark, the first frame. Nothing has run yet.
- **AV1 movies:** all 26 movies are AV1, and the host's `nn::codec` video decoders have no AV1 (`hle_codec_video.c` answers its initialization unsupported). Whether the boot plays one (a logo or title movie) and what the movie player does with the failure is not known.
- **Mesh and task programs:** 500 mesh and 20 task programs; on a phone without `VK_EXT_mesh_shader` the replay runs them as compute (`DM_VK_MESH_CS`), checked only on Mario Kart World.
- Audio, the save path and input are untested.
- The window size constant (`window_fn`) and the render-scale stub are not looked for.

## Found on this game

- [NVN2 task and mesh stages in a BNSH program's slots 7 and 8](../../../docs/reference/shaders.md#nvn2-task-and-mesh-stages-in-a-bnsh-programs-slots-7-and-8): 491 mesh and 17 task programs the split did not take; `runtime_shaders.py` found nothing in a RomFS that uses zstd dictionaries.
