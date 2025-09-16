import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=bool(int(os.getenv("FLASK_DEBUG", "1"))))
