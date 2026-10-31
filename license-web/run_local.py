"""Run the FrameLink Supabase admin dashboard on localhost."""
from __future__ import annotations
import webbrowser
from werkzeug.serving import make_server
from app import app

HOST, PORT = "127.0.0.1", 8765
URL = f"http://{HOST}:{PORT}/"

if __name__ == "__main__":
    try:
        server = make_server(HOST, PORT, app, threaded=True)
        print(f"FrameLink License Console: {URL}")
        print("The Supabase project must be configured in static/supabase-config.js.")
        print("Keep this window open while using the local dashboard. Press Ctrl+C to stop.")
        webbrowser.open(URL)
        server.serve_forever()
    except OSError as exc:
        print(f"Could not start the local dashboard: {exc}")
        input("Press Enter to close...")
