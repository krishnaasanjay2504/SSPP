"""Start the Smart Student Placement Portal.

    python run.py              # production server (waitress) on http://0.0.0.0:5000
    python run.py --dev        # Flask development server with auto-reload
"""
import os
import sys

from sspp import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    if "--dev" in sys.argv:
        app.run(host=host, port=port, debug=True)
    else:
        from waitress import serve
        print(f"SSPP running on http://localhost:{port}  (Ctrl+C to stop)")
        serve(app, host=host, port=port, threads=8)
