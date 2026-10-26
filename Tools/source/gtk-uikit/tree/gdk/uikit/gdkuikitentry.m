/* GDK - UIKit (iOS) backend: program entry point.
 *
 * A GTK program's main() runs GTK's own loop and never returns to UIKit,
 * so it cannot be the process entry point on iOS. Apps are linked with
 *
 *     -Wl,-e,_gdk_uikit_entry
 *
 * which makes gdk_uikit_entry() the entry point instead. It prepares the
 * environment (data, settings and font locations inside the app bundle and
 * its container), starts UIKit, creates the window, and then runs the
 * program's unmodified main() on its own thread (see the threading notes
 * in gdkuikit-private.h). When main() returns, the process exits with its
 * status.
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#import <UIKit/UIKit.h>

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#include <glib.h>

extern int main (int argc, char **argv);
UIViewController *gdk_uikit_ui_create_root_controller (void);
int gdk_uikit_entry (int argc, char **argv);

static int app_argc;
static char **app_argv;

static void
set_default (const char *name, const char *value)
{
  setenv (name, value, 0);
}

static void
make_dirs (NSString *path)
{
  [[NSFileManager defaultManager] createDirectoryAtPath:path
                            withIntermediateDirectories:YES
                                             attributes:nil
                                                  error:NULL];
}

static NSString *
xml_escape (NSString *s)
{
  s = [s stringByReplacingOccurrencesOfString:@"&" withString:@"&amp;"];
  s = [s stringByReplacingOccurrencesOfString:@"<" withString:@"&lt;"];
  return [s stringByReplacingOccurrencesOfString:@">" withString:@"&gt;"];
}

/* Fontconfig reads its configuration from a file; the bundle path is only
 * known at run time, so the file is written on every launch. */
static void
write_fontconfig (NSString *bundle, NSString *caches)
{
  NSString *dir = [caches stringByAppendingPathComponent:@"fontconfig"];
  NSString *conf = [dir stringByAppendingPathComponent:@"fonts.conf"];
  NSString *fonts = [bundle stringByAppendingPathComponent:@"share/fonts"];
  NSString *xml;

  make_dirs (dir);
  xml = [NSString stringWithFormat:
    @"<?xml version=\"1.0\"?>\n"
     "<!DOCTYPE fontconfig SYSTEM \"urn:fontconfig:fonts.dtd\">\n"
     "<fontconfig>\n"
     "  <dir>%@</dir>\n"
     "  <dir>/System/Library/Fonts</dir>\n"
     "  <cachedir>%@</cachedir>\n"
     "  <alias><family>sans-serif</family><prefer>"
     "<family>Adwaita Sans</family><family>Cantarell</family>"
     "<family>Helvetica Neue</family><family>PingFang SC</family>"
     "<family>Apple Color Emoji</family></prefer></alias>\n"
     "  <alias><family>serif</family><prefer>"
     "<family>Times New Roman</family><family>Georgia</family></prefer></alias>\n"
     "  <alias><family>monospace</family><prefer>"
     "<family>Adwaita Mono</family><family>Menlo</family><family>Courier New</family></prefer></alias>\n"
     "  <alias><family>Sans</family><prefer><family>sans-serif</family></prefer></alias>\n"
     "  <alias><family>Monospace</family><prefer><family>monospace</family></prefer></alias>\n"
     "  <match target=\"font\"><edit name=\"rgba\" mode=\"assign\"><const>none</const></edit>"
     "<edit name=\"hintstyle\" mode=\"assign\"><const>hintslight</const></edit>"
     "<edit name=\"antialias\" mode=\"assign\"><bool>true</bool></edit></match>\n"
     "</fontconfig>\n",
    xml_escape (fonts), xml_escape (dir)];
  [xml writeToFile:conf atomically:YES encoding:NSUTF8StringEncoding error:NULL];
  setenv ("FONTCONFIG_FILE", conf.fileSystemRepresentation, 1);
}

static void
prepare_environment (void)
{
  NSString *bundle = [[NSBundle mainBundle] bundlePath];
  NSString *home = NSHomeDirectory ();
  NSString *library = [home stringByAppendingPathComponent:@"Library"];
  NSString *support = [library stringByAppendingPathComponent:@"Application Support"];
  NSString *prefs = [library stringByAppendingPathComponent:@"Preferences"];
  NSString *caches = [library stringByAppendingPathComponent:@"Caches"];
  NSString *state = [support stringByAppendingPathComponent:@"state"];
  NSString *share = [bundle stringByAppendingPathComponent:@"share"];
  NSString *schemas = [share stringByAppendingPathComponent:@"glib-2.0/schemas"];

  for (NSString *d in @[ support, prefs, caches, state ])
    make_dirs (d);

  /* The XDG locations GLib and GTK use, mapped onto the app container
   * (writable) and the bundle (read-only data). */
  setenv ("HOME", home.fileSystemRepresentation, 1);
  setenv ("XDG_DATA_HOME", support.fileSystemRepresentation, 1);
  setenv ("XDG_CONFIG_HOME", prefs.fileSystemRepresentation, 1);
  setenv ("XDG_CACHE_HOME", caches.fileSystemRepresentation, 1);
  setenv ("XDG_STATE_HOME", state.fileSystemRepresentation, 1);
  setenv ("XDG_RUNTIME_DIR", NSTemporaryDirectory ().fileSystemRepresentation, 1);
  setenv ("XDG_DATA_DIRS", share.fileSystemRepresentation, 1);
  setenv ("XDG_CONFIG_DIRS", [share stringByAppendingPathComponent:@"xdg"].fileSystemRepresentation, 1);

  if ([[NSFileManager defaultManager] fileExistsAtPath:schemas])
    setenv ("GSETTINGS_SCHEMA_DIR", schemas.fileSystemRepresentation, 1);
  /* No dconf on iOS: settings are kept in a key file in the container. */
  set_default ("GSETTINGS_BACKEND", "keyfile");

  write_fontconfig (bundle, caches);

  set_default ("GDK_BACKEND", "uikit");
  set_default ("GSK_RENDERER", "cairo");
  set_default ("GTK_IM_MODULE", "uikit");
  set_default ("GTK_A11Y", "none");
  set_default ("GIO_USE_VFS", "local");
  set_default ("GIO_USE_VOLUME_MONITOR", "unix");
  set_default ("GTK_USE_PORTAL", "0");
  set_default ("ADW_DISABLE_PORTAL", "1");
  set_default ("LANG", "en_US.UTF-8");

  /* Relative data paths (and Linux-style "./data" lookups) resolve inside
   * the bundle. */
  chdir (bundle.fileSystemRepresentation);
}

static void *
gtk_thread (void *data)
{
  int status;

  pthread_setname_np ("GTK");
  status = main (app_argc, app_argv);
  fflush (stdout);
  fflush (stderr);
  exit (status);
  return NULL;
}

static void
start_gtk_thread (void)
{
  pthread_attr_t attr;
  pthread_t thread;

  pthread_attr_init (&attr);
  /* GTK and the apps on top of it recurse deeply; give the thread the
   * stack size a desktop main thread has. */
  pthread_attr_setstacksize (&attr, 16 * 1024 * 1024);
  pthread_attr_setdetachstate (&attr, PTHREAD_CREATE_DETACHED);
  pthread_create (&thread, &attr, gtk_thread, NULL);
  pthread_attr_destroy (&attr);
}

@interface GdkUIKitAppDelegate : UIResponder <UIApplicationDelegate>
@property (strong, nonatomic) UIWindow *window;
@end

@implementation GdkUIKitAppDelegate

- (BOOL)application:(UIApplication *)application
    didFinishLaunchingWithOptions:(NSDictionary *)options
{
  self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
  self.window.rootViewController = gdk_uikit_ui_create_root_controller ();
  [self.window makeKeyAndVisible];
  [self.window layoutIfNeeded];

  start_gtk_thread ();
  return YES;
}

@end

int
gdk_uikit_entry (int argc, char **argv)
{
  @autoreleasepool
    {
      app_argc = argc;
      app_argv = argv;
      prepare_environment ();
      return UIApplicationMain (argc, argv, nil, NSStringFromClass ([GdkUIKitAppDelegate class]));
    }
}
