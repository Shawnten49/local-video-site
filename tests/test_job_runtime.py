import threading
import unittest

from local_video_site.jobs.runtime import InvalidJobTransition, JobOutcome, JobResult, JobRunner, JobStateMachine


class JobRuntimeTests(unittest.TestCase):
    def test_state_machine_accepts_valid_and_rejects_terminal_reopen(self):
        JobStateMachine.validate("pending", "running")
        JobStateMachine.validate("running", "success")
        JobStateMachine.validate("running", "pending")
        self.assertFalse(JobStateMachine.is_active("success"))
        self.assertTrue(JobStateMachine.is_active("needs_cleanup"))
        self.assertTrue(JobStateMachine.is_terminal("cancelled"))
        with self.assertRaises(InvalidJobTransition):
            JobStateMachine.validate("success", "running")

    def test_runner_claims_until_stopped_and_counts_failures(self):
        jobs = [{"id": 1}, {"id": 2}, {"id": 3}]
        jobs_lock = threading.Lock()
        processed = []
        stop_event = threading.Event()

        def claim():
            with jobs_lock:
                if jobs:
                    return jobs.pop(0)
            stop_event.set()
            return None

        def process(job):
            processed.append(job["id"])
            if job["id"] == 2:
                raise RuntimeError("expected test failure")

        runner = JobRunner(
            name="test-runner",
            stop_event=stop_event,
            claim_job=claim,
            process_job=process,
            poll_seconds=lambda: 0.01,
            enable_leases=False,
        )
        runner.run()
        snapshot = runner.snapshot()
        self.assertEqual(processed, [1, 2, 3])
        self.assertEqual(snapshot.processed, 3)
        self.assertEqual(snapshot.succeeded, 2)
        self.assertEqual(snapshot.failures, 1)
        self.assertEqual(snapshot.last_job_id, "3")

    def test_result_failure_is_counted_without_exception(self):
        jobs = [{"id": 1}]
        stop_event = threading.Event()

        def claim():
            if jobs:
                return jobs.pop(0)
            stop_event.set()
            return None

        runner = JobRunner(
            name="result-runner",
            stop_event=stop_event,
            claim_job=claim,
            process_job=lambda _job: JobResult(
                JobOutcome.FAILED,
                "handled failure",
            ),
            poll_seconds=lambda: 0.01,
            enable_leases=False,
        )
        runner.run()
        snapshot = runner.snapshot()
        self.assertEqual(snapshot.processed, 1)
        self.assertEqual(snapshot.failures, 1)
        self.assertEqual(snapshot.last_outcome, "failed")


if __name__ == "__main__":
    unittest.main()
