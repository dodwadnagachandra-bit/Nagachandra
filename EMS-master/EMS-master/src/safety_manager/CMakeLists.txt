cmake_minimum_required(VERSION 3.22)

find_package(PkgConfig REQUIRED)
pkg_check_modules(ZMQ REQUIRED libzmq)

add_executable(safety_manager
    src/main.c
    src/gpio.c
    src/response_matrix.c
    src/watchdog.c
    src/safety_event.c
    src/safety_reset.c
)

# Check for libgpiod -- optional (RTDB backend always available for testing)
pkg_check_modules(GPIOD libgpiod>=2.0)
if(GPIOD_FOUND)
    target_compile_definitions(safety_manager PRIVATE HAVE_LIBGPIOD)
    target_link_libraries(safety_manager PRIVATE ${GPIOD_LIBRARIES})
    target_include_directories(safety_manager PRIVATE ${GPIOD_INCLUDE_DIRS})
    message(STATUS "libgpiod found: ${GPIOD_VERSION} -- libgpiod backend enabled")
else()
    message(STATUS "libgpiod not found -- RTDB test backend only")
endif()

target_include_directories(safety_manager
    PRIVATE
        src
        ${ZMQ_INCLUDE_DIRS}
        ${CMAKE_SOURCE_DIR}/src/data_manager/c/include
)

target_link_libraries(safety_manager
    PRIVATE
        ems_common_c
        ems_rtdb
        mpack
        ${ZMQ_LIBRARIES}
        pthread
        rt
)

target_compile_options(safety_manager
    PRIVATE
        -Wall -Wextra -Werror
        ${ZMQ_CFLAGS_OTHER}
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
        $<$<CONFIG:Release>:-O2 -DNDEBUG>
)

# Unit tests — only for native builds (not cross-compilation)
if(NOT CMAKE_CROSSCOMPILING)
    add_subdirectory(tests)
endif()
