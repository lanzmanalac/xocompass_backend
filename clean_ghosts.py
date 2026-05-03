from sqlalchemy import text
from repository.model_repository import SessionLocal

def clean_database():
    db = SessionLocal()
    try:
        # Drop the tables if they exist
        db.execute(text("DROP TABLE IF EXISTS audit_logs CASCADE;"))
        db.execute(text("DROP TABLE IF EXISTS invite_tokens CASCADE;"))
        db.execute(text("DROP TABLE IF EXISTS global_settings CASCADE;"))
        db.execute(text("DROP TABLE IF EXISTS users CASCADE;"))
        
        # Drop the custom Postgres Enum types if they exist
        db.execute(text("DROP TYPE IF EXISTS user_role CASCADE;"))
        db.execute(text("DROP TYPE IF EXISTS audit_status CASCADE;"))
        
        db.commit()
        print("Ghost tables and types demolished successfully!")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    clean_database()