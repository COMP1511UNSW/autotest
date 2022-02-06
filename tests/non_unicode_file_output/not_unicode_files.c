#include <stdio.h>
#include <stdint.h>

int main(void) {
    uint32_t a = 0xFF;
    uint32_t b = 0xA5;
    uint32_t c = 0x71;

    FILE *f = fopen("test_file", "wb");
    fputc(a, f);
    fputc(b, f);
    fclose(f);

    FILE *f2 = fopen("test_file2", "wb");
    fputc(b, f);
    fputc(c, f);
    fputc(a, f);
    fputc(a, f);
    fputc(b, f);
    fputc(c, f);
    fclose(f2);

    return 0;
}