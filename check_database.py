
from app import app, db
from sqlalchemy import inspect

with app.app_context():
    inspector = inspect(db.engine)

    print("\n--- USER DATABASE CHECK ---")

    if "user" not in inspector.get_table_names():
        print("ERROR: The user table was not found.")
    else:
        columns = {
            column["name"]
            for column in inspector.get_columns("user")
        }

        for field in ["avatar_filename", "is_admin"]:
            print(
                f"{field}: "
                + ("EXISTS" if field in columns else "MISSING")
            )

    print("\nCheck complete.")
