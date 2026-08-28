cmake_minimum_required(VERSION 3.22)

# ems_common_c: INTERFACE library exposing shared C headers to all modules.
# Modules link against this target to access ems_types.h and future shared headers.
add_library(ems_common_c INTERFACE)

target_include_directories(ems_common_c
    INTERFACE
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)
