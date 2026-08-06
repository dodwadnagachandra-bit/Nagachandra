cmake_minimum_required(VERSION 3.22)

add_executable(logger_c
    src/main.c
)

target_link_libraries(logger_c
    PRIVATE
        ems_common_c
)

target_compile_options(logger_c
    PRIVATE
        -Wall -Wextra -Werror
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
        $<$<CONFIG:Release>:-O2 -DNDEBUG>
)

if(NOT CMAKE_CROSSCOMPILING)
    # add_subdirectory(tests)  # Uncomment in M1 when unit tests are added
endif()
