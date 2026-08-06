# Deferred Items - Phase 11

## Pre-existing Test Failures

- **test_integration_modbus.py::TestPcsPollingWritesRtdb::test_pcs_polling_writes_rtdb**: Pre-existing failure. PCS poll returns None when using mini Modbus TCP server. Not caused by 11-05 changes. Likely a pymodbus TCP server setup issue in the integration test.
