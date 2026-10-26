# iNux iOS app adapter.
#
# Injected into the app module's configure with
#   -DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=<this file>
# so a KDE/Qt app's unmodified CMake produces an iOS app bundle. After the
# whole project has been read, it finds the app's executable(s) and:
#   - makes them MACOSX_BUNDLE apps with a bundle id and display name,
#   - adds the iNux bootstrap (bundled data -> Application Support links),
#   - links the iNux compat libraries,
#   - runs qt_finalize_target, which on iOS sets Qt's UIKit entry point
#     (qt_main_wrapper), the Info.plist and launch screen, and imports the
#     static Qt plugins and QML modules the app uses.
#
# Inputs (cache variables): INUX_APP_TARGET (preferred executable name),
# INUX_BUNDLE_ID, INUX_DISPLAY_NAME, INUX_COMPAT_PREFIX.

if(DEFINED _INUX_IOS_APP_INCLUDED)
    return()
endif()
set(_INUX_IOS_APP_INCLUDED TRUE)
set(_INUX_IOS_DIR "${CMAKE_CURRENT_LIST_DIR}")

function(_inux_collect_executables dir out)
    set(result)
    get_property(targets DIRECTORY "${dir}" PROPERTY BUILDSYSTEM_TARGETS)
    foreach(t IN LISTS targets)
        get_target_property(type ${t} TYPE)
        if(type STREQUAL "EXECUTABLE")
            list(APPEND result ${t})
        endif()
    endforeach()
    get_property(subdirs DIRECTORY "${dir}" PROPERTY SUBDIRECTORIES)
    foreach(sub IN LISTS subdirs)
        _inux_collect_executables("${sub}" sub_result)
        list(APPEND result ${sub_result})
    endforeach()
    set(${out} ${result} PARENT_SCOPE)
endfunction()

function(_inux_finalize_ios_apps)
    _inux_collect_executables("${CMAKE_SOURCE_DIR}" exes)
    set(apps)
    if(INUX_APP_TARGET AND TARGET "${INUX_APP_TARGET}")
        set(apps "${INUX_APP_TARGET}")
    else()
        foreach(t IN LISTS exes)
            if(NOT t MATCHES "test|Test|bench|example|tool")
                list(APPEND apps ${t})
            endif()
        endforeach()
    endif()
    if(NOT apps)
        message(WARNING "iNux: no app executable found among: ${exes}")
        return()
    endif()
    message(STATUS "iNux: making iOS app bundle(s): ${apps}")

    foreach(app IN LISTS apps)
        set(bundle_id "${INUX_BUNDLE_ID}")
        if(NOT bundle_id)
            set(bundle_id "com.inux.ported.${app}")
        endif()
        set(display "${INUX_DISPLAY_NAME}")
        if(NOT display)
            set(display "${app}")
        endif()
        set_target_properties(${app} PROPERTIES
            MACOSX_BUNDLE TRUE
            MACOSX_BUNDLE_GUI_IDENTIFIER "${bundle_id}"
            MACOSX_BUNDLE_BUNDLE_NAME "${display}"
            MACOSX_BUNDLE_SHORT_VERSION_STRING "1.0"
            MACOSX_BUNDLE_BUNDLE_VERSION "1"
            XCODE_ATTRIBUTE_PRODUCT_BUNDLE_IDENTIFIER "${bundle_id}"
            XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED NO
            QT_QML_ROOT_PATH "${CMAKE_SOURCE_DIR}")
        target_sources(${app} PRIVATE "${_INUX_IOS_DIR}/inux_ios_bootstrap.cpp")
        if(INUX_COMPAT_PREFIX AND EXISTS "${INUX_COMPAT_PREFIX}/lib/libInuxCompat.a")
            target_link_libraries(${app} PRIVATE
                "${INUX_COMPAT_PREFIX}/lib/libInuxCompat.a"
                "${INUX_COMPAT_PREFIX}/lib/libintl.a")
        endif()
        # Dependencies of third-party static libraries (e.g. libsndfile's
        # codecs, OpenAL's CoreAudio frameworks) that projects locate with a
        # plain find_library() and so do not link transitively.
        foreach(lib IN LISTS INUX_EXTRA_LINK_LIBS)
            if(lib MATCHES "^-framework ")
                string(REPLACE "-framework " "" fw "${lib}")
                target_link_libraries(${app} PRIVATE "-framework ${fw}")
            elseif(EXISTS "${lib}")
                target_link_libraries(${app} PRIVATE "${lib}")
            endif()
        endforeach()
        if(COMMAND qt_finalize_target)
            qt_finalize_target(${app})
        elseif(COMMAND qt6_finalize_target)
            qt6_finalize_target(${app})
        else()
            message(WARNING "iNux: Qt's finalize command is unavailable; ${app} may not start on iOS")
        endif()
    endforeach()
endfunction()

cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL _inux_finalize_ios_apps)
