# conftest.py (ROOT - /home/minds3t/omnipkg/conftest.py)

import pytest


def pytest_collect_file(parent, file_path):
    """Intercept test_old_rich.py before pytest tries to import it."""
    if file_path.name == "test_old_rich.py":
        return OmnipkgDemoCollector.from_parent(parent, path=file_path)


class OmnipkgDemoCollector(pytest.File):
    def collect(self):
        yield OmnipkgDemoItem.from_parent(self, name="test_omnipkg_healing_demo")


class OmnipkgDemoItem(pytest.Item):
    def runtest(self):
        pytest.skip("Run via: 8pkg run src/tests/test_old_rich.py")

    def repr_failure(self, excinfo):
        return str(excinfo.value)

    def reportinfo(self):
        return self.fspath, 0, "omnipkg healing demo — run via 8pkg run"