import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import pytest
from alertgen.common import Config
from alertgen.build import generate


@pytest.fixture(scope="session")
def ds():
    cfg = Config(seed=20261022, scale=0.05)
    T, truth, info = generate(cfg, verbose=False)
    return cfg, T, truth
