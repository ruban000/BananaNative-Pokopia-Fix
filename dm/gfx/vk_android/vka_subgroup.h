// Subgroup sizes the translated library can run unchanged. Its 64-lane modules split Maxwell's operations into 32-lane halves, so 32 is also correct.
#ifndef VKA_SUBGROUP_H
#define VKA_SUBGROUP_H
static inline int vka_subgroup_fits(unsigned built, unsigned size) {
    return built == 64 ? size == 32 || size == 64 : size == built;
}
// 0 = automatic. A requested size must also be supported by the device and by the library; never send a merely preferred size to Vulkan.
static inline unsigned vka_subgroup_pin_size(unsigned built, unsigned lo, unsigned hi, unsigned wanted) {
    if (wanted) return wanted >= lo && wanted <= hi && !(wanted & (wanted - 1)) && vka_subgroup_fits(built, wanted) ? wanted : 0;
    for (unsigned n = built; n >= 32; n /= 2) if (n >= lo && n <= hi && vka_subgroup_fits(built, n)) return n;
    return 0;
}
#endif
