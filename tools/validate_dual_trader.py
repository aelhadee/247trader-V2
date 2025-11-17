#!/usr/bin/env python3
"""
Dual-Trader System Validation Script

Quick health check for dual-trader implementation.
Run before each deployment phase.
"""

import sys
from pathlib import Path
import subprocess
import json


def check_file_exists(path: str, description: str) -> bool:
    """Check if a required file exists."""
    if Path(path).exists():
        print(f"✅ {description}: {path}")
        return True
    else:
        print(f"❌ {description} MISSING: {path}")
        return False


def run_tests() -> bool:
    """Run dual-trader test suite."""
    print("\n🧪 Running dual-trader tests...")
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/test_dual_trader.py", "-v", "--tb=short"],
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        # Count passing tests
        lines = result.stdout.split("\n")
        for line in lines:
            if "passed" in line:
                print(f"✅ Tests: {line.strip()}")
                return True
        print("✅ Tests: All passed")
        return True
    else:
        print(f"❌ Tests FAILED:\n{result.stdout}\n{result.stderr}")
        return False


def validate_config() -> bool:
    """Check configuration file."""
    print("\n⚙️  Validating configuration...")
    
    # Check app.yaml exists
    if not Path("config/app.yaml").exists():
        print("❌ config/app.yaml not found")
        return False
    
    # Run config validator
    result = subprocess.run(
        ["python", "tools/config_validator.py", "config"],
        capture_output=True,
        text=True,
    )
    
    if result.returncode == 0:
        print("✅ Configuration valid")
        return True
    else:
        print(f"❌ Configuration validation failed:\n{result.stderr}")
        return False


def check_imports() -> bool:
    """Verify all modules import correctly."""
    print("\n📦 Checking imports...")
    
    modules = [
        "ai.llm_client",
        "ai.snapshot_builder",
        "ai.arbiter_client",
        "strategy.ai_trader_strategy",
        "strategy.meta_arb",
    ]
    
    all_ok = True
    
    # Add project root to path for imports
    import os
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    
    for module in modules:
        try:
            __import__(module)
            print(f"✅ Import: {module}")
        except ImportError as e:
            print(f"❌ Import FAILED: {module} - {e}")
            all_ok = False
    
    return all_ok


def main():
    """Run all validation checks."""
    print("=" * 60)
    print("Dual-Trader System Validation")
    print("=" * 60)
    
    checks = []
    
    # Check files
    print("\n📁 Checking files...")
    checks.append(check_file_exists("ai/llm_client.py", "AI Client"))
    checks.append(check_file_exists("ai/snapshot_builder.py", "Snapshot Builder"))
    checks.append(check_file_exists("ai/arbiter_client.py", "AI Arbiter"))
    checks.append(check_file_exists("strategy/ai_trader_strategy.py", "AI Trader Strategy"))
    checks.append(check_file_exists("strategy/meta_arb.py", "Meta-Arbitrator"))
    checks.append(check_file_exists("tests/test_dual_trader.py", "Test Suite"))
    checks.append(check_file_exists("docs/DUAL_TRADER_ARCHITECTURE.md", "Architecture Doc"))
    checks.append(check_file_exists("docs/DUAL_TRADER_DEPLOYMENT_CHECKLIST.md", "Deployment Doc"))
    
    # Check imports
    checks.append(check_imports())
    
    # Check config
    checks.append(validate_config())
    
    # Run tests
    checks.append(run_tests())
    
    # Summary
    print("\n" + "=" * 60)
    passed = sum(checks)
    total = len(checks)
    
    if passed == total:
        print(f"✅ ALL CHECKS PASSED ({passed}/{total})")
        print("=" * 60)
        print("\n🚀 System ready for deployment!")
        print("\nNext steps:")
        print("  1. Review docs/DUAL_TRADER_DEPLOYMENT_CHECKLIST.md")
        print("  2. Start Phase 1 (Mock Mode):")
        print("     - Edit config/app.yaml: dual_trader.enabled=true, provider='mock'")
        print("     - Run: ./app_run_live.sh --loop")
        print("  3. Monitor logs: tail -f logs/247trader-v2.log | grep '⚖️'")
        return 0
    else:
        print(f"❌ CHECKS FAILED ({total - passed}/{total})")
        print("=" * 60)
        print("\n⚠️  Fix issues before deployment")
        return 1


if __name__ == "__main__":
    sys.exit(main())
