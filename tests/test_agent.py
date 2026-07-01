from __future__ import annotations

import os
import unittest
from pathlib import Path

from laptop_health_agent.config import load_config, AgentConfig
from laptop_health_agent.safety import (
    is_protected_path, can_delete_path, can_terminate_process, can_modify_startup
)
from laptop_health_agent.history import init_db, save_history, get_history, DB_PATH
from laptop_health_agent.ai_assistant import ask_assistant


class TestLaptopHealthAgent(unittest.TestCase):

    def setUp(self):
        self.config = load_config()

    def test_config_loading(self):
        self.assertIsInstance(self.config, AgentConfig)
        self.assertTrue(len(self.config.storage_roots) > 0)
        self.assertTrue(len(self.config.protected_paths) > 0)

    def test_safety_protected_paths(self):
        # System directory should be protected
        windir = Path(os.environ.get("WINDIR", "C:\\Windows"))
        self.assertTrue(is_protected_path(windir, self.config))
        self.assertTrue(is_protected_path(windir / "System32", self.config))
        
        # User workspace profile should be protected (configured in defaults.json)
        workspace = Path(os.path.expandvars("%USERPROFILE%\\OneDrive\\Documents\\LaptopHealthAgent"))
        self.assertTrue(is_protected_path(workspace, self.config))

    def test_safety_can_delete(self):
        # Target that doesn't exist
        non_existent = Path("C:\\this_file_does_not_exist_xyz.txt")
        allowed, reason = can_delete_path(non_existent, self.config)
        self.assertFalse(allowed)
        self.assertEqual(reason, "Target no longer exists.")

    def test_safety_processes(self):
        # System PIDs should be protected
        allowed, reason = can_terminate_process("System", 4, self.config)
        self.assertFalse(allowed)
        self.assertEqual(reason, "System process IDs are protected.")

        # Whitelisted processes should be protected
        allowed, reason = can_terminate_process("explorer.exe", 1024, self.config)
        self.assertFalse(allowed)
        self.assertTrue("whitelisted" in reason)

        # Non-whitelisted should be allowed
        allowed, reason = can_terminate_process("heavy_malware.exe", 9999, self.config)
        self.assertTrue(allowed)

    def test_safety_startup(self):
        # Critical startup items should be blocked
        allowed, reason = can_modify_startup("WindowsDefender", self.config)
        self.assertFalse(allowed)

        allowed, reason = can_modify_startup("MyCustomApp", self.config)
        self.assertTrue(allowed)

    def test_history_database(self):
        # Init DB and perform writes
        init_db()
        self.assertTrue(DB_PATH.exists())
        
        # Save snapshot
        save_history(
            health_score=85,
            cpu_percent=24.5,
            memory_percent=60.0,
            storage_used_bytes=1000000,
            storage_total_bytes=5000000,
            battery_percent=95.0
        )
        
        history = get_history(limit=5)
        self.assertTrue(len(history) > 0)
        latest = history[-1]
        self.assertEqual(latest["health_score"], 85)
        self.assertEqual(latest["cpu_percent"], 24.5)

    def test_ai_assistant_fallback(self):
        stats = {
            "performance": {
                "cpu_percent": 15.0,
                "memory_percent": 45.0,
                "battery_percent": 88.0
            },
            "diagnostics": {
                "health_score": 90,
                "temp_files_bytes": 10000,
                "recycle_bin_bytes": 5000,
                "battery": {"health_percent": 92},
                "security": {"defender_enabled": True, "firewall_enabled": True},
                "updates": {"pending_updates_count": 0},
                "hardware": {"is_overheating": False}
            }
        }
        
        # Test performance question
        res_perf = ask_assistant("Why is my laptop slow?", stats)
        self.assertTrue("Health Diagnostics" in res_perf or "Score" in res_perf)
        
        # Test storage question
        res_store = ask_assistant("What can I clean up?", stats)
        self.assertTrue("temporary files" in res_store.lower() or "recycle bin" in res_store.lower())


if __name__ == "__main__":
    unittest.main()
