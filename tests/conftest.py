import pytest

from emporio.data import StoreData, load_store

pytest_plugins = ['nicegui.testing.user_plugin']  # `user` fixture for tests that drive the storefront


@pytest.fixture(scope='session')
def store() -> StoreData:
    return load_store()
