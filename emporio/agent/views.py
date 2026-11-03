"""How products, the cart and orders are shown to the model: plain dicts with every value already computed.

Money always comes from the same properties the storefront uses, formatted by `brl()`, so the model only has to
repeat values, never calculate them.
"""
from __future__ import annotations

from datetime import date

from .. import policies
from ..cart import Cart
from ..data import Category, Order, Product, StoreData
from ..i18n import Translator
from ..format import br_date, brl

SPEC_NAMES = {
    'top': 'tampo', 'back_sides': 'fundo e laterais', 'neck': 'braço', 'strings': 'cordas', 'scale': 'escala',
    'electronics': 'captação (eletroacústico)', 'color': 'cor', 'body': 'corpo', 'shells': 'cascos',
    'pieces': 'peças', 'hardware': 'ferragens', 'keys': 'teclas', 'type': 'tipo', 'polyphony': 'polifonia',
}
SPEC_VALUES = {'nylon': 'nylon', 'steel': 'aço', 'yes': 'sim', 'no': 'não', 'included': 'inclusas',
               'Synthesizer': 'sintetizador'}
UNVERIFIED_NOTE = ('A descrição cadastrada deste produto é de outro instrumento. Só nome, preço e disponibilidade são '
                   'confiáveis: marca, modelo e especificações precisam ser confirmados com a equipe.')
AVAILABILITY_NOTES = {
    'Esgotado': 'Temporariamente sem estoque; o sistema não tem previsão de reposição nem reserva de preço (6.2).',
    'Fora de linha': 'Não faz mais parte do catálogo.',
    'Em breve': 'Ainda não chegou à loja e não tem data prevista.',
}
QUOTE_WITH_TEAM = f'A cotação é individual, com a equipe pelo {policies.STORE.phone} (5.2).'
STALE_AFTER_DAYS = 15  # an order waiting longer than this without shipping is treated as a problem
TREAT_AS_COMPLAINT = (f'Pedimos desculpas pela demora. Para resolver, fale com a equipe pelo {policies.STORE.phone}: o retorno '
                      'é em até 24 horas úteis (7.3). Quem avalia cancelamento e reembolso é a equipe.')
OTHER_CITIES = ('Fora da região de Campo Grande o frete é calculado pelo CEP: PAC de 5 a 12 dias úteis, SEDEX de 2 a 5 '
                f'e Jadlog de 3 a 8. A vitrine não calcula esse valor: quem informa é a equipe, pelo {policies.STORE.phone}. '
                'O manual não lista as cidades da região: numa cidade vizinha, a equipe confirma se o frete de Campo '
                'Grande vale.')
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


def product_details(product: Product, store: StoreData) -> dict:
    view = product_summary(product, store)
    view['parcelas_disponiveis'] = installment_options(product.price)
    if product.status == 'active':
        view['estoque'] = product.stock  # discontinued and upcoming items keep stale counts in the CSV
    if note := AVAILABILITY_NOTES.get(product.availability_label):
        view['situacao'] = note
    view['frete_outras_cidades'] = OTHER_CITIES
    if product.category_id in policies.LARGE_INSTRUMENT_CATEGORIES:
        view['frete_especial'] = f'Instrumento de grande porte: pode exigir frete especial. {QUOTE_WITH_TEAM}'
    if product.details_verified:
        view['descricao'] = product.description
    return view


def cart_view(cart: Cart) -> dict:
    if not cart.lines:
        return {'itens': [], 'total': brl(0)}
    items = []
    for line in cart.lines:
        item = {'id': line.product.id, 'nome': line.product.name, 'quantidade': line.quantity,
                'preco_unitario': brl(line.product.price)}
        if promotion := line.product.promotion:  # the original price and the discount always go along (6.2)
            item['preco_original'] = brl(line.product.list_price)
            item['promocao'] = f'{promotion.description}: {promotion.discount_percent}% de desconto'
        items.append(item | {'subtotal': brl(line.total)})
    view = {'itens': items, 'total': brl(cart.total)}
    if cart.promo_savings:
        view['economia_em_promocoes'] = brl(cart.promo_savings)
    view['total_no_pix'] = brl(cart.pix_total)
    view['parcelas'] = installments(cart.total)
    view['parcelas_disponiveis'] = installment_options(cart.total)
    fee = policies.local_shipping_fee(cart.total)
    view['frete_em_campo_grande'] = 'grátis' if fee == 0 else (
        f'{brl(fee)} (frete grátis acima de {brl(policies.FREE_SHIPPING_ABOVE)})')
    if fee:  # the sum the model would do by itself, showing only the result
        view['com_frete_em_campo_grande'] = (f'no PIX, {brl(cart.pix_total)} + {brl(fee)} de frete = {brl(cart.pix_total + fee)}; '
                                             f'no cartão, {brl(cart.total)} + {brl(fee)} de frete = {brl(cart.total + fee)}')
    view['frete_outras_cidades'] = OTHER_CITIES
    if cart.needs_freight_quote:
        view['frete_especial'] = f'Há bateria acústica no carrinho: pode exigir frete especial. {QUOTE_WITH_TEAM}'
    view['pagamento_combinado'] = ('permitido (ex.: PIX + cartão)' if policies.can_split_payment(cart.total)
                                   else f'só em compras acima de {brl(policies.SPLIT_PAYMENT_ABOVE)}')
    return view


def order_view(order: Order, store: StoreData, today: date) -> dict:
    """The order as recorded: totals are never recalculated with today's prices or rules."""
    view = {
        'pedido': order.id,
        'cliente': store.customers[order.customer_id].first_name,
        'feito_em': br_date(order.order_date),
        'situacao': order.status_label,
        'itens': [{'id': product_id, 'nome': store.products[product_id].name, 'quantidade': quantity}
                  for product_id, quantity in order.items],
        'total_registrado': brl(order.total),
        'pagamento': order.payment_label,
    }
    if order.tracking_code:
        view['codigo_de_rastreio'] = order.tracking_code
    if order.estimated_delivery:
        view['previsao_de_entrega'] = br_date(order.estimated_delivery)  # the CSV has no actual delivery date
    if order.status in ('pending', 'confirmed', 'shipped'):
        waiting = (today - order.order_date).days  # the CSV has no status history
        if late := policies.delivery_days_late(order.estimated_delivery, today):
            view['atraso'] = f'{late} dias depois da previsão de entrega, que era {br_date(order.estimated_delivery)}'
        elif order.status != 'shipped':
            view['dias_desde_o_pedido'] = waiting
        if late or (order.status != 'shipped' and waiting > STALE_AFTER_DAYS):
            view['o_que_fazer'] = TREAT_AS_COMPLAINT
    if order.notes:
        view['observacoes'] = order.notes
    return view
