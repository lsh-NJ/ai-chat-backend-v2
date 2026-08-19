"""End-to-end load scenario for normal and streaming chat endpoints."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests
from locust import HttpUser, between, events, task
from locust.exception import StopUser
from locust.stats import StatsEntry

PASSWORD = "load-test-password"
EXPECTED_REPLY = "mock assistant reply"
SETUP_TIMEOUT_SECONDS = 10
SUMMARY_PATH = Path("/results/week8_summary.json")


def _serialize_stats(entry: StatsEntry) -> dict[str, float | int | str | None]:
    return {
        "method": entry.method or "ALL",
        "name": entry.name,
        "request_count": entry.num_requests,
        "failure_count": entry.num_failures,
        "error_rate_percent": round(entry.fail_ratio * 100, 4),
        "requests_per_second": round(entry.total_rps, 2),
        "average_ms": round(entry.avg_response_time, 2),
        "p50_ms": entry.get_response_time_percentile(0.50),
        "p95_ms": entry.get_response_time_percentile(0.95),
        "min_ms": entry.min_response_time,
        "max_ms": entry.max_response_time,
    }


@events.test_stop.add_listener
def write_final_summary(environment, **_kwargs: object) -> None:
    """Persist final counters after all in-flight requests have completed."""
    options = environment.parsed_options
    entries = sorted(
        environment.stats.entries.values(),
        key=lambda entry: (entry.name, entry.method or ""),
    )
    summary = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "configured_users": getattr(options, "num_users", None),
        "spawn_rate": getattr(options, "spawn_rate", None),
        "run_time_seconds": getattr(options, "run_time", None),
        "endpoints": [_serialize_stats(entry) for entry in entries],
        "aggregated": _serialize_stats(environment.stats.total),
    }
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class ChatUser(HttpUser):
    wait_time = between(0.3, 0.7)

    def on_start(self) -> None:
        username = f"load-{uuid4().hex[:20]}"
        try:
            register_response = requests.post(
                f"{self.host}/auth/register",
                json={"username": username, "password": PASSWORD},
                timeout=SETUP_TIMEOUT_SECONDS,
            )
            register_response.raise_for_status()

            login_response = requests.post(
                f"{self.host}/auth/login",
                data={"username": username, "password": PASSWORD},
                timeout=SETUP_TIMEOUT_SECONDS,
            )
            login_response.raise_for_status()
            token = login_response.json()["access_token"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            self.environment.process_exit_code = 2
            if self.environment.runner is not None:
                self.environment.runner.quit()
            raise StopUser() from exc

        self.auth_headers = {"Authorization": f"Bearer {token}"}

    @task(1)
    def normal_chat(self) -> None:
        with self.client.post(
            "/chat",
            name="POST /chat",
            headers=self.auth_headers,
            json={"conversation_id": None, "message": "load test message"},
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"unexpected status {response.status_code}")
                return
            try:
                payload = response.json()
            except ValueError:
                response.failure("response is not JSON")
                return
            if payload.get("reply") != EXPECTED_REPLY:
                response.failure("unexpected reply")
            elif not isinstance(payload.get("conversation_id"), int):
                response.failure("missing integer conversation_id")

    @task(1)
    def streaming_chat(self) -> None:
        with self.client.post(
            "/chat/stream",
            name="POST /chat/stream",
            headers=self.auth_headers,
            json={"conversation_id": None, "message": "load test stream"},
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"unexpected status {response.status_code}")
            elif not response.headers.get("X-Conversation-Id", "").isdigit():
                response.failure("missing X-Conversation-Id")
            elif response.text != EXPECTED_REPLY:
                response.failure("unexpected streamed reply")
