"""Single-user authentication and encrypted provider secrets; no secrets in browser storage."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from cryptography.fernet import Fernet


class Security:
    def __init__(self, data: Path, password: str | None = None):
        data.mkdir(parents=True, exist_ok=True)
        keyfile = data / "master.key"
        if not keyfile.exists():
            # Atomic, mode 0600 creation; never silently replace a key after data loss.
            try:
                fd = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            except FileExistsError:
                pass
        self.key = keyfile.read_bytes()
        self.fernet = Fernet(self.key)
        password = password or os.getenv("APP_PASSWORD")
        passfile = data / "initial-password.txt"
        if not password:
            if passfile.exists():
                password = passfile.read_text().strip()
            else:
                password = secrets.token_urlsafe(20)
                fd = os.open(passfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(password)
                print(f"[墨流] 首次登录密码已写入 {passfile.resolve()}，请仅在本机查看。", flush=True)
        if password == "replace-with-your-own-long-random-password":
            raise ValueError("请先把 .env 中的示例密码换成你自己的随机密码")
        if len(password) < 12:
            raise ValueError("APP_PASSWORD 至少 12 个字符")
        self.password_hash = hashlib.scrypt(password.encode(), salt=self.key, n=16384, r=8, p=1)
        self.attempts: dict[str, list[float]] = {}

    def encrypt(self, data: dict) -> str:
        return self.fernet.encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()

    def decrypt(self, data: str) -> dict:
        return json.loads(self.fernet.decrypt(data.encode()))

    def login(self, password: str, client: str) -> str | None:
        now = time.time()
        self.attempts = {k: [t for t in v if t > now - 300] for k, v in self.attempts.items() if v and v[-1] > now - 300}
        attempts = self.attempts.setdefault(client, [])
        if len(attempts) >= 8 or len(self.attempts) > 10000:
            raise ValueError("登录尝试过多，请稍后重试")
        attempts.append(now)
        candidate = hashlib.scrypt(password.encode(), salt=self.key, n=16384, r=8, p=1)
        if not hmac.compare_digest(candidate, self.password_hash):
            return None
        self.attempts.pop(client, None)
        body = base64.urlsafe_b64encode(f"{int(now)}:{secrets.token_hex(16)}".encode()).decode()
        # A changed password invalidates outstanding sessions.
        sig = hmac.new(self.password_hash, body.encode(), hashlib.sha256).hexdigest()
        return body + "." + sig

    def valid(self, token: str) -> bool:
        try:
            body, sig = token.split(".", 1)
            expected = hmac.new(self.password_hash, body.encode(), hashlib.sha256).hexdigest()
            timestamp = int(base64.urlsafe_b64decode(body).decode().split(":", 1)[0])
            return hmac.compare_digest(sig, expected) and 0 <= time.time() - timestamp <= 86400
        except (ValueError, UnicodeError):
            return False
