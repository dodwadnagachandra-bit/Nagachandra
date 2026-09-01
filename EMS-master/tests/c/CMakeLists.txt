cmake_minimum_required(VERSION 3.22)

add_executable(test_rtdb test_rtdb.c)
target_link_libraries(test_rtdb PRIVATE ems_common_c)
target_compile_options(test_rtdb PRIVATE -Wall -Wextra -Werror)

add_test(NAME rtdb_layout COMMAND test_rtdb)

add_executable(test_ipc_interop test_ipc_interop.c)
target_link_libraries(test_ipc_interop PRIVATE ems_common_c mpack)
target_compile_options(test_ipc_interop PRIVATE -Wall -Wextra -Werror)

# Two test modes:
# 1. Self-test: encode + decode in C, verify values (standalone ctest)
# 2. Interop output: encode to file for Python to decode (used by pytest)
add_test(NAME ipc_interop_self COMMAND test_ipc_interop --self-test)

add_executable(test_rtdb_lifecycle test_rtdb_lifecycle.c)
target_link_libraries(test_rtdb_lifecycle PRIVATE ems_rtdb ems_common_c)
target_compile_options(test_rtdb_lifecycle PRIVATE -Wall -Wextra -Werror)

add_test(NAME rtdb_lifecycle COMMAND test_rtdb_lifecycle)
