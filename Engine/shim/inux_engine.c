/*
 * iNux engine glue around Blink. Compiled WITHOUT inux_shim.h so that the
 * real exit/abort/kill stay reachable here.
 */
#if !defined(__APPLE__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE /* posix_openpt, ptsname */
#endif

#include "inux_engine.h"

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

/* Provided by Blink's blink.c, compiled with -Dmain=inux_blink_main. */
extern int inux_blink_main(int argc, char *argv[]);

/* Blink's blink.c is compiled with -Denviron=inux_guest_environ, so the
   guest gets this environment instead of the host process's one. */
char **inux_guest_environ;

static atomic_int g_started;
static atomic_int g_running;
static atomic_int g_reported;
static atomic_int g_status = -1;
static inux_exit_callback g_callback;
static void *g_context;

struct start_args {
  int argc;
  char **argv;
};

static void report(int status) {
  int expected = 0;
  if (!atomic_compare_exchange_strong(&g_reported, &expected, 1)) return;
  atomic_store(&g_status, status);
  atomic_store(&g_running, 0);
  if (g_callback) g_callback(status, g_context);
}

__attribute__((__noreturn__)) void inux_exit(int status) {
  report(status & 0xff);
  pthread_exit(NULL);
}

__attribute__((__noreturn__)) void inux_abort(void) {
  report(128 + SIGABRT);
  pthread_exit(NULL);
}

/* Blink's TerminateSignal() ends the process with kill(getpid(), sig)
   when the guest dies from a signal. blink.c is compiled with
   -Dkill=inux_kill so that only that call lands here. */
int inux_kill(pid_t pid, int sig) {
  if (pid == getpid() || pid == 0) {
    if (sig == 0) return 0;
    report(128 + sig);
    pthread_exit(NULL);
  }
  return kill(pid, sig);
}

static void *engine_thread(void *arg) {
  struct start_args *a = (struct start_args *)arg;
  int rc = inux_blink_main(a->argc, a->argv);
  /* Blink's main does not normally return, but handle it anyway. */
  report(rc & 0xff);
  return NULL;
}

static char **copy_strings(int n, const char *const *src) {
  int i;
  char **out = (char **)calloc((size_t)n + 1, sizeof(char *));
  if (!out) return NULL;
  for (i = 0; i < n; ++i) {
    if (!(out[i] = strdup(src[i]))) return NULL;
  }
  return out;
}

int inux_engine_start(const char *root, int argc, const char *const *argv,
                      const char *const *envp, inux_exit_callback callback,
                      void *context) {
  int expected = 0, i, n, envc, rc;
  pthread_t th;
  pthread_attr_t attr;
  struct start_args *a;
  const char **full;

  if (!root || argc < 1 || !argv || !argv[0]) return EINVAL;
  if (!atomic_compare_exchange_strong(&g_started, &expected, 1)) return EBUSY;

  g_callback = callback;
  g_context = context;

  /* blink -C <root> <argv...> */
  n = argc + 3;
  if (!(full = (const char **)calloc((size_t)n + 1, sizeof(char *)))) return ENOMEM;
  full[0] = "blink";
  full[1] = "-C";
  full[2] = root;
  for (i = 0; i < argc; ++i) full[3 + i] = argv[i];

  if (!(a = (struct start_args *)calloc(1, sizeof(*a)))) return ENOMEM;
  a->argc = n;
  if (!(a->argv = copy_strings(n, full))) return ENOMEM;
  free(full);

  for (envc = 0; envp && envp[envc]; ++envc) {
  }
  if (!(inux_guest_environ = copy_strings(envc, envp))) return ENOMEM;

  atomic_store(&g_running, 1);
  pthread_attr_init(&attr);
  /* Blink keeps large buffers on the stack. */
  pthread_attr_setstacksize(&attr, 64 * 1024 * 1024);
  pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
  rc = pthread_create(&th, &attr, engine_thread, a);
  pthread_attr_destroy(&attr);
  if (rc) {
    atomic_store(&g_running, 0);
    return rc;
  }
  return 0;
}

void inux_resize_terminal(int fd, int cols, int rows) {
  struct winsize ws;
  if (fd < 0 || cols <= 0 || rows <= 0) return;
  memset(&ws, 0, sizeof(ws));
  ws.ws_col = (unsigned short)cols;
  ws.ws_row = (unsigned short)rows;
  ioctl(fd, TIOCSWINSZ, &ws);
}

static int attach_fd(int fd, int target) {
  if (fd == target) return 0;
  return dup2(fd, target) == -1 ? -1 : 0;
}

int inux_attach_terminal(int cols, int rows, int *read_fd, int *write_fd) {
  int master, slave, in[2], out[2];
  char *name;

  master = posix_openpt(O_RDWR | O_NOCTTY);
  if (master != -1 && !grantpt(master) && !unlockpt(master) &&
      (name = ptsname(master)) &&
      (slave = open(name, O_RDWR | O_NOCTTY)) != -1) {
    struct termios t;
    if (!tcgetattr(slave, &t)) {
      t.c_iflag |= ICRNL | IUTF8;
      t.c_oflag |= OPOST | ONLCR;
      t.c_lflag |= ICANON | ECHO | ECHOE | ISIG | IEXTEN;
      tcsetattr(slave, TCSANOW, &t);
    }
    inux_resize_terminal(master, cols, rows);
    if (attach_fd(slave, 0) || attach_fd(slave, 1) || attach_fd(slave, 2)) {
      close(slave);
      close(master);
      return -1;
    }
    if (slave > 2) close(slave);
    fcntl(master, F_SETFD, FD_CLOEXEC);
    *read_fd = master;
    *write_fd = master;
    return 1;
  }
  if (master != -1) close(master);

  if (pipe(in) || pipe(out)) return -1;
  if (attach_fd(in[0], 0) || attach_fd(out[1], 1) || attach_fd(out[1], 2)) return -1;
  close(in[0]);
  close(out[1]);
  fcntl(in[1], F_SETFD, FD_CLOEXEC);
  fcntl(out[0], F_SETFD, FD_CLOEXEC);
  *read_fd = out[0];
  *write_fd = in[1];
  return 0;
}

int inux_engine_running(void) { return atomic_load(&g_running); }

int inux_engine_exit_status(void) { return atomic_load(&g_status); }
