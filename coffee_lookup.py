"""Bean profile lookup via the find-coffee companion app API.

Queries the find-coffee Flask app for professional cupping notes,
flavor dimension scores, and cupping chart scores for beans.

Lifecycle: callers should use ensure_server_running() before a batch
of lookups, then stop_server() when done. If the server was already
running externally, stop_server() leaves it alone.
"""

import os
import subprocess
import time

import requests

# Track whether WE started the server (vs it was already running)
_server_process = None
_we_started_it = False


def _get_base_url():
    """Get the find-coffee API base URL from FIND_COFFEE_URL env var."""
    return os.environ.get("FIND_COFFEE_URL")


def _is_server_up(url):
    """Quick health check — hit the API root to see if it responds."""
    try:
        resp = requests.get(f"{url}/api/purchased_coffees", timeout=2)
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def ensure_server_running(base_url=None):
    """Check if find-coffee is responding. Auto-start if needed.

    If the server is already running externally, we leave it alone.
    If we have to start it ourselves, we track that so stop_server()
    only kills a process we own.

    Call this once before a batch of lookups, not per-lookup.

    Args:
        base_url: Override the default URL.

    Returns:
        (True, status_message) if the server is responding.
        (False, status_message) with reason on failure.
    """
    global _server_process, _we_started_it
    url = base_url or _get_base_url()
    if not url:
        return False, "FIND_COFFEE_URL not set"

    # Already running (either externally or by us) — nothing to do
    if _is_server_up(url):
        return True, "find-coffee server already running"

    # Try to start it via the wrapper script
    wrapper = os.environ.get("FIND_COFFEE_WRAPPER")
    if not wrapper:
        return False, "FIND_COFFEE_WRAPPER not set"
    # Expand ~ so paths like ~/.local/bin/run_find-coffee resolve correctly
    wrapper = os.path.expanduser(wrapper)
    if not os.path.exists(wrapper):
        return False, f"FIND_COFFEE_WRAPPER not found: {wrapper}"

    try:
        _server_process = subprocess.Popen(
            [wrapper, "-m", "find_coffee.cli", "web", "--port", "5000", "--no-debug"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _we_started_it = True

        # Wait up to 10 seconds for the server to start
        for _ in range(20):
            time.sleep(0.5)
            if _is_server_up(url):
                return True, "find-coffee server started"

        # Server didn't start in time — clean up
        stop_server()
        return False, f"find-coffee server failed to start within 10s (wrapper: {wrapper})"

    except (OSError, subprocess.SubprocessError) as e:
        return False, f"find-coffee server failed to launch: {e}"


def stop_server():
    """Stop the find-coffee server ONLY if we started it ourselves.

    If the user had find-coffee running before we checked, we don't
    touch it. Call this once after a batch of lookups is complete.
    """
    global _server_process, _we_started_it
    if _server_process is not None and _we_started_it:
        try:
            _server_process.terminate()
            _server_process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                _server_process.kill()
                _server_process.wait(timeout=2)
            except OSError:
                pass
        _server_process = None
        _we_started_it = False


def lookup_bean(bean_name, base_url=None):
    """Search find-coffee for a matching bean.

    The server must already be running (call ensure_server_running() first).
    The API does case-insensitive LIKE matching.
    Returns (result_dict, status) or (None, status).

    Args:
        bean_name: Name to search for (e.g., "Ethiopia Gerba").
        base_url: Override the default URL.

    Returns:
        Tuple of (coffee_data_dict or None, status_string).
    """
    url = base_url or _get_base_url()
    if not url:
        return None, "FIND_COFFEE_URL not set"

    # Check the server is actually responding
    if not _is_server_up(url):
        return None, "find-coffee server not responding"

    try:
        resp = requests.get(
            f"{url}/api/purchased_coffees",
            params={"name": bean_name},
            timeout=5,
        )
        if resp.status_code != 200:
            return None, f"find-coffee API returned HTTP {resp.status_code}"

        results = resp.json()
        if not results:
            # Try a shorter search term (first two words)
            words = bean_name.strip().split()
            if len(words) > 2:
                short_name = " ".join(words[:2])
                resp = requests.get(
                    f"{url}/api/purchased_coffees",
                    params={"name": short_name},
                    timeout=5,
                )
                if resp.status_code == 200:
                    results = resp.json()

        if results:
            return results[0], "found"
        return None, f"no match for '{bean_name}' in find-coffee database"

    except (requests.RequestException, ValueError) as e:
        return None, f"find-coffee query failed: {e}"


def extract_bean_profile(coffee_data):
    """Extract fields relevant to roast analysis from API response.

    Args:
        coffee_data: Dict from the find-coffee API (a purchased coffee record).

    Returns:
        Dict with cupping notes, flavor scores, and cupping chart scores.
        Returns None if coffee_data is None.
    """
    if not coffee_data:
        return None

    # Flavor dimension scores (integer 0-10)
    flavor_scores = {
        "floral": coffee_data.get("floral_score", 0),
        "honey": coffee_data.get("honey_score", 0),
        "sugar": coffee_data.get("sugar_score", 0),
        "caramel": coffee_data.get("caramel_score", 0),
        "fruit": coffee_data.get("fruit_score", 0),
        "citrus": coffee_data.get("citrus_score", 0),
        "berry": coffee_data.get("berry_score", 0),
        "cocoa": coffee_data.get("cocoa_score", 0),
        "nut": coffee_data.get("nut_score", 0),
        "rustic": coffee_data.get("rustic_score", 0),
        "spice": coffee_data.get("spice_score", 0),
        "body": coffee_data.get("body_score", 0),
    }

    # Cupping chart scores (float)
    cupping_scores = {
        "dry_fragrance": coffee_data.get("dry_fragrance_score", 0),
        "wet_aroma": coffee_data.get("wet_aroma_score", 0),
        "brightness": coffee_data.get("brightness_score", 0),
        "flavor": coffee_data.get("flavor_score", 0),
        "body": coffee_data.get("cupping_body_score", 0),
        "finish": coffee_data.get("finish_score", 0),
        "sweetness": coffee_data.get("sweetness_score", 0),
        "clean_cup": coffee_data.get("clean_cup_score", 0),
        "complexity": coffee_data.get("complexity_score", 0),
        "uniformity": coffee_data.get("uniformity_score", 0),
    }

    # Identify dominant flavors (top 3 scoring dimensions)
    sorted_flavors = sorted(flavor_scores.items(), key=lambda x: x[1], reverse=True)
    dominant_flavors = [(name, score) for name, score in sorted_flavors if score > 0][:3]

    return {
        "name": coffee_data.get("name", ""),
        "cupping_notes": coffee_data.get("cupping_notes", ""),
        "overall_score": coffee_data.get("score", 0),
        "chart_score": coffee_data.get("chart_score", 0),
        "flavor_scores": flavor_scores,
        "cupping_scores": cupping_scores,
        "dominant_flavors": dominant_flavors,
        "attributes": coffee_data.get("attributes", ""),
    }
