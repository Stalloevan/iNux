/* GDK - UIKit (iOS) backend, private declarations.
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Threading model
 * ---------------
 * UIKit must run on the process's main thread, and an iOS app must return
 * from application:didFinishLaunchingWithOptions: promptly. GTK, on the
 * other hand, expects to own its thread and run a GMainLoop. So the backend
 * runs the program's main() on a dedicated "GTK thread" (see
 * gdkuikitentry.m) and all of GDK/GTK lives there; UIKit lives on the main
 * thread.
 *
 *   GTK thread -> main thread: dispatch_async / dispatch_sync on the main
 *     queue (gdk_uikit_ui_* functions, implemented in gdkuikitui.m).
 *   main thread -> GTK thread: g_main_context_invoke on the default
 *     context (gdk_uikit_post_* functions below).
 *
 * The main thread never waits for the GTK thread, so the two cannot
 * deadlock. Surfaces are referred to across the boundary by a numeric id;
 * the GTK side looks the id up and ignores events for surfaces that are
 * gone.
 */
#pragma once

#include "config.h"

#include "gdkdisplayprivate.h"
#include "gdksurfaceprivate.h"
#include "gdkcairocontextprivate.h"
#include "gdkdeviceprivate.h"
#include "gdkseatprivate.h"
#include "gdkkeysprivate.h"
#include "gdkmonitorprivate.h"

#include "gdkuikit.h"
#include "gdkuikitbridge.h"

G_BEGIN_DECLS

/* ------------------------------------------------------------ display -- */

typedef struct _GdkUIKitDisplayClass GdkUIKitDisplayClass;

struct _GdkUIKitDisplay
{
  GdkDisplay parent_instance;

  GdkMonitor *monitor;
  GListStore *monitors;
  GdkSeat *seat;
  GdkKeymap *keymap;

  GdkDevice *core_pointer;
  GdkDevice *core_keyboard;
  GdkDevice *touchscreen;

  GSource *event_source;

  /* id -> GdkSurface (not owned) */
  GHashTable *surfaces;
  guint next_surface_id;
  gulong serial;

  /* Usable area (safe area minus on-screen keyboard), in the root view's
   * coordinate space, in points (GDK logical pixels). */
  GdkRectangle layout;
  double scale;
  gboolean dark;

  /* Stacking: toplevels in present order, last = front. */
  GList *toplevels;
  /* Mapped autohide popups, last = topmost. */
  GList *grab_popups;

  /* Touch currently driving the logical pointer, for get_device_state. */
  double last_x, last_y;
  GdkSurface *last_surface;
  guint32 last_time;
};

struct _GdkUIKitDisplayClass
{
  GdkDisplayClass parent_class;
};

GdkDisplay *_gdk_uikit_display_open (const char *display_name);
GdkUIKitDisplay *_gdk_uikit_display_get (void);

guint  _gdk_uikit_display_add_surface    (GdkUIKitDisplay *self,
                                          GdkSurface      *surface);
void   _gdk_uikit_display_remove_surface (GdkUIKitDisplay *self,
                                          guint            id);
gulong _gdk_uikit_display_next_serial    (GdkUIKitDisplay *self);
GdkSurface *_gdk_uikit_display_key_target (GdkUIKitDisplay *self);
void   _gdk_uikit_display_update_focus   (GdkUIKitDisplay *self);

/* ------------------------------------------------------------ surface -- */

#define GDK_TYPE_UIKIT_SURFACE       (gdk_uikit_surface_get_type ())
#define GDK_UIKIT_SURFACE(o)         (G_TYPE_CHECK_INSTANCE_CAST ((o), GDK_TYPE_UIKIT_SURFACE, GdkUIKitSurface))
#define GDK_IS_UIKIT_SURFACE(o)      (G_TYPE_CHECK_INSTANCE_TYPE ((o), GDK_TYPE_UIKIT_SURFACE))

#define GDK_TYPE_UIKIT_TOPLEVEL      (gdk_uikit_toplevel_get_type ())
#define GDK_TYPE_UIKIT_POPUP         (gdk_uikit_popup_get_type ())
#define GDK_TYPE_UIKIT_DRAG_SURFACE  (gdk_uikit_drag_surface_get_type ())

typedef struct _GdkUIKitSurface      GdkUIKitSurface;
typedef struct _GdkUIKitSurfaceClass GdkUIKitSurfaceClass;

struct _GdkUIKitSurface
{
  GdkSurface parent_instance;

  guint id;
  gpointer view;          /* retained UIView, owned by the main thread side */

  /* Position of the surface in the root view, in points. */
  int root_x;
  int root_y;

  gboolean visible;
  gboolean popup_grab;
  gboolean fills_screen;  /* main windows take the whole usable area */

  guint compute_size_source_id;
  int last_computed_width;
  int last_computed_height;

  int shadow_left, shadow_right, shadow_top, shadow_bottom;
};

struct _GdkUIKitSurfaceClass
{
  GdkSurfaceClass parent_class;
};

GType gdk_uikit_surface_get_type (void);
GType gdk_uikit_toplevel_get_type (void);
GType gdk_uikit_popup_get_type (void);
GType gdk_uikit_drag_surface_get_type (void);

void _gdk_uikit_surface_move_resize (GdkSurface *surface,
                                     gboolean    with_move,
                                     int         x,
                                     int         y,
                                     int         width,
                                     int         height);
void _gdk_uikit_surface_relayout_all (GdkUIKitDisplay *display);

/* ------------------------------------------------------ cairo context -- */

#define GDK_TYPE_UIKIT_CAIRO_CONTEXT (gdk_uikit_cairo_context_get_type ())
GType gdk_uikit_cairo_context_get_type (void);

/* ------------------------------------------------- devices, seat, keys -- */

#define GDK_TYPE_UIKIT_DEVICE (gdk_uikit_device_get_type ())
GType gdk_uikit_device_get_type (void);

#define GDK_TYPE_UIKIT_SEAT (gdk_uikit_seat_get_type ())
GType gdk_uikit_seat_get_type (void);
GdkSeat *_gdk_uikit_seat_new (GdkDisplay *display,
                              GdkDevice  *pointer,
                              GdkDevice  *keyboard);
void _gdk_uikit_seat_add_physical (GdkSeat *seat, GdkDevice *device);

#define GDK_TYPE_UIKIT_KEYMAP (gdk_uikit_keymap_get_type ())
GType gdk_uikit_keymap_get_type (void);

G_END_DECLS
