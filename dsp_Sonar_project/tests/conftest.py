"""
Shared pytest fixtures for the sonar test suite.
"""

import pytest
import numpy as np


@pytest.fixture
def default_fs():
    """Default sampling frequency."""
    return 48000.0


@pytest.fixture
def default_params(default_fs):
    """A dictionary of reasonable default sonar parameters."""
    return {
        "f0": 6000,
        "f1": 12000,
        "fs": default_fs,
        "Npulse": 500,
        "Nseg": 4096,
        "Nrep": 24,
        "Nplot": 200,
        "maxdist": 200,
        "temperature": 21.0,
    }
