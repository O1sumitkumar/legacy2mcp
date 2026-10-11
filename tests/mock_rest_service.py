"""
A tiny Flask REST service, used two ways:

1. As a pytest fixture (spun up on localhost) so the REST adapter test
   suite doesn't depend on any external network access.
2. As a fixture for the discovery scanner tests -- this file's own route
   decorators are exactly the shape `discovery/scan.py` is meant to detect.

Mirrors the role `tests/mock_soap_service.py` plays for the SOAP adapter.
"""

from __future__ import annotations

from flask import Flask, jsonify, request

_ORDERS = {
    1: {"id": 1, "status": "shipped", "total": 42.5},
    2: {"id": 2, "status": "pending", "total": 10.0},
}


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/orders")
    def list_orders():
        """List orders, optionally filtered by status."""
        status = request.args.get("status")
        orders = list(_ORDERS.values())
        if status:
            orders = [o for o in orders if o["status"] == status]
        return jsonify(orders)

    @app.get("/orders/<int:order_id>")
    def get_order(order_id: int):
        """Fetch one order by id."""
        order = _ORDERS.get(order_id)
        if order is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(order)

    @app.post("/orders")
    def create_order():
        """Create a new order."""
        body = request.get_json(force=True)
        new_id = max(_ORDERS) + 1
        order = {"id": new_id, "status": "pending", "total": body.get("total", 0)}
        _ORDERS[new_id] = order
        return jsonify(order), 201

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"ok": True})

    return app
