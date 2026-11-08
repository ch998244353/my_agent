from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "scripts" / "start_myagent.ps1"


class WindowsLauncherTests(unittest.TestCase):
    def test_launcher_keeps_the_safe_start_only_contract(self) -> None:
        script = LAUNCHER.read_text(encoding="utf-8")

        for expected in (
            "[switch]$NoBrowser",
            "docker compose",
            "up -d --wait --wait-timeout 90 postgres",
            "MYAGENT_POSTGRES_DSN",
            "MYAGENT_WORKSPACE_ROOT = $projectRoot",
            "python scripts/verify_current_baseline.py",
            "my-agent-chat-http.exe",
            "http://127.0.0.1:8000/health",
            "Start-Process -FilePath $appUrl",
        ):
            self.assertIn(expected, script)

        lowered = script.lower()
        self.assertNotIn("docker compose down", lowered)
        self.assertNotIn("remove-item", lowered)
        self.assertNotIn("stop-process", lowered)
        self.assertIn('$erroractionpreference = "silentlycontinue"', lowered)

    @unittest.skipUnless(os.name == "nt", "PowerShell syntax check requires Windows")
    def test_launcher_has_valid_windows_powershell_syntax(self) -> None:
        environment = os.environ.copy()
        environment["MYAGENT_LAUNCHER_UNDER_TEST"] = str(LAUNCHER)
        command = (
            "$errors=$null; $tokens=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:MYAGENT_LAUNCHER_UNDER_TEST, [ref]$tokens, [ref]$errors) "
            "| Out-Null; if ($errors.Count) { "
            "$errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
