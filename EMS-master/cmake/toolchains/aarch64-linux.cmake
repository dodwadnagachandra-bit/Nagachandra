# cmake/toolchains/aarch64-linux.cmake
# ARM64 cross-compilation toolchain for Advantech ECU-1170-552A (TI AM6548, A53 cores)
# Usage: cmake -B build-arm -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/aarch64-linux.cmake
#
# Prerequisites (Ubuntu/Debian):  sudo apt-get install -y gcc-aarch64-linux-gnu g++-aarch64-linux-gnu
# Prerequisites (Arch Linux):     sudo pacman -S aarch64-linux-gnu-gcc aarch64-linux-gnu-binutils
#
# CI: ubuntu-22.04 runner uses apt-get install (see .github/workflows/master-merge.yml)

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# Standard Debian/Ubuntu aarch64 cross-compiler (also used on Arch with same binary path)
set(CMAKE_C_COMPILER /usr/bin/aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER /usr/bin/aarch64-linux-gnu-g++)

# Sysroot: uncomment and set when a cross-sysroot is available (Yocto SDK, M5+)
# set(CMAKE_SYSROOT /path/to/aarch64-sysroot)

# Prevent CMake from using host libraries/includes/packages for the target
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
