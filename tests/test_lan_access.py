import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from tests.auth_test_utils import admin_client
from local_video_site.core import config
from local_video_site import database
from local_video_site.cli.commands.serve import publish_error as _lan_publish_error
from local_video_site.app import app
from local_video_site.core.network import find_lan_ip, is_lan_address


class LanAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.data_dir = self.root / "data"
        self.patches = (
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DATABASE_PATH", self.data_dir / "library.db"),
            patch.object(config, "THUMBNAIL_DIR", self.data_dir / "thumbnails"),
        )
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup)
        database.init_db()

    def _cleanup(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_private_network_detection(self):
        for value in (
            "127.0.0.1",
            "192.168.1.20",
            "10.0.0.5",
            "172.16.10.8",
            "::1",
            "fd00::5",
        ):
            self.assertTrue(is_lan_address(value), value)
        for value in (
            "8.8.8.8",
            "1.1.1.1",
            "2001:4860:4860::8888",
            "0.0.0.0",
            "224.0.0.1",
            "invalid",
        ):
            self.assertFalse(is_lan_address(value), value)

    def test_lan_ip_falls_back_to_ifconfig(self):
        completed = Mock(
            stdout=(
                "lo0: flags=8049<UP>\n"
                "\tinet 127.0.0.1 netmask 0xff000000\n"
                "en0: flags=8863<UP>\n"
                "\tinet 192.168.50.20 netmask 0xffffff00\n"
                "utun0: flags=8051<UP>\n"
                "\tinet 100.64.0.2 netmask 0xffffffff\n"
            )
        )
        with (
            patch.dict(os.environ, {"LOCAL_VIDEO_LAN_IP": ""}, clear=False),
            patch(
                "local_video_site.core.network._interface_address",
                return_value=None,
            ),
            patch(
                "local_video_site.core.network._default_interface",
                return_value=None,
            ),
            patch(
                "local_video_site.core.network.subprocess.run",
                return_value=completed,
            ),
        ):
            self.assertEqual(find_lan_ip(), "192.168.50.20")

    def test_lan_startup_requires_active_admin(self):
        with patch.dict(
            os.environ,
            {"LOCAL_VIDEO_LAN_MODE": "1"},
            clear=False,
        ):
            self.assertIn("没有有效管理员账号", _lan_publish_error())
            admin_client(client=("192.168.1.50", 50000))
            self.assertEqual(_lan_publish_error(), "")

    def test_lan_mode_allows_guests_and_restricts_admin_actions(self):
        environment = {
            "LOCAL_VIDEO_LAN_MODE": "1",
        }
        with patch.dict(os.environ, environment, clear=False):
            private_client = TestClient(
                app,
                client=("192.168.1.50", 50000),
            )
            public_client = TestClient(
                app,
                client=("8.8.8.8", 50000),
            )

            health = private_client.get("/api/health")
            guest_home = private_client.get("/")
            protected_api = private_client.get("/api/scan/status")
            public = public_client.get("/")
            admin = admin_client(client=("192.168.1.50", 50000))
            admin_api = admin.get("/api/scan/status")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mode"], "lan")
        self.assertEqual(guest_home.status_code, 200)
        self.assertEqual(protected_api.status_code, 401)
        self.assertEqual(public.status_code, 403)
        self.assertEqual(admin_api.status_code, 200)


if __name__ == "__main__":
    unittest.main()
