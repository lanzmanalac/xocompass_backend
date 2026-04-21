import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

def test_connection():
    db_url = os.getenv("DATABASE_URL")
    # This just shows you which IP you are hitting to verify your .env is right
    print(f"Checking connection to: {db_url.split('@')[-1]}...") 
    
    try:
        engine = create_engine(db_url)
        # We use a simple 'SELECT 1' to see if the DB responds
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            print("✅ SUCCESS! Your laptop just talked to Google Cloud SQL.")
    except Exception as e:
        print("❌ CONNECTION FAILED.")
        print(f"Error details: {e}")

if __name__ == "__main__":
    test_connection()