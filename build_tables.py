import os
from dotenv import load_dotenv

# Load the .env file so it connects to GCP
load_dotenv()

# 1. Import your SQLAlchemy engine and Base
from repository.model_repository import engine, Base

# 2. Import ALL your model files here so SQLAlchemy knows they exist
# (Change this line to wherever your Python classes/models are defined)
# import repository.models 

def create_cloud_tables():
    print("Connecting to Google Cloud SQL...")
    # This command looks at all your Python models and physically creates the tables in the DB
    Base.metadata.create_all(bind=engine)
    print("✅ Tables built successfully! The filing cabinets are ready.")

if __name__ == "__main__":
    create_cloud_tables()