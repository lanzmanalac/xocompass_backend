from repository.model_repository import engine
from domain.models import Base

def initialize_database():
    print("🔨 Dropping old schema (if exists)...")
    Base.metadata.drop_all(bind=engine)
    
    print("🏗️ Building the new 5-Table Schema...")
    Base.metadata.create_all(bind=engine)
    
    print("✅ Success! Check your 'data' folder for 'xocompass_models.db'.")

if __name__ == "__main__":
    initialize_database()