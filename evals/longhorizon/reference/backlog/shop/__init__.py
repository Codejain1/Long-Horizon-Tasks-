"""Inventory and orders for a small shop."""
from shop.inventory import add_item, low_stock, remove_item, reset, stock
from shop.orders import (apply_discount, cancel_order, daily_revenue, export_orders_csv, get_order, order_total,
                         orders_placed_on, place_order, refund, reset_orders)
