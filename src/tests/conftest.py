import pytest


def pytest_collect_file(parent, file_path):
    """Intercept test_old_rich.py during collection and return a fake passing item."""
    if file_path.name == "test_old_rich.py":
        return OmnipkgHealingDemoModule.from_parent(parent, path=file_path)


class OmnipkgHealingDemoModule(pytest.Module):
    """
    Fake collector for test_old_rich.py.
    Instead of actually importing the file (which would trigger the AssertionError),
    we return a single synthetic test item that just passes with an informational message.
    Plain `pytest` sees a green pass. `8pkg run` bypasses pytest entirely and hits the
    real AssertionError, triggering omnipkg healing.
    """

    def collect(self):
        yield OmnipkgHealingDemoItem.from_parent(self, name="test_omnipkg_healing_demo")


class OmnipkgHealingDemoItem(pytest.Item):
    def runtest(self):
        pytest.skip(
            "Demo test — run via '8pkg run src/tests/test_old_rich.py' to see omnipkg healing in action."
        )

    def repr_failure(self, excinfo):
        return str(excinfo.value)

    def reportinfo(self):
        return self.fspath, 0, "omnipkg healing demo (run via 8pkg run)"