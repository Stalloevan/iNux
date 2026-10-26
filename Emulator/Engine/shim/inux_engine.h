/*
 * Public interface of the iNux execution engine (Blink x86-64 emulator).
 * Used from Swift through a bridging header and from Engine/test/inux-run.c.
 */
#ifndef INUX_ENGINE_H_
#define INUX_ENGINE_H_

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Called once when the guest program ends. status is the exit code, or
   128 + signal number when the guest was killed by a signal. Runs on an
   engine thread, not the main thread. */
typedef void (*inux_exit_callback)(int status, void *context);

/* Starts Blink on a new thread, as if running
       blink -C <root> <program> <args...>
   with the given guest environment ("KEY=VALUE" strings).
   argv[0] is the guest program path, as seen inside <root>.
   The host working directory should be inside <root> beforehand; it
   becomes the guest's working directory.
   Returns 0 on success, or an errno value. Only one guest can run per
   process: Blink keeps global state, so a second call returns EBUSY. */
int inux_engine_start(const char *root, int argc, const char *const *argv,
                      const char *const *envp, inux_exit_callback callback,
                      void *context);

/* Points this process's stdin, stdout and stderr at a new terminal, which
   the guest inherits. Tries a pseudo-terminal first (the guest then sees a
   real tty), then falls back to pipes.
   *read_fd receives guest output; bytes written to *write_fd reach the
   guest's stdin (the same descriptor when a pty is used).
   Returns 1 for a pty, 0 for pipes, -1 on failure. */
int inux_attach_terminal(int cols, int rows, int *read_fd, int *write_fd);

/* Updates the pty window size (no-op for pipes). */
void inux_resize_terminal(int fd, int cols, int rows);

/* 1 while a guest is running. */
int inux_engine_running(void);

/* Exit status of the finished guest, or -1 if it has not finished. */
int inux_engine_exit_status(void);

#ifdef __cplusplus
}
#endif

#endif /* INUX_ENGINE_H_ */
