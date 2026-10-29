import logging
import random
import time
from pathlib import Path

LOG_FILE = Path(__file__).parent / "logs" / "app.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
    force=True,
)

logger = logging.getLogger("hotel_automation")

rooms = [101, 102, 103, 201, 202]
guests = ["Ravi", "Priya", "Anil", "Kavya", "Suresh"]

logger.info("Hotel automation application started")

try:
    while True:
        guest = random.choice(guests)
        room = random.choice(rooms)
        event = random.randint(1, 6)

        if event == 1:
            logger.info(
                "Booking confirmed for guest %s, room %s",
                guest, room,
            )
        elif event == 2:
            logger.info(
                "Guest %s checked into room %s",
                guest, room,
            )
        elif event == 3:
            logger.info(
                "Payment received for guest %s",
                guest,
            )
        elif event == 4:
            logger.warning(
                "Room %s cleaning delayed; guest %s waiting",
                room, guest,
            )
        elif event == 5:
            logger.error(
                "Payment gateway timeout for guest %s",
                guest,
            )
        else:
            logger.error(
                "Database connection failed while saving booking"
            )

        time.sleep(2)

except KeyboardInterrupt:
    logger.info("Hotel automation application stopped")
