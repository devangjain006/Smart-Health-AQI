
from backend.db import Session, ReadingModel, SensorModel

# Create a session
session = Session()

# Test: count rows in each table
print("Number of sensors:", session.query(SensorModel).count())
print("Number of readings:", session.query(ReadingModel).count())

session.close()
