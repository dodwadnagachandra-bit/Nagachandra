cmake_minimum_required(VERSION 3.22)
project(EMS C CXX)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Enable testing at top level (required for ctest to discover tests)
include(CTest)

# Shared C library (headers + common types)
add_subdirectory(src/common/c)

# L1 Safety
add_subdirectory(src/safety_manager)

# L2 Comms (C portion)
add_subdirectory(src/comm_manager/c)

# L3 Data (C portion)
add_subdirectory(src/data_manager/c)
add_subdirectory(src/logger/c)

# L4 Control (C portion)
add_subdirectory(src/control_manager/c)

# Vendored libraries
add_subdirectory(src/vendor/mpack)

# Guard test subdirectory targets (future C unit tests)
if(NOT CMAKE_CROSSCOMPILING)
    add_subdirectory(tests/c)
endif()
