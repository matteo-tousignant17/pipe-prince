from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_ENV: str = "development"
    SHADOW_MODE: bool = True
    LOG_LEVEL: str = "INFO"

    # Database — defaults to /tmp on Vercel (only writable path in serverless)
    DATABASE_URL: str = "sqlite:////tmp/pipestream.db"

    # Anthropic
    ANTHROPIC_API_KEY: str = "placeholder"
    CLAUDE_MODEL: str = "claude-haiku-4-5-20251001"

    # Slack
    SLACK_BOT_TOKEN: str = "placeholder"
    SLACK_SIGNING_SECRET: str = "placeholder"
    SLACK_CHANNEL_ID: str = "C0000000000"
    SLACK_VALIDATOR_IDS: str = "U0000000000"  # comma-separated: Pat, Jack, etc.
    SLACK_MATTEO_USER_ID: str = "U0000000001"

    # Inbound webhook
    POSTMARK_WEBHOOK_TOKEN: str = "placeholder"

    # Outbound email
    SMTP_HOST: str = "smtp.postmarkapp.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = "placeholder"
    SMTP_PASSWORD: str = "placeholder"
    FROM_EMAIL: str = "bot@pipestream.ai"

    # Business config
    YARD_MANAGER_EMAIL: str = "yard@example.com"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
