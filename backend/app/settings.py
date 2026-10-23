from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://localhost:55432/embodied_lab"
    storage_root: Path = Path("storage")
    lease_seconds: int = 15
    heartbeat_seconds: float = 3
    poll_seconds: float = 0.5
    max_import_bytes: int = 1024 * 1024 * 1024

    def prepare(self):
        self.storage_root = self.storage_root.resolve()
        for name in ("incoming", "datasets", "artifacts", "staging", "logs"):
            (self.storage_root / name).mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.prepare()
