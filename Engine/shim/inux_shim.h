/*
 * Force-included (clang -include) into every Blink source file.
 *
 * Blink is written as a stand-alone program: when the guest exits, or
 * Blink hits a fatal error, it calls exit(), _Exit() or abort(), which
 * would take the whole iOS app down with it. Inside iNux, Blink runs on
 * its own thread, so these calls are redirected to functions that report
 * the status to the app and end only that thread.
 */
#ifndef INUX_SHIM_H_
#define INUX_SHIM_H_

#ifdef __cplusplus
extern "C" {
#endif

__attribute__((__noreturn__)) void inux_exit(int status);
__attribute__((__noreturn__)) void inux_abort(void);

#ifdef __cplusplus
}
#endif

#define exit  inux_exit
#define _Exit inux_exit
#define _exit inux_exit
#define abort inux_abort

#endif /* INUX_SHIM_H_ */
