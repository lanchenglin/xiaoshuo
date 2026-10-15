"""Run one process only; persisted SQLite jobs survive a service restart."""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:create_app",factory=True,host=os.getenv("HOST","127.0.0.1"),
                port=int(os.getenv("PORT","8765")),workers=1,proxy_headers=False)
