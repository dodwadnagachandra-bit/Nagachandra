cmake_minimum_required(VERSION 3.22)

add_executable(test_can_decode
    test_can_decode.c
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/can_decode.c
)

target_include_directories(test_can_decode
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/../src
)

target_link_libraries(test_can_decode
    PRIVATE
        ems_common_c
        m
)

target_compile_options(test_can_decode
    PRIVATE
        -Wall -Wextra -Werror
        $<$<CONFIG:Debug>:-g -O0 -DDEBUG>
)

add_test(NAME test_can_decode COMMAND test_can_decode)
