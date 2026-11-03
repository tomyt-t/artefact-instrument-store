"""How products, the cart and orders are shown to the model: plain dicts with every value already computed.

Money always comes from the same properties the storefront uses, formatted by `brl()`, so the model only has to
repeat values, never calculate them.
"""
from __future__ import annotations


from .. import policies
from ..data import Category, Product, StoreData
from ..i18n import Translator
from ..format import brl

SPEC_NAMES = {
    'top': 'tampo', 'back_sides': 'fundo e laterais', 'neck': 'braço', 'strings': 'cordas', 'scale': 'escala',
    'electronics': 'captação (eletroacústico)', 'color': 'cor', 'body': 'corpo', 'shells': 'cascos',
    'pieces': 'peças', 'hardware': 'ferragens', 'keys': 'teclas', 'type': 'tipo', 'polyphony': 'polifonia',
}
SPEC_VALUES = {'nylon': 'nylon', 'steel': 'aço', 'yes': 'sim', 'no': 'não', 'included': 'inclusas',
               'Synthesizer': 'sintetizador'}
UNVERIFIED_NOTE = ('A descrição cadastrada deste produto é de outro instrumento. Só nome, preço e disponibilidade são '
                   'confiáveis: marca, modelo e especificações precisam ser confirmados com a equipe.')
ELECTRIC_GUITARS, ACOUSTIC_GUITARS = 1, 5
CATEGORY_CHOICES = {
    ('pt-BR', ELECTRIC_GUITARS): 'Guitarras (só "electric guitar", "guitare électrique")',
    ('pt-BR', ACOUSTIC_GUITARS): 'Violões (também "guitar", "acoustic guitar", "guitare")',
    ('en', ACOUSTIC_GUITARS): 'Guitars',
    ('fr', ACOUSTIC_GUITARS): 'Guitares',
}
NO_EXTRA_PIX_DISCOUNT = 'Sem desconto adicional no PIX: a promoção não se soma aos 5% (política 6.2).'


def installments(amount: float) -> str:
    n = policies.max_installments(amount)
    return f'até {n}x de {brl(amount / n)} sem juros' if n > 1 else 'só à vista (valor abaixo da parcela mínima)'


def installment_options(amount: float) -> dict[str, str]:
    """Every number of installments allowed, so 'and in 5x?' has an answer the model doesn't need to calculate."""
    return {f'{n}x': brl(amount / n) for n in range(2, policies.max_installments(amount) + 1)}


def category_choice(category: Category, t: Translator) -> str:
    """The category as the model sees it. In this store 'guitars' means Violões, and 'Guitarras' looks like that word."""
    return CATEGORY_CHOICES.get((t.language, category.id), t.category_name(category))


def product_summary(product: Product, store: StoreData) -> dict:
    view = {
        'id': product.id,
        'nome': product.name,
        'categoria': store.categories[product.category_id].name,
        'disponibilidade': product.availability_label,
        'preco': brl(product.price),
    }
    if product.promotion:
        view['preco_original'] = brl(product.list_price)
        view['promocao'] = f'{product.promotion.description}: {product.promotion.discount_percent}% de desconto'
        view['economia'] = brl(product.list_price - product.price)
        view['pix'] = NO_EXTRA_PIX_DISCOUNT
    view['preco_pix'] = brl(product.pix_price)
    view['parcelas'] = installments(product.price)
    view['frete_em_campo_grande'] = ('grátis' if policies.local_shipping_fee(product.price) == 0
                                     else brl(policies.LOCAL_SHIPPING_FEE))
    if product.details_verified:
        view['especificacoes'] = {SPEC_NAMES.get(key, key): SPEC_VALUES.get(value, value)
                                  for key, value in product.specs.items()}
    else:
        view['aviso'] = UNVERIFIED_NOTE
    return view
