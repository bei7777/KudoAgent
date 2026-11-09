import os
import re
from pathlib import Path

from dotenv import load_dotenv
from platformdirs import user_data_path
from pydantic import Field, SecretStr

from kudo_agent.research_core.models import Model


class Settings(Model):
    data_dir: Path = Field(default_factory=lambda: user_data_path("kudo-agent"))
    base_url: str = ""
    model: str = ""
    api_key: SecretStr = SecretStr("")
    api_key_env: str = "KUDO_API_KEY"
    model_timeout: float = Field(default=120, gt=0)

    @classmethod
    def from_env(cls, env_file: Path | None = None, data_dir: Path | None = None):
        if env_file is None and os.environ.get("KUDO_ENV_FILE"):
            env_file = Path(os.environ["KUDO_ENV_FILE"]).expanduser()
        if env_file is not None:
            if not env_file.is_file():
                raise FileNotFoundError(env_file)
            load_dotenv(env_file, override=False, encoding="utf-8")
        key_env = os.environ.get("KUDO_API_KEY_ENV", "KUDO_API_KEY")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
            raise ValueError("KUDO_API_KEY_ENV 必须是有效的环境变量名称")
        values = {
            "base_url": os.environ.get("KUDO_BASE_URL", ""),
            "model": os.environ.get("KUDO_MODEL", ""),
            "api_key": os.environ.get(key_env, ""),
            "api_key_env": key_env,
        }
        selected_dir = data_dir or os.environ.get("KUDO_DATA_DIR")
        if selected_dir:
            values["data_dir"] = Path(selected_dir).expanduser().resolve()
        return cls(**values)

    def require_model(self):
        missing = [
            name for name, value in (
                ("KUDO_BASE_URL", self.base_url),
                ("KUDO_MODEL", self.model),
                (self.api_key_env, self.api_key.get_secret_value()),
            ) if not value
        ]
        if missing:
            raise ValueError("缺少模型配置：" + ", ".join(missing))
