import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from app.core.macro import MacroExecutor, MacroError, validate
from app.core.scheduler import Schedule, ScheduleStore, describe_cron
from app.tools import auto_tools
from app.core.execution import OperationRisk, PolicyDeniedError, RiskAssessment, ExecutionPolicy

# Mock Policy for testing
class MockPolicy(ExecutionPolicy):
    def allows(self, risk):
        return risk == OperationRisk.SAFE

policy = MockPolicy()

def test_p01_macro_security():
    print("Testing P0.1 Macro Engine Security...")
    executor = MacroExecutor(policy=policy, dry_run=True)
    
    # Test Risk Classification & Policy Gate
    macro = {
        "name": "test_destructive",
        "steps": [{"type": "delete", "path": "/tmp/test"}]
    }
    try:
        executor.execute(macro)
        print("FAIL: PolicyDeniedError should have been raised for DESTRUCTIVE operation")
    except PolicyDeniedError as e:
        print(f"PASS: PolicyDeniedError caught - {e.assessment.risk}")
    except Exception as e:
        print(f"FAIL: Unexpected error - {e}")

    # Test Safe Destination Validation (Path Traversal)
    executor_safe = MacroExecutor(policy=policy, dry_run=True, output_dir="/home/user/WinGenius/output")
    macro_unsafe = {
        "name": "test_traversal",
        "steps": [{"type": "copy", "src": "/etc/passwd", "dst": "../../etc/shadow"}]
    }
    # In dry run, policy gate still checks path traversal
    try:
        executor_safe.execute(macro_unsafe)
        print("FAIL: Path traversal should be blocked")
    except PolicyDeniedError as e:
        print(f"PASS: Path traversal blocked - {e.assessment.title_en}")
    except Exception as e:
        print(f"FAIL: Unexpected error in traversal - {e}")

    print("P0.1 Audit Complete.")

def test_p02_scheduler_security():
    print("Testing P0.2 Scheduler Authorization...")
    schedule = Schedule(
        name="secure_job",
        cron="0 9 * * *",
        kind="tool",
        target_id="image_compress",
        risk_classification="DESTRUCTIVE",
        authorized_by="admin_01",
        scopes=["file_write"],
        recovery_info="/backup/schedule_01"
    )
    
    # Test Risk Summary
    summary = schedule.risk_summary(lang="en")
    assert "Risk: DESTRUCTIVE" in summary
    assert "Scopes: file_write" in summary
    assert "Auth: admin_01" in summary
    print(f"PASS: Risk summary - {summary}")

    # Test Serialization
    data = schedule.as_dict()
    new_sched = Schedule.from_dict(data)
    assert new_sched.risk_classification == "DESTRUCTIVE"
    assert new_sched.authorized_by == "admin_01"
    print("PASS: Serialization/Deserialization")

    # Test Store Import Fix
    store = auto_tools.store
    assert isinstance(store, ScheduleStore)
    print("PASS: auto_tools.store instantiation")
    
    print("P0.2 Audit Complete.")

if __name__ == "__main__":
    test_p01_macro_security()
    test_p02_scheduler_security()
    print("P0 Audit Tests Finished Successfully.")
