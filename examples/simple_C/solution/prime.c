#include <stdio.h>
#include <stdlib.h>

int is_prime(int n);

int main(int argc, char *argv[]) {
	int n;
	if (argc > 1) {
		n = atoi(argv[1]);
	} else {
		if (scanf("%d", &n) != 1) {
			fprintf(stderr, "Error\n");
			return 1;
		}
	}
	
	FILE *f = fopen("result.txt", "w");
	if (is_prime(n)) {
		printf("%d is prime.\n", n);
		fprintf(f, "%d is prime.\n", n);
	} else {
		printf("%d is not prime.\n", n);
		fprintf(f, "%d is not prime.\n", n);
	}
	return 0;
}


int is_prime(int n) {
	int max_possible_factor = n < 0 ? -n : n;
	
	for (int possible_factor = 2; possible_factor < max_possible_factor; possible_factor++) {
		if (n % possible_factor == 0) {
			return 0;
		}
	}
	
	return 1;
}