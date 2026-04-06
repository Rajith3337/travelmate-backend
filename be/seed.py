import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db.session import SessionLocal
from models.models import (
    User, Trip, Place, Expense, ItineraryDay, ChecklistItem,
    TripStatus, ExpenseCategory, PlaceStatus
)
from core.security import hash_password
from sqlalchemy.future import select


TRIPS = [
    # ─────────────────────────────────────────────────────────────────────────
    # 1. GOA
    # ─────────────────────────────────────────────────────────────────────────
    {
        "trip": dict(
            name="Goa Beach Vacation 🏖️",
            destination="North Goa, South Goa",
            start_date="2026-05-10", end_date="2026-05-16",
            budget=45000.0, cover_emoji="🌊", cover_color="#00BCD4",
            status=TripStatus.planning
        ),
        "places": [
            dict(name="Baga Beach", address="North Goa", place_type="Nature",
                 notes="Famous for water sports and shacks. Best at sunset.",
                 status=PlaceStatus.planned, latitude=15.5527, longitude=73.7524),
            dict(name="Dudhsagar Waterfalls", address="Mollem National Park", place_type="Nature",
                 notes="Stunning 4-tiered waterfall. Take a jeep from Collem.",
                 status=PlaceStatus.planned, latitude=15.3145, longitude=74.3141),
            dict(name="Basilica of Bom Jesus", address="Old Goa", place_type="Attraction",
                 notes="UNESCO site. Contains St. Francis Xavier's tomb.",
                 status=PlaceStatus.planned, latitude=15.5009, longitude=73.9116),
            dict(name="Calangute Shack Row", address="Calangute, North Goa", place_type="Food",
                 notes="Try fish thali and cold Kingfisher!",
                 status=PlaceStatus.planned, latitude=15.5434, longitude=73.7518),
        ],
        "itinerary": [
            dict(day_number=1, title="Arrival & North Goa Vibes",
                 notes="Flight to GOI. Check in to beach resort. Evening walk at Calangute. Seafood dinner.",
                 place_names="Goa Airport, Calangute, Baga Beach"),
            dict(day_number=2, title="Water Sports & Nightlife",
                 notes="Banana boat, parasailing, jet ski at Baga. Evening at Tito's Lane.",
                 place_names="Baga Beach, Anjuna Flea Market"),
            dict(day_number=3, title="Old Goa & History",
                 notes="Visit Basilica of Bom Jesus and Se Cathedral. Lunch at Venite restaurant.",
                 place_names="Old Goa, Panjim"),
            dict(day_number=4, title="Dudhsagar Day Trip",
                 notes="Early morning jeep safari to Dudhsagar Falls. Swim in the natural pool!",
                 place_names="Dudhsagar Falls, Mollem"),
        ],
        "expenses": [
            dict(title="IndiGo Flights (BLR-GOI)", amount=6500.0, category=ExpenseCategory.transport),
            dict(title="Beach Resort (5 nights)", amount=18000.0, category=ExpenseCategory.accommodation),
            dict(title="Water Sports Package", amount=3500.0, category=ExpenseCategory.activities),
            dict(title="Seafood Dinners", amount=4200.0, category=ExpenseCategory.food),
            dict(title="Dudhsagar Jeep Safari", amount=1800.0, category=ExpenseCategory.tickets),
            dict(title="Cashew Feni & Souvenirs", amount=2200.0, category=ExpenseCategory.shopping),
        ],
        "checklist": [
            dict(text="Aadhar Card / ID Proof", category="Essentials", order_idx=0),
            dict(text="Swimwear (2 sets)", category="Clothing", order_idx=1),
            dict(text="Sunscreen SPF 50+", category="Toiletries", order_idx=2),
            dict(text="Flip Flops", category="Clothing", order_idx=3),
            dict(text="Waterproof Phone Pouch", category="Electronics", order_idx=4),
            dict(text="Insect Repellent", category="Health", order_idx=5),
        ],
    },
    # ─────────────────────────────────────────────────────────────────────────
    # 2. KERALA
    # ─────────────────────────────────────────────────────────────────────────
    {
        "trip": dict(
            name="Kerala Backwaters & Spices 🌿",
            destination="Munnar, Alleppey, Kovalam",
            start_date="2026-06-20", end_date="2026-06-27",
            budget=55000.0, cover_emoji="🛶", cover_color="#4CAF50",
            status=TripStatus.planning
        ),
        "places": [
            dict(name="Alleppey Backwaters", address="Alappuzha, Kerala", place_type="Nature",
                 notes="Houseboat stay overnight. Book through KTDC for best rates.",
                 status=PlaceStatus.planned, latitude=9.4981, longitude=76.3388),
            dict(name="Munnar Tea Estates", address="Munnar, Kerala", place_type="Nature",
                 notes="Rolling green tea hills. Visit the TATA Tea Museum.",
                 status=PlaceStatus.planned, latitude=10.0889, longitude=77.0595),
            dict(name="Periyar Wildlife Sanctuary", address="Thekkady", place_type="Nature",
                 notes="Elephant sightings and boat safari on Periyar lake.",
                 status=PlaceStatus.planned, latitude=9.4864, longitude=77.1909),
            dict(name="Kovalam Lighthouse Beach", address="Kovalam, Trivandrum", place_type="Nature",
                 notes="Iconic red-and-white lighthouse. Great for swimming.",
                 status=PlaceStatus.planned, latitude=8.3988, longitude=76.9784),
        ],
        "itinerary": [
            dict(day_number=1, title="Fly to Kochi — Drive to Munnar",
                 notes="Morning flight to COK. Scenic 4-hour drive up into the hills. Check into tea estate resort.",
                 place_names="Kochi Airport, Munnar"),
            dict(day_number=2, title="Tea Hills & Waterfalls",
                 notes="Eravikulam National Park. Trek to Attukal Waterfalls. TATA Tea Museum.",
                 place_names="Eravikulam NP, Attukal Waterfalls"),
            dict(day_number=3, title="Thekkady Spice Trail",
                 notes="Drive to Thekkady. Spice plantation tour. Evening Kathakali performance.",
                 place_names="Periyar Lake, Spice Garden"),
            dict(day_number=4, title="Alleppey Houseboat",
                 notes="Drive to Alleppey. Board houseboat at 12 PM. Cruise through narrow canals. Overnight on boat.",
                 place_names="Alleppey Jetty, Backwater Canals"),
        ],
        "expenses": [
            dict(title="Air India Flights (BLR-COK)", amount=8200.0, category=ExpenseCategory.transport),
            dict(title="Houseboat (1 night, AC)", amount=12000.0, category=ExpenseCategory.accommodation),
            dict(title="Resort Munnar (3 nights)", amount=15000.0, category=ExpenseCategory.accommodation),
            dict(title="Spice Garden Tour", amount=800.0, category=ExpenseCategory.tickets),
            dict(title="Kathakali Show Tickets", amount=600.0, category=ExpenseCategory.tickets),
            dict(title="Local Sadya Meals", amount=3600.0, category=ExpenseCategory.food),
            dict(title="Spices & Coconut Oil", amount=2800.0, category=ExpenseCategory.shopping),
        ],
        "checklist": [
            dict(text="Aadhar Card", category="Essentials", order_idx=0),
            dict(text="Light Cotton Clothes", category="Clothing", order_idx=1),
            dict(text="Rain Jacket / Poncho", category="Clothing", order_idx=2),
            dict(text="Mosquito Repellent", category="Health", order_idx=3),
            dict(text="Trekking Shoes", category="Clothing", order_idx=4),
            dict(text="Binoculars (for wildlife)", category="Electronics", order_idx=5),
        ],
    },
    # ─────────────────────────────────────────────────────────────────────────
    # 3. RAJASTHAN
    # ─────────────────────────────────────────────────────────────────────────
    {
        "trip": dict(
            name="Rajasthan Royal Heritage 🏰",
            destination="Jaipur, Jodhpur, Udaipur",
            start_date="2026-11-01", end_date="2026-11-10",
            budget=75000.0, cover_emoji="🐪", cover_color="#FF9800",
            status=TripStatus.planning
        ),
        "places": [
            dict(name="Amber Fort", address="Amer, Jaipur", place_type="Attraction",
                 notes="Hilltop fort. Ride up on elephant or jeep. Light & Sound show in evening.",
                 status=PlaceStatus.planned, latitude=26.9855, longitude=75.8513),
            dict(name="Mehrangarh Fort", address="Jodhpur", place_type="Attraction",
                 notes="Perched 400 ft above the Blue City. Museum inside has royal artifacts.",
                 status=PlaceStatus.planned, latitude=26.2980, longitude=73.0185),
            dict(name="Lake Pichola", address="Udaipur", place_type="Nature",
                 notes="Sunset boat ride around the lake. Views of City Palace are breathtaking.",
                 status=PlaceStatus.planned, latitude=24.5764, longitude=73.6802),
            dict(name="Jaisalmer Sand Dunes", address="Sam Sand Dunes, Jaisalmer", place_type="Nature",
                 notes="Camel safari at sunset. Camp overnight under the stars.",
                 status=PlaceStatus.planned, latitude=26.8318, longitude=70.7090),
        ],
        "itinerary": [
            dict(day_number=1, title="Jaipur — The Pink City",
                 notes="Fly to JAI. City Palace, Jantar Mantar, and Hawa Mahal exterior. Rajasthani Thali dinner.",
                 place_names="Jaipur Airport, Hawa Mahal, City Palace"),
            dict(day_number=2, title="Amber Fort & Bazaars",
                 notes="Morning at Amber Fort. Afternoon shopping on Johari Bazaar for gems, textiles, juttis.",
                 place_names="Amber Fort, Johari Bazaar"),
            dict(day_number=3, title="Drive to Jodhpur",
                 notes="5-hour road trip past villages. Heritage hotel check-in. Evening at Mehrangarh Fort.",
                 place_names="Mehrangarh Fort, Clock Tower Bazaar"),
            dict(day_number=4, title="Udaipur — City of Lakes",
                 notes="Drive to Udaipur via Ranakpur Jain Temples. Evening boat ride on Lake Pichola.",
                 place_names="Ranakpur Temples, Lake Pichola, City Palace"),
        ],
        "expenses": [
            dict(title="IndiGo Jaipur Flight", amount=5800.0, category=ExpenseCategory.transport),
            dict(title="Heritage Hotel (9 nights)", amount=36000.0, category=ExpenseCategory.accommodation),
            dict(title="Taxi / Cab Intercity", amount=12000.0, category=ExpenseCategory.transport),
            dict(title="Fort Entry Tickets (all)", amount=3200.0, category=ExpenseCategory.tickets),
            dict(title="Rajasthani Thali & Meals", amount=6500.0, category=ExpenseCategory.food),
            dict(title="Handicrafts & Textiles", amount=8000.0, category=ExpenseCategory.shopping),
            dict(title="Camel Safari & Camp", amount=4500.0, category=ExpenseCategory.activities),
        ],
        "checklist": [
            dict(text="Aadhar Card", category="Essentials", order_idx=0),
            dict(text="Scarf / Dupatta (for temple)", category="Clothing", order_idx=1),
            dict(text="Sunglasses & Sun Hat", category="Clothing", order_idx=2),
            dict(text="Sunscreen SPF 50+", category="Toiletries", order_idx=3),
            dict(text="Light Cotton Kurtas", category="Clothing", order_idx=4),
            dict(text="Cash in small denominations", category="Essentials", order_idx=5),
        ],
    },
    # ─────────────────────────────────────────────────────────────────────────
    # 4. HIMACHAL PRADESH
    # ─────────────────────────────────────────────────────────────────────────
    {
        "trip": dict(
            name="Himachal Hills & Snow ❄️",
            destination="Shimla, Manali, Kasol",
            start_date="2026-12-22", end_date="2026-12-30",
            budget=40000.0, cover_emoji="🏔️", cover_color="#5C6BC0",
            status=TripStatus.planning
        ),
        "places": [
            dict(name="Rohtang Pass", address="Manali, HP", place_type="Nature",
                 notes="Snow-covered pass at 13,050 ft. Online permit required in advance!",
                 status=PlaceStatus.planned, latitude=32.3719, longitude=77.2453),
            dict(name="Mall Road Shimla", address="Shimla", place_type="Attraction",
                 notes="Heart of Shimla. Walk along the ridge, visit Christ Church.",
                 status=PlaceStatus.planned, latitude=31.1048, longitude=77.1734),
            dict(name="Kasol Village", address="Parvati Valley", place_type="Nature",
                 notes="Tiny Israeli-influenced village. Trek to Kheerganga hot springs (12 km).",
                 status=PlaceStatus.planned, latitude=32.0111, longitude=77.3151),
            dict(name="Solang Valley", address="Manali", place_type="Nature",
                 notes="Paragliding, zorbing, and skiing in winter. 14 km from Manali town.",
                 status=PlaceStatus.planned, latitude=32.3308, longitude=77.1526),
        ],
        "itinerary": [
            dict(day_number=1, title="Arrive Shimla by Toy Train",
                 notes="Take Kalka-Shimla toy train (UNESCO listed). Check in. Evening on Mall Road.",
                 place_names="Kalka Station, Mall Road, Christ Church"),
            dict(day_number=2, title="Kufri & Jakhu Hill",
                 notes="Drive to Kufri for snow play. Ropeway to Jakhu Temple.",
                 place_names="Kufri, Jakhu Hill, Ridge"),
            dict(day_number=3, title="Drive to Manali",
                 notes="Scenic 8-hour drive. Stop at Kullu Shawl Factory. Arrive Manali. Old Manali walk.",
                 place_names="Kullu, Manali Town, Old Manali"),
            dict(day_number=4, title="Rohtang Pass Day",
                 notes="Permit booked. 6AM jeep to Rohtang. Snow activities. Breathtaking views. Back by 3PM.",
                 place_names="Rohtang Pass, Solang Valley"),
        ],
        "expenses": [
            dict(title="Train to Kalka + Toy Train", amount=2200.0, category=ExpenseCategory.transport),
            dict(title="Bus/Cab Shimla to Manali", amount=2800.0, category=ExpenseCategory.transport),
            dict(title="Hotel Shimla (2 nights)", amount=8000.0, category=ExpenseCategory.accommodation),
            dict(title="Hotel Manali (4 nights)", amount=10000.0, category=ExpenseCategory.accommodation),
            dict(title="Rohtang Permit + Jeep", amount=3500.0, category=ExpenseCategory.tickets),
            dict(title="Maggi & Local Dhabas", amount=3200.0, category=ExpenseCategory.food),
            dict(title="Woollen Shawls & Handicrafts", amount=5000.0, category=ExpenseCategory.shopping),
        ],
        "checklist": [
            dict(text="Thermal Inners (top & bottom)", category="Clothing", order_idx=0),
            dict(text="Heavy Winter Jacket", category="Clothing", order_idx=1),
            dict(text="Snow Boots / Waterproof Shoes", category="Clothing", order_idx=2),
            dict(text="Woollen Gloves & Socks", category="Clothing", order_idx=3),
            dict(text="Aadhar Card", category="Essentials", order_idx=4),
            dict(text="Altitude Sickness Tablets", category="Health", order_idx=5),
            dict(text="Power Bank (cold drains battery fast)", category="Electronics", order_idx=6),
        ],
    },
]


async def seed():
    print("Connecting to Supabase Database...")
    async with SessionLocal() as db:
        try:
            # Find or create user
            users = (await db.execute(select(User))).scalars().all()
            if not users:
                print("No users found. Creating 'testuser'...")
                u = User(
                    email="test@example.com",
                    username="testuser",
                    full_name="Test User",
                    hashed_password=hash_password("password123")
                )
                db.add(u)
                await db.commit()
                await db.refresh(u)
                users = [u]

            for user in users:
                print(f"\nSeeding 4 Indian trips for user: {user.username}")
                for td in TRIPS:
                    trip = Trip(owner_id=user.id, **td["trip"])
                    db.add(trip)
                    await db.flush()

                    db.add_all([Place(trip_id=trip.id, **p) for p in td["places"]])
                    db.add_all([ItineraryDay(trip_id=trip.id, **d) for d in td["itinerary"]])
                    db.add_all([Expense(trip_id=trip.id, **e) for e in td["expenses"]])
                    db.add_all([ChecklistItem(trip_id=trip.id, **c) for c in td["checklist"]])
                    print(f"  ✓ {td['trip']['name']}")

            await db.commit()
            print("\n🎉 All 4 Indian trips seeded successfully into Supabase!")

        except Exception as e:
            await db.rollback()
            print("\n❌ Error seeding data:", e)
            import traceback; traceback.print_exc()


async def main():
    await seed()
    from db.session import engine
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
