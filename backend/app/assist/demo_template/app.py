"""Demo web shop (never deployed - it only exists to be scanned)."""
from flask import Flask, jsonify

import config

app = Flask(__name__)


@app.get("/")
def home():
    return jsonify({"shop": "open"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
