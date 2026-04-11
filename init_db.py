# init_db.py
import os
from dotenv import load_dotenv

# 1. Get the exact folder this script is running in
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, ".env")

print("--------------------------------------------------")
print(f"📂 Looking for .env at: {env_path}")
print(f"👀 Does file exist? {os.path.exists(env_path)}")

# 2. Force load that specific file
load_dotenv(dotenv_path=env_path)

# 3. Now import the database stuff
from repository.model_repository import engine
from domain.models import Base, SarimaxModel, ForecastSnapshot, ModelDiagnostic, TrainingDataLog, ForecastCache

print(f"🔍 TARGET DATABASE: {engine.url}")
print("--------------------------------------------------")

if "sqlite" in str(engine.url):
    print("❌ ERROR: Still connected to SQLite.")
    print("👉 Your .env file either doesn't exist at the path above, OR the DATABASE_URL inside it has a typo.")
else:
    print("🚀 Connected to Cloud Postgres! Building tables...")
    Base.metadata.create_all(bind=engine)
    print("✅ Tables successfully built in Neon!")