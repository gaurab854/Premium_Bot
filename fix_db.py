import asyncio
from database.engine import async_engine
from sqlalchemy import text

async def upgrade_database():
    print("Connecting to the database...")
    async with async_engine.begin() as conn:
        print("Executing ALTER TABLE to add 'allow_promo' to 'products'...")
        try:
            await conn.execute(text("ALTER TABLE products ADD COLUMN allow_promo BOOLEAN NOT NULL DEFAULT FALSE;"))
            print("✅ Successfully added 'allow_promo' column!")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("✅ Column 'allow_promo' already exists. No action needed.")
            else:
                print(f"❌ Error adding column: {e}")

if __name__ == "__main__":
    asyncio.run(upgrade_database())
