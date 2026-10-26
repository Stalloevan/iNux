/* GTK - UIKit (iOS) input method: shows the on-screen keyboard while a
 * text widget has focus. Typed text reaches GTK as key events (see the
 * UIKit GDK backend), which the simple input method commits.
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#include "config.h"

#include "gtk/gtkimcontextsimple.h"
#include "gtk/gtkimmoduleprivate.h"
#include "gtk/gtkwidget.h"

#include "gdk/uikit/gdkuikit.h"

G_GNUC_BEGIN_IGNORE_DEPRECATIONS

#define GTK_TYPE_IM_CONTEXT_UIKIT (gtk_im_context_uikit_get_type ())
#define GTK_IM_CONTEXT_UIKIT(obj) (G_TYPE_CHECK_INSTANCE_CAST ((obj), GTK_TYPE_IM_CONTEXT_UIKIT, GtkIMContextUIKit))

typedef struct
{
  GtkIMContextSimple parent;
  GtkWidget *client_widget;
} GtkIMContextUIKit;

typedef struct
{
  GtkIMContextSimpleClass parent_class;
} GtkIMContextUIKitClass;

GType gtk_im_context_uikit_get_type (void);

G_DEFINE_TYPE_WITH_CODE (GtkIMContextUIKit, gtk_im_context_uikit, GTK_TYPE_IM_CONTEXT_SIMPLE,
                         gtk_im_module_ensure_extension_point ();
                         g_io_extension_point_implement (GTK_IM_MODULE_EXTENSION_POINT_NAME,
                                                         g_define_type_id,
                                                         "uikit",
                                                         10))

static void
uikit_set_client_widget (GtkIMContext *context,
                         GtkWidget    *widget)
{
  GTK_IM_CONTEXT_UIKIT (context)->client_widget = widget;

  if (GTK_IM_CONTEXT_CLASS (gtk_im_context_uikit_parent_class)->set_client_widget)
    GTK_IM_CONTEXT_CLASS (gtk_im_context_uikit_parent_class)->set_client_widget (context, widget);
}

static void
set_keyboard (GtkIMContext *context,
              gboolean      visible)
{
  GtkIMContextUIKit *self = GTK_IM_CONTEXT_UIKIT (context);
  GdkDisplay *display;

  if (!self->client_widget)
    return;

  display = gtk_widget_get_display (self->client_widget);
  if (GDK_IS_UIKIT_DISPLAY (display))
    gdk_uikit_display_set_keyboard_visible (display, visible);
}

static void
uikit_focus_in (GtkIMContext *context)
{
  if (GTK_IM_CONTEXT_CLASS (gtk_im_context_uikit_parent_class)->focus_in)
    GTK_IM_CONTEXT_CLASS (gtk_im_context_uikit_parent_class)->focus_in (context);
  set_keyboard (context, TRUE);
}

static void
uikit_focus_out (GtkIMContext *context)
{
  if (GTK_IM_CONTEXT_CLASS (gtk_im_context_uikit_parent_class)->focus_out)
    GTK_IM_CONTEXT_CLASS (gtk_im_context_uikit_parent_class)->focus_out (context);
  set_keyboard (context, FALSE);
}

static void
gtk_im_context_uikit_class_init (GtkIMContextUIKitClass *klass)
{
  GtkIMContextClass *im_class = GTK_IM_CONTEXT_CLASS (klass);

  im_class->set_client_widget = uikit_set_client_widget;
  im_class->focus_in = uikit_focus_in;
  im_class->focus_out = uikit_focus_out;
}

static void
gtk_im_context_uikit_init (GtkIMContextUIKit *self)
{
}

G_GNUC_END_IGNORE_DEPRECATIONS
