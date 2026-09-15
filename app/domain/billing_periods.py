"""Tabelas de periodicidade de cobranca compartilhadas entre invoice e recurring."""

PERIOD_DAYS: dict[str, int] = {"monthly": 30, "semiannual": 180, "annual": 365}

# subscription_type do Marketfy -> SubscriptionType do billing-core
CYCLE_MAP: dict[str, str] = {"monthly": "MONTHLY", "semiannual": "SEMIANNUALLY", "annual": "YEARLY"}
