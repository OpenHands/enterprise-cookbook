#!/usr/bin/env python3
"""
Simple test to verify the warm-sandbox-pool structure is complete.
Run this before deploying to catch any missing files.
"""

import subprocess
from pathlib import Path


def check_structure():
    """Verify all expected files exist."""
    base = Path(__file__).parent

    required_files = [
        "README.md",
        "QUICKSTART.md",
        "pool_controller.py",
        "requirements.txt",
        "pyproject.toml",
        "sandbox_prep/init_ruby_service.sh",
        "sandbox_prep/quote_service.rb",
        "static/app.js",
        "static/styles.css",
        "templates/index.html",
    ]

    missing = []
    for file_path in required_files:
        full_path = base / file_path
        if not full_path.exists():
            missing.append(file_path)

    if missing:
        print("❌ Missing files:")
        for f in missing:
            print(f"   - {f}")
        return False

    print("✅ All required files present")

    # Check that scripts are executable
    scripts = [
        base / "pool_controller.py",
        base / "sandbox_prep" / "init_ruby_service.sh",
    ]

    non_executable = []
    for script in scripts:
        if not script.stat().st_mode & 0o111:  # Check execute bit
            non_executable.append(script.name)

    if non_executable:
        print("⚠️  Scripts missing execute permission:")
        for s in non_executable:
            print(f"   - {s}")
        print("   Run: chmod +x pool_controller.py sandbox_prep/*.sh")
    else:
        print("✅ All scripts are executable")

    # Check README has warm sandbox pool context
    readme = (base / "README.md").read_text()
    if "Warm Sandbox Pool" in readme:
        print("✅ README mentions Warm Sandbox Pool")
    else:
        print("⚠️  README doesn't mention Warm Sandbox Pool")

    # Check init script has proper shebang
    init_script = (base / "sandbox_prep" / "init_ruby_service.sh").read_text()
    if init_script.startswith("#!/bin/bash"):
        print("✅ Init script has proper shebang")
    else:
        print("⚠️  Init script missing #!/bin/bash shebang")

    syntax_ok = True
    bash_check = subprocess.run(
        ["bash", "-n", str(base / "sandbox_prep" / "init_ruby_service.sh")],
        capture_output=True,
        text=True,
    )
    if bash_check.returncode == 0:
        print("✅ Init script passes bash -n")
    else:
        syntax_ok = False
        print(f"❌ Init script syntax error:\n{bash_check.stderr}")

    try:
        compile((base / "pool_controller.py").read_text(), "pool_controller.py", "exec")
        print("✅ pool_controller.py compiles")
    except SyntaxError as e:
        syntax_ok = False
        print(f"❌ pool_controller.py syntax error: {e}")

    print("\n📊 Structure check complete!")
    return len(missing) == 0 and syntax_ok


if __name__ == "__main__":
    import sys

    success = check_structure()
    sys.exit(0 if success else 1)
