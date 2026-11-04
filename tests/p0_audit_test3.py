import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from app.core.macro import MacroExecutor
from app.core.execution import OperationRisk, PolicyDeniedError, RiskAssessment, ExecutionPolicy

class MockPolicy(ExecutionPolicy):
    def allows(self, risk):
        return risk == OperationRisk.SAFE

policy = MockPolicy()
executor = MacroExecutor(policy=policy, dry_run=False, output_dir="/home/user/WinGenius/output")

# Let's inspect _check_file_policy directly
print("Inspecting _check_file_policy...")
try:
    from pathlib import Path
    executor._check_file_policy("delete", path=Path("/tmp/test"))
    print("FAIL: _check_file_policy did not raise PolicyDeniedError")
except PolicyDeniedError as e:
    print(f"PASS: _check_file_policy raised PolicyDeniedError - {e.assessment.risk}")
except Exception as e:
    print(f"FAIL: Unexpected error - {type(e).__name__}: {e}")

# Let's inspect _execute_steps
print("Inspecting _execute_steps...")
macro = {"name": "test", "steps": [{"type": "delete", "path": "/tmp/test"}]}
try:
    executor.execute(macro)
    print("FAIL: execute should have raised PolicyDeniedError")
except PolicyDeniedError as e:
    print(f"PASS: execute raised PolicyDeniedError - {e.assessment.risk}")
except Exception as e:
    print(f"FAIL: Unexpected error in execute - {type(e).__name__}: {e}")
