/* GDK - UIKit (iOS) backend
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#pragma once

#include <gdk/gdk.h>

G_BEGIN_DECLS

#define GDK_TYPE_UIKIT_DISPLAY  (gdk_uikit_display_get_type ())
#define GDK_UIKIT_DISPLAY(o)    (G_TYPE_CHECK_INSTANCE_CAST ((o), GDK_TYPE_UIKIT_DISPLAY, GdkUIKitDisplay))
#define GDK_IS_UIKIT_DISPLAY(o) (G_TYPE_CHECK_INSTANCE_TYPE ((o), GDK_TYPE_UIKIT_DISPLAY))

typedef struct _GdkUIKitDisplay GdkUIKitDisplay;

GDK_AVAILABLE_IN_ALL
GType gdk_uikit_display_get_type (void) G_GNUC_CONST;

/**
 * gdk_uikit_display_set_keyboard_visible:
 * @display: a UIKit display
 * @visible: whether the on-screen keyboard should be shown
 *
 * Shows or hides the iOS on-screen keyboard. Text typed on it is delivered
 * as key events to the focused surface.
 */
GDK_AVAILABLE_IN_ALL
void  gdk_uikit_display_set_keyboard_visible (GdkDisplay *display,
                                              gboolean    visible);

G_END_DECLS
