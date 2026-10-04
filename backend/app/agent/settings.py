"""Only credentials and deployment identity belong in the environment."""
import os
from dataclasses import dataclass

GRAPH_VERSION = 'v23.0'
JEV_MODEL = 'jev-1.13.0'


@dataclass(frozen=True)
class IntegrationSettings:
    typesafe_key: str
    whatsapp_token: str
    whatsapp_secret: str
    whatsapp_verify: str
    whatsapp_number: str

    @property
    def whatsapp_enabled(self) -> bool:
        return all((self.whatsapp_token, self.whatsapp_secret, self.whatsapp_verify, self.whatsapp_number))


def settings() -> IntegrationSettings:
    return IntegrationSettings(*(os.getenv(name, '').strip() for name in (
        'TYPESAFE_API_KEY', 'WHATSAPP_ACCESS_TOKEN', 'WHATSAPP_APP_SECRET',
        'WHATSAPP_VERIFY_TOKEN', 'WHATSAPP_PHONE_NUMBER_ID')))
