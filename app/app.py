import uuid
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify

from database import (
    init_db,
    hash_request_body,
    get_idempotency_record,
    create_order_atomic,
    get_order,
    check_db_connection,
)


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            log_record.update(record.extra)
        return json.dumps(log_record)


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("order_api")
logger.setLevel(logging.INFO)
logger.addHandler(handler)


app = Flask(__name__)


@app.before_request
def assign_request_id():
    request.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))


@app.route("/health", methods=["GET"])
def health():
    """
    Returns 200 + {"status":"ok","db":"connected"} when healthy.
    Returns 503 + {"status":"error","db":"unreachable"} when the DB is down.
    Used by Docker HEALTHCHECK and ALB target group health checks.
    """
    if check_db_connection():
        return jsonify({"status": "ok", "db": "connected"}), 200
    else:
        logger.error("Health check failed: DB unreachable")
        return jsonify({"status": "error", "db": "unreachable"}), 503


@app.route("/orders", methods=["POST"])
def create_order_endpoint():
    request_id = request.request_id

    if not request.is_json:
        logger.warning("Not JSON request", extra={"request_id": request_id})
        return jsonify({"error": "Not JSON request"}), 400

    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        logger.warning("Missing idempotency key", extra={"request_id": request_id})
        return jsonify({"error": "Missing idempotency key"}), 400

    body = request.get_json()
    customer_id = body.get("customer_id")
    item_id = body.get("item_id")
    quantity = body.get("quantity")

    if not customer_id or not item_id or quantity is None:
        logger.warning(
            "Missing customer_id, item_id, and quantity",
            extra={"request_id": request_id},
        )
        return jsonify({"error": "Missing customer_id, item_id, and quantity"}), 400

    if not isinstance(quantity, int) or quantity <= 0:
        logger.warning("Invalid quantity", extra={"request_id": request_id})
        return jsonify({"error": "Invalid quantity"}), 400

    request_hash = hash_request_body(body)

    logger.info(
        "Incoming POST /orders",
        extra={
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        },
    )

    existing = get_idempotency_record(idempotency_key)

    if existing:
        if existing["request_hash"] != request_hash:
            logger.warning(
                "Idempotency key conflict",
                extra={
                    "request_id": request_id,
                    "idempotency_key": idempotency_key,
                },
            )
            return (
                jsonify({"error": "Idempotency key already used with a different payload"}),
                409,
            )

        logger.info(
            "Idempotent retry detected, returning cached response",
            extra={"request_id": request_id, "idempotency_key": idempotency_key},
        )
        return jsonify(json.loads(existing["response_body"])), existing["status_code"]

    order_id = str(uuid.uuid4())
    ledger_id = str(uuid.uuid4())
    response_body = {"order_id": order_id, "status": "created"}

    create_order_atomic(
        order_id=order_id,
        customer_id=customer_id,
        item_id=item_id,
        quantity=quantity,
        ledger_id=ledger_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        response_body=response_body,
    )

    logger.info(
        "Order, ledger, and idempotency record committed atomically",
        extra={"request_id": request_id, "order_id": order_id, "ledger_id": ledger_id},
    )

    if request.headers.get("X-Debug-Fail-After-Commit") == "true":
        logger.warning(
            "Simulated failure after commit triggered",
            extra={"request_id": request_id, "order_id": order_id},
        )
        return jsonify({"error": "Simulated server failure after commit"}), 500

    logger.info(
        "Order created successfully",
        extra={"request_id": request_id, "order_id": order_id},
    )

    return jsonify(response_body), 201


@app.route("/orders/<order_id>", methods=["GET"])
def get_order_endpoint(order_id):
    request_id = request.request_id

    logger.info(
        "Incoming GET /orders/<order_id>",
        extra={"request_id": request_id, "order_id": order_id},
    )

    row = get_order(order_id)

    if row is None:
        logger.warning(
            "Order not found",
            extra={"request_id": request_id, "order_id": order_id},
        )
        return jsonify({"error": "Order not found"}), 404

    order = {
        "order_id": row["order_id"],
        "customer_id": row["customer_id"],
        "item_id": row["item_id"],
        "quantity": row["quantity"],
        "status": row["status"],
        "created_at": str(row["created_at"]),
    }

    logger.info(
        "Order retrieved successfully",
        extra={"request_id": request_id, "order_id": order_id},
    )

    return jsonify(order), 200


if __name__ == "__main__":
    init_db()
    logger.info("Database initialized")
    logger.info("Starting Order API on port 8080")
    app.run(host="0.0.0.0", port=8080, debug=False)