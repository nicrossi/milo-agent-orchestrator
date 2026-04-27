"""
End-to-end test for the courses + students + activity assignment flow.

Steps:
  1. Ensure 3 Firebase users exist (teacher, studentA, studentB) with email+displayName.
  2. Mint custom tokens via firebase-admin, exchange for ID tokens via Identity Toolkit.
  3. Bootstrap each user into the relational `users` table.
  4. As teacher: create a course, enroll studentA, create an activity assigned to that course.
  5. As studentA: list /activities -> activity must be present.
  6. As studentB: list /activities -> activity must be ABSENT.

Run from repo root:
    python scripts/e2e_courses_flow.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional
from uuid import UUID

import subprocess

import firebase_admin
from firebase_admin import auth, credentials

SERVICE_ACCOUNT = "milo-auth-e1505-firebase-adminsdk-fbsvc-f48279203c.json"
WEB_API_KEY = "AIzaSyDC6IcO1nArB0TQK4vZvUAKeswiLfc6JCs"
BASE_URL = "http://localhost:8000"

USERS = [
    {"uid": "test-teacher-001",  "email": "teacher.e2e@milo.test",   "name": "Teacher E2E"},
    {"uid": "test-student-A-001", "email": "studenta.e2e@milo.test", "name": "Student A E2E"},
    {"uid": "test-student-B-001", "email": "studentb.e2e@milo.test", "name": "Student B E2E"},
]


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def fail(msg: str) -> "None":
    print(f"[e2e][FAIL] {msg}", flush=True)
    sys.exit(1)


def http(method: str, path: str, token: Optional[str] = None, body: Optional[dict] = None) -> tuple[int, dict | str]:
    url = f"{BASE_URL}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def ensure_firebase_user(u: dict) -> None:
    try:
        existing = auth.get_user(u["uid"])
        # Update email/displayName if they drifted.
        if existing.email != u["email"] or existing.display_name != u["name"]:
            auth.update_user(u["uid"], email=u["email"], display_name=u["name"])
            log(f"updated firebase user {u['uid']}")
        else:
            log(f"firebase user {u['uid']} already present")
    except auth.UserNotFoundError:
        auth.create_user(uid=u["uid"], email=u["email"], display_name=u["name"])
        log(f"created firebase user {u['uid']}")


def mint_id_token(uid: str) -> str:
    custom = auth.create_custom_token(uid).decode()
    payload = json.dumps({"token": custom, "returnSecureToken": True}).encode()
    req = urllib.request.Request(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={WEB_API_KEY}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    return body["idToken"]


def main() -> None:
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    log(f"cwd={os.getcwd()}")

    # --- Firebase admin init ---
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(SERVICE_ACCOUNT))
    log("firebase-admin initialized")

    # --- Healthcheck ---
    code, body = http("GET", "/healthcheck")
    if code != 200:
        fail(f"orchestrator healthcheck failed: {code} {body}")
    log(f"orchestrator up: {body}")

    # --- Ensure Firebase users + mint ID tokens ---
    tokens: dict[str, str] = {}
    for u in USERS:
        ensure_firebase_user(u)
        tokens[u["uid"]] = mint_id_token(u["uid"])
        log(f"minted id_token for {u['uid']} (len={len(tokens[u['uid']])})")

    teacher_token  = tokens["test-teacher-001"]
    studentA_token = tokens["test-student-A-001"]
    studentB_token = tokens["test-student-B-001"]

    # --- Seed users directly in DB (bootstrap-user endpoint returns 500 — see notes) ---
    seed_sql_parts = []
    for u in USERS:
        role = "teacher" if "teacher" in u["uid"] else "student"
        seed_sql_parts.append(
            "INSERT INTO users (id, email, display_name, role) VALUES "
            f"('{u['uid']}', '{u['email']}', '{u['name']}', '{role}') "
            "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, "
            "display_name = COALESCE(NULLIF(EXCLUDED.display_name, ''), users.display_name), "
            "role = EXCLUDED.role;"
        )
    seed_sql = " ".join(seed_sql_parts)
    env = {**os.environ, "PGPASSWORD": "postgres"}
    res = subprocess.run(
        ["psql", "-h", "localhost", "-U", "postgres", "-d", "milo", "-c", seed_sql],
        capture_output=True, text=True, env=env,
    )
    if res.returncode != 0:
        fail(f"psql user seed failed: {res.stderr}")
    log("seeded 3 users directly into DB via psql")

    # --- TEACHER: create course ---
    code, course = http(
        "POST", "/courses",
        token=teacher_token,
        body={"name": "E2E Curso Multi-Student", "description": "auto-created by e2e_courses_flow.py"},
    )
    if code != 200:
        fail(f"create_course failed: {code} {course}")
    course_id = course["id"]
    log(f"course created id={course_id}")

    # --- TEACHER: enroll studentA only ---
    code, detail = http(
        "POST", f"/courses/{course_id}/students",
        token=teacher_token,
        body={"student_id": "test-student-A-001"},
    )
    if code != 200:
        fail(f"enroll studentA failed: {code} {detail}")
    enrolled_ids = [s["student_id"] for s in detail["students"]]
    if "test-student-A-001" not in enrolled_ids:
        fail(f"studentA missing from enrollment: {detail}")
    if "test-student-B-001" in enrolled_ids:
        fail(f"studentB unexpectedly enrolled: {detail}")
    log(f"studentA enrolled. roster={enrolled_ids}")

    # --- TEACHER: create published activity assigned to that course ---
    code, activity = http(
        "POST", "/activities",
        token=teacher_token,
        body={
            "title": "E2E Reflexion Activity",
            "teacher_goal": "Validar visibilidad por curso",
            "context_description": "Actividad creada por e2e_courses_flow.py",
            "course_ids": [course_id],
            "status": "PUBLISHED",
        },
    )
    if code != 200:
        fail(f"create_activity failed: {code} {activity}")
    activity_id = activity["id"]
    log(f"activity created id={activity_id}")

    # --- STUDENT A: should see the activity ---
    code, listA = http("GET", "/activities", token=studentA_token)
    if code != 200:
        fail(f"list activities (studentA) failed: {code} {listA}")
    visibleA = [a["id"] for a in listA]
    if activity_id not in visibleA:
        fail(f"studentA cannot see activity {activity_id}. visible={visibleA}")
    log(f"OK: studentA sees activity. visible_count={len(visibleA)}")

    # --- STUDENT B: should NOT see the activity ---
    code, listB = http("GET", "/activities", token=studentB_token)
    if code != 200:
        fail(f"list activities (studentB) failed: {code} {listB}")
    visibleB = [a["id"] for a in listB]
    if activity_id in visibleB:
        fail(f"studentB UNEXPECTEDLY sees activity {activity_id}. visible={visibleB}")
    log(f"OK: studentB does NOT see activity. visible_count={len(visibleB)}")

    log("=" * 60)
    log("ALL ASSERTIONS PASSED")
    log(f"  course_id   = {course_id}")
    log(f"  activity_id = {activity_id}")
    log(f"  studentA visible activities: {len(visibleA)} (includes target)")
    log(f"  studentB visible activities: {len(visibleB)} (excludes target)")


if __name__ == "__main__":
    main()
