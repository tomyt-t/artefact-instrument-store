"""Shopping cart shared by the storefront and the agent; persisted in any mutable mapping."""
from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass

from . import policies
from .data import Product, StoreData
from .i18n import STORE_LANGUAGE, Translator


@dataclass(frozen=True)
class CartLine:
    product: Product
    quantity: int

    @property
    def total(self) -> float:
        return round(self.product.price * self.quantity, 2)


class Cart:
    def __init__(self, store: StoreData, storage: MutableMapping[str, int], t: Translator | None = None) -> None:
        self.store = store
        self.t = t or Translator(STORE_LANGUAGE)  # the language of the error messages
        self._items = storage  # str(product_id) -> quantity; str keys keep it JSON-serializable
        self._listeners: list[Callable[[], None]] = []

    def on_change(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def add(self, product_id: int, quantity: int = 1) -> str | None:
        """Adds to the cart, returning an error message when the product can't be sold."""
        # ids and quantities may come from a language model: 81.0 or True would otherwise reach the stored keys
        if isinstance(product_id, bool) or not isinstance(product_id, int):
            return self.t('cart.not_found')
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return self.t('cart.bad_quantity')
        product = self.store.products.get(product_id)
        if product is None:
            return self.t('cart.not_found')
        if not product.is_available:
            return self.t('cart.unavailable', name=self.t.product_name(product),
                          availability=self.t(f'availability.{product.availability}').lower())
        current = self._items.get(str(product_id), 0)
        if current + quantity > product.stock:
            return self.t('cart.stock', stock=product.stock, name=self.t.product_name(product))
        self._items[str(product_id)] = current + quantity
        self._notify()
        return None

    def set_quantity(self, product_id: int, quantity: int) -> None:
        product = self.store.products[product_id]
        if quantity <= 0:
            self._items.pop(str(product_id), None)
        else:
            self._items[str(product_id)] = min(quantity, product.stock)
        self._notify()

    def remove(self, product_id: int) -> None:
        self.set_quantity(product_id, 0)

    @property
    def lines(self) -> list[CartLine]:
        return [CartLine(self.store.products[int(pid)], qty)
                for pid, qty in self._items.items() if int(pid) in self.store.products]

    @property
    def count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def list_total(self) -> float:
        return round(sum(line.product.list_price * line.quantity for line in self.lines), 2)

    @property
    def total(self) -> float:
        return round(sum(line.total for line in self.lines), 2)

    @property
    def promo_savings(self) -> float:
        return round(self.list_total - self.total, 2)

    @property
    def pix_total(self) -> float:
        return round(sum(line.product.pix_price * line.quantity for line in self.lines), 2)

    @property
    def missing_for_free_shipping(self) -> float:
        """How much must still be exceeded to get free local shipping (0 when it already applies)."""
        return 0.0 if self.total > policies.FREE_SHIPPING_ABOVE else round(policies.FREE_SHIPPING_ABOVE - self.total, 2)

    @property
    def needs_freight_quote(self) -> bool:
        return any(line.product.category_id in policies.LARGE_INSTRUMENT_CATEGORIES for line in self.lines)

    def snapshot(self) -> dict:
        """Plain summary handed to the agent so it can talk about what is in the cart."""
        return {
            'items': [{'product_id': line.product.id, 'name': line.product.name,
                       'quantity': line.quantity, 'unit_price': line.product.price} for line in self.lines],
            'total': self.total,
            'pix_total': self.pix_total,
        }

    def _notify(self) -> None:
        for callback in self._listeners:
            callback()
