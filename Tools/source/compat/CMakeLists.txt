# iNux compat libraries: stand-ins for pieces iOS lacks.
#   InuxCompat - QProcess for Qt builds without process support
#   intl       - libintl without translation catalogs
cmake_minimum_required(VERSION 3.21)
project(InuxCompat LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 REQUIRED COMPONENTS Core)

add_library(InuxCompat STATIC inux_qprocess.cpp inux_qprocess.h)
target_link_libraries(InuxCompat PUBLIC Qt6::Core)

add_library(intl STATIC libintl.c)

install(TARGETS InuxCompat intl ARCHIVE DESTINATION lib)
install(FILES inux_qprocess.h DESTINATION include/inux-compat)
install(FILES libintl.h DESTINATION include)
