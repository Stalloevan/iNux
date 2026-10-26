/* GDK - UIKit (iOS) backend: the UIKit side.
 *
 * Everything here runs on the main thread. Each GDK surface is a plain
 * UIView in the root view controller's view, positioned in points; its
 * layer shows the frames GTK renders (see gdkuikitsurface.c). Touches,
 * trackpad/mouse input, scrolling and keyboard input are turned into calls
 * to gdk_uikit_post_*, which hand them to the GTK thread.
 *
 * Copyright (C) 2026 iNux contributors
 * SPDX-License-Identifier: LGPL-2.1-or-later
 */
#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>

#include "gdkuikitbridge.h"
#include "gdkkeysyms.h"

enum { VIEW_TOPLEVEL = 0, VIEW_POPUP = 1, VIEW_AUTOHIDE_POPUP = 2 };

static UIView *root_view = nil;
static UIView *key_input = nil;
static CGFloat keyboard_overlap = 0;
/* Pasteboard change count after our own last write, so the change
 * notification for it is not mistaken for another app's copy. */
static NSInteger own_pasteboard_change = -1;
/* Autohide popups currently shown, so a touch elsewhere can dismiss them. */
static NSHashTable<UIView *> *open_popups = nil;

static guint32
event_time (NSTimeInterval timestamp)
{
  return (guint32) (timestamp * 1000.0);
}

guint32
gdk_uikit_ui_time_ms (void)
{
  return event_time (CACurrentMediaTime ());
}

static GdkModifierType
modifiers_from_flags (UIKeyModifierFlags flags)
{
  GdkModifierType state = 0;

  if (flags & UIKeyModifierShift)
    state |= GDK_SHIFT_MASK;
  if (flags & UIKeyModifierAlphaShift)
    state |= GDK_LOCK_MASK;
  /* GTK's <Primary> is Control outside macOS; map Command onto it so the
   * usual iPad shortcuts (Cmd-C, Cmd-V, ...) work. */
  if (flags & (UIKeyModifierControl | UIKeyModifierCommand))
    state |= GDK_CONTROL_MASK;
  if (flags & UIKeyModifierAlternate)
    state |= GDK_ALT_MASK;

  return state;
}

/* ------------------------------------------------------------ layout -- */

static void
compute_layout (GdkRectangle *out, double *scale, gboolean *dark)
{
  CGRect bounds = root_view ? root_view.bounds : UIScreen.mainScreen.bounds;
  UIEdgeInsets insets = root_view ? root_view.safeAreaInsets : UIEdgeInsetsZero;
  CGFloat top = insets.top;
  CGFloat left = insets.left;
  CGFloat right = bounds.size.width - insets.right;
  CGFloat bottom = bounds.size.height - MAX (insets.bottom, keyboard_overlap);

  out->x = (int) ceil (left);
  out->y = (int) ceil (top);
  out->width = MAX (1, (int) floor (right - left));
  out->height = MAX (1, (int) floor (bottom - top));

  *scale = root_view.window.screen.scale ?: UIScreen.mainScreen.scale;
  *dark = root_view.traitCollection.userInterfaceStyle == UIUserInterfaceStyleDark;
}

static void
post_layout (void)
{
  GdkRectangle r;
  double scale;
  gboolean dark;

  compute_layout (&r, &scale, &dark);
  gdk_uikit_post_layout (r.x, r.y, r.width, r.height, scale, dark);
}

void
gdk_uikit_ui_get_layout (GdkRectangle *layout,
                         double       *scale,
                         gboolean     *dark)
{
  if ([NSThread isMainThread])
    {
      compute_layout (layout, scale, dark);
      return;
    }

  dispatch_sync (dispatch_get_main_queue (), ^{
    [root_view layoutIfNeeded];
    compute_layout (layout, scale, dark);
  });
}

/* ------------------------------------------------------------- keys -- */

static guint
keyval_for_usage (UIKeyboardHIDUsage usage)
{
  switch (usage)
    {
    case UIKeyboardHIDUsageKeyboardReturnOrEnter: return GDK_KEY_Return;
    case UIKeyboardHIDUsageKeypadEnter:           return GDK_KEY_KP_Enter;
    case UIKeyboardHIDUsageKeyboardEscape:        return GDK_KEY_Escape;
    case UIKeyboardHIDUsageKeyboardDeleteOrBackspace: return GDK_KEY_BackSpace;
    case UIKeyboardHIDUsageKeyboardDeleteForward: return GDK_KEY_Delete;
    case UIKeyboardHIDUsageKeyboardTab:           return GDK_KEY_Tab;
    case UIKeyboardHIDUsageKeyboardRightArrow:    return GDK_KEY_Right;
    case UIKeyboardHIDUsageKeyboardLeftArrow:     return GDK_KEY_Left;
    case UIKeyboardHIDUsageKeyboardDownArrow:     return GDK_KEY_Down;
    case UIKeyboardHIDUsageKeyboardUpArrow:       return GDK_KEY_Up;
    case UIKeyboardHIDUsageKeyboardHome:          return GDK_KEY_Home;
    case UIKeyboardHIDUsageKeyboardEnd:           return GDK_KEY_End;
    case UIKeyboardHIDUsageKeyboardPageUp:        return GDK_KEY_Page_Up;
    case UIKeyboardHIDUsageKeyboardPageDown:      return GDK_KEY_Page_Down;
    case UIKeyboardHIDUsageKeyboardInsert:        return GDK_KEY_Insert;
    case UIKeyboardHIDUsageKeyboardF1:  return GDK_KEY_F1;
    case UIKeyboardHIDUsageKeyboardF2:  return GDK_KEY_F2;
    case UIKeyboardHIDUsageKeyboardF3:  return GDK_KEY_F3;
    case UIKeyboardHIDUsageKeyboardF4:  return GDK_KEY_F4;
    case UIKeyboardHIDUsageKeyboardF5:  return GDK_KEY_F5;
    case UIKeyboardHIDUsageKeyboardF6:  return GDK_KEY_F6;
    case UIKeyboardHIDUsageKeyboardF7:  return GDK_KEY_F7;
    case UIKeyboardHIDUsageKeyboardF8:  return GDK_KEY_F8;
    case UIKeyboardHIDUsageKeyboardF9:  return GDK_KEY_F9;
    case UIKeyboardHIDUsageKeyboardF10: return GDK_KEY_F10;
    case UIKeyboardHIDUsageKeyboardF11: return GDK_KEY_F11;
    case UIKeyboardHIDUsageKeyboardF12: return GDK_KEY_F12;
    case UIKeyboardHIDUsageKeyboardLeftShift:   return GDK_KEY_Shift_L;
    case UIKeyboardHIDUsageKeyboardRightShift:  return GDK_KEY_Shift_R;
    case UIKeyboardHIDUsageKeyboardLeftControl: return GDK_KEY_Control_L;
    case UIKeyboardHIDUsageKeyboardRightControl: return GDK_KEY_Control_R;
    case UIKeyboardHIDUsageKeyboardLeftAlt:     return GDK_KEY_Alt_L;
    case UIKeyboardHIDUsageKeyboardRightAlt:    return GDK_KEY_Alt_R;
    case UIKeyboardHIDUsageKeyboardLeftGUI:     return GDK_KEY_Meta_L;
    case UIKeyboardHIDUsageKeyboardRightGUI:    return GDK_KEY_Meta_R;
    default: return 0;
    }
}

/* Translates a hardware key. @text_handled is set when the key is plain
 * text that a focused text field receives through insertText: instead. */
static guint
keyval_for_key (UIKey *key, gboolean text_field_focused, gboolean *text_handled)
{
  guint keyval = keyval_for_usage (key.keyCode);
  UIKeyModifierFlags shortcut = UIKeyModifierControl | UIKeyModifierCommand | UIKeyModifierAlternate;
  NSString *chars;

  *text_handled = FALSE;
  if (keyval)
    return keyval;

  if (key.modifierFlags & shortcut)
    chars = key.charactersIgnoringModifiers;
  else
    {
      chars = key.characters;
      if (text_field_focused && chars.length > 0)
        {
          *text_handled = TRUE;
          return 0;
        }
    }

  if (chars.length == 0)
    return 0;

  return gdk_unicode_to_keyval ([chars characterAtIndex:0]);
}

static BOOL
handle_presses (NSSet<UIPress *> *presses, gboolean press, gboolean text_field_focused)
{
  BOOL handled_all = YES;

  for (UIPress *p in presses)
    {
      gboolean text_handled;
      guint keyval;

      if (p.key == nil)
        {
          handled_all = NO;
          continue;
        }

      keyval = keyval_for_key (p.key, text_field_focused, &text_handled);
      if (text_handled || keyval == 0)
        {
          handled_all = NO;
          continue;
        }

      gdk_uikit_post_key (keyval, modifiers_from_flags (p.key.modifierFlags),
                          press, event_time (p.timestamp));
    }

  return handled_all;
}

/* A hidden view that owns the on-screen keyboard while a GTK text widget
 * has focus. Typed text becomes key events (see handle_text in
 * gdkuikitdisplay.c), which GTK's input method turns back into text. */
@interface GdkUIKitKeyInput : UIView <UIKeyInput>
@property (nonatomic) UITextAutocorrectionType autocorrectionType;
@property (nonatomic) UITextAutocapitalizationType autocapitalizationType;
@property (nonatomic) UITextSpellCheckingType spellCheckingType;
@property (nonatomic) UITextSmartQuotesType smartQuotesType;
@property (nonatomic) UITextSmartDashesType smartDashesType;
@property (nonatomic) UITextSmartInsertDeleteType smartInsertDeleteType;
@property (nonatomic) UIKeyboardType keyboardType;
@property (nonatomic) UIKeyboardAppearance keyboardAppearance;
@property (nonatomic) UIReturnKeyType returnKeyType;
@end

@implementation GdkUIKitKeyInput

- (instancetype)initWithFrame:(CGRect)frame
{
  if ((self = [super initWithFrame:frame]))
    {
      _autocorrectionType = UITextAutocorrectionTypeNo;
      _autocapitalizationType = UITextAutocapitalizationTypeNone;
      _spellCheckingType = UITextSpellCheckingTypeNo;
      _smartQuotesType = UITextSmartQuotesTypeNo;
      _smartDashesType = UITextSmartDashesTypeNo;
      _smartInsertDeleteType = UITextSmartInsertDeleteTypeNo;
      _keyboardType = UIKeyboardTypeDefault;
      _keyboardAppearance = UIKeyboardAppearanceDefault;
      _returnKeyType = UIReturnKeyDefault;
    }
  return self;
}

- (BOOL)canBecomeFirstResponder { return YES; }
- (BOOL)hasText { return YES; }

- (void)insertText:(NSString *)text
{
  gdk_uikit_post_text (text.UTF8String, gdk_uikit_ui_time_ms ());
}

- (void)deleteBackward
{
  guint32 t = gdk_uikit_ui_time_ms ();

  gdk_uikit_post_key (GDK_KEY_BackSpace, 0, TRUE, t);
  gdk_uikit_post_key (GDK_KEY_BackSpace, 0, FALSE, t);
}

- (void)pressesBegan:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event
{
  if (!handle_presses (presses, TRUE, TRUE))
    [super pressesBegan:presses withEvent:event];
}

- (void)pressesEnded:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event
{
  if (!handle_presses (presses, FALSE, TRUE))
    [super pressesEnded:presses withEvent:event];
}

@end

/* ------------------------------------------------------ surface views -- */

@interface GdkUIKitSurfaceView : UIView
@property (nonatomic) guint surfaceId;
@property (nonatomic) int kind;
@end

@implementation GdkUIKitSurfaceView
{
  NSMutableDictionary<NSValue *, NSNumber *> *_touches;
  NSMutableSet<NSValue *> *_ignored;
  guint _nextSequence;
  BOOL _pointerDown;
}

- (instancetype)initWithFrame:(CGRect)frame
{
  if ((self = [super initWithFrame:frame]))
    {
      _touches = [NSMutableDictionary new];
      _ignored = [NSMutableSet new];
      _nextSequence = 1;
      self.multipleTouchEnabled = YES;
      self.opaque = NO;
      self.backgroundColor = UIColor.clearColor;
      self.layer.contentsGravity = kCAGravityTopLeft;
      self.layer.masksToBounds = YES;

      UIHoverGestureRecognizer *hover =
        [[UIHoverGestureRecognizer alloc] initWithTarget:self action:@selector(hover:)];
      [self addGestureRecognizer:hover];

      /* Trackpad and mouse-wheel scrolling only; finger drags arrive as
       * touches, which GTK scrolls kinetically itself. */
      UIPanGestureRecognizer *scroll =
        [[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(scroll:)];
      scroll.allowedScrollTypesMask = UIScrollTypeMaskAll;
      scroll.allowedTouchTypes = @[];
      [self addGestureRecognizer:scroll];
    }
  return self;
}

- (void)hover:(UIHoverGestureRecognizer *)g
{
  CGPoint p = [g locationInView:self];

  if (g.state == UIGestureRecognizerStateBegan || g.state == UIGestureRecognizerStateChanged)
    gdk_uikit_post_pointer (self.surfaceId, GDK_UIKIT_POINTER_MOTION, 0,
                            modifiers_from_flags (g.modifierFlags),
                            p.x, p.y, gdk_uikit_ui_time_ms ());
}

- (void)scroll:(UIPanGestureRecognizer *)g
{
  CGPoint p = [g locationInView:self];
  CGPoint t = [g translationInView:self];
  gboolean stop = g.state == UIGestureRecognizerStateEnded ||
                  g.state == UIGestureRecognizerStateCancelled;

  [g setTranslation:CGPointZero inView:self];
  gdk_uikit_post_scroll (self.surfaceId, -t.x, -t.y, stop,
                         modifiers_from_flags (g.modifierFlags),
                         p.x, p.y, gdk_uikit_ui_time_ms ());
}

static BOOL
is_pointer (UITouch *touch)
{
  return touch.type == UITouchTypeIndirectPointer;
}

- (void)forwardTouches:(NSSet<UITouch *> *)touches
                 event:(UIEvent *)event
                 phase:(GdkUIKitTouchPhase)phase
{
  for (UITouch *touch in touches)
    {
      NSValue *key = [NSValue valueWithNonretainedObject:touch];
      CGPoint p = [touch locationInView:self];
      guint32 t = event_time (touch.timestamp);

      if (phase == GDK_UIKIT_TOUCH_BEGIN)
        {
          /* A touch outside the open menus closes them and goes no
           * further, as a click outside a popup does on the desktop. */
          if (self.kind == VIEW_TOPLEVEL && open_popups.count > 0)
            {
              [_ignored addObject:key];
              gdk_uikit_post_dismiss_popups ();
              continue;
            }
        }
      else if ([_ignored containsObject:key])
        {
          if (phase == GDK_UIKIT_TOUCH_END || phase == GDK_UIKIT_TOUCH_CANCEL)
            [_ignored removeObject:key];
          continue;
        }

      if (is_pointer (touch))
        {
          guint button = 1;
          GdkModifierType state = 0;

          if (@available (iOS 13.4, *))
            {
              if (event.buttonMask & UIEventButtonMaskSecondary)
                button = 3;
              state = modifiers_from_flags (event.modifierFlags);
            }

          switch (phase)
            {
            case GDK_UIKIT_TOUCH_BEGIN:
              _pointerDown = YES;
              gdk_uikit_post_pointer (self.surfaceId, GDK_UIKIT_POINTER_MOTION, 0, state, p.x, p.y, t);
              gdk_uikit_post_pointer (self.surfaceId, GDK_UIKIT_POINTER_PRESS, button, state, p.x, p.y, t);
              break;
            case GDK_UIKIT_TOUCH_UPDATE:
              gdk_uikit_post_pointer (self.surfaceId, GDK_UIKIT_POINTER_MOTION, 0,
                                      state | GDK_BUTTON1_MASK, p.x, p.y, t);
              break;
            default:
              if (_pointerDown)
                gdk_uikit_post_pointer (self.surfaceId, GDK_UIKIT_POINTER_RELEASE, button,
                                        state | GDK_BUTTON1_MASK, p.x, p.y, t);
              _pointerDown = NO;
              break;
            }
          continue;
        }

      NSNumber *seq = _touches[key];
      if (phase == GDK_UIKIT_TOUCH_BEGIN || seq == nil)
        {
          if (phase != GDK_UIKIT_TOUCH_BEGIN)
            continue;
          seq = @(_nextSequence++);
          if (_nextSequence == 0)
            _nextSequence = 1;
          _touches[key] = seq;
        }

      /* The first finger also drives pointer emulation. */
      gboolean emulating = _touches.count == 1;

      gdk_uikit_post_touch (self.surfaceId, phase, seq.unsignedIntValue, emulating, p.x, p.y, t);

      if (phase == GDK_UIKIT_TOUCH_END || phase == GDK_UIKIT_TOUCH_CANCEL)
        [_touches removeObjectForKey:key];
    }
}

- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event
{
  [self forwardTouches:touches event:event phase:GDK_UIKIT_TOUCH_BEGIN];
}

- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event
{
  [self forwardTouches:touches event:event phase:GDK_UIKIT_TOUCH_UPDATE];
}

- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event
{
  [self forwardTouches:touches event:event phase:GDK_UIKIT_TOUCH_END];
}

- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event
{
  [self forwardTouches:touches event:event phase:GDK_UIKIT_TOUCH_CANCEL];
}

@end

/* ---------------------------------------------------- root controller -- */

@interface GdkUIKitRootController : UIViewController
@end

@implementation GdkUIKitRootController

- (void)loadView
{
  UIView *view = [[UIView alloc] initWithFrame:UIScreen.mainScreen.bounds];

  view.backgroundColor = UIColor.systemBackgroundColor;
  view.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
  self.view = view;
  root_view = view;

  key_input = [[GdkUIKitKeyInput alloc] initWithFrame:CGRectMake (-10, -10, 1, 1)];
  key_input.hidden = NO;
  key_input.alpha = 0.0;
  [view addSubview:key_input];

  open_popups = [NSHashTable weakObjectsHashTable];

  [[NSNotificationCenter defaultCenter] addObserver:self
                                           selector:@selector(keyboardWillChange:)
                                               name:UIKeyboardWillChangeFrameNotification
                                             object:nil];
  [[NSNotificationCenter defaultCenter] addObserver:self
                                           selector:@selector(keyboardWillHide:)
                                               name:UIKeyboardWillHideNotification
                                             object:nil];
  [[NSNotificationCenter defaultCenter] addObserver:self
                                           selector:@selector(pasteboardMayHaveChanged:)
                                               name:UIPasteboardChangedNotification
                                             object:nil];
  [[NSNotificationCenter defaultCenter] addObserver:self
                                           selector:@selector(pasteboardMayHaveChanged:)
                                               name:UIApplicationDidBecomeActiveNotification
                                             object:nil];
}

- (void)pasteboardMayHaveChanged:(NSNotification *)note
{
  UIPasteboard *pb = UIPasteboard.generalPasteboard;

  if (pb.changeCount == own_pasteboard_change)
    return;
  own_pasteboard_change = pb.changeCount;
  /* hasStrings does not trigger the paste permission prompt. */
  gdk_uikit_post_pasteboard_changed (pb.hasStrings);
}

- (BOOL)canBecomeFirstResponder { return YES; }

- (void)viewDidAppear:(BOOL)animated
{
  [super viewDidAppear:animated];
  [self becomeFirstResponder];
}

- (void)viewDidLayoutSubviews
{
  [super viewDidLayoutSubviews];
  post_layout ();
}

- (void)viewSafeAreaInsetsDidChange
{
  [super viewSafeAreaInsetsDidChange];
  post_layout ();
}

- (void)traitCollectionDidChange:(UITraitCollection *)previous
{
  [super traitCollectionDidChange:previous];
  post_layout ();
}

- (void)keyboardWillChange:(NSNotification *)note
{
  CGRect end = [note.userInfo[UIKeyboardFrameEndUserInfoKey] CGRectValue];
  CGRect local = [self.view convertRect:end fromCoordinateSpace:UIScreen.mainScreen.coordinateSpace];
  CGFloat overlap = CGRectGetMaxY (self.view.bounds) - CGRectGetMinY (local);

  if (CGRectIsNull (local) || CGRectGetMinY (local) >= CGRectGetMaxY (self.view.bounds))
    overlap = 0;
  keyboard_overlap = MAX (0, overlap);
  post_layout ();
}

- (void)keyboardWillHide:(NSNotification *)note
{
  keyboard_overlap = 0;
  post_layout ();
}

- (void)pressesBegan:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event
{
  if (!handle_presses (presses, TRUE, FALSE))
    [super pressesBegan:presses withEvent:event];
}

- (void)pressesEnded:(NSSet<UIPress *> *)presses withEvent:(UIPressesEvent *)event
{
  if (!handle_presses (presses, FALSE, FALSE))
    [super pressesEnded:presses withEvent:event];
}

@end

UIViewController *gdk_uikit_ui_create_root_controller (void);

UIViewController *
gdk_uikit_ui_create_root_controller (void)
{
  return [GdkUIKitRootController new];
}

/* ------------------------------------------- calls from the GTK thread -- */

static void
on_main (dispatch_block_t block)
{
  if ([NSThread isMainThread])
    block ();
  else
    dispatch_async (dispatch_get_main_queue (), block);
}

gpointer
gdk_uikit_ui_create_view (guint surface_id,
                          int   kind)
{
  __block GdkUIKitSurfaceView *view = nil;
  dispatch_block_t create = ^{
    view = [[GdkUIKitSurfaceView alloc] initWithFrame:CGRectMake (0, 0, 1, 1)];
    view.surfaceId = surface_id;
    view.kind = kind;
    view.hidden = YES;
    [root_view addSubview:view];
  };

  if ([NSThread isMainThread])
    create ();
  else
    dispatch_sync (dispatch_get_main_queue (), create);

  return (gpointer) CFBridgingRetain (view);
}

void
gdk_uikit_ui_destroy_view (gpointer handle)
{
  UIView *view = CFBridgingRelease (handle);

  on_main (^{
    [open_popups removeObject:view];
    [view removeFromSuperview];
  });
}

void
gdk_uikit_ui_set_frame (gpointer handle,
                        int      x,
                        int      y,
                        int      width,
                        int      height)
{
  UIView *view = (__bridge UIView *) handle;

  on_main (^{
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    view.frame = CGRectMake (x, y, width, height);
    [CATransaction commit];
  });
}

void
gdk_uikit_ui_set_visible (gpointer handle,
                          gboolean visible)
{
  GdkUIKitSurfaceView *view = (__bridge GdkUIKitSurfaceView *) handle;

  on_main (^{
    view.hidden = !visible;
    if (view.kind == VIEW_AUTOHIDE_POPUP)
      {
        if (visible)
          [open_popups addObject:view];
        else
          [open_popups removeObject:view];
      }
  });
}

void
gdk_uikit_ui_raise (gpointer handle)
{
  UIView *view = (__bridge UIView *) handle;

  on_main (^{
    [view.superview bringSubviewToFront:view];
  });
}

static void
release_pixels (void *info, const void *data, size_t size)
{
  g_free ((gpointer) data);
}

void
gdk_uikit_ui_set_contents (gpointer handle,
                           guchar  *pixels,
                           int      width,
                           int      height,
                           int      stride,
                           double   scale)
{
  UIView *view = (__bridge UIView *) handle;
  CGDataProviderRef provider;
  CGColorSpaceRef space;
  CGImageRef image;

  provider = CGDataProviderCreateWithData (NULL, pixels, (size_t) stride * height, release_pixels);
  space = CGColorSpaceCreateWithName (kCGColorSpaceSRGB);
  image = CGImageCreate (width, height, 8, 32, stride, space,
                         kCGBitmapByteOrder32Little | kCGImageAlphaPremultipliedFirst,
                         provider, NULL, false, kCGRenderingIntentDefault);
  CGColorSpaceRelease (space);
  CGDataProviderRelease (provider);

  if (image == NULL)
    return;

  on_main (^{
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    view.layer.contentsScale = scale;
    view.layer.contents = (__bridge id) image;
    [CATransaction commit];
    CGImageRelease (image);
  });
}

void
gdk_uikit_ui_show_keyboard (gboolean show)
{
  on_main (^{
    if (show)
      {
        ((GdkUIKitKeyInput *) key_input).keyboardAppearance =
          root_view.traitCollection.userInterfaceStyle == UIUserInterfaceStyleDark
            ? UIKeyboardAppearanceDark : UIKeyboardAppearanceLight;
        [key_input becomeFirstResponder];
      }
    else if (key_input.isFirstResponder)
      {
        [key_input resignFirstResponder];
        [root_view.window.rootViewController becomeFirstResponder];
      }
  });
}

void
gdk_uikit_ui_beep (void)
{
  on_main (^{
    UINotificationFeedbackGenerator *g = [UINotificationFeedbackGenerator new];
    [g notificationOccurred:UINotificationFeedbackTypeWarning];
  });
}

void
gdk_uikit_ui_open_uri (const char *uri)
{
  NSURL *url = [NSURL URLWithString:@(uri)];

  if (url == nil)
    return;
  on_main (^{
    [UIApplication.sharedApplication openURL:url options:@{} completionHandler:nil];
  });
}

void
gdk_uikit_ui_set_clipboard_text (const char *utf8)
{
  NSString *text = @(utf8);

  on_main (^{
    UIPasteboard.generalPasteboard.string = text;
    own_pasteboard_change = UIPasteboard.generalPasteboard.changeCount;
  });
}

gboolean
gdk_uikit_ui_pasteboard_has_text (void)
{
  __block gboolean has = FALSE;
  dispatch_block_t check = ^{
    has = UIPasteboard.generalPasteboard.hasStrings;
    own_pasteboard_change = UIPasteboard.generalPasteboard.changeCount;
  };

  if ([NSThread isMainThread])
    check ();
  else
    dispatch_sync (dispatch_get_main_queue (), check);

  return has;
}

char *
gdk_uikit_ui_get_clipboard_text (void)
{
  __block char *result = NULL;
  dispatch_block_t read = ^{
    NSString *s = UIPasteboard.generalPasteboard.string;
    result = s ? g_strdup (s.UTF8String) : NULL;
  };

  if ([NSThread isMainThread])
    read ();
  else
    dispatch_sync (dispatch_get_main_queue (), read);

  return result;
}
