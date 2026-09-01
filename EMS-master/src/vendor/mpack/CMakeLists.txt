cmake_minimum_required(VERSION 3.22)

add_library(mpack STATIC mpack.c)
target_include_directories(mpack PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_options(mpack PRIVATE -w)  # Suppress warnings in vendored code
