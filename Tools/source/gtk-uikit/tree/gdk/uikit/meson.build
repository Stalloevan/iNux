# UIKit (iOS) backend. Added by iNux; see gdkuikit-private.h for the
# threading model and gdkuikitentry.m for how apps start.

add_languages('objc', native: false, required: true)

gdk_uikit_sources = files([
  'gdkuikitdisplay.c',
  'gdkuikitsurface.c',
  'gdkuikitui.m',
  'gdkuikitentry.m',
])

gdk_uikit_public_headers = files([
  'gdkuikit.h',
])

install_headers(gdk_uikit_public_headers, subdir: 'gtk-4.0/gdk/uikit/')

gdk_uikit_frameworks = [
  'UIKit',
  'QuartzCore',
  'CoreGraphics',
  'CoreFoundation',
  'Foundation',
]

gdk_uikit_deps = [
  dependency('appleframeworks', modules: gdk_uikit_frameworks),
]

libgdk_uikit = static_library('gdk-uikit',
  sources: [ gdk_uikit_sources, gdk_gen_headers ],
  include_directories: [ confinc, gdkinc, ],
  c_args: [ libgdk_c_args, common_cflags, ],
  objc_args: [ libgdk_c_args, '-fobjc-arc', '-Wno-deprecated-declarations' ],
  dependencies: [ gdk_deps, gdk_uikit_deps, ],
  gnu_symbol_visibility: symbol_visibility,
)
