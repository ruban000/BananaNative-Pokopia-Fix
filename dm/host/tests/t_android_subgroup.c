#include <assert.h>
#include <stdio.h>
#include "../../gfx/vk_android/vka_subgroup.h"
int main(void) {
    assert(vka_subgroup_pin_size(64, 32, 64, 0) == 64);
    assert(vka_subgroup_pin_size(64, 64, 128, 0) == 64);
    assert(vka_subgroup_pin_size(64, 32, 64, 32) == 32);
    assert(vka_subgroup_pin_size(64, 64, 128, 32) == 0);
    assert(vka_subgroup_pin_size(64, 32, 128, 128) == 0);
    assert(vka_subgroup_pin_size(64, 32, 64, 48) == 0);
    assert(vka_subgroup_pin_size(32, 32, 64, 64) == 0);
    assert(vka_subgroup_pin_size(64, 64, 64, 64) == 64);
    assert(vka_subgroup_pin_size(64, 128, 128, 0) == 0);
    assert(vka_subgroup_pin_size(64, 32, 64, (unsigned)-1) == 0);
    puts("t_android_subgroup: compatible and device-supported subgroup sizes only");
}
