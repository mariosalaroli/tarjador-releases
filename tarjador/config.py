from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_key: str = "changeme-dev-key"
    max_file_size_mb: int = 50
    spacy_model: str = "pt_core_news_sm"


settings = Settings()
