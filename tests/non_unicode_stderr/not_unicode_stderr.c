#include <stdio.h>
#include <stdint.h>

int main(void) {
    uint32_t a = 0xFA;
    fputc(a, stderr);
    fputc(a, stderr);
    return 0;
}