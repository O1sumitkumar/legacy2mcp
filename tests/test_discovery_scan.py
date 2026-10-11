from __future__ import annotations

from legacy2mcp.discovery.scan import scan_repo

FLASK_APP = '''
from flask import Flask

app = Flask(__name__)


@app.get("/orders/<int:order_id>")
def get_order(order_id: int):
    """Fetch one order."""
    return {}


@app.route("/orders", methods=["POST"])
def create_order(total: float):
    return {}
'''

FASTAPI_APP = '''
from fastapi import FastAPI, Depends

app = FastAPI()


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str = None):
    return {}


@app.post("/items")
def create_item(session=Depends(get_session), price: float = 0):
    return {}
'''

FLASK_REQUEST_ARGS_APP = '''
from flask import Flask, request

app = Flask(__name__)


@app.get("/orders")
def list_orders():
    status = request.args.get("status")
    limit = request.args["limit"]
    return {}
'''

SQLALCHEMY_MODEL = '''
import psycopg2
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    status = Column(String)
    customer_email = Column(String)
'''

SOAP_CLIENT = '''
import zeep

client = zeep.Client("https://service.example.com/Legacy?wsdl")
'''


def test_scan_flask_routes(tmp_path):
    (tmp_path / "app.py").write_text(FLASK_APP)
    result = scan_repo(tmp_path)

    by_name = {r.name: r for r in result.routes}
    assert "get_order" in by_name
    get_order = by_name["get_order"]
    assert get_order.method == "GET"
    assert get_order.path == "/orders/{order_id}"
    path_params = [p for p in get_order.params if p.location == "path"]
    assert path_params[0].name == "order_id"
    assert path_params[0].type == "integer"

    assert "create_order" in by_name
    assert by_name["create_order"].method == "POST"
    body_or_query = {p.name: p for p in by_name["create_order"].params}
    assert body_or_query["total"].type == "number"


def test_scan_flask_request_args_params(tmp_path):
    (tmp_path / "app.py").write_text(FLASK_REQUEST_ARGS_APP)
    result = scan_repo(tmp_path)

    by_name = {r.name: r for r in result.routes}
    params = {p.name: p for p in by_name["list_orders"].params}
    assert params["status"].location == "query"
    assert params["status"].required is False
    assert params["limit"].required is True


def test_scan_fastapi_routes(tmp_path):
    (tmp_path / "main.py").write_text(FASTAPI_APP)
    result = scan_repo(tmp_path)

    by_name = {r.name: r for r in result.routes}
    assert by_name["read_item"].path == "/items/{item_id}"
    params = {p.name: p for p in by_name["read_item"].params}
    assert params["item_id"].location == "path"
    assert params["q"].location == "query"

    # Depends(...) params are dependency injection, not client-facing args.
    create_params = {p.name for p in by_name["create_item"].params}
    assert "session" not in create_params
    assert "price" in create_params


def test_scan_sqlalchemy_model(tmp_path):
    (tmp_path / "models.py").write_text(SQLALCHEMY_MODEL)
    result = scan_repo(tmp_path)

    assert result.db is not None
    assert result.db.dialect == "postgres"
    assert len(result.db.tables) == 1
    table = result.db.tables[0]
    assert table.table == "orders"
    assert set(table.columns) == {"id", "status", "customer_email"}


def test_scan_soap_client(tmp_path):
    (tmp_path / "client.py").write_text(SOAP_CLIENT)
    result = scan_repo(tmp_path)

    assert result.soap is not None
    assert result.soap.wsdl_url == "https://service.example.com/Legacy?wsdl"


def test_scan_empty_repo_finds_nothing(tmp_path):
    (tmp_path / "util.py").write_text("def add(a, b):\n    return a + b\n")
    result = scan_repo(tmp_path)

    assert result.routes == []
    assert result.soap is None
    assert result.db is None
