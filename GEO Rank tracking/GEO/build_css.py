"""Build Tailwind CSS from templates. Requires npx (Node.js) installed."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def build(watch: bool = False):
    cmd = [
        "npx", "--yes", "tailwindcss@3",
        "-i", str(ROOT / "app" / "static" / "css" / "input.css"),
        "-o", str(ROOT / "app" / "static" / "css" / "app.css"),
        "-c", str(ROOT / "tailwind.config.js"),
        "--minify",
    ]
    if watch:
        cmd.append("--watch")
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    build(watch="--watch" in sys.argv)
