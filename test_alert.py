"""
Mock test for the alert flow — does NOT hit the real portals.
Creates a fake morning slot result and runs it through the full
notification pipeline (SMS + email) exactly as main.py would.

  python test_alert.py
"""
from dotenv import load_dotenv
load_dotenv()

from scraper import ScrapeResult, TimeSlot
from notifications import send_slot_alert

fake_uci = ScrapeResult(
    university="UCI",
    target_date="July 8, 2026",
    connected=True,
    slots=[TimeSlot(time_str="9:00 AM", is_morning=True)],
    morning_slots=[TimeSlot(time_str="9:00 AM", is_morning=True)],
)

print("Sending mock slot alert for UCI 9:00 AM...")
send_slot_alert(fake_uci.university, fake_uci.target_date, fake_uci.morning_slots)
print("Done — check your phone and email.")
