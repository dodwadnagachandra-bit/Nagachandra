cmake_minimum_required(VERSION 3.22)

# libems_rtdb: shared library providing RTDB lifecycle API (create/attach/detach/destroy)
add_library(ems_rtdb SHARED
    src/rtdb_lifecycle.c
)

target_include_directories(ems_rtdb
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(ems_rtdb
    PUBLIC
        ems_common_c
    PRIVATE
        rt
        pthread
)

target_compile_options(ems_rtdb
    PRIVATE
        -Wall -Wextra -Werror
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
        $<$<CONFIG:Release>:-O2 -DNDEBUG>
)

set_target_properties(ems_rtdb PROPERTIES
    VERSION 1.0.0
    SOVERSION 1
)

install(TARGETS ems_rtdb
    LIBRARY DESTINATION lib
)

install(FILES include/rtdb_lifecycle.h
    DESTINATION include
)

# data_manager_c: RTDB owner executable
add_executable(data_manager_c
    src/main.c
)

target_link_libraries(data_manager_c
    PRIVATE
        ems_rtdb
        ems_common_c
)

target_compile_options(data_manager_c
    PRIVATE
        -Wall -Wextra -Werror
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
        $<$<CONFIG:Release>:-O2 -DNDEBUG>
)

if(NOT CMAKE_CROSSCOMPILING)
    # add_subdirectory(tests)  # Uncomment in M1 when unit tests are added
endif()
