"""
Seed 2 teachers + 3 students via the open /admin/users endpoint.
Skips users that already exist (409 from Firebase) and reports their stored credentials only on first creation.

Run from repo root:
    python scripts/seed_admin_accounts.py
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
ACCOUNTS = [
    {"display_name": "Profesora Ana",   "email": "ana.teacher@milo.test",   "password": "Teach-Ana-1234"},
    {"display_name": "Profesor Bruno",  "email": "bruno.teacher@milo.test", "password": "Teach-Bruno-1234"},
    {"display_name": "Alumno Carlos",   "email": "carlos.student@milo.test","password": "Stud-Carlos-1234"},
    {"display_name": "Alumna Diana",    "email": "diana.student@milo.test", "password": "Stud-Diana-1234"},
    {"display_name": "Alumno Eduardo",  "email": "eduardo.student@milo.test","password":"Stud-Eduardo-1234"},
]


def post(path: str, body: dict) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except json.JSONDecodeError:
            return e.code, e.read().decode()


def main() -> None:
    print("=" * 78)
    print(f"{'role':<8} {'email':<32} {'password':<22} {'uid'}")
    print("-" * 78)
    for acct in ACCOUNTS:
        status, body = post("/admin/users", acct)
        if status == 200:
            role = "TEACHER" if "teacher" in acct["email"] else "STUDENT"
            print(f"{role:<8} {body['email']:<32} {body['password']:<22} {body['uid']}")
        elif status == 409:
            role = "TEACHER" if "teacher" in acct["email"] else "STUDENT"
            print(f"{role:<8} {acct['email']:<32} {acct['password']:<22} (already exists — same password)")
        else:
            print(f"FAIL {acct['email']}: {status} {body}")
    print("=" * 78)
    print(f"\nDashboard:  {BASE}/admin\n")


if __name__ == "__main__":
    main()
