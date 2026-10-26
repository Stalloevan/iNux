project('inux-gtk-selftest', 'c', version: '1.0')

# A small GTK 4 app that exercises what a port needs: a header bar, text
# entry (on-screen keyboard), buttons, a popover menu, a drop-down, a
# scrolled list (touch scrolling) and a dialog-style secondary window.
gtk = dependency('gtk4')

executable('inux-gtk-selftest', 'main.c',
  dependencies: gtk,
  install: true)

install_data('org.inux.GtkSelfTest.metainfo.xml', install_dir: get_option('datadir') / 'metainfo')
