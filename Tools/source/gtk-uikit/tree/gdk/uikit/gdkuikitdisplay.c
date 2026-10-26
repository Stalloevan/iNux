/* GDK - UIKit (iOS) backend: display, monitor, input devices, keymap and
 * the event plumbing from the UIKit main thread into the GTK thread.
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#include "config.h"

#include <math.h>
#include <string.h>

#include "gdkuikit-private.h"

#include "gdkeventsprivate.h"
#include "gdkkeysyms.h"
#include "gdkprivate.h"

/* For GtkInterfaceColorScheme. */
#include "gtk/gtkenums.h"

static GdkUIKitDisplay *the_display = NULL;

/* ============================================================ devices == */

typedef struct { GdkDevice parent_instance; } GdkUIKitDevice;
typedef struct { GdkDeviceClass parent_class; } GdkUIKitDeviceClass;

G_DEFINE_TYPE (GdkUIKitDevice, gdk_uikit_device, GDK_TYPE_DEVICE)

static void
gdk_uikit_device_set_surface_cursor (GdkDevice  *device,
                                     GdkSurface *surface,
                                     GdkCursor  *cursor)
{
  /* Touch screens have no cursor; iPad pointer shapes are left to UIKit. */
}

static GdkSurface *
gdk_uikit_device_surface_at_position (GdkDevice       *device,
                                      double          *win_x,
                                      double          *win_y,
                                      GdkModifierType *mask)
{
  GdkUIKitDisplay *self = GDK_UIKIT_DISPLAY (gdk_device_get_display (device));

  if (win_x)
    *win_x = self->last_x;
  if (win_y)
    *win_y = self->last_y;
  if (mask)
    *mask = 0;

  return self->last_surface;
}

static void
gdk_uikit_device_class_init (GdkUIKitDeviceClass *klass)
{
  GdkDeviceClass *device_class = GDK_DEVICE_CLASS (klass);

  device_class->set_surface_cursor = gdk_uikit_device_set_surface_cursor;
  device_class->surface_at_position = gdk_uikit_device_surface_at_position;
}

static void
gdk_uikit_device_init (GdkUIKitDevice *self)
{
  GdkDevice *device = GDK_DEVICE (self);

  _gdk_device_add_axis (device, GDK_AXIS_X, 0, 0, 1);
  _gdk_device_add_axis (device, GDK_AXIS_Y, 0, 0, 1);
}

static GdkDevice *
create_device (GdkDisplay     *display,
               const char     *name,
               GdkInputSource  source,
               gboolean        has_cursor)
{
  return g_object_new (GDK_TYPE_UIKIT_DEVICE,
                       "name", name,
                       "source", source,
                       "has-cursor", has_cursor,
                       "display", display,
                       NULL);
}

/* =============================================================== seat == */

typedef struct
{
  GdkSeat parent_instance;

  GdkDevice *logical_pointer;
  GdkDevice *logical_keyboard;
  GList *physical_pointers;
  GList *physical_keyboards;
  GdkSeatCapabilities capabilities;
} GdkUIKitSeat;

typedef struct { GdkSeatClass parent_class; } GdkUIKitSeatClass;

G_DEFINE_TYPE (GdkUIKitSeat, gdk_uikit_seat, GDK_TYPE_SEAT)

static GdkSeatCapabilities
device_capability (GdkDevice *device)
{
  switch ((int) gdk_device_get_source (device))
    {
    case GDK_SOURCE_KEYBOARD:
      return GDK_SEAT_CAPABILITY_KEYBOARD;
    case GDK_SOURCE_TOUCHSCREEN:
      return GDK_SEAT_CAPABILITY_TOUCH;
    case GDK_SOURCE_PEN:
      return GDK_SEAT_CAPABILITY_TABLET_STYLUS;
    default:
      return GDK_SEAT_CAPABILITY_POINTER;
    }
}

static void
gdk_uikit_seat_dispose (GObject *object)
{
  GdkUIKitSeat *self = (GdkUIKitSeat *) object;
  GList *l;

  if (self->logical_pointer)
    {
      gdk_seat_device_removed (GDK_SEAT (self), self->logical_pointer);
      g_clear_object (&self->logical_pointer);
    }
  if (self->logical_keyboard)
    {
      gdk_seat_device_removed (GDK_SEAT (self), self->logical_keyboard);
      g_clear_object (&self->logical_keyboard);
    }
  for (l = self->physical_pointers; l; l = l->next)
    {
      gdk_seat_device_removed (GDK_SEAT (self), l->data);
      g_object_unref (l->data);
    }
  for (l = self->physical_keyboards; l; l = l->next)
    {
      gdk_seat_device_removed (GDK_SEAT (self), l->data);
      g_object_unref (l->data);
    }
  g_clear_pointer (&self->physical_pointers, g_list_free);
  g_clear_pointer (&self->physical_keyboards, g_list_free);

  G_OBJECT_CLASS (gdk_uikit_seat_parent_class)->dispose (object);
}

static GdkSeatCapabilities
gdk_uikit_seat_get_capabilities (GdkSeat *seat)
{
  return ((GdkUIKitSeat *) seat)->capabilities;
}

static GdkGrabStatus
gdk_uikit_seat_grab (GdkSeat    *seat,
                     GdkSurface *surface)
{
  /* There is one app on screen; grabs always succeed. Popup dismissal on
   * an outside tap is done by the UI side (see gdkuikitui.m). */
  return GDK_GRAB_SUCCESS;
}

static void
gdk_uikit_seat_ungrab (GdkSeat    *seat,
                       GdkSurface *surface)
{
}

static GdkDevice *
gdk_uikit_seat_get_logical_device (GdkSeat             *seat,
                                   GdkSeatCapabilities  capability)
{
  GdkUIKitSeat *self = (GdkUIKitSeat *) seat;

  switch ((guint) capability)
    {
    case GDK_SEAT_CAPABILITY_POINTER:
    case GDK_SEAT_CAPABILITY_TOUCH:
      return self->logical_pointer;
    case GDK_SEAT_CAPABILITY_KEYBOARD:
      return self->logical_keyboard;
    default:
      return NULL;
    }
}

static GList *
append_filtered (GList               *list,
                 GList               *devices,
                 GdkSeatCapabilities  capabilities)
{
  for (GList *l = devices; l; l = l->next)
    if (device_capability (l->data) & capabilities)
      list = g_list_prepend (list, l->data);
  return list;
}

static GList *
gdk_uikit_seat_get_devices (GdkSeat             *seat,
                            GdkSeatCapabilities  capabilities)
{
  GdkUIKitSeat *self = (GdkUIKitSeat *) seat;
  GList *devices = NULL;

  if (capabilities & GDK_SEAT_CAPABILITY_ALL_POINTING)
    devices = append_filtered (devices, self->physical_pointers, capabilities);
  if (capabilities & GDK_SEAT_CAPABILITY_KEYBOARD)
    devices = append_filtered (devices, self->physical_keyboards, capabilities);

  return devices;
}

static GList *
gdk_uikit_seat_get_tools (GdkSeat *seat)
{
  return NULL;
}

static void
gdk_uikit_seat_class_init (GdkUIKitSeatClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  GdkSeatClass *seat_class = GDK_SEAT_CLASS (klass);

  object_class->dispose = gdk_uikit_seat_dispose;

  seat_class->get_capabilities = gdk_uikit_seat_get_capabilities;
  seat_class->grab = gdk_uikit_seat_grab;
  seat_class->ungrab = gdk_uikit_seat_ungrab;
  seat_class->get_logical_device = gdk_uikit_seat_get_logical_device;
  seat_class->get_devices = gdk_uikit_seat_get_devices;
  seat_class->get_tools = gdk_uikit_seat_get_tools;
}

static void
gdk_uikit_seat_init (GdkUIKitSeat *self)
{
}

GdkSeat *
_gdk_uikit_seat_new (GdkDisplay *display,
                     GdkDevice  *pointer,
                     GdkDevice  *keyboard)
{
  GdkUIKitSeat *self = g_object_new (GDK_TYPE_UIKIT_SEAT, "display", display, NULL);

  self->logical_pointer = g_object_ref (pointer);
  self->logical_keyboard = g_object_ref (keyboard);
  gdk_seat_device_added (GDK_SEAT (self), self->logical_pointer);
  gdk_seat_device_added (GDK_SEAT (self), self->logical_keyboard);

  return GDK_SEAT (self);
}

void
_gdk_uikit_seat_add_physical (GdkSeat   *seat,
                              GdkDevice *device)
{
  GdkUIKitSeat *self = (GdkUIKitSeat *) seat;
  GdkSeatCapabilities cap = device_capability (device);

  if (cap & GDK_SEAT_CAPABILITY_ALL_POINTING)
    self->physical_pointers = g_list_prepend (self->physical_pointers, g_object_ref (device));
  else
    self->physical_keyboards = g_list_prepend (self->physical_keyboards, g_object_ref (device));

  self->capabilities |= cap;
  gdk_seat_device_added (seat, device);
}

/* ============================================================= keymap == */

/* Key events carry the keyval as their keycode (as in the Broadway
 * backend): text arrives from the on-screen keyboard as characters, and
 * hardware keys are translated to keyvals in gdkuikitui.m. */

typedef struct { GdkKeymap parent_instance; } GdkUIKitKeymap;
typedef struct { GdkKeymapClass parent_class; } GdkUIKitKeymapClass;

G_DEFINE_TYPE (GdkUIKitKeymap, gdk_uikit_keymap, GDK_TYPE_KEYMAP)

static PangoDirection
gdk_uikit_keymap_get_direction (GdkKeymap *keymap)
{
  return PANGO_DIRECTION_NEUTRAL;
}

static gboolean
gdk_uikit_keymap_false (GdkKeymap *keymap)
{
  return FALSE;
}

static gboolean
gdk_uikit_keymap_get_entries_for_keyval (GdkKeymap *keymap,
                                         guint      keyval,
                                         GArray    *retval)
{
  GdkKeymapKey key = { keyval, 0, 0 };

  g_array_append_val (retval, key);
  return TRUE;
}

static gboolean
gdk_uikit_keymap_get_entries_for_keycode (GdkKeymap     *keymap,
                                          guint          hardware_keycode,
                                          GdkKeymapKey **keys,
                                          guint        **keyvals,
                                          int           *n_entries)
{
  if (n_entries)
    *n_entries = 1;
  if (keys)
    {
      *keys = g_new0 (GdkKeymapKey, 1);
      (*keys)->keycode = hardware_keycode;
    }
  if (keyvals)
    {
      *keyvals = g_new0 (guint, 1);
      (*keyvals)[0] = hardware_keycode;
    }
  return TRUE;
}

static guint
gdk_uikit_keymap_lookup_key (GdkKeymap          *keymap,
                             const GdkKeymapKey *key)
{
  return key->keycode;
}

static gboolean
gdk_uikit_keymap_translate_keyboard_state (GdkKeymap       *keymap,
                                           guint            hardware_keycode,
                                           GdkModifierType  state,
                                           int              group,
                                           guint           *keyval,
                                           int             *effective_group,
                                           int             *level,
                                           GdkModifierType *consumed_modifiers)
{
  if (keyval)
    *keyval = hardware_keycode;
  if (effective_group)
    *effective_group = 0;
  if (level)
    *level = 0;
  if (consumed_modifiers)
    *consumed_modifiers = 0;
  return TRUE;
}

static void
gdk_uikit_keymap_class_init (GdkUIKitKeymapClass *klass)
{
  GdkKeymapClass *keymap_class = GDK_KEYMAP_CLASS (klass);

  keymap_class->get_direction = gdk_uikit_keymap_get_direction;
  keymap_class->have_bidi_layouts = gdk_uikit_keymap_false;
  keymap_class->get_caps_lock_state = gdk_uikit_keymap_false;
  keymap_class->get_num_lock_state = gdk_uikit_keymap_false;
  keymap_class->get_scroll_lock_state = gdk_uikit_keymap_false;
  keymap_class->get_entries_for_keyval = gdk_uikit_keymap_get_entries_for_keyval;
  keymap_class->get_entries_for_keycode = gdk_uikit_keymap_get_entries_for_keycode;
  keymap_class->lookup_key = gdk_uikit_keymap_lookup_key;
  keymap_class->translate_keyboard_state = gdk_uikit_keymap_translate_keyboard_state;
}

static void
gdk_uikit_keymap_init (GdkUIKitKeymap *self)
{
}

/* ======================================================= event source == */

typedef struct
{
  GSource source;
  GdkDisplay *display;
} GdkUIKitEventSource;

static gboolean
event_source_prepare (GSource *source,
                      int     *timeout)
{
  GdkDisplay *display = ((GdkUIKitEventSource *) source)->display;

  *timeout = -1;
  return _gdk_event_queue_find_first (display) != NULL;
}

static gboolean
event_source_check (GSource *source)
{
  GdkDisplay *display = ((GdkUIKitEventSource *) source)->display;

  return _gdk_event_queue_find_first (display) != NULL;
}

static gboolean
event_source_dispatch (GSource     *source,
                       GSourceFunc  callback,
                       gpointer     user_data)
{
  GdkDisplay *display = ((GdkUIKitEventSource *) source)->display;
  GdkEvent *event = gdk_display_get_event (display);

  if (event)
    {
      _gdk_event_emit (event);
      gdk_event_unref (event);
    }

  return G_SOURCE_CONTINUE;
}

static GSourceFuncs event_source_funcs = {
  event_source_prepare,
  event_source_check,
  event_source_dispatch,
  NULL,
};

static GSource *
event_source_new (GdkDisplay *display)
{
  GSource *source = g_source_new (&event_source_funcs, sizeof (GdkUIKitEventSource));

  ((GdkUIKitEventSource *) source)->display = display;
  g_source_set_name (source, "GDK UIKit event source");
  g_source_set_priority (source, GDK_PRIORITY_EVENTS);
  g_source_set_can_recurse (source, TRUE);
  g_source_attach (source, NULL);

  return source;
}

static void
queue_event (GdkUIKitDisplay *self,
             GdkEvent        *event)
{
  GdkDisplay *display = GDK_DISPLAY (self);
  GList *node = _gdk_event_queue_append (display, event);

  _gdk_windowing_got_event (display, node, event, _gdk_uikit_display_next_serial (self));
}

/* ============================================================ display == */

G_DEFINE_TYPE (GdkUIKitDisplay, gdk_uikit_display, GDK_TYPE_DISPLAY)

static const char *
gdk_uikit_display_get_name (GdkDisplay *display)
{
  return "UIKit";
}

static void
gdk_uikit_display_beep (GdkDisplay *display)
{
  gdk_uikit_ui_beep ();
}

static void
gdk_uikit_display_noop (GdkDisplay *display)
{
}

static gulong
gdk_uikit_display_get_next_serial (GdkDisplay *display)
{
  return _gdk_uikit_display_next_serial (GDK_UIKIT_DISPLAY (display));
}

static void
gdk_uikit_display_notify_startup_complete (GdkDisplay *display,
                                           const char *startup_id)
{
}

static GdkKeymap *
gdk_uikit_display_get_keymap (GdkDisplay *display)
{
  return GDK_UIKIT_DISPLAY (display)->keymap;
}

static GdkSeat *
gdk_uikit_display_get_default_seat (GdkDisplay *display)
{
  return GDK_UIKIT_DISPLAY (display)->seat;
}

static GListModel *
gdk_uikit_display_get_monitors (GdkDisplay *display)
{
  return G_LIST_MODEL (GDK_UIKIT_DISPLAY (display)->monitors);
}

static GdkMonitor *
gdk_uikit_display_get_monitor_at_surface (GdkDisplay *display,
                                          GdkSurface *surface)
{
  return GDK_UIKIT_DISPLAY (display)->monitor;
}

static gboolean
gdk_uikit_display_get_setting (GdkDisplay *display,
                               const char *name,
                               GValue     *value)
{
  GdkUIKitDisplay *self = GDK_UIKIT_DISPLAY (display);

  if (g_str_equal (name, "gtk-interface-color-scheme"))
    {
      g_value_set_enum (value, self->dark ? GTK_INTERFACE_COLOR_SCHEME_DARK
                                          : GTK_INTERFACE_COLOR_SCHEME_LIGHT);
      return TRUE;
    }
  if (g_str_equal (name, "gtk-application-prefer-dark-theme"))
    {
      g_value_set_boolean (value, self->dark);
      return TRUE;
    }
  if (g_str_equal (name, "gtk-decoration-layout"))
    {
      /* One window at a time fills the screen; keep a close button so
       * secondary windows and dialogs can be dismissed. */
      g_value_set_static_string (value, ":close");
      return TRUE;
    }
  if (g_str_equal (name, "gtk-font-name"))
    {
      g_value_set_static_string (value, "Adwaita Sans 11");
      return TRUE;
    }
  if (g_str_equal (name, "gtk-icon-theme-name"))
    {
      g_value_set_static_string (value, "Adwaita");
      return TRUE;
    }
  if (g_str_equal (name, "gtk-enable-primary-paste"))
    {
      g_value_set_boolean (value, FALSE);
      return TRUE;
    }
  if (g_str_equal (name, "gtk-dialogs-use-header"))
    {
      g_value_set_boolean (value, TRUE);
      return TRUE;
    }
  if (g_str_equal (name, "gtk-overlay-scrolling"))
    {
      g_value_set_boolean (value, TRUE);
      return TRUE;
    }
  if (g_str_equal (name, "gtk-dnd-drag-threshold"))
    {
      g_value_set_int (value, 12);
      return TRUE;
    }

  return FALSE;
}

static void
gdk_uikit_display_dispose (GObject *object)
{
  GdkUIKitDisplay *self = GDK_UIKIT_DISPLAY (object);

  if (self->event_source)
    {
      g_source_destroy (self->event_source);
      g_clear_pointer (&self->event_source, g_source_unref);
    }
  if (self->monitors)
    g_list_store_remove_all (self->monitors);

  G_OBJECT_CLASS (gdk_uikit_display_parent_class)->dispose (object);
}

static void
gdk_uikit_display_finalize (GObject *object)
{
  GdkUIKitDisplay *self = GDK_UIKIT_DISPLAY (object);

  g_clear_object (&self->monitors);
  g_clear_object (&self->monitor);
  g_clear_object (&self->keymap);
  g_clear_object (&self->seat);
  g_clear_object (&self->core_pointer);
  g_clear_object (&self->core_keyboard);
  g_clear_object (&self->touchscreen);
  g_clear_pointer (&self->surfaces, g_hash_table_unref);
  g_list_free (self->toplevels);
  g_list_free (self->grab_popups);

  if (the_display == self)
    the_display = NULL;

  G_OBJECT_CLASS (gdk_uikit_display_parent_class)->finalize (object);
}

static void
gdk_uikit_display_class_init (GdkUIKitDisplayClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  GdkDisplayClass *display_class = GDK_DISPLAY_CLASS (klass);

  object_class->dispose = gdk_uikit_display_dispose;
  object_class->finalize = gdk_uikit_display_finalize;

  display_class->toplevel_type = GDK_TYPE_UIKIT_TOPLEVEL;
  display_class->popup_type = GDK_TYPE_UIKIT_POPUP;
  display_class->cairo_context_type = GDK_TYPE_UIKIT_CAIRO_CONTEXT;
  display_class->vk_context_type = G_TYPE_NONE;
  display_class->vk_extension_name = NULL;

  display_class->get_name = gdk_uikit_display_get_name;
  display_class->beep = gdk_uikit_display_beep;
  display_class->sync = gdk_uikit_display_noop;
  display_class->flush = gdk_uikit_display_noop;
  display_class->queue_events = gdk_uikit_display_noop;
  display_class->get_next_serial = gdk_uikit_display_get_next_serial;
  display_class->notify_startup_complete = gdk_uikit_display_notify_startup_complete;
  display_class->get_keymap = gdk_uikit_display_get_keymap;
  display_class->get_default_seat = gdk_uikit_display_get_default_seat;
  display_class->get_monitors = gdk_uikit_display_get_monitors;
  display_class->get_monitor_at_surface = gdk_uikit_display_get_monitor_at_surface;
  display_class->get_setting = gdk_uikit_display_get_setting;
  /* init_gl is left at the default, which reports that GL is unavailable;
   * GSK then renders with Cairo. */
}

static void
gdk_uikit_display_init (GdkUIKitDisplay *self)
{
  GdkDisplay *display = GDK_DISPLAY (self);

  self->surfaces = g_hash_table_new (NULL, NULL);
  self->next_surface_id = 1;
  self->scale = 1.0;

  gdk_display_set_composited (display, TRUE);
  gdk_display_set_rgba (display, TRUE);
  gdk_display_set_input_shapes (display, FALSE);
  gdk_display_set_shadow_width (display, FALSE);
}

static void
update_monitor (GdkUIKitDisplay *self)
{
  GdkRectangle *l = &self->layout;

  gdk_monitor_set_geometry (self->monitor, l);
  gdk_monitor_set_scale_factor (self->monitor, (int) ceil (self->scale));
  gdk_monitor_set_scale (self->monitor, self->scale);
  /* Roughly an iPhone's density; only used for physical size reports. */
  gdk_monitor_set_physical_size (self->monitor,
                                 (int) (l->width * 25.4 / 163.0),
                                 (int) (l->height * 25.4 / 163.0));
}

GdkDisplay *
_gdk_uikit_display_open (const char *display_name)
{
  GdkUIKitDisplay *self;
  GdkDisplay *display;
  GdkSeat *seat;

  if (the_display != NULL)
    return NULL;

  self = g_object_new (GDK_TYPE_UIKIT_DISPLAY, NULL);
  display = GDK_DISPLAY (self);
  the_display = self;

  gdk_uikit_ui_get_layout (&self->layout, &self->scale, &self->dark);

  self->monitor = g_object_new (GDK_TYPE_MONITOR, "display", display, NULL);
  gdk_monitor_set_manufacturer (self->monitor, "Apple");
  gdk_monitor_set_model (self->monitor, "iOS");
  gdk_monitor_set_connector (self->monitor, "screen");
  gdk_monitor_set_refresh_rate (self->monitor, 60000);
  update_monitor (self);
  self->monitors = g_list_store_new (GDK_TYPE_MONITOR);
  g_list_store_append (self->monitors, self->monitor);

  self->core_pointer = create_device (display, "Core Pointer", GDK_SOURCE_MOUSE, TRUE);
  self->core_keyboard = create_device (display, "Core Keyboard", GDK_SOURCE_KEYBOARD, FALSE);
  self->touchscreen = create_device (display, "Touchscreen", GDK_SOURCE_TOUCHSCREEN, FALSE);

  _gdk_device_set_associated_device (self->core_pointer, self->core_keyboard);
  _gdk_device_set_associated_device (self->core_keyboard, self->core_pointer);
  _gdk_device_set_associated_device (self->touchscreen, self->core_pointer);
  _gdk_device_add_physical_device (self->core_pointer, self->touchscreen);

  seat = _gdk_uikit_seat_new (display, self->core_pointer, self->core_keyboard);
  _gdk_uikit_seat_add_physical (seat, self->touchscreen);
  gdk_display_add_seat (display, seat);
  self->seat = seat;

  self->keymap = g_object_new (GDK_TYPE_UIKIT_KEYMAP, NULL);
  self->keymap->display = display;

  self->event_source = event_source_new (display);

  gdk_display_emit_opened (display);

  return display;
}

GdkUIKitDisplay *
_gdk_uikit_display_get (void)
{
  return the_display;
}

guint
_gdk_uikit_display_add_surface (GdkUIKitDisplay *self,
                                GdkSurface      *surface)
{
  guint id = self->next_surface_id++;

  g_hash_table_insert (self->surfaces, GUINT_TO_POINTER (id), surface);
  return id;
}

void
_gdk_uikit_display_remove_surface (GdkUIKitDisplay *self,
                                   guint            id)
{
  GdkSurface *surface = g_hash_table_lookup (self->surfaces, GUINT_TO_POINTER (id));

  if (surface == NULL)
    return;

  g_hash_table_remove (self->surfaces, GUINT_TO_POINTER (id));
  self->toplevels = g_list_remove (self->toplevels, surface);
  self->grab_popups = g_list_remove (self->grab_popups, surface);
  if (self->last_surface == surface)
    self->last_surface = NULL;
}

gulong
_gdk_uikit_display_next_serial (GdkUIKitDisplay *self)
{
  return ++self->serial;
}

static GdkSurface *
lookup_surface (GdkUIKitDisplay *self,
                guint            id)
{
  GdkSurface *surface = g_hash_table_lookup (self->surfaces, GUINT_TO_POINTER (id));

  if (surface == NULL || GDK_SURFACE_DESTROYED (surface))
    return NULL;
  return surface;
}

static GdkSurface *
front_toplevel (GdkUIKitDisplay *self)
{
  for (GList *l = g_list_last (self->toplevels); l; l = l->prev)
    {
      GdkSurface *s = l->data;
      if (GDK_SURFACE_IS_MAPPED (s) && !GDK_SURFACE_DESTROYED (s))
        return s;
    }
  return NULL;
}

GdkSurface *
_gdk_uikit_display_key_target (GdkUIKitDisplay *self)
{
  GList *last = g_list_last (self->grab_popups);

  if (last)
    return last->data;
  return front_toplevel (self);
}

void
_gdk_uikit_display_update_focus (GdkUIKitDisplay *self)
{
  GdkSurface *front = front_toplevel (self);

  for (GList *l = self->toplevels; l; l = l->next)
    {
      GdkSurface *s = l->data;
      gboolean focused = (s == front);
      gboolean was = (s->state & GDK_TOPLEVEL_STATE_FOCUSED) != 0;

      if (focused == was || GDK_SURFACE_DESTROYED (s))
        continue;

      gdk_synthesize_surface_state (s,
                                    focused ? 0 : GDK_TOPLEVEL_STATE_FOCUSED,
                                    focused ? GDK_TOPLEVEL_STATE_FOCUSED : 0);
      queue_event (self, gdk_focus_event_new (s, self->core_keyboard, focused));
    }
}

/**
 * gdk_uikit_display_set_keyboard_visible:
 * @display: a UIKit display
 * @visible: whether to show the on-screen keyboard
 */
void
gdk_uikit_display_set_keyboard_visible (GdkDisplay *display,
                                        gboolean    visible)
{
  g_return_if_fail (GDK_IS_UIKIT_DISPLAY (display));

  gdk_uikit_ui_show_keyboard (visible);
}

/* =================================== posts from the UIKit main thread == */

static void
post (GSourceFunc func,
      gpointer    data)
{
  /* Not g_main_context_invoke(): from the main thread that could acquire
   * the default context and run GDK code there before the GTK thread's
   * loop starts. An idle source always runs on the loop's own thread. */
  GSource *source = g_idle_source_new ();

  g_source_set_priority (source, GDK_PRIORITY_EVENTS);
  g_source_set_callback (source, func, data, g_free);
  g_source_set_static_name (source, "[gdk-uikit] input");
  g_source_attach (source, NULL);
  g_source_unref (source);
}

typedef struct
{
  guint surface_id;
  int kind;
  guint sequence_or_button;
  gboolean flag;
  GdkModifierType state;
  double x, y, dx, dy;
  guint32 time;
} InputMsg;

static void
note_position (GdkUIKitDisplay *self,
               GdkSurface      *surface,
               double           x,
               double           y,
               guint32          time)
{
  self->last_surface = surface;
  self->last_x = x;
  self->last_y = y;
  self->last_time = time;
}

static gboolean
handle_touch (gpointer data)
{
  InputMsg *m = data;
  GdkUIKitDisplay *self = the_display;
  GdkSurface *surface;
  GdkEventType type;
  GdkModifierType state = 0;

  if (!self || !(surface = lookup_surface (self, m->surface_id)))
    return G_SOURCE_REMOVE;

  switch (m->kind)
    {
    case GDK_UIKIT_TOUCH_BEGIN:
      type = GDK_TOUCH_BEGIN;
      state = GDK_BUTTON1_MASK;
      break;
    case GDK_UIKIT_TOUCH_UPDATE:
      type = GDK_TOUCH_UPDATE;
      state = GDK_BUTTON1_MASK;
      break;
    case GDK_UIKIT_TOUCH_END:
      type = GDK_TOUCH_END;
      break;
    default:
      type = GDK_TOUCH_CANCEL;
      break;
    }

  note_position (self, surface, m->x, m->y, m->time);
  queue_event (self, gdk_touch_event_new (type,
                                          GUINT_TO_POINTER (m->sequence_or_button),
                                          surface,
                                          self->core_pointer,
                                          m->time,
                                          state,
                                          m->x, m->y,
                                          NULL,
                                          m->flag));
  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_touch (guint              surface_id,
                      GdkUIKitTouchPhase phase,
                      guint              sequence,
                      gboolean           emulating,
                      double             x,
                      double             y,
                      guint32            time)
{
  InputMsg *m = g_new0 (InputMsg, 1);

  m->surface_id = surface_id;
  m->kind = phase;
  m->sequence_or_button = sequence;
  m->flag = emulating;
  m->x = x;
  m->y = y;
  m->time = time;
  post (handle_touch, m);
}

static GdkSurface *pointer_surface = NULL;

static gboolean
handle_pointer (gpointer data)
{
  InputMsg *m = data;
  GdkUIKitDisplay *self = the_display;
  GdkSurface *surface;

  if (!self || !(surface = lookup_surface (self, m->surface_id)))
    return G_SOURCE_REMOVE;

  if (pointer_surface != surface)
    {
      if (pointer_surface && g_hash_table_contains (self->surfaces, GUINT_TO_POINTER (GDK_UIKIT_SURFACE (pointer_surface)->id)))
        queue_event (self, gdk_crossing_event_new (GDK_LEAVE_NOTIFY, pointer_surface,
                                                   self->core_pointer, m->time, m->state,
                                                   m->x, m->y,
                                                   GDK_CROSSING_NORMAL, GDK_NOTIFY_NONLINEAR));
      pointer_surface = surface;
      queue_event (self, gdk_crossing_event_new (GDK_ENTER_NOTIFY, surface,
                                                 self->core_pointer, m->time, m->state,
                                                 m->x, m->y,
                                                 GDK_CROSSING_NORMAL, GDK_NOTIFY_NONLINEAR));
    }

  note_position (self, surface, m->x, m->y, m->time);

  switch (m->kind)
    {
    case GDK_UIKIT_POINTER_MOTION:
      queue_event (self, gdk_motion_event_new (surface, self->core_pointer, NULL,
                                               m->time, m->state, m->x, m->y, NULL));
      break;
    case GDK_UIKIT_POINTER_PRESS:
    case GDK_UIKIT_POINTER_RELEASE:
      queue_event (self, gdk_button_event_new (m->kind == GDK_UIKIT_POINTER_PRESS
                                                 ? GDK_BUTTON_PRESS : GDK_BUTTON_RELEASE,
                                               surface, self->core_pointer, NULL,
                                               m->time, m->state,
                                               m->sequence_or_button,
                                               m->x, m->y, NULL));
      break;
    default:
      break;
    }

  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_pointer (guint               surface_id,
                        GdkUIKitPointerKind kind,
                        guint               button,
                        GdkModifierType     state,
                        double              x,
                        double              y,
                        guint32             time)
{
  InputMsg *m = g_new0 (InputMsg, 1);

  m->surface_id = surface_id;
  m->kind = kind;
  m->sequence_or_button = button;
  m->state = state;
  m->x = x;
  m->y = y;
  m->time = time;
  post (handle_pointer, m);
}

static gboolean
handle_scroll (gpointer data)
{
  InputMsg *m = data;
  GdkUIKitDisplay *self = the_display;
  GdkSurface *surface;

  if (!self || !(surface = lookup_surface (self, m->surface_id)))
    return G_SOURCE_REMOVE;

  note_position (self, surface, m->x, m->y, m->time);
  queue_event (self, gdk_scroll_event_new (surface, self->core_pointer, NULL,
                                           m->time, m->state,
                                           m->dx, m->dy, m->flag,
                                           GDK_SCROLL_UNIT_SURFACE,
                                           GDK_SCROLL_RELATIVE_DIRECTION_UNKNOWN));
  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_scroll (guint           surface_id,
                       double          dx,
                       double          dy,
                       gboolean        is_stop,
                       GdkModifierType state,
                       double          x,
                       double          y,
                       guint32         time)
{
  InputMsg *m = g_new0 (InputMsg, 1);

  m->surface_id = surface_id;
  m->dx = dx;
  m->dy = dy;
  m->flag = is_stop;
  m->state = state;
  m->x = x;
  m->y = y;
  m->time = time;
  post (handle_scroll, m);
}

static gboolean
is_modifier_keyval (guint keyval)
{
  switch (keyval)
    {
    case GDK_KEY_Shift_L: case GDK_KEY_Shift_R:
    case GDK_KEY_Control_L: case GDK_KEY_Control_R:
    case GDK_KEY_Alt_L: case GDK_KEY_Alt_R:
    case GDK_KEY_Meta_L: case GDK_KEY_Meta_R:
    case GDK_KEY_Super_L: case GDK_KEY_Super_R:
    case GDK_KEY_Caps_Lock:
      return TRUE;
    default:
      return FALSE;
    }
}

static void
send_key (GdkUIKitDisplay *self,
          guint            keyval,
          GdkModifierType  state,
          gboolean         press,
          guint32          time)
{
  GdkSurface *surface = _gdk_uikit_display_key_target (self);
  GdkTranslatedKey translated = { keyval, 0, 0, 0 };

  if (surface == NULL)
    return;

  queue_event (self, gdk_key_event_new (press ? GDK_KEY_PRESS : GDK_KEY_RELEASE,
                                        surface,
                                        self->core_keyboard,
                                        time,
                                        keyval,
                                        state,
                                        is_modifier_keyval (keyval),
                                        &translated,
                                        &translated,
                                        NULL));
}

static gboolean
handle_key (gpointer data)
{
  InputMsg *m = data;

  if (the_display)
    send_key (the_display, m->sequence_or_button, m->state, m->flag, m->time);
  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_key (guint           keyval,
                    GdkModifierType state,
                    gboolean        press,
                    guint32         time)
{
  InputMsg *m = g_new0 (InputMsg, 1);

  m->sequence_or_button = keyval;
  m->state = state;
  m->flag = press;
  m->time = time;
  post (handle_key, m);
}

typedef struct
{
  guint32 time;
  char text[1];
} TextMsg;

static gboolean
handle_text (gpointer data)
{
  TextMsg *m = data;
  GdkUIKitDisplay *self = the_display;
  const char *p;

  if (!self)
    return G_SOURCE_REMOVE;

  for (p = m->text; *p; p = g_utf8_next_char (p))
    {
      gunichar c = g_utf8_get_char (p);
      guint keyval;

      if (c == '\n' || c == '\r')
        keyval = GDK_KEY_Return;
      else if (c == '\t')
        keyval = GDK_KEY_Tab;
      else if (c == '\b')
        keyval = GDK_KEY_BackSpace;
      else
        keyval = gdk_unicode_to_keyval (c);

      send_key (self, keyval, 0, TRUE, m->time);
      send_key (self, keyval, 0, FALSE, m->time);
    }

  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_text (const char *utf8,
                     guint32     time)
{
  gsize len = strlen (utf8);
  TextMsg *m = g_malloc0 (sizeof (TextMsg) + len);

  m->time = time;
  memcpy (m->text, utf8, len);
  post (handle_text, m);
}

typedef struct
{
  GdkRectangle layout;
  double scale;
  gboolean dark;
} LayoutMsg;

static gboolean
handle_layout (gpointer data)
{
  LayoutMsg *m = data;
  GdkUIKitDisplay *self = the_display;
  gboolean dark_changed;

  if (!self)
    return G_SOURCE_REMOVE;

  dark_changed = self->dark != m->dark;
  self->layout = m->layout;
  self->scale = m->scale;
  self->dark = m->dark;
  update_monitor (self);

  if (dark_changed)
    {
      gdk_display_setting_changed (GDK_DISPLAY (self), "gtk-interface-color-scheme");
      gdk_display_setting_changed (GDK_DISPLAY (self), "gtk-application-prefer-dark-theme");
    }

  _gdk_uikit_surface_relayout_all (self);
  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_layout (int      x,
                       int      y,
                       int      width,
                       int      height,
                       double   scale,
                       gboolean dark)
{
  LayoutMsg *m = g_new0 (LayoutMsg, 1);

  m->layout = (GdkRectangle) { x, y, width, height };
  m->scale = scale;
  m->dark = dark;
  post (handle_layout, m);
}

static gboolean
handle_dismiss_popups (gpointer data)
{
  GdkUIKitDisplay *self = the_display;
  GList *popups;

  if (!self)
    return G_SOURCE_REMOVE;

  /* Hiding a popup removes it from grab_popups; work on a copy. */
  popups = g_list_copy (self->grab_popups);
  for (GList *l = g_list_last (popups); l; l = l->prev)
    gdk_surface_hide (l->data);
  g_list_free (popups);

  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_dismiss_popups (void)
{
  post (handle_dismiss_popups, g_new0 (char, 1));
}

static gboolean
handle_close_front (gpointer data)
{
  GdkUIKitDisplay *self = the_display;
  GdkSurface *front;

  if (!self || !(front = front_toplevel (self)))
    return G_SOURCE_REMOVE;

  queue_event (self, gdk_delete_event_new (front));
  return G_SOURCE_REMOVE;
}

void
gdk_uikit_post_close_front (void)
{
  post (handle_close_front, g_new0 (char, 1));
}
