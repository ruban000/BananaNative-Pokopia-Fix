# dav1d 1.5.4 (BSD-2-Clause) as a static library for the host's AV1 movies: the port of upstream's meson recipe (src/meson.build), 8-bit pictures only.
# Sources come from dm/third_party/dav1d/{src,include} (made by vendor.sh) or from an unpacked upstream tarball: -DDAV1D_SRC=<dir>/dav1d-1.5.4.
# Target: dav1d_nx (include directories and the NX_DAV1D definition come with it). aarch64 builds the NEON assembly (config/config.h), other CPUs the C code.
cmake_minimum_required(VERSION 3.10)
if(NOT PROJECT_NAME)
  project(dav1d_nx C ASM)
endif()
enable_language(ASM)
if(NOT DAV1D_SRC)
  set(DAV1D_SRC ${CMAKE_CURRENT_LIST_DIR})
endif()
if(NOT EXISTS ${DAV1D_SRC}/src/lib.c)
  message(FATAL_ERROR "dav1d sources not found in ${DAV1D_SRC}: run dm/third_party/dav1d/vendor.sh, or pass -DDAV1D_SRC=<unpacked dav1d-1.5.4>")
endif()
set(DAV1D_CFG ${CMAKE_CURRENT_LIST_DIR}/config)
set(S ${DAV1D_SRC}/src)

set(DAV1D_CORE cdf.c cpu.c ctx.c data.c decode.c dequant_tables.c getbits.c intra_edge.c itx_1d.c lf_mask.c lib.c log.c mem.c msac.c obu.c pal.c picture.c qm.c ref.c
    refmvs.c scan.c tables.c thread_task.c warpmv.c wedge.c)
set(DAV1D_TMPL cdef_apply_tmpl.c cdef_tmpl.c fg_apply_tmpl.c filmgrain_tmpl.c ipred_prepare_tmpl.c ipred_tmpl.c itx_tmpl.c lf_apply_tmpl.c loopfilter_tmpl.c
    looprestoration_tmpl.c lr_apply_tmpl.c mc_tmpl.c recon_tmpl.c)   # compiled once a bit depth; only 8 here

set(SRCS)
foreach(f ${DAV1D_CORE})
  list(APPEND SRCS ${S}/${f})
endforeach()
foreach(f ${DAV1D_TMPL})
  list(APPEND SRCS ${S}/${f})
  set_source_files_properties(${S}/${f} PROPERTIES COMPILE_DEFINITIONS BITDEPTH=8)
endforeach()
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64")
  list(APPEND SRCS ${S}/arm/cpu.c ${S}/arm/64/itx.S ${S}/arm/64/looprestoration_common.S ${S}/arm/64/msac.S ${S}/arm/64/refmvs.S
       ${S}/arm/64/cdef.S ${S}/arm/64/filmgrain.S ${S}/arm/64/ipred.S ${S}/arm/64/loopfilter.S ${S}/arm/64/looprestoration.S ${S}/arm/64/mc.S ${S}/arm/64/mc_dotprod.S)
endif()

add_library(dav1d_nx STATIC ${SRCS})
set_target_properties(dav1d_nx PROPERTIES C_STANDARD 11 POSITION_INDEPENDENT_CODE ON)
# config/ first: its config.h and vcs_version.h replace the ones meson generates
target_include_directories(dav1d_nx PRIVATE ${DAV1D_CFG} ${DAV1D_SRC} ${DAV1D_SRC}/include ${DAV1D_SRC}/include/dav1d ${S})
target_include_directories(dav1d_nx PUBLIC ${DAV1D_SRC}/include)
target_compile_definitions(dav1d_nx PRIVATE _GNU_SOURCE _FILE_OFFSET_BITS=64)
target_compile_definitions(dav1d_nx PUBLIC NX_DAV1D=1)
target_compile_options(dav1d_nx PRIVATE -O2 -w -fvisibility=hidden -fno-align-functions)
find_package(Threads REQUIRED)
target_link_libraries(dav1d_nx PUBLIC Threads::Threads)
