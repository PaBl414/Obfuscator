"""Build Password-Generator into a temporary tree and exercise its Qt UI.

Requires the example's PySide6 dependency. No generated project is retained.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from code_editors import PipelineConfig, process_folder

SMOKE = r'''
import importlib, time
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QTextEdit, QTabBar, QCheckBox
try:
    import obfuscator_bootstrap
except ModuleNotFoundError:
    pass
app = QApplication([])
ui = importlib.import_module('app.ui')
window_type = next(value for value in vars(ui).values() if isinstance(value, type)
    and value is not QMainWindow and issubclass(value, QMainWindow))
window = window_type()
button = window.findChild(QPushButton, 'generate')
result = window.findChild(QTextEdit, 'password')
tabs = next(tab for tab in window.findChildren(QTabBar) if tab.count() >= 2)
assert button is not None and result is not None
def generate():
    result.clear()
    button.click()
    deadline = time.monotonic() + 15
    while not result.toPlainText() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert result.toPlainText(), 'No password produced'
    assert button.isEnabled(), 'Generate button remained disabled'
for mode in (0, 1):
    tabs.setCurrentIndex(mode)
    generate()
tabs.setCurrentIndex(0)
advanced = next(box for box in window.findChildren(QCheckBox)
    if 'advanced rules' in box.text().lower() or 'zaawansowane' in box.text().lower())
advanced.click()
generate()
window.close()
app.processEvents()
print('Qt startup, manager, human and advanced generation: OK')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, nargs="?", default=ROOT / "Password-Generator")
    parser.add_argument("--seed", type=int, default=391)
    parser.add_argument("--rename-modules", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="obfuscator-qt-") as temporary:
        output = Path(temporary) / "output"
        process_folder(args.source, output, PipelineConfig(seed=args.seed,
            import_mode="runtime", literal_mode="polymorphic", dispatch_mode="table",
            extended_flow=True, rename_modules=args.rename_modules))
        count = 0
        for path in output.rglob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            count += 1
        environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, "-c", SMOKE], cwd=output,
            env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        print(f"Compiled {count} generated Python files")
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
