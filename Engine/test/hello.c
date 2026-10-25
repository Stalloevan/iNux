/* Test guest for the iNux engine: exercises the dynamic loader, a library
   pulled in by the porter (zlib), files, the environment and the terminal. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/utsname.h>
#include <unistd.h>
#include <zlib.h>

int main(int argc, char *argv[]) {
  struct utsname u;
  char cwd[512];
  FILE *f;
  char line[64] = {0};

  printf("Hello from iNux\n");
  if (!uname(&u)) printf("kernel: %s %s %s\n", u.sysname, u.release, u.machine);
  printf("zlib: %s\n", zlibVersion());
  printf("HOME=%s\n", getenv("HOME") ? getenv("HOME") : "(unset)");
  if (getcwd(cwd, sizeof(cwd))) printf("cwd: %s\n", cwd);

  f = fopen("/tmp/inux-test.txt", "w");
  if (f) {
    fputs("written in the guest\n", f);
    fclose(f);
    f = fopen("/tmp/inux-test.txt", "r");
    if (f && fgets(line, sizeof(line), f)) printf("tmp file: %s", line);
    if (f) fclose(f);
  } else {
    perror("/tmp/inux-test.txt");
  }

  printf("stdin is %sa terminal\n", isatty(0) ? "" : "not ");
  if (argc > 1 && !strcmp(argv[1], "--interactive")) {
    char name[128];
    printf("What is your name? ");
    fflush(stdout);
    if (fgets(name, sizeof(name), stdin)) {
      name[strcspn(name, "\n")] = 0;
      printf("Nice to meet you, %s.\n", name);
    }
  }
  return 0;
}
