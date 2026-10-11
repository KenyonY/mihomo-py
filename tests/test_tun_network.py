import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
def test_tun_in_isolated_network_namespace():
    for tool in ("ip", "unshare", "nsenter", "setpriv", "openssl"):
        if not shutil.which(tool):
            pytest.skip(f"isolated TUN test requires {tool}")
    if not Path("/dev/net/tun").exists():
        pytest.skip("isolated TUN test requires /dev/net/tun")
    probe = subprocess.run(
        ["unshare", "--user", "--map-root-user", "--net", "true"], capture_output=True
    )
    if probe.returncode:
        pytest.skip("user/network namespaces unavailable")
    helper = Path(__file__).parent / "helpers" / "tun_namespace.py"
    result = subprocess.run(
        ["unshare", "--user", "--map-root-user", "--net", sys.executable, str(helper)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "cleanup passed" in result.stdout
