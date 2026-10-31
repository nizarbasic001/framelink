"""Local static host for the Supabase-backed FrameLink admin dashboard."""
from flask import Flask, send_from_directory
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
app = Flask(__name__, static_folder=str(STATIC), static_url_path="")


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, debug=False)
