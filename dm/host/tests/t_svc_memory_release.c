// Strong graphics hook for t_svc_memory: the production SVC owns the ordering.
#include <stdint.h>
int test_svc_memory_release(uintptr_t lo, uintptr_t hi);
int dm_gfx_guest_release(uintptr_t lo, uintptr_t hi) { return test_svc_memory_release(lo, hi); }
