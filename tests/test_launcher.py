from __future__ import annotations

import unittest

import launcher


class LauncherTests(unittest.TestCase):
    def test_frozen_console_child_uses_independent_pyinstaller_environment(self) -> None:
        command, environment = launcher.console_process_spec(frozen=True)
        self.assertEqual(command, [launcher.sys.executable, "--console"])
        self.assertEqual(environment.get("PYINSTALLER_RESET_ENVIRONMENT"), "1")

    def test_source_console_child_runs_main_script(self) -> None:
        command, environment = launcher.console_process_spec(frozen=False)
        self.assertEqual(command, [launcher.sys.executable, str(launcher.ROOT / "main.py")])
        self.assertEqual(
            environment.get("PYINSTALLER_RESET_ENVIRONMENT"),
            launcher.os.environ.get("PYINSTALLER_RESET_ENVIRONMENT"),
        )


if __name__ == "__main__":
    unittest.main()
