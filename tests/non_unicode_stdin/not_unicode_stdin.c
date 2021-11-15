#include <stdio.h>
#include <stdint.h>

// flips bits of first stdin byte
int main(int argc, char **argv) {
    uint32_t a = fgetc(stdin);
    a = a ^ 0xFF;
    fputc(a, stdout);
    return 0;
}