cmake_minimum_required(VERSION 3.22)

find_package(PkgConfig REQUIRED)
pkg_check_modules(ZMQ REQUIRED libzmq)

add_executable(comm_manager_c
    src/main.c
    src/can_decode.c
    src/can_reader.c
    src/can_health.c
    src/comm_event.c
)

target_include_directories(comm_manager_c
    PRIVATE
        src
        ${ZMQ_INCLUDE_DIRS}
        ${CMAKE_SOURCE_DIR}/src/data_manager/c/include
)

target_link_libraries(comm_manager_c
    PRIVATE
        ems_common_c
        ems_rtdb
        mpack
        ${ZMQ_LIBRARIES}
        pthread
        rt
)

target_compile_options(comm_manager_c
    PRIVATE
        -Wall -Wextra -Werror
        ${ZMQ_CFLAGS_OTHER}
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
        $<$<CONFIG:Release>:-O2 -DNDEBUG>
)

if(NOT CMAKE_CROSSCOMPILING)
    add_subdirectory(tests)
endif()
