#include <stdio.h>
#include <stdint.h>

int main(void) {
    uint32_t a = 0xFFFF;
    uint32_t b = 0xA571;

    FILE *f = fopen("test_file", "wb");
    fputc(a, f);
    fputc(b, f);
    fclose(f);

    FILE *f2 = fopen("test_file2", "wb");
    fputc(b, f);
    fputc(a, f);
    fputc(a, f);
    fputc(b, f);
    fclose(f2);

    return 0;
}