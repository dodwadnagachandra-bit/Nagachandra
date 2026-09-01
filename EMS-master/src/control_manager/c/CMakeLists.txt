cmake_minimum_required(VERSION 3.22)

add_executable(control_manager_c
    src/main.c
)

target_link_libraries(control_manager_c
    PRIVATE
        ems_common_c
)

target_compile_options(control_manager_c
    PRIVATE
        -Wall -Wextra -Werror
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
        $<$<CONFIG:Release>:-O2 -DNDEBUG>
)

if(NOT CMAKE_CROSSCOMPILING)
    # add_subdirectory(tests)  # Uncomment in M2 when unit tests are added
endif()
