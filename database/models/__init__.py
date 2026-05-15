"""
Database models package.

Import every model here so that:
  1. Alembic's autogenerate discovers all tables.
  2. Base.metadata is fully populated before create_all().
"""

from database.models.user import User
from database.models.wallet import Wallet
from database.models.transaction import Transaction, TransactionType, TransactionStatus
from database.models.deposit import Deposit, DepositStatus
from database.models.product import Product
from database.models.inventory import Inventory
from database.models.order import Order, OrderStatus
from database.models.order_item import OrderItem
from database.models.admin_log import AdminLog
from database.models.promo_code import PromoCode
from database.models.promo_order_request import PromoOrderRequest, PromoOrderStatus

__all__ = [
    # Models
    "User",
    "Wallet",
    "Transaction",
    "Deposit",
    "Product",
    "Inventory",
    "Order",
    "OrderItem",
    "AdminLog",
    "PromoCode",
    "PromoOrderRequest",
    # Enums
    "TransactionType",
    "TransactionStatus",
    "DepositStatus",
    "OrderStatus",
    "PromoOrderStatus",
]
