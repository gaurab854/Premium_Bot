"""
Database repositories package.

Each repository encapsulates all SQL queries for a single model,
keeping handlers and services free of raw SQLAlchemy code.
"""

from database.repositories.user import UserRepository
from database.repositories.wallet import WalletRepository
from database.repositories.inventory import InventoryRepository
from database.repositories.deposit import DepositRepository
from database.repositories.product import ProductRepository
from database.repositories.order import OrderRepository

__all__ = [
    "UserRepository",
    "WalletRepository",
    "InventoryRepository",
    "DepositRepository",
    "ProductRepository",
    "OrderRepository",
]
