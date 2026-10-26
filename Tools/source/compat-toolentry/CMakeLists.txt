# iNux: stand-in qt_main_wrapper for tools inside library builds (see .c).
cmake_minimum_required(VERSION 3.21)
project(InuxToolEntry LANGUAGES C)
add_library(InuxToolEntry STATIC inux_tool_entry.c)
install(TARGETS InuxToolEntry ARCHIVE DESTINATION lib)
