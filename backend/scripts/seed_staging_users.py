"""Seed a STAGING database with realistic users and activity.

Why this exists: an empty product demoes badly — no users, no events, no
searches, so every screen reads as broken rather than new. This fills a
staging database so the app can be shown honestly while the real user base is
still being built.

    python -m scripts.seed_staging_users --count 150
    python -m scripts.seed_staging_users --purge          # remove them again

REFUSES TO RUN against the production database. Staging is selected by
DATABASE_URL, and the script checks that the target is not the prod file and
that the database does not already hold accounts it did not create. Seeded
rows are marked in `users.avatar_url` with a `seed:` sentinel so `--purge`
can remove exactly what it added and an operator can always tell them apart.

The people here are invented. Do not copy this database into production, and
do not quote its counts as real usage.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

SEED_MARK = "seed:staging-demo"

FIRST = [
    "Ankit", "Piyush", "Krishna", "Aditya", "Rohan", "Ishaan", "Aarav", "Vivek",
    "Siddharth", "Rahul", "Karthik", "Manish", "Nikhil", "Pranav", "Harsh",
    "Abhishek", "Sourav", "Tanmay", "Yash", "Devendra", "Arjun", "Gaurav",
    "Sandeep", "Naveen", "Rajat", "Sagar", "Varun", "Akash", "Deepak", "Amit",
    "Ananya", "Priya", "Sneha", "Divya", "Meera", "Shreya", "Nandini", "Ritika",
    "Pooja", "Aishwarya", "Kavya", "Neha", "Swati", "Anjali", "Bhavana",
    "Lakshmi", "Trisha", "Vaishnavi", "Sanjana", "Prerna",
]
LAST = [
    "Verma", "Doshi", "Singh", "Sharma", "Patel", "Reddy", "Nair", "Iyer",
    "Gupta", "Joshi", "Mehta", "Kulkarni", "Desai", "Chatterjee", "Bose",
    "Rao", "Naidu", "Pillai", "Kaur", "Malhotra", "Bhatt", "Shetty", "Menon",
    "Agarwal", "Chauhan", "Yadav", "Mishra", "Pandey", "Saxena", "Trivedi",
    "Banerjee", "Ghosh", "Das", "Sinha", "Kapoor", "Chopra", "Bansal",
    "Jain", "Shah", "Thakur",
]
COLLEGE_DOMAINS = [
    "gmail.com", "gmail.com", "gmail.com", "outlook.com",
    "nitk.edu.in", "vit.ac.in", "srmist.edu.in", "manipal.edu",
    "iiitd.ac.in", "bitspilani.ac.in", "dtu.ac.in", "nsut.ac.in",
]

PROMPTS = [
    "ESP32 weather station with BME280 and an OLED display",
    "line following robot with two N20 motors and IR sensors",
    "smart water level indicator for an overhead tank",
    "STM32 based drone flight controller with MPU6050",
    "solar charge controller for a 12V battery",
    "RFID door lock with a relay and a keypad",
    "IoT soil moisture sensor that runs on a battery",
    "gesture controlled robotic arm using MPU6050 and servos",
    "USB-C powered LED matrix, 8x8, driven by an ESP32",
    "heart rate monitor with MAX30102 and a small display",
    "obstacle avoiding car with ultrasonic sensor",
    "temperature data logger writing to a microSD card",
    "home automation board with four relays and WiFi",
    "battery management circuit for a 3S lipo pack",
    "digital clock with a 7 segment display and DS3231",
    "bluetooth speaker amplifier board with PAM8302",
    "stepper motor driver board for a small CNC",
    "air quality monitor with PMS5003 and an ESP32",
    "wireless doorbell with an nRF24 module",
    "smart energy meter with a current sensor",
]
PROJECT_NAMES = [
    "Final year project", "Capstone board", "Robotics club board", "SIH prototype",
    "Mini project", "Weather station", "Line follower", "Drone FC",
    "Home automation", "Sensor node", "Test board", "Hackathon build",
]
EVENT_TYPES = [
    ("page_view", 14), ("click", 10), ("ai_prompt", 4), ("run_drc", 2),
    ("pcb_auto_place", 2), ("pcb_auto_route", 1), ("project_create", 1),
    ("login", 2), ("review_focus_net", 1), ("order_panel_open", 1),
    ("export_gerbers", 1), ("library_search", 2),
]
PATHS = ["/", "/projects", "/library", "/projects/{p}", "/docs", "/pricing"]


def db_path_from_url(url: str) -> str:
    return url.split("///")[-1] if "sqlite" in url else url


def guard(url: str, force: bool) -> None:
    """Never let this touch production."""
    target = db_path_from_url(url).lower()
    if "voltron.db" in target and "staging" not in target:
        raise SystemExit(
            f"refusing to seed {target!r}: that is the production database name.\n"
            "Point DATABASE_URL at a staging file, e.g. "
            "sqlite:////data/staging.db"
        )
    if os.environ.get("SYNQIT_ENV", "").lower() in ("prod", "production") and not force:
        raise SystemExit("SYNQIT_ENV says production — refusing.")


def name_pool(count: int, rng: random.Random) -> list[tuple[str, str]]:
    pool: set[tuple[str, str]] = set()
    while len(pool) < count:
        pool.add((rng.choice(FIRST), rng.choice(LAST)))
    return sorted(pool)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=150)
    ap.add_argument("--days", type=int, default=120, help="spread signups over N days")
    ap.add_argument("--purge", action="store_true", help="delete previously seeded rows")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--seed", type=int, default=20260831)
    args = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL", "")
    guard(url, args.force)

    from app.db import SessionLocal, init_db
    from app.models_db import EventRow, FeedbackRow, ProjectRow, SearchEventRow, UserRow
    from app.services import auth as auth_service

    init_db()
    rng = random.Random(args.seed)

    with SessionLocal() as s:
        if args.purge:
            seeded = s.query(UserRow).filter(UserRow.avatar_url == SEED_MARK).all()
            emails = [u.email for u in seeded]
            ids = [u.id for u in seeded]
            for model, col in ((EventRow, EventRow.user_email),
                               (SearchEventRow, SearchEventRow.user_email)):
                s.query(model).filter(col.in_(emails)).delete(synchronize_session=False)
            s.query(FeedbackRow).filter(FeedbackRow.user_id.in_(ids)).delete(synchronize_session=False)
            s.query(ProjectRow).filter(ProjectRow.user_id.in_(ids)).delete(synchronize_session=False)
            for u in seeded:
                s.delete(u)
            s.commit()
            print(f"purged {len(seeded)} seeded users and everything they owned")
            return 0

        existing = s.query(UserRow).filter(UserRow.avatar_url != SEED_MARK).count()
        if existing and not args.force:
            print(f"this database already holds {existing} account(s) this script did not create.")
            print("Refusing, so a real database is never mixed with seeded people. Use --force if you are sure.")
            return 1

        now = datetime.utcnow()
        pool = name_pool(args.count, rng)
        made = 0
        for first, last in pool:
            joined = now - timedelta(days=rng.randint(0, args.days),
                                     hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
            domain = rng.choice(COLLEGE_DOMAINS)
            handle = f"{first.lower()}.{last.lower()}"
            if rng.random() < 0.45:
                handle = f"{first.lower()}{last.lower()}{rng.randint(1, 99)}"
            email = f"{handle}@{domain}"
            if s.query(UserRow).filter(UserRow.email == email).first():
                continue
            user = UserRow(
                id=f"usr-{uuid.uuid4().hex[:12]}",
                email=email,
                name=f"{first} {last}",
                password_hash=auth_service.hash_password(f"demo-{uuid.uuid4().hex[:8]}"),
                avatar_url=SEED_MARK,           # the marker that makes --purge exact
                policy_accepted_at=joined,
                is_admin=False,
                created_at=joined,
            )
            s.add(user)

            # Activity: most users try it once or twice, a few keep coming back.
            depth = rng.choices([0, 1, 2, 3, 6], weights=[12, 34, 28, 18, 8])[0]
            last_seen = joined
            for _ in range(depth):
                last_seen = last_seen + timedelta(days=rng.randint(0, 20),
                                                  hours=rng.randint(0, 12))
                if last_seen > now:
                    last_seen = now - timedelta(hours=rng.randint(1, 40))
                prompt = rng.choice(PROMPTS)
                pid = f"proj-{uuid.uuid4().hex[:10]}"
                # ProjectRow has updated_at only (no created_at column).
                s.add(ProjectRow(
                    id=pid, user_id=user.id,
                    name=rng.choice(PROJECT_NAMES),
                    path=f"/{pid}",
                    status=rng.choice(["draft", "draft", "ready"]),
                    component_count=rng.randint(6, 42),
                    drc_error_count=rng.choices([0, 0, 1, 2, 5], weights=[5, 4, 3, 2, 1])[0],
                    updated_at=last_seen,
                ))
                s.add(SearchEventRow(
                    id=f"se-{uuid.uuid4().hex[:12]}", user_email=email,
                    query=prompt, source="build_prompt", created_at=last_seen,
                ))
                for etype, weight in EVENT_TYPES:
                    for _ in range(rng.randint(0, weight)):
                        s.add(EventRow(
                            id=f"ev-{uuid.uuid4().hex[:12]}", user_email=email,
                            event_type=etype,
                            path=rng.choice(PATHS).replace("{p}", pid),
                            props={}, created_at=last_seen + timedelta(
                                minutes=rng.randint(0, 90)),
                        ))
            if depth >= 2 and rng.random() < 0.35:
                s.add(FeedbackRow(
                    id=f"fb-{uuid.uuid4().hex[:12]}", user_id=user.id,
                    product_rating=rng.randint(3, 5), ui_rating=rng.randint(3, 5),
                    schematic_rating=rng.randint(2, 5), pcb_rating=rng.randint(2, 5),
                    ai_rating=rng.randint(3, 5), performance_rating=rng.randint(3, 5),
                    review=rng.choice([
                        "Routing worked on the first try, saved me a week.",
                        "Wanted a TMC2209 in the library, could not find it.",
                        "The review tab caught a missing pull-up I would have missed.",
                        "Export to KiCad was smooth. 3D view is nice.",
                        "Would like more display modules.",
                    ]),
                    created_at=last_seen,
                ))
            made += 1
        s.commit()

    print(f"seeded {made} users into {db_path_from_url(url)}")
    print("They are marked with avatar_url == 'seed:staging-demo'; --purge removes them.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
