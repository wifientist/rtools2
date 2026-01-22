#!/usr/bin/env python3
"""
Quick SmartZone connectivity test script.
Usage: python test_sz_connection.py [host] [port] [username] [password] [api_version]

Or run inside docker:
  docker-compose exec rtools-backend python test_sz_connection.py 52.5.169.114 8443 admin mypass v11_1
"""

import sys
import socket

# Defaults - override with command line args
HOST = "52.5.169.114"
PORT = 8443
USERNAME = ""
PASSWORD = ""
API_VERSION = "v11_1"

def test_tcp(host: str, port: int) -> bool:
    """Test basic TCP connectivity"""
    print(f"\n[1] Testing TCP connection to {host}:{port}...")
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.close()
        print(f"    ✓ TCP connection successful")
        return True
    except socket.timeout:
        print(f"    ✗ Connection timed out")
        return False
    except ConnectionRefusedError:
        print(f"    ✗ Connection refused (port closed or filtered)")
        return False
    except Exception as e:
        print(f"    ✗ Failed: {e}")
        return False

def test_https(host: str, port: int) -> bool:
    """Test HTTPS connectivity"""
    print(f"\n[2] Testing HTTPS connection to {host}:{port}...")
    try:
        import httpx
        url = f"https://{host}:{port}"
        response = httpx.get(url, verify=False, timeout=10)
        print(f"    ✓ HTTPS connection successful (status: {response.status_code})")
        return True
    except Exception as e:
        print(f"    ✗ Failed: {e}")
        return False

def test_api_endpoint(host: str, port: int, api_version: str) -> bool:
    """Test SmartZone API endpoint is reachable"""
    print(f"\n[3] Testing SmartZone API endpoint ({api_version})...")
    try:
        import httpx
        # Just test that the endpoint exists (will return 401 or similar without auth)
        url = f"https://{host}:{port}/wsg/api/public/{api_version}/serviceTicket"
        response = httpx.post(url, json={}, verify=False, timeout=10)
        print(f"    ✓ API endpoint reachable (status: {response.status_code})")
        if response.status_code == 400:
            print(f"    ✓ SmartZone API responding (expects credentials)")
        return True
    except Exception as e:
        print(f"    ✗ Failed: {e}")
        return False

def test_login(host: str, port: int, username: str, password: str, api_version: str) -> bool:
    """Test actual SmartZone login"""
    if not username or not password:
        print(f"\n[4] Skipping login test (no credentials provided)")
        return True

    print(f"\n[4] Testing SmartZone login as '{username}'...")
    try:
        import httpx
        url = f"https://{host}:{port}/wsg/api/public/{api_version}/serviceTicket"
        response = httpx.post(
            url,
            json={"username": username, "password": password},
            verify=False,
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            ticket = data.get("serviceTicket", "")[:20] + "..." if data.get("serviceTicket") else "none"
            print(f"    ✓ Login successful! Service ticket: {ticket}")
            return True
        else:
            print(f"    ✗ Login failed (status: {response.status_code})")
            print(f"    Response: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"    ✗ Failed: {e}")
        return False

def main():
    global HOST, PORT, USERNAME, PASSWORD, API_VERSION

    # Parse command line args
    if len(sys.argv) >= 2:
        HOST = sys.argv[1]
    if len(sys.argv) >= 3:
        PORT = int(sys.argv[2])
    if len(sys.argv) >= 4:
        USERNAME = sys.argv[3]
    if len(sys.argv) >= 5:
        PASSWORD = sys.argv[4]
    if len(sys.argv) >= 6:
        API_VERSION = sys.argv[5]

    print("=" * 50)
    print("SmartZone Connectivity Test")
    print("=" * 50)
    print(f"Host:        {HOST}")
    print(f"Port:        {PORT}")
    print(f"API Version: {API_VERSION}")
    print(f"Username:    {USERNAME or '(not provided)'}")
    print(f"Password:    {'***' if PASSWORD else '(not provided)'}")

    results = []

    # Run tests
    results.append(("TCP", test_tcp(HOST, PORT)))

    if results[-1][1]:  # Only continue if TCP works
        results.append(("HTTPS", test_https(HOST, PORT)))

        if results[-1][1]:  # Only continue if HTTPS works
            results.append(("API Endpoint", test_api_endpoint(HOST, PORT, API_VERSION)))
            results.append(("Login", test_login(HOST, PORT, USERNAME, PASSWORD, API_VERSION)))

    # Summary
    print("\n" + "=" * 50)
    print("Summary")
    print("=" * 50)
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {name}: {status}")

    all_passed = all(r[1] for r in results)
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed."))
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
