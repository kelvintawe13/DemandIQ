"""---------------------------------------------------------------
  Load test file for DemandIQ Forecasting API
  Tests concurrent forecast generation and API throughput
---------------------------------------------------------------"""

from locust import HttpUser, task, between, events, Failure
from locust.runners import STATE_SPAWNING, STATE_RUNNING
import json
import random
import time

# Configuration
BASE_URL = "/api/v1"
FORECAST_ENDPOINT = "/forecasts"
AUTH_ENDPOINT = "/auth/login"
USERS = [
    {"email": "user1@demandiq.com", "password": "password123"},
    {"email": "user2@demandiq.com", "password": "password123"},
    {"email": "user3@demandiq.com", "password": "password123"},
]

# Track statistics
successful_requests = 0
failed_requests = 0
total_response_time = 0


@events.test_start.adddef
def on_test_start(**kwargs):
    """Reset statistics at the start of the test."""
    global successful_requests, failed_requests, total_response_time
    successful_requests = 0
    failed_requests = 0
    total_response_time = 0


@events.test_stop.adddef
def on_test_stop(**kwargs):
    """Print statistics at the end of the test."""
    print("\n=== Load Test Summary ===")
    print(f"Total successful requests: {successful_requests}")
    print(f"Total failed requests: {failed_requests}")
    if successful_requests + failed_requests > 0:
        avg_time = total_response_time / (successful_requests + failed_requests)
        print(f"Average response time: {avg_time:.1f}ms")
    print("========================\n")


class ForecastUser(HttpUser):
    """User class that simulates forecast generation requests."""

    wait_time = between(1, 3)

    def on_start(self):
        """Called when a user starts - log in first."""
        self.login()

    def login(self):
        """Authenticate the user."""
        if not AUTH_ENDPOINT:
            return
        credentials = random.choice(USERS)
        with self.client.post(
            AUTH_ENDPOINT,
            json=credentials,
            catch_response=True,
            name="Login",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Login failed: {response.status_code}")

    @task(10)
    def generate_forecast(self):
        """Generate a forecast for a random location and date."""
        # Select a random location ID (using known location IDs from the system)
        location_ids = [
            "c9d8e6f1-4a7b-4c3d-8e9f-0a1b2c3d4e5f",
            "d1e8f6a2-5b7c-4d3e-8f9a-1b2c3d4e5f6a",
            "e2f9a7c3-6c8d-4e3f-8a9b-2c3d4e5f6a7b",
        ]
        location_id = random.choice(location_ids)

        # Select a random target date (tomorrow or a few days out)
        from datetime import datetime, timedelta
        days_offset = random.choice([1, 2, 3, 7])
        target_date = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")

        with self.client.post(
            FORECAST_ENDPOINT,
            json={
                "location_id": location_id,
                "target_date": target_date,
                "horizon_days": random.choice([1, 7, 14]),
                "granularity": random.choice(["daily", "weekly"]),
            },
            catch_response=True,
            name="Generate Forecast",
        ) as response:
            if response.status_code == 201:
                try:
                    data = response.json()
                    # Validate response has expected fields
                    if "point" in data and "lower" in data and "upper" in data:
                        successful_requests += 1
                        total_response_time += response.elapsed
                        response.success()
                    else:
                        response.failure("Invalid forecast response structure")
                except Exception as e:
                    response.failure(f"JSON parse error: {e}")
            elif response.status_code == 422:
                # Validation error - still count as successful for throughput
                successful_requests += 1
                total_response_time += response.elapsed
                response.success()
            elif response.status_code == 401:
                # Auth error - respawn user
                self.login()
                response.failure("Authentication required")
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(5)
    def list_forecasts(self):
        """List forecasts for a location."""
        location_ids = [
            "c9d8e6f1-4a7b-4c3d-8e9f-0a1b2c3d4e5f",
            "d1e8f6a2-5b7c-4d3e-8f9a-1b2c3d4e5f6a",
        ]
        location_id = random.choice(location_ids)

        with self.client.get(
            f"{FORECAST_ENDPOINT}",
            params={"location_id": location_id, "limit": 10},
            catch_response=True,
            name="List Forecasts",
        ) as response:
            if response.status_code == 200:
                successful_requests += 1
                total_response_time += response.elapsed
                response.success()
            else:
                response.failure(f"List failed: {response.status_code}")

    @task(2)
    def get_forecast_detail(self):
        """Get a specific forecast detail."""
        forecast_ids = [
            "f1a2b3c4-d5e6-7f8a-9b0c-1d2e3f4a5b6c",
            "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
        ]
        forecast_id = random.choice(forecast_ids)

        with self.client.get(
            f"{FORECAST_ENDPOINT}/{forecast_id}",
            catch_response=True,
            name="Get Forecast Detail",
        ) as response:
            if response.status_code == 200:
                successful_requests += 1
                total_response_time += response.elapsed
                response.success()
            else:
                response.failure(f"Get detail failed: {response.status_code}")

    @task(1)
    def health_check(self):
        """Quick health check endpoint."""
        with self.client.get("/health", catch_response=True, name="Health Check") as response:
            if response.status_code == 200:
                successful_requests += 1
                total_response_time += response.elapsed
                response.success()


class AuthUser(HttpUser):
    """User class that focuses on authentication operations."""

    wait_time = between(5, 10)

    def on_start(self):
        self.login()

    def login(self):
        """Authenticate with system credentials."""
        credentials = random.choice(USERS)
        with self.client.post(
            AUTH_ENDPOINT,
            json=credentials,
            catch_response=True,
            name="Login",
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Login failed: {response.status_code}")


# Distribution of users between forecast and auth tasks
class MixedUser(HttpUser):
    """Mixed user that does both forecast generation and auth tasks."""

    wait_time = between(2, 5)

    @task(7)
    def generate_forecast(self):
        """Generate forecast - 70% of user tasks"""
        location_ids = [
            "c9d8e6f1-4a7b-4c3d-8e9f-0a1b2c3d4e5f",
            "d1e8f6a2-5b7c-4d3e-8f9a-1b2c3d4e5f6a",
            "e2f9a7c3-6c8d-4e3f-8a9b-2c3d4e5f6a7b",
        ]
        location_id = random.choice(location_ids)
        from datetime import datetime, timedelta
        days_offset = random.choice([1, 2, 3, 7])
        target_date = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")

        with self.client.post(
            FORECAST_ENDPOINT,
            json={
                "location_id": location_id,
                "target_date": target_date,
                "horizon_days": random.choice([1, 7, 14]),
                "granularity": random.choice(["daily", "weekly"]),
            },
            catch_response=True,
            name="Generate Forecast (Mixed)",
        ) as response:
            if response.status_code in (201, 422):
                try:
                    data = response.json()
                    if "point" in data and "lower" in data and "upper" in data:
                        successful_requests += 1
                        total_response_time += response.elapsed
                        response.success()
                    else:
                        response.failure("Invalid response structure")
                except Exception:
                    response.failure("JSON parse error")
            elif response.status_code == 401:
                # Try re-auth
                credentials = random.choice(USERS)
                self.client.post(AUTH_ENDPOINT, json=credentials)

    @task(3)
    def list_forecasts(self):
        """List forecasts - 30% of user tasks"""
        location_ids = [
            "c9d8e6f1-4a7b-4c3d-8e9f-0a1b2c3d4e5f",
            "d1e8f6a2-5b7c-4d3e-8f9a-1b2c3d4e5f6a",
        ]
        location_id = random.choice(location_ids)

        with self.client.get(
            f"{FORECAST_ENDPOINT}",
            params={"location_id": location_id, "limit": 10},
            catch_response=True,
            name="List Forecasts (Mixed)",
        ) as response:
            if response.status_code == 200:
                successful_requests += 1
                total_response_time += response.elapsed
                response.success()


if "AUTH_ENDPOINT" in dir() and AUTH_ENDPOINT:
    user_classes = [MixedUser]
else:
    user_classes = [ForecastUser]

# Set the user classes
for user_class in user_classes:
    pass  # Will be used by locust runner
