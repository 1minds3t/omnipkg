def pytest_configure(config):
    config.addinivalue_line("markers", "fast: quick smoke tests, no GPU, no framework boot")
    config.addinivalue_line("markers", "slow: tests that load large frameworks or use CUDA")
    config.addinivalue_line("markers", "gpu:  tests that require CUDA to be available")
    config.addinivalue_line("markers", "daemon: tests that exercise the daemon subsystem")
    config.addinivalue_line("markers", "loader: tests that exercise the in-process loader")