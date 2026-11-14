"""Package the noncommercial local SIWC runtime beside the frozen app."""
import argparse
import os
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("destination", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
source = root / "siwc"
node = os.environ.get("KONSPEKT_NODE") or str(source / "node-bin" / "node.exe")
if not Path(node).is_file():
    node = shutil.which("node") or ""
if not node:
    raise SystemExit("Node.js 22.12+ required. Set KONSPEKT_NODE to node.exe.")
version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
if tuple(map(int, version.split(".")[:2])) < (22, 12):
    raise SystemExit("Node.js 22.12+ required. Set KONSPEKT_NODE to the correct executable.")
if not (source / "node_modules").is_dir():
    raise SystemExit("Run npm ci inside siwc first.")
args.destination.mkdir(parents=True, exist_ok=True)
for name in ("bridge.mjs", "package.json", "package-lock.json", "LICENSE", "THIRD_PARTY_NOTICES.md", "NOTICE.md", "local", "node_modules"):
    src, dst = source / name, args.destination / name
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
(args.destination / "node-bin").mkdir(exist_ok=True)
shutil.copy2(node, args.destination / "node-bin" / "node.exe")
shutil.copy2(Path(node).parent / "LICENSE", args.destination / "node-bin" / "LICENSE")
print(f"Packaged ChatGPT runtime: {args.destination}")
