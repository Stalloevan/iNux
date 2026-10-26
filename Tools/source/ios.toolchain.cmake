# Minimal CMake toolchain for iOS device (arm64), used by iNux source builds.
# Deliberately small; set INUX_IOS_SYSROOT / deployment target from the driver.
set(CMAKE_SYSTEM_NAME iOS)
set(CMAKE_SYSTEM_PROCESSOR arm64)
set(CMAKE_OSX_ARCHITECTURES arm64 CACHE STRING "")

if(NOT DEFINED CMAKE_OSX_DEPLOYMENT_TARGET)
  set(CMAKE_OSX_DEPLOYMENT_TARGET "16.0" CACHE STRING "")
endif()

execute_process(COMMAND xcrun --sdk iphoneos --show-sdk-path
                OUTPUT_VARIABLE _sdk OUTPUT_STRIP_TRAILING_WHITESPACE)
set(CMAKE_OSX_SYSROOT "${_sdk}" CACHE STRING "")

execute_process(COMMAND xcrun --sdk iphoneos --find clang
                OUTPUT_VARIABLE _cc OUTPUT_STRIP_TRAILING_WHITESPACE)
execute_process(COMMAND xcrun --sdk iphoneos --find clang++
                OUTPUT_VARIABLE _cxx OUTPUT_STRIP_TRAILING_WHITESPACE)
set(CMAKE_C_COMPILER "${_cc}")
set(CMAKE_CXX_COMPILER "${_cxx}")

# No code signing at build time; the IPA is signed later by the user.
set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED NO)
set(CMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_REQUIRED NO)

# Static libraries: iOS forbids loading unsigned shared objects from the
# bundle, so every dependency is linked into the app.
set(BUILD_SHARED_LIBS OFF CACHE BOOL "")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM BOTH)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY BOTH)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)

# iOS has no fork/exec/system; tell probes these are absent so libraries
# take their "sandboxed platform" code paths where they have one.
set(HAVE_FORK 0 CACHE INTERNAL "")
set(HAVE_SYSTEM 0 CACHE INTERNAL "")
