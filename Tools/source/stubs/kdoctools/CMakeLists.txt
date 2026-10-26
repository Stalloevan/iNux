# iNux stand-in for KF6DocTools on iOS.
#
# KDocTools turns DocBook handbooks into help files using host programs
# (meinproc6) and libxml2/libxslt. An iOS app has no KDE help viewer, so
# this package provides the same CMake API (find_package(KF6DocTools),
# kdoctools_create_handbook, kdoctools_create_manpage, kdoctools_install)
# with functions that do nothing, letting apps build without their docs.
cmake_minimum_required(VERSION 3.21)
project(INuxKDocToolsStub VERSION 6.31.0 LANGUAGES NONE)

set(KF_VERSION "${INUX_KF_VERSION}")
if(NOT KF_VERSION)
    set(KF_VERSION "${PROJECT_VERSION}")
endif()

include(CMakePackageConfigHelpers)
set(cfgdir "${CMAKE_INSTALL_PREFIX}/lib/cmake/KF6DocTools")
file(WRITE "${CMAKE_BINARY_DIR}/KF6DocToolsConfig.cmake" [=[
# iNux: KF6DocTools stand-in (documentation is not built for iOS).
set(KF6DocTools_FOUND TRUE)
set(KDocTools_FOUND TRUE)
function(kdoctools_create_handbook docbook)
endfunction()
function(kdoctools_create_manpage docbook section)
endfunction()
function(kdoctools_install podir)
endfunction()
function(kdoctools_encode_uri _var)
endfunction()
if(NOT TARGET KF6::DocTools)
    add_library(KF6::DocTools INTERFACE IMPORTED)
endif()
]=])
write_basic_package_version_file("${CMAKE_BINARY_DIR}/KF6DocToolsConfigVersion.cmake"
    VERSION "${KF_VERSION}" COMPATIBILITY AnyNewerVersion ARCH_INDEPENDENT)
install(FILES "${CMAKE_BINARY_DIR}/KF6DocToolsConfig.cmake"
              "${CMAKE_BINARY_DIR}/KF6DocToolsConfigVersion.cmake"
        DESTINATION "${cfgdir}")
