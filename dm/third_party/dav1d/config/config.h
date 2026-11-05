/* dav1d 1.5.4 configuration for the nxnative host (8-bit pictures only): what upstream's meson run writes into config.h, fixed for the targets the host builds for.
 * aarch64 (Android, Apple silicon) gets the NEON assembly, every other CPU the portable C code (Linux x86-64 for the unit test). */
#ifndef DAV1D_CONFIG_H
#define DAV1D_CONFIG_H

#define CONFIG_8BPC 1
#define CONFIG_16BPC 0
#define CONFIG_LOG 1
#define TRIM_DSP_FUNCTIONS 0
#define ENDIANNESS_BIG 0

#define HAVE_CLOCK_GETTIME 1
#define HAVE_POSIX_MEMALIGN 1
#define HAVE_ALIGNED_ALLOC 1
#define HAVE_MEMALIGN 0
#define HAVE_DLSYM 1
#define HAVE_UNISTD_H 1
#define HAVE_PTHREAD_NP_H 0
#define HAVE_PTHREAD_GETAFFINITY_NP 0
#define HAVE_PTHREAD_SETAFFINITY_NP 0
#define HAVE_PTHREAD_SETNAME_NP 0
#define HAVE_PTHREAD_SET_NAME_NP 0
#define HAVE_ELF_AUX_INFO 0
#define HAVE_C11_GENERIC 1

#if defined(__aarch64__) || defined(__arm64__)
#define ARCH_AARCH64 1
#define HAVE_ASM 1
#define HAVE_AS_FUNC 0
#define HAVE_AS_ARCH_DIRECTIVE 1
#define AS_ARCH_LEVEL armv8-a
#define HAVE_AS_ARCHEXT_DOTPROD_DIRECTIVE 1
#define HAVE_AS_ARCHEXT_I8MM_DIRECTIVE 1
#define HAVE_AS_ARCHEXT_SVE_DIRECTIVE 0
#define HAVE_AS_ARCHEXT_SVE2_DIRECTIVE 0
#define HAVE_DOTPROD 1
#define HAVE_I8MM 1
#define HAVE_SVE 0
#define HAVE_SVE2 0
#if defined(__linux__)
#define HAVE_GETAUXVAL 1
#else
#define HAVE_GETAUXVAL 0
#endif
#else
#define ARCH_AARCH64 0
#define HAVE_ASM 0
#define HAVE_GETAUXVAL 0
#endif
#define ARCH_ARM 0
#define ARCH_X86 0
#define ARCH_X86_32 0
#define ARCH_X86_64 0
#define ARCH_PPC64LE 0
#define ARCH_RISCV 0
#define ARCH_RV32 0
#define ARCH_RV64 0
#define ARCH_LOONGARCH 0
#define ARCH_LOONGARCH32 0
#define ARCH_LOONGARCH64 0

#endif
