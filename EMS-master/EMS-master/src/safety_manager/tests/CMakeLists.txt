cmake_minimum_required(VERSION 3.22)

# Test executable: pure logic tests for response matrix (no GPIO hardware)
add_executable(test_response_matrix
    test_response_matrix.c
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/response_matrix.c
)

target_include_directories(test_response_matrix
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/../src
)

target_link_libraries(test_response_matrix
    PRIVATE
        ems_common_c
)

target_compile_options(test_response_matrix
    PRIVATE
        -Wall -Wextra -Werror
        -g -O0
)

# Register with CTest
add_test(
    NAME test_response_matrix
    COMMAND test_response_matrix
)
