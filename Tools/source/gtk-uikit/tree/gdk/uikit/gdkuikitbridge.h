/* GDK - UIKit (iOS) backend: the interface between the GTK thread and the
 * UIKit main thread. Kept free of GDK private headers so the Objective-C
 * side (gdkuikitui.m, gdkuikitentry.m) only sees plain C types.
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#pragma once

#include <glib.h>
#include <gdk/gdk.h>

G_BEGIN_DECLS

/* ------------------------------------- main thread -> GTK thread posts -- */

typedef enum {
  GDK_UIKIT_TOUCH_BEGIN,
  GDK_UIKIT_TOUCH_UPDATE,
  GDK_UIKIT_TOUCH_END,
  GDK_UIKIT_TOUCH_CANCEL,
} GdkUIKitTouchPhase;

typedef enum {
  GDK_UIKIT_POINTER_MOTION,
  GDK_UIKIT_POINTER_PRESS,
  GDK_UIKIT_POINTER_RELEASE,
} GdkUIKitPointerKind;

/* All of these may be called from any thread; the work happens on the GTK
 * thread. Coordinates are surface-local points. */
void gdk_uikit_post_touch     (guint surface_id, GdkUIKitTouchPhase phase,
                               guint sequence, gboolean emulating,
                               double x, double y, guint32 time);
void gdk_uikit_post_pointer   (guint surface_id, GdkUIKitPointerKind kind,
                               guint button, GdkModifierType state,
                               double x, double y, guint32 time);
void gdk_uikit_post_scroll    (guint surface_id, double dx, double dy,
                               gboolean is_stop, GdkModifierType state,
                               double x, double y, guint32 time);
void gdk_uikit_post_key       (guint keyval, GdkModifierType state,
                               gboolean press, guint32 time);
void gdk_uikit_post_text      (const char *utf8, guint32 time);
void gdk_uikit_post_layout    (int x, int y, int width, int height,
                               double scale, gboolean dark);
void gdk_uikit_post_dismiss_popups (void);
void gdk_uikit_post_close_front (void);

/* ------------------------------------ GTK thread -> main thread (UI) -- */

/* Implemented in gdkuikitui.m. Views are created hidden. @kind: 0 for a
 * toplevel, 1 for a popup, 2 for a popup that closes on outside taps. */
gpointer gdk_uikit_ui_create_view   (guint surface_id, int kind);
void     gdk_uikit_ui_destroy_view  (gpointer view);
void     gdk_uikit_ui_set_frame     (gpointer view, int x, int y, int width, int height);
void     gdk_uikit_ui_set_visible   (gpointer view, gboolean visible);
void     gdk_uikit_ui_raise         (gpointer view);
/* Takes ownership of @pixels (g_free'd), BGRA premultiplied, @stride bytes
 * per row, @width x @height device pixels. */
void     gdk_uikit_ui_set_contents  (gpointer view, guchar *pixels,
                                     int width, int height, int stride,
                                     double scale);
void     gdk_uikit_ui_show_keyboard (gboolean show);
void     gdk_uikit_ui_beep          (void);
/* Synchronously reads the current usable area, scale and appearance. */
void     gdk_uikit_ui_get_layout    (GdkRectangle *layout, double *scale,
                                     gboolean *dark);
guint32  gdk_uikit_ui_time_ms       (void);
void     gdk_uikit_ui_open_uri      (const char *uri);
void     gdk_uikit_ui_set_clipboard_text (const char *utf8);
char    *gdk_uikit_ui_get_clipboard_text (void);

G_END_DECLS
