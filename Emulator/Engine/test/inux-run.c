/*
 * inux-run: runs a guest program through the iNux engine exactly the way
 * the iOS app does (engine thread, private root, explicit environment),
 * but on a Linux or macOS host. Used by CI to smoke-test ported apps.
 *
 *   inux-run [-t SECONDS] [-e KEY=VALUE]... ROOT PROGRAM [ARGS...]
 *
 * Exits with the guest's exit status, or 124 on timeout.
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "inux_engine.h"

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_cond = PTHREAD_COND_INITIALIZER;
static int g_done;
static int g_status;

static void on_exit_cb(int status, void *ctx) {
  (void)ctx;
  pthread_mutex_lock(&g_lock);
  g_done = 1;
  g_status = status;
  pthread_cond_broadcast(&g_cond);
  pthread_mutex_unlock(&g_lock);
}

int main(int argc, char *argv[]) {
  int i = 1, rc, timeout = 120, envc = 0;
  const char *envp[256];
  struct timespec deadline;
  char cwd[4096];

  while (i < argc && argv[i][0] == '-') {
    if (!strcmp(argv[i], "-t") && i + 1 < argc) {
      timeout = atoi(argv[i + 1]);
      i += 2;
    } else if (!strcmp(argv[i], "-e") && i + 1 < argc && envc < 255) {
      envp[envc++] = argv[i + 1];
      i += 2;
    } else {
      break;
    }
  }
  envp[envc] = NULL;
  if (argc - i < 2) {
    fprintf(stderr, "usage: %s [-t SECONDS] [-e K=V]... ROOT PROGRAM [ARGS...]\n", argv[0]);
    return 2;
  }
  if (!realpath(argv[i], cwd)) {
    perror(argv[i]);
    return 2;
  }
  if (chdir(cwd)) {
    perror("chdir");
    return 2;
  }

  rc = inux_engine_start(cwd, argc - i - 1, (const char *const *)argv + i + 1,
                         envp, on_exit_cb, NULL);
  if (rc) {
    fprintf(stderr, "inux_engine_start failed: %s\n", strerror(rc));
    return 2;
  }

  clock_gettime(CLOCK_REALTIME, &deadline);
  deadline.tv_sec += timeout;
  pthread_mutex_lock(&g_lock);
  while (!g_done) {
    if (pthread_cond_timedwait(&g_cond, &g_lock, &deadline)) break;
  }
  rc = g_done ? g_status : 124;
  pthread_mutex_unlock(&g_lock);
  fflush(stdout);
  fprintf(stderr, "[inux-run] guest %s with status %d\n",
          rc == 124 && !g_done ? "timed out" : "exited", rc);
  /* Exiting the host process here also proves the guest's exit did not. */
  return rc;
}
