cmake_minimum_required(VERSION 3.21)
project(INuxQtSelfTest VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets)
qt_standard_project_setup()

qt_add_executable(INuxQtSelfTest MANUAL_FINALIZATION main.cpp)
target_link_libraries(INuxQtSelfTest PRIVATE Qt6::Widgets)

set_target_properties(INuxQtSelfTest PROPERTIES
    MACOSX_BUNDLE TRUE
    MACOSX_BUNDLE_GUI_IDENTIFIER com.inux.ported.qtselftest
    MACOSX_BUNDLE_BUNDLE_NAME "iNux Qt"
    MACOSX_BUNDLE_SHORT_VERSION_STRING "1.0"
    XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED NO)

qt_finalize_target(INuxQtSelfTest)
