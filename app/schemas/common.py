"""Shared schema types."""

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

from app.core.money import decimal_str

# Decimal rendered in plain notation in JSON ("0.00000000", never "0E-8").
DecimalStr = Annotated[Decimal, PlainSerializer(decimal_str, return_type=str, when_used="json")]
