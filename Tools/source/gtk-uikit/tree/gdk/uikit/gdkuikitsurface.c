/* GDK - UIKit (iOS) backend: surfaces (toplevels, popups, drag icons) and
 * the Cairo draw context that hands finished frames to UIKit.
 *
 * Window management on a phone: a toplevel without a transient parent (an
 * app's main window) fills the usable screen area and is reported as
 * maximized; a transient toplevel (a dialog or secondary window) gets its
 * natural size, capped to the screen, centred above its parent. The front
 * toplevel has keyboard focus. Popups (menus, popovers) are placed with
 * GDK's popup layout helper, as on other backends.
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#include "config.h"

#include <math.h>
#include <string.h>

#include "gdkuikit-private.h"

#include "gdkcairo.h"
#include "gdkcolorstateprivate.h"
#include "gdkdragsurfaceprivate.h"
#include "gdkdragsurfacesizeprivate.h"
#include "gdkframeclockidleprivate.h"
#include "gdkpopupprivate.h"
#include "gdktoplevelprivate.h"
#include "gdktoplevelsizeprivate.h"

#define LAST_PROP 1

static GdkUIKitDisplay *
display_of (GdkSurface *surface)
{
  return GDK_UIKIT_DISPLAY (gdk_surface_get_display (surface));
}

/* ======================================================== base surface == */

G_DEFINE_TYPE (GdkUIKitSurface, gdk_uikit_surface, GDK_TYPE_SURFACE)

static void
sync_view_frame (GdkUIKitSurface *self)
{
  GdkSurface *surface = GDK_SURFACE (self);

  if (self->view)
    gdk_uikit_ui_set_frame (self->view, self->root_x, self->root_y,
                            MAX (surface->width, 1), MAX (surface->height, 1));
}

static void
sync_children (GdkSurface *parent)
{
  GdkUIKitSurface *parent_impl = GDK_UIKIT_SURFACE (parent);

  for (GList *l = parent->children; l; l = l->next)
    {
      GdkSurface *child = l->data;
      GdkUIKitSurface *impl = GDK_UIKIT_SURFACE (child);
      int root_x = parent_impl->root_x + child->x;
      int root_y = parent_impl->root_y + child->y;

      if (root_x != impl->root_x || root_y != impl->root_y)
        {
          impl->root_x = root_x;
          impl->root_y = root_y;
          sync_view_frame (impl);
          sync_children (child);
        }
    }
}

/* x, y are relative to the parent for popups, root coordinates for
 * toplevels. */
void
_gdk_uikit_surface_move_resize (GdkSurface *surface,
                                gboolean    with_move,
                                int         x,
                                int         y,
                                int         width,
                                int         height)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);
  gboolean size_changed = FALSE;

  if (with_move)
    {
      surface->x = x;
      surface->y = y;
      self->root_x = x;
      self->root_y = y;
      if (surface->parent)
        {
          self->root_x += GDK_UIKIT_SURFACE (surface->parent)->root_x;
          self->root_y += GDK_UIKIT_SURFACE (surface->parent)->root_y;
        }
    }

  if (width > 0 && height > 0 &&
      (width != surface->width || height != surface->height))
    {
      surface->width = width;
      surface->height = height;
      size_changed = TRUE;
    }

  sync_view_frame (self);
  sync_children (surface);

  if (size_changed)
    {
      _gdk_surface_update_size (surface);
      gdk_surface_invalidate_rect (surface, NULL);
    }
}

static void
gdk_uikit_surface_constructed (GObject *object)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (object);
  GdkSurface *surface = GDK_SURFACE (object);
  GdkUIKitDisplay *display = display_of (surface);

  self->id = _gdk_uikit_display_add_surface (display, surface);
  self->view = gdk_uikit_ui_create_view (self->id,
                                        surface->parent == NULL ? 0 :
                                        surface->autohide ? 2 : 1);

  G_OBJECT_CLASS (gdk_uikit_surface_parent_class)->constructed (object);
}

static void
gdk_uikit_surface_release_view (GdkUIKitSurface *self)
{
  if (self->view)
    {
      gdk_uikit_ui_destroy_view (self->view);
      self->view = NULL;
    }
}

static void
gdk_uikit_surface_finalize (GObject *object)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (object);
  GdkUIKitDisplay *display = display_of (GDK_SURFACE (self));

  g_clear_handle_id (&self->compute_size_source_id, g_source_remove);
  if (display)
    _gdk_uikit_display_remove_surface (display, self->id);
  gdk_uikit_surface_release_view (self);

  G_OBJECT_CLASS (gdk_uikit_surface_parent_class)->finalize (object);
}

static void
show_surface (GdkSurface *surface)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);
  gboolean was_mapped = GDK_SURFACE_IS_MAPPED (surface);

  if (surface->destroyed)
    return;

  self->visible = TRUE;
  if (!was_mapped)
    gdk_surface_set_is_mapped (surface, TRUE);

  sync_view_frame (self);
  gdk_uikit_ui_set_visible (self->view, TRUE);
  gdk_uikit_ui_raise (self->view);

  if (!was_mapped)
    gdk_surface_invalidate_rect (surface, NULL);
}

static void
gdk_uikit_surface_hide (GdkSurface *surface)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);
  GdkUIKitDisplay *display = display_of (surface);

  self->visible = FALSE;
  g_clear_handle_id (&self->compute_size_source_id, g_source_remove);

  if (self->popup_grab)
    {
      gdk_seat_ungrab (gdk_display_get_default_seat (surface->display), surface);
      self->popup_grab = FALSE;
    }
  display->grab_popups = g_list_remove (display->grab_popups, surface);

  if (self->view)
    gdk_uikit_ui_set_visible (self->view, FALSE);

  gdk_seat_break_grab (gdk_display_get_default_seat (surface->display), surface);

  if (!surface->parent)
    _gdk_uikit_display_update_focus (display);
}

static void
gdk_uikit_surface_get_geometry (GdkSurface *surface,
                                int        *x,
                                int        *y,
                                int        *width,
                                int        *height)
{
  if (x)
    *x = surface->x;
  if (y)
    *y = surface->y;
  if (width)
    *width = surface->width;
  if (height)
    *height = surface->height;
}

static void
gdk_uikit_surface_get_root_coords (GdkSurface *surface,
                                   int         x,
                                   int         y,
                                   int        *root_x,
                                   int        *root_y)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);

  if (root_x)
    *root_x = x + self->root_x;
  if (root_y)
    *root_y = y + self->root_y;
}

static gboolean
gdk_uikit_surface_get_device_state (GdkSurface      *surface,
                                    GdkDevice       *device,
                                    double          *x,
                                    double          *y,
                                    GdkModifierType *mask)
{
  GdkUIKitDisplay *display = display_of (surface);
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);

  if (GDK_SURFACE_DESTROYED (surface))
    return FALSE;

  if (display->last_surface)
    {
      GdkUIKitSurface *last = GDK_UIKIT_SURFACE (display->last_surface);
      *x = display->last_x + last->root_x - self->root_x;
      *y = display->last_y + last->root_y - self->root_y;
    }
  else
    {
      *x = -1;
      *y = -1;
    }
  if (mask)
    *mask = 0;

  return *x >= 0 && *y >= 0 && *x < surface->width && *y < surface->height;
}

static void
gdk_uikit_surface_set_input_region (GdkSurface     *surface,
                                    cairo_region_t *region)
{
}

static void
gdk_uikit_surface_destroy (GdkSurface *surface,
                           gboolean    foreign_destroy)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);
  GdkUIKitDisplay *display = display_of (surface);

  g_clear_handle_id (&self->compute_size_source_id, g_source_remove);
  gdk_seat_break_grab (gdk_display_get_default_seat (surface->display), surface);
  _gdk_uikit_display_remove_surface (display, self->id);
  gdk_uikit_surface_release_view (self);

  if (!surface->parent)
    _gdk_uikit_display_update_focus (display);
}

static gboolean
gdk_uikit_surface_beep (GdkSurface *surface)
{
  gdk_uikit_ui_beep ();
  return TRUE;
}

static GdkDrag *
gdk_uikit_surface_drag_begin (GdkSurface         *surface,
                              GdkDevice          *device,
                              GdkContentProvider *content,
                              GdkDragAction       actions,
                              double              dx,
                              double              dy)
{
  /* Drag and drop between surfaces is not supported yet. */
  return NULL;
}

static double
gdk_uikit_surface_get_scale (GdkSurface *surface)
{
  return display_of (surface)->scale;
}

/* -------------------------------------------------- toplevel sizing -- */

static void
compute_toplevel_size (GdkSurface *surface)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);
  GdkUIKitDisplay *display = display_of (surface);
  GdkRectangle *area = &display->layout;
  GdkToplevelSize size;
  int width, height, x, y;

  gdk_toplevel_size_init (&size, area->width, area->height);
  gdk_toplevel_notify_compute_size (GDK_TOPLEVEL (surface), &size);

  if (size.shadow.is_valid)
    {
      self->shadow_left = size.shadow.left;
      self->shadow_right = size.shadow.right;
      self->shadow_top = size.shadow.top;
      self->shadow_bottom = size.shadow.bottom;
    }

  if (self->fills_screen)
    {
      /* A window narrower than its minimum size would be cut off; let it
       * extend past the screen edge rather than squeeze it. */
      width = MAX (area->width, size.min_width);
      height = MAX (area->height, size.min_height);
      x = area->x;
      y = area->y;
    }
  else
    {
      width = MAX (MIN (size.width, area->width), size.min_width);
      height = MAX (MIN (size.height, area->height), size.min_height);
      x = area->x + MAX (0, (area->width - width) / 2);
      y = area->y + MAX (0, (area->height - height) / 2);
    }

  width = MAX (width, 1);
  height = MAX (height, 1);

  if (width != self->last_computed_width || height != self->last_computed_height ||
      x != self->root_x || y != self->root_y)
    {
      self->last_computed_width = width;
      self->last_computed_height = height;
      _gdk_uikit_surface_move_resize (surface, TRUE, x, y, width, height);
    }
}

static gboolean
compute_size_idle (gpointer data)
{
  GdkSurface *surface = data;
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);

  self->compute_size_source_id = 0;
  compute_toplevel_size (surface);

  return G_SOURCE_REMOVE;
}

static void
gdk_uikit_surface_request_layout (GdkSurface *surface)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);

  if (!self->compute_size_source_id && GDK_IS_TOPLEVEL (surface))
    self->compute_size_source_id = g_idle_add_full (G_PRIORITY_HIGH - 10,
                                                    compute_size_idle,
                                                    surface, NULL);
}

static gboolean
gdk_uikit_surface_compute_size (GdkSurface *surface)
{
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);

  if (GDK_IS_TOPLEVEL (surface))
    {
      g_clear_handle_id (&self->compute_size_source_id, g_source_remove);
      compute_toplevel_size (surface);
    }

  return FALSE;
}

static void
gdk_uikit_surface_class_init (GdkUIKitSurfaceClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  GdkSurfaceClass *surface_class = GDK_SURFACE_CLASS (klass);

  object_class->constructed = gdk_uikit_surface_constructed;
  object_class->finalize = gdk_uikit_surface_finalize;

  surface_class->hide = gdk_uikit_surface_hide;
  surface_class->get_geometry = gdk_uikit_surface_get_geometry;
  surface_class->get_root_coords = gdk_uikit_surface_get_root_coords;
  surface_class->get_device_state = gdk_uikit_surface_get_device_state;
  surface_class->set_input_region = gdk_uikit_surface_set_input_region;
  surface_class->destroy = gdk_uikit_surface_destroy;
  surface_class->beep = gdk_uikit_surface_beep;
  surface_class->drag_begin = gdk_uikit_surface_drag_begin;
  surface_class->get_scale = gdk_uikit_surface_get_scale;
  surface_class->request_layout = gdk_uikit_surface_request_layout;
  surface_class->compute_size = gdk_uikit_surface_compute_size;
}

static void
gdk_uikit_surface_init (GdkUIKitSurface *self)
{
}

void
_gdk_uikit_surface_relayout_all (GdkUIKitDisplay *display)
{
  for (GList *l = display->toplevels; l; l = l->next)
    {
      GdkSurface *surface = l->data;

      if (GDK_SURFACE_DESTROYED (surface))
        continue;
      compute_toplevel_size (surface);
      if (GDK_SURFACE_IS_MAPPED (surface))
        gdk_surface_request_layout (surface);
    }
}

/* ============================================================ toplevel == */

typedef struct { GdkUIKitSurface parent_instance; } GdkUIKitToplevel;
typedef struct { GdkUIKitSurfaceClass parent_class; } GdkUIKitToplevelClass;

static void gdk_uikit_toplevel_iface_init (GdkToplevelInterface *iface);

G_DEFINE_TYPE_WITH_CODE (GdkUIKitToplevel, gdk_uikit_toplevel, GDK_TYPE_UIKIT_SURFACE,
                         G_IMPLEMENT_INTERFACE (GDK_TYPE_TOPLEVEL,
                                                gdk_uikit_toplevel_iface_init))

static void
gdk_uikit_toplevel_init (GdkUIKitToplevel *self)
{
}

static void
gdk_uikit_toplevel_constructed (GObject *object)
{
  GdkSurface *surface = GDK_SURFACE (object);
  GdkFrameClock *frame_clock = _gdk_frame_clock_idle_new ();

  gdk_surface_set_frame_clock (surface, frame_clock);
  g_object_unref (frame_clock);

  GDK_UIKIT_SURFACE (surface)->fills_screen = TRUE;

  G_OBJECT_CLASS (gdk_uikit_toplevel_parent_class)->constructed (object);
}

static void
gdk_uikit_toplevel_finalize (GObject *object)
{
  GdkSurface *surface = GDK_SURFACE (object);

  g_clear_object (&surface->transient_for);

  G_OBJECT_CLASS (gdk_uikit_toplevel_parent_class)->finalize (object);
}

static void
gdk_uikit_toplevel_set_property (GObject      *object,
                                 guint         prop_id,
                                 const GValue *value,
                                 GParamSpec   *pspec)
{
  GdkSurface *surface = GDK_SURFACE (object);

  switch (prop_id)
    {
    case LAST_PROP + GDK_TOPLEVEL_PROP_TRANSIENT_FOR:
      g_clear_object (&surface->transient_for);
      surface->transient_for = g_value_dup_object (value);
      GDK_UIKIT_SURFACE (surface)->fills_screen = (surface->transient_for == NULL);
      if (GDK_SURFACE_IS_MAPPED (surface))
        gdk_surface_request_layout (surface);
      g_object_notify_by_pspec (object, pspec);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_MODAL:
      surface->modal_hint = g_value_get_boolean (value);
      g_object_notify_by_pspec (object, pspec);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_TITLE:
    case LAST_PROP + GDK_TOPLEVEL_PROP_STARTUP_ID:
      g_object_notify_by_pspec (object, pspec);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_ICON_LIST:
    case LAST_PROP + GDK_TOPLEVEL_PROP_DECORATED:
    case LAST_PROP + GDK_TOPLEVEL_PROP_DELETABLE:
    case LAST_PROP + GDK_TOPLEVEL_PROP_FULLSCREEN_MODE:
    case LAST_PROP + GDK_TOPLEVEL_PROP_SHORTCUTS_INHIBITED:
    case LAST_PROP + GDK_TOPLEVEL_PROP_GRAVITY:
      break;

    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
      break;
    }
}

static void
gdk_uikit_toplevel_get_property (GObject    *object,
                                 guint       prop_id,
                                 GValue     *value,
                                 GParamSpec *pspec)
{
  GdkSurface *surface = GDK_SURFACE (object);

  switch (prop_id)
    {
    case LAST_PROP + GDK_TOPLEVEL_PROP_STATE:
      g_value_set_flags (value, surface->state);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_TITLE:
    case LAST_PROP + GDK_TOPLEVEL_PROP_STARTUP_ID:
      g_value_set_static_string (value, "");
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_TRANSIENT_FOR:
      g_value_set_object (value, surface->transient_for);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_MODAL:
      g_value_set_boolean (value, surface->modal_hint);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_ICON_LIST:
      g_value_set_pointer (value, NULL);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_DECORATED:
    case LAST_PROP + GDK_TOPLEVEL_PROP_DELETABLE:
      g_value_set_boolean (value, TRUE);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_FULLSCREEN_MODE:
      g_value_set_enum (value, surface->fullscreen_mode);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_SHORTCUTS_INHIBITED:
      g_value_set_boolean (value, surface->shortcuts_inhibited);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_CAPABILITIES:
      /* Windows are laid out by the backend; none of the window manager
       * actions apply. */
      g_value_set_flags (value, 0);
      break;

    case LAST_PROP + GDK_TOPLEVEL_PROP_GRAVITY:
      g_value_set_enum (value, GDK_GRAVITY_NORTH_WEST);
      break;

    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
      break;
    }
}

static void
gdk_uikit_toplevel_class_init (GdkUIKitToplevelClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->constructed = gdk_uikit_toplevel_constructed;
  object_class->finalize = gdk_uikit_toplevel_finalize;
  object_class->get_property = gdk_uikit_toplevel_get_property;
  object_class->set_property = gdk_uikit_toplevel_set_property;

  gdk_toplevel_install_properties (object_class, LAST_PROP);
}

static void
gdk_uikit_toplevel_present (GdkToplevel       *toplevel,
                            GdkToplevelLayout *layout)
{
  GdkSurface *surface = GDK_SURFACE (toplevel);
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);
  GdkUIKitDisplay *display = display_of (surface);
  GdkToplevelState want;

  if (GDK_SURFACE_DESTROYED (surface))
    return;

  /* A full-screen window is drawn without rounded corners, shadows or
   * resize borders. */
  want = self->fills_screen ? GDK_TOPLEVEL_STATE_MAXIMIZED : 0;
  if ((surface->state & GDK_TOPLEVEL_STATE_MAXIMIZED) != want)
    gdk_synthesize_surface_state (surface,
                                  want ? 0 : GDK_TOPLEVEL_STATE_MAXIMIZED,
                                  want);

  compute_toplevel_size (surface);

  display->toplevels = g_list_remove (display->toplevels, surface);
  display->toplevels = g_list_append (display->toplevels, surface);

  show_surface (surface);
  gdk_surface_request_layout (surface);
  _gdk_uikit_display_update_focus (display);
}

static gboolean
gdk_uikit_toplevel_minimize (GdkToplevel *toplevel)
{
  return FALSE;
}

static gboolean
gdk_uikit_toplevel_lower (GdkToplevel *toplevel)
{
  return FALSE;
}

static void
gdk_uikit_toplevel_focus (GdkToplevel *toplevel,
                          guint32      timestamp)
{
  GdkSurface *surface = GDK_SURFACE (toplevel);
  GdkUIKitDisplay *display = display_of (surface);

  if (!GDK_SURFACE_IS_MAPPED (surface))
    return;

  display->toplevels = g_list_remove (display->toplevels, surface);
  display->toplevels = g_list_append (display->toplevels, surface);
  gdk_uikit_ui_raise (GDK_UIKIT_SURFACE (surface)->view);
  _gdk_uikit_display_update_focus (display);
}

static gboolean
gdk_uikit_toplevel_show_window_menu (GdkToplevel *toplevel,
                                     GdkEvent    *event)
{
  return FALSE;
}

static gboolean
gdk_uikit_toplevel_supports_edge_constraints (GdkToplevel *toplevel)
{
  return FALSE;
}

static void
gdk_uikit_toplevel_begin_resize (GdkToplevel    *toplevel,
                                 GdkSurfaceEdge  edge,
                                 GdkDevice      *device,
                                 int             button,
                                 double          x,
                                 double          y,
                                 guint32         timestamp)
{
}

static void
gdk_uikit_toplevel_begin_move (GdkToplevel *toplevel,
                               GdkDevice   *device,
                               int          button,
                               double       x,
                               double       y,
                               guint32      timestamp)
{
}

static void
gdk_uikit_toplevel_iface_init (GdkToplevelInterface *iface)
{
  iface->present = gdk_uikit_toplevel_present;
  iface->minimize = gdk_uikit_toplevel_minimize;
  iface->lower = gdk_uikit_toplevel_lower;
  iface->focus = gdk_uikit_toplevel_focus;
  iface->show_window_menu = gdk_uikit_toplevel_show_window_menu;
  iface->supports_edge_constraints = gdk_uikit_toplevel_supports_edge_constraints;
  iface->begin_resize = gdk_uikit_toplevel_begin_resize;
  iface->begin_move = gdk_uikit_toplevel_begin_move;
}

/* =============================================================== popup == */

typedef struct { GdkUIKitSurface parent_instance; } GdkUIKitPopup;
typedef struct { GdkUIKitSurfaceClass parent_class; } GdkUIKitPopupClass;

static void gdk_uikit_popup_iface_init (GdkPopupInterface *iface);

G_DEFINE_TYPE_WITH_CODE (GdkUIKitPopup, gdk_uikit_popup, GDK_TYPE_UIKIT_SURFACE,
                         G_IMPLEMENT_INTERFACE (GDK_TYPE_POPUP,
                                                gdk_uikit_popup_iface_init))

static void
gdk_uikit_popup_init (GdkUIKitPopup *self)
{
}

static void
gdk_uikit_popup_constructed (GObject *object)
{
  GdkSurface *surface = GDK_SURFACE (object);
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (object);

  self->root_x = GDK_UIKIT_SURFACE (surface->parent)->root_x;
  self->root_y = GDK_UIKIT_SURFACE (surface->parent)->root_y;

  gdk_surface_set_frame_clock (surface, gdk_surface_get_frame_clock (surface->parent));

  G_OBJECT_CLASS (gdk_uikit_popup_parent_class)->constructed (object);
}

static void
gdk_uikit_popup_get_property (GObject    *object,
                              guint       prop_id,
                              GValue     *value,
                              GParamSpec *pspec)
{
  GdkSurface *surface = GDK_SURFACE (object);

  switch (prop_id)
    {
    case LAST_PROP + GDK_POPUP_PROP_PARENT:
      g_value_set_object (value, surface->parent);
      break;
    case LAST_PROP + GDK_POPUP_PROP_AUTOHIDE:
      g_value_set_boolean (value, surface->autohide);
      break;
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
      break;
    }
}

static void
gdk_uikit_popup_set_property (GObject      *object,
                              guint         prop_id,
                              const GValue *value,
                              GParamSpec   *pspec)
{
  GdkSurface *surface = GDK_SURFACE (object);

  switch (prop_id)
    {
    case LAST_PROP + GDK_POPUP_PROP_PARENT:
      surface->parent = g_value_dup_object (value);
      if (surface->parent != NULL)
        surface->parent->children = g_list_prepend (surface->parent->children, surface);
      break;
    case LAST_PROP + GDK_POPUP_PROP_AUTOHIDE:
      surface->autohide = g_value_get_boolean (value);
      break;
    default:
      G_OBJECT_WARN_INVALID_PROPERTY_ID (object, prop_id, pspec);
      break;
    }
}

static void
gdk_uikit_popup_class_init (GdkUIKitPopupClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->constructed = gdk_uikit_popup_constructed;
  object_class->get_property = gdk_uikit_popup_get_property;
  object_class->set_property = gdk_uikit_popup_set_property;

  gdk_popup_install_properties (object_class, LAST_PROP);
}

static gboolean
gdk_uikit_popup_present (GdkPopup       *popup,
                         int             width,
                         int             height,
                         GdkPopupLayout *layout)
{
  GdkSurface *surface = GDK_SURFACE (popup);
  GdkUIKitSurface *self = GDK_UIKIT_SURFACE (surface);
  GdkUIKitDisplay *display = display_of (surface);
  GdkMonitor *monitor;
  GdkRectangle bounds;
  GdkRectangle final_rect;
  int shadow_left, shadow_right, shadow_top, shadow_bottom;

  gdk_popup_layout_get_shadow_width (layout, &shadow_left, &shadow_right,
                                     &shadow_top, &shadow_bottom);
  monitor = gdk_surface_get_layout_monitor (surface, layout, gdk_monitor_get_geometry);
  gdk_monitor_get_geometry (monitor, &bounds);

  gdk_surface_layout_popup_helper (surface,
                                   width, height,
                                   shadow_left, shadow_right,
                                   shadow_top, shadow_bottom,
                                   monitor,
                                   &bounds,
                                   layout,
                                   GDK_SURFACE_LAYOUT_POPUP_HELPER_DEFAULT,
                                   &final_rect);

  _gdk_uikit_surface_move_resize (surface, TRUE,
                                  final_rect.x, final_rect.y,
                                  final_rect.width, final_rect.height);

  if (GDK_SURFACE_IS_MAPPED (surface))
    return TRUE;

  show_surface (surface);

  if (surface->autohide)
    {
      gdk_seat_grab (gdk_display_get_default_seat (surface->display), surface);
      self->popup_grab = TRUE;
      display->grab_popups = g_list_remove (display->grab_popups, surface);
      display->grab_popups = g_list_append (display->grab_popups, surface);
    }

  return GDK_SURFACE_IS_MAPPED (surface);
}

static GdkGravity
gdk_uikit_popup_get_surface_anchor (GdkPopup *popup)
{
  return GDK_SURFACE (popup)->popup.surface_anchor;
}

static GdkGravity
gdk_uikit_popup_get_rect_anchor (GdkPopup *popup)
{
  return GDK_SURFACE (popup)->popup.rect_anchor;
}

static int
gdk_uikit_popup_get_position_x (GdkPopup *popup)
{
  return GDK_SURFACE (popup)->x;
}

static int
gdk_uikit_popup_get_position_y (GdkPopup *popup)
{
  return GDK_SURFACE (popup)->y;
}

static void
gdk_uikit_popup_iface_init (GdkPopupInterface *iface)
{
  iface->present = gdk_uikit_popup_present;
  iface->get_surface_anchor = gdk_uikit_popup_get_surface_anchor;
  iface->get_rect_anchor = gdk_uikit_popup_get_rect_anchor;
  iface->get_position_x = gdk_uikit_popup_get_position_x;
  iface->get_position_y = gdk_uikit_popup_get_position_y;
}

/* ======================================================== drag surface == */

/* Never shown (drag_begin returns NULL), but the type must exist. */

typedef struct { GdkUIKitSurface parent_instance; } GdkUIKitDragSurface;
typedef struct { GdkUIKitSurfaceClass parent_class; } GdkUIKitDragSurfaceClass;

static void gdk_uikit_drag_surface_iface_init (GdkDragSurfaceInterface *iface);

G_DEFINE_TYPE_WITH_CODE (GdkUIKitDragSurface, gdk_uikit_drag_surface, GDK_TYPE_UIKIT_SURFACE,
                         G_IMPLEMENT_INTERFACE (GDK_TYPE_DRAG_SURFACE,
                                                gdk_uikit_drag_surface_iface_init))

static void
gdk_uikit_drag_surface_init (GdkUIKitDragSurface *self)
{
}

static void
gdk_uikit_drag_surface_constructed (GObject *object)
{
  GdkSurface *surface = GDK_SURFACE (object);
  GdkFrameClock *frame_clock = _gdk_frame_clock_idle_new ();

  gdk_surface_set_frame_clock (surface, frame_clock);
  g_object_unref (frame_clock);

  G_OBJECT_CLASS (gdk_uikit_drag_surface_parent_class)->constructed (object);
}

static void
gdk_uikit_drag_surface_class_init (GdkUIKitDragSurfaceClass *klass)
{
  G_OBJECT_CLASS (klass)->constructed = gdk_uikit_drag_surface_constructed;
}

static gboolean
gdk_uikit_drag_surface_present (GdkDragSurface *drag_surface,
                                int             width,
                                int             height)
{
  GdkSurface *surface = GDK_SURFACE (drag_surface);

  _gdk_uikit_surface_move_resize (surface, FALSE, 0, 0, width, height);
  show_surface (surface);
  return TRUE;
}

static void
gdk_uikit_drag_surface_iface_init (GdkDragSurfaceInterface *iface)
{
  iface->present = gdk_uikit_drag_surface_present;
}

/* ======================================================= cairo context == */

typedef struct
{
  GdkCairoContext parent_instance;

  cairo_surface_t *paint_surface;
} GdkUIKitCairoContext;

typedef struct { GdkCairoContextClass parent_class; } GdkUIKitCairoContextClass;

G_DEFINE_TYPE (GdkUIKitCairoContext, gdk_uikit_cairo_context, GDK_TYPE_CAIRO_CONTEXT)

static void
gdk_uikit_cairo_context_dispose (GObject *object)
{
  GdkUIKitCairoContext *self = (GdkUIKitCairoContext *) object;

  g_clear_pointer (&self->paint_surface, cairo_surface_destroy);

  G_OBJECT_CLASS (gdk_uikit_cairo_context_parent_class)->dispose (object);
}

/* The frame buffer is kept between frames, so only damaged areas are
 * redrawn; it is replaced when the surface changes size. */
static void
gdk_uikit_cairo_context_begin_frame (GdkDrawContext  *draw_context,
                                     gpointer         context_data,
                                     cairo_region_t  *region,
                                     GdkColorState  **out_color_state,
                                     GdkMemoryDepth  *out_depth)
{
  GdkUIKitCairoContext *self = (GdkUIKitCairoContext *) draw_context;
  guint width, height;
  cairo_t *cr;

  gdk_draw_context_get_buffer_size (draw_context, &width, &height);
  width = MAX (width, 1);
  height = MAX (height, 1);

  if (self->paint_surface == NULL ||
      cairo_image_surface_get_width (self->paint_surface) != (int) width ||
      cairo_image_surface_get_height (self->paint_surface) != (int) height)
    {
      g_clear_pointer (&self->paint_surface, cairo_surface_destroy);
      self->paint_surface = cairo_image_surface_create (CAIRO_FORMAT_ARGB32, width, height);
      cairo_region_union_rectangle (region, &(cairo_rectangle_int_t) { 0, 0, width, height });
    }

  cr = cairo_create (self->paint_surface);
  gdk_cairo_region (cr, region);
  cairo_clip (cr);
  cairo_set_operator (cr, CAIRO_OPERATOR_CLEAR);
  cairo_paint (cr);
  cairo_destroy (cr);

  *out_color_state = GDK_COLOR_STATE_SRGB;
  *out_depth = gdk_color_state_get_depth (GDK_COLOR_STATE_SRGB);
}

static void
gdk_uikit_cairo_context_end_frame (GdkDrawContext *draw_context,
                                   gpointer        context_data,
                                   cairo_region_t *painted)
{
  GdkUIKitCairoContext *self = (GdkUIKitCairoContext *) draw_context;
  GdkSurface *surface = gdk_draw_context_get_surface (draw_context);
  GdkUIKitSurface *impl = GDK_UIKIT_SURFACE (surface);
  int width, height, stride;
  guchar *copy;

  if (self->paint_surface == NULL || impl->view == NULL)
    return;

  cairo_surface_flush (self->paint_surface);
  width = cairo_image_surface_get_width (self->paint_surface);
  height = cairo_image_surface_get_height (self->paint_surface);
  stride = cairo_image_surface_get_stride (self->paint_surface);

  /* UIKit composites the frame later on the main thread, so it gets its
   * own copy; the next frame draws into the kept buffer meanwhile. */
  copy = g_memdup2 (cairo_image_surface_get_data (self->paint_surface),
                    (gsize) stride * height);
  gdk_uikit_ui_set_contents (impl->view, copy, width, height, stride,
                             gdk_surface_get_scale (surface));
}

static void
gdk_uikit_cairo_context_surface_resized (GdkDrawContext *draw_context)
{
  GdkUIKitCairoContext *self = (GdkUIKitCairoContext *) draw_context;

  g_clear_pointer (&self->paint_surface, cairo_surface_destroy);
}

static cairo_t *
gdk_uikit_cairo_context_cairo_create (GdkCairoContext *context)
{
  GdkUIKitCairoContext *self = (GdkUIKitCairoContext *) context;

  return cairo_create (self->paint_surface);
}

static void
gdk_uikit_cairo_context_class_init (GdkUIKitCairoContextClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  GdkDrawContextClass *draw_context_class = GDK_DRAW_CONTEXT_CLASS (klass);
  GdkCairoContextClass *cairo_context_class = GDK_CAIRO_CONTEXT_CLASS (klass);

  object_class->dispose = gdk_uikit_cairo_context_dispose;

  draw_context_class->begin_frame = gdk_uikit_cairo_context_begin_frame;
  draw_context_class->end_frame = gdk_uikit_cairo_context_end_frame;
  draw_context_class->surface_resized = gdk_uikit_cairo_context_surface_resized;

  cairo_context_class->cairo_create = gdk_uikit_cairo_context_cairo_create;
}

static void
gdk_uikit_cairo_context_init (GdkUIKitCairoContext *self)
{
}
