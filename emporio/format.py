"""Brazilian formatting helpers."""
from __future__ import annotations

from datetime import date


def brl(value: float) -> str:
    """1299.9 -> 'R$ 1.299,90'"""
    return 'R$ ' + f'{value:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def br_date(value: date) -> str:
    return value.strftime('%d/%m/%Y')
