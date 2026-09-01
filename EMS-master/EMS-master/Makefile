# Makefile — Developer interface for EMS monorepo
# Usage: make <target>
# Run `make help` to see all available targets.

.PHONY: help setup build build-arm test test-integration test-integration-m2 test-integration-m3 test-integration-m4 lint fmt sim sim-all sim-can sim-modbus sim-gpio build-hmi dev-hmi test-hmi flash clean validate tui

BUILD_DIR  := build
BUILD_ARM  := build-arm
ECU_HOST   ?= ems@192.168.1.100

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Install all dependencies (run once after clone)
	@echo "--- Installing system deps ---"
	sudo apt-get install -y cmake ninja-build gcc-aarch64-linux-gnu \
	  clang-format libgpiod-dev libzmq3-dev can-utils socat mosquitto
	@echo "--- Setting up virtual CAN ---"
	sudo modprobe vcan can can_raw || true
	sudo ip link add dev vcan0 type vcan 2>/dev/null || true
	sudo ip link set up vcan0 || true
	@echo "--- Installing Python 3.12 via uv ---"
	uv python install 3.12
	@echo "--- Installing Python deps ---"
	uv sync --dev --all-packages
	@echo "--- Installing HMI deps ---"
	cd src/hmi_server/frontend && bun install
	@echo "--- Verifying dev environment ---"
	bash tools/verify-dev-env.sh

build: ## Build all C targets (native, Debug)
	cmake -B $(BUILD_DIR) -DCMAKE_BUILD_TYPE=Debug
	cmake --build $(BUILD_DIR) -- -j$(nproc)

build-arm: ## Cross-compile for ARM64 (ECU-1170)
	cmake -B $(BUILD_ARM) \
	  -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/aarch64-linux.cmake \
	  -DCMAKE_BUILD_TYPE=Release
	cmake --build $(BUILD_ARM) -- -j$(nproc)

test: ## Run all unit tests (ctest + pytest)
	ctest --test-dir $(BUILD_DIR) --output-on-failure
	uv run pytest tests/ -v

test-integration: ## Run integration tests (~40 min, local only)
	uv run pytest tests/integration/ -v -m integration --timeout=900

test-integration-m2: ## Run M2 integration tests (control + alarm)
	uv run pytest tests/integration/test_m2_integration.py tests/integration/test_startup.py tests/integration/test_crash_recovery.py -v -m integration --timeout=600

test-integration-m3: ## Run M3 integration tests (HMI + scheduler + full system)
	uv run pytest tests/integration/test_startup.py tests/integration/test_crash_recovery.py tests/integration/test_m3_integration.py -v -m integration --timeout=600

test-integration-m4: ## Run M4 integration tests (cloud + OTA + full system)
	uv run pytest tests/integration/test_m4_integration.py tests/integration/test_startup.py tests/integration/test_crash_recovery.py -v -m integration --timeout=600

lint: ## Check C formatting and Python lint (no changes)
	find src -name "*.c" -o -name "*.h" | xargs clang-format --dry-run --Werror
	uv run ruff check src/
	uv run ruff format --check src/

fmt: ## Auto-format C and Python code
	find src -name "*.c" -o -name "*.h" | xargs clang-format -i
	uv run ruff format src/
	uv run ruff check --fix src/

sim-all: ## Start all simulators (CAN + Modbus + GPIO)
	bash tools/sim-all.sh --profile $(or $(PROFILE),residential)

sim: ## Start simulators (vcan + socat virtual serial)
	@echo "Starting virtual interfaces..."
	sudo modprobe vcan can can_raw || true
	sudo ip link add dev vcan0 type vcan 2>/dev/null || true
	sudo ip link set up vcan0 || true

sim-modbus: ## Start Modbus PCS simulator (RTU over socat PTY)
	uv run python -m tools.simulators.modbus_sim --transport rtu --verbose

sim-gpio: ## Start GPIO test harness (RTDB mode, show all pins)
	uv run python -m tools.simulators.gpio_harness get all

sim-can: ## Start CAN bus simulator on vcan0
	@sudo modprobe vcan can can_raw 2>/dev/null || true
	@sudo ip link add dev vcan0 type vcan 2>/dev/null || true
	@sudo ip link set up vcan0 2>/dev/null || true
	uv run python -m tools.simulators.can_sim --interface vcan0 --verbose

build-hmi: ## Build HMI React frontend (production)
	cd src/hmi_server/frontend && bun run build

dev-hmi: ## Start HMI dev server
	cd src/hmi_server/frontend && bun run dev

test-hmi: ## Run HMI unit tests
	cd src/hmi_server/frontend && bun test

flash: ## Deploy to ECU via rsync (requires ECU_HOST env var or default 192.168.1.100)
	@echo "Deploying to $(ECU_HOST)..."
	rsync -avz --delete $(BUILD_ARM)/ $(ECU_HOST):/opt/ems/bin/
	rsync -avz config/ $(ECU_HOST):/opt/ems/config/

validate: ## Validate all YAML config files against JSON Schema
	uv run python tools/validate_config.py

tui: ## Launch EMS developer TUI process manager
	uv run python -m tools.tui --profile $(or $(PROFILE),residential)

clean: ## Remove build artifacts
	rm -rf $(BUILD_DIR) $(BUILD_ARM)
