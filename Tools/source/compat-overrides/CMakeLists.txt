# iNux compat pieces installed just before the KDE Frameworks are built:
#   - include/inux-compat-override/{QProcess,qprocess.h}: put first in the
#     include path, so #include <QProcess> resolves to the stand-in;
#   - libInuxToolEntry.a: qt_main_wrapper for tools inside library builds.
cmake_minimum_required(VERSION 3.21)
project(InuxCompatOverrides LANGUAGES C)
add_library(InuxToolEntry STATIC inux_tool_entry.c)
install(TARGETS InuxToolEntry ARCHIVE DESTINATION lib)
install(FILES override/QProcess override/qprocess.h DESTINATION include/inux-compat-override)
