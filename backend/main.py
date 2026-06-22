import os

from app.main import app

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)