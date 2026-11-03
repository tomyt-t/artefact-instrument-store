"""Tools the agent calls: deterministic Python over the store data, the cart and the policy manual.

The model sees each tool's name, description and JSON Schema (`Toolbox.schemas()`) and calls it with JSON
arguments. `Toolbox.call` validates them and never raises: a bad call comes back as {'erro': ...}, so the model can
fix it or explain the problem to the customer. Storefront actions (highlight products, change the cart, open an
order) come back as events for the agent to stream to the interface.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from .. import manual, policies
from ..cart import Cart
from ..data import STOPWORDS, Category, Product, StoreData, normalize
from ..format import br_date, brl
from ..i18n import LANGUAGES, Translator
from . import views
from .base import AgentContext
from .events import AddToCart, OpenCart, OpenProduct, RemoveFromCart, ShowOrder, ShowProducts, UIAction

log = logging.getLogger(__name__)

MAX_LISTED = 15  # products described to the model per search; the storefront highlights all of them
MAX_FAILED_LOOKUPS = 5  # wrong order number/e-mail pairs per session before order lookups are blocked
MAX_ARGUMENT_CHARS = 2000  # legitimate calls are far shorter; a huge one could hang the parsers
ACCESSORIES = frozenset({
    'corda', 'cordas', 'encordoamento', 'encordoamentos', 'palheta', 'palhetas', 'cabo', 'cabos', 'case', 'cases',
    'capa', 'capas', 'bag', 'bags', 'pedal', 'pedais', 'pedaleira', 'pedaleiras', 'amplificador', 'amplificadores',
    'afinador', 'afinadores', 'baqueta', 'baquetas', 'correia', 'correias', 'estante', 'estantes', 'suporte',
    'suportes', 'capotraste', 'capotrastes',
    'string', 'strings', 'pick', 'picks', 'cable', 'cables', 'amp', 'amps', 'amplifier', 'amplifiers', 'tuner', 'tuners',
    'drumstick', 'drumsticks', 'strap', 'straps', 'stand', 'stands', 'capo', 'capos',
    'corde', 'cordes', 'mediator', 'mediators', 'ampli', 'amplis', 'amplificateur', 'amplificateurs', 'accordeur',
    'accordeurs', 'etui', 'etuis', 'housse', 'housses', 'baguette', 'baguettes', 'sangle', 'sangles'})
NOT_IN_CATALOG = 'Esse produto não está no catálogo. Cite só os produtos que vieram aqui, nunca outra marca ou modelo.'
ORDER_NOT_FOUND = 'Não encontrei pedido com esse número e e-mail. Confira os dois dados com o cliente.'
ORDER_BLOCKED = (f'Consulta de pedidos bloqueada para este cliente depois de {MAX_FAILED_LOOKUPS} tentativas sem '
                 f'sucesso. Ele pode falar com a equipe pelo {policies.STORE.phone}.')
AFTER_DEADLINES = ('Depois desses prazos, o produto pode ainda ter a garantia do fabricante (8.2): a maioria oferece de 6 '
                   'meses a 2 anos, e o prazo está no certificado de garantia, que o cliente precisa conferir. A loja '
                   'pode intermediar o contato com o fabricante (4.2).')
SPECIAL_DATES = ('Em datas especiais (Black Friday, aniversário da loja, Natal) o horário pode ser estendido; a loja '
                 'avisa com antecedência nas redes sociais e no WhatsApp.')


class ToolError(Exception):
    """A call the tool can't carry out; the message goes back to the model."""


@dataclass(frozen=True)
class ToolResult:
    data: dict  # what the model reads
    events: tuple[UIAction, ...] = ()  # what the storefront does
    suggestions: tuple[str, ...] = ()  # quick replies offered to the customer after the answer


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments
    run: Callable[..., ToolResult]


class OrderGuard:
    """Failed order lookups per session, kept across turns: guessing order numbers gets blocked."""

    def __init__(self) -> None:
        self.failures: defaultdict[str, int] = defaultdict(int)


class Toolbox:
    """The tools for one turn of one conversation.

    Cart changes are tried on a copy of the customer's cart first, so the model learns the real outcome ('só temos
    2 unidades') before it writes the answer; the interface then applies the same change to the real cart.
    """

    def __init__(self, store: StoreData, context: AgentContext, guard: OrderGuard) -> None:
        self.store = store
        self.context = context
        self.t = Translator(context.language)  # what the customer reads directly
        self.guard = guard
        now = context.now or datetime.now(policies.TIMEZONE)
        self.now = now.astimezone(policies.TIMEZONE) if now.tzinfo else now.replace(tzinfo=policies.TIMEZONE)
        self.cart = Cart(store, {str(item['product_id']): item['quantity'] for item in context.cart.get('items', [])})
        # products the model has really looked at this turn: history is text only, so an id from memory may be a guess
        self.seen_ids = {line.product.id for line in self.cart.lines} | {context.viewing_product_id}
        self.tools = {tool.name: tool for tool in self._tools()}

    @property
    def today(self) -> date:
        return self.now.date()

    def schemas(self) -> list[dict]:
        """The tools in the OpenAI function-calling format."""
        return [{'type': 'function', 'function': {'name': t.name, 'description': t.description,
                                                  'parameters': t.parameters}} for t in self.tools.values()]

    def call(self, name: str, arguments: str | dict | None) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult({'erro': f'Ferramenta desconhecida: {name}.'})
        if isinstance(arguments, str) and len(arguments) > MAX_ARGUMENT_CHARS:
            return ToolResult({'erro': 'Os argumentos estão longos demais.'})
        try:
            args = json.loads(arguments or '{}') if isinstance(arguments, str) else dict(arguments or {})
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            return ToolResult({'erro': 'Os argumentos precisam ser um objeto JSON.'})
        if not isinstance(args, dict):
            return ToolResult({'erro': 'Os argumentos precisam ser um objeto JSON.'})
        args = {key: value for key, value in args.items()
                if key in tool.parameters['properties'] and value not in (None, '')}
        if missing := [key for key in tool.parameters.get('required', ()) if key not in args]:
            return ToolResult({'erro': f'Faltou informar: {", ".join(missing)}.'})
        try:
            return tool.run(**args)
        except ToolError as error:
            return ToolResult({'erro': str(error)})
        except Exception:  # a bug must not end the conversation
            log.exception('tool %s failed with arguments %s', name, sorted(args))  # names only: values may be e-mails
            return ToolResult({'erro': 'Falha interna nesta consulta. Peça desculpas e ofereça o contato da equipe: '
                                       f'{policies.STORE.phone}.'})

    # --- catalog -------------------------------------------------------------------------------------------------

    def search_products(self, consulta: str = '', categoria: str = '', preco_maximo: float | None = None,
                        em_promocao: bool = False, so_disponiveis: bool = True, ordem: str = '') -> ToolResult:
        query = _text(consulta, 'consulta') if consulta else ''
        words = re.findall(r'[a-z0-9]+', normalize(query))
        if not set(words) - STOPWORDS:  # '???' or 'de' say nothing, and an empty query means the whole catalog
            query = ''
        category = self._category(categoria) if categoria else None
        max_price = _number(preco_maximo, 'preco_maximo') if preco_maximo is not None else None
        on_sale = _boolean(em_promocao, 'em_promocao')
        only_available = _boolean(so_disponiveis, 'so_disponiveis')
        if not (query or category or max_price or on_sale):
            raise ToolError('Informe ao menos consulta, categoria, preco_maximo ou em_promocao.')

        def matching(*, only_available: bool) -> list[Product]:
            products = self.store.search(query, category and category.id, max_price, only_available, prefix_last=False)
            return [p for p in products if p.promotion] if on_sale else products

        found = matching(only_available=only_available)
        elsewhere = ''
        if query and category and not found and not matching(only_available=False):
            asked, category = category, None  # 'Fender' under Guitars, which here means Violões: the brand says more
            found = matching(only_available=only_available)
            if found:
                elsewhere = f'Nada em {asked.name} com "{query}". Estes são de outras categorias: diga isso ao cliente.'
            else:
                category = asked
        if ordem in ('menor_preco', 'maior_preco'):
            found.sort(key=lambda p: p.price, reverse=ordem == 'maior_preco')
        data: dict = {'encontrados': len(found),
                      'produtos': [views.product_summary(p, self.store) for p in found[:MAX_LISTED]]}
        if elsewhere:
            data['outras_categorias'] = elsewhere
        if 0 < len(found) <= 3:  # 'and in 5x?' usually comes with the price, in the same message
            for view, product in zip(data['produtos'], found, strict=True):
                view['parcelas_disponiveis'] = views.installment_options(product.price)
        self.seen_ids.update(p.id for p in found[:MAX_LISTED])
        if not found and only_available:
            if unavailable := matching(only_available=False):
                data['indisponiveis'] = [views.product_summary(p, self.store) for p in unavailable[:MAX_LISTED]]
                self.seen_ids.update(p.id for p in unavailable[:MAX_LISTED])
                data['alternativas'] = self._alternatives_view(unavailable[0])
        elif found and not any(p.is_available for p in found):
            data['alternativas'] = self._alternatives_view(found[0])
        if not found and 'indisponiveis' not in data and not on_sale:  # 'no promotion' says nothing about the catalog
            if query and max_price is None:
                data['aviso'] = NOT_IN_CATALOG
                similar = next(filter(None, (self.store.search(word, only_available=True, prefix_last=False)
                                             for word in words if word not in STOPWORDS)), [])
                if similar:
                    data['parecidos'] = [views.product_summary(p, self.store) for p in similar[:3]]
                    self.seen_ids.update(p.id for p in similar[:3])
        # '7 cordas', 'violão com afinador' and 'pré-amplificador' describe an instrument; 'cordas' alone may be strings
        accessory = any(word in ACCESSORIES and not (i and (words[i - 1] in ('com', 'pre') or (
            word.startswith(('corda', 'string', 'corde')) and words[i - 1].isdigit()))) for i, word in enumerate(words))
        if accessory:
            data['escopo'] = ('A loja só vende instrumentos musicais, não acessórios avulsos (cordas, palhetas, cabos, '
                              'cases, pedais, amplificadores). Se o cliente procura um acessório, explique isso.')
        title = ' '.join(filter(None, [self.t.category_name(category) if category else self.t('agent.products'),
                                       self.t('agent.on_sale') if on_sale else '',
                                       f'"{query}"' if query else '',
                                       self.t('agent.up_to', amount=brl(max_price)) if max_price is not None else '']))
        # 'cordas de aço' alone may be strings, not a guitar: nothing lights up until the customer says which
        show = found and not (accessory and category is None)
        events = (ShowProducts(tuple(p.id for p in found), title),) if show else ()
        if len(found) > MAX_LISTED:
            data['observacao'] = f'Só os {MAX_LISTED} primeiros estão listados.' + (
                f' A vitrine destaca os {len(found)}.' if show else '')
        return ToolResult(data, events, (self.t('suggestion.on_sale'), self.t('suggestion.recommend')) if found else ())

    def view_product(self, produto_id: int) -> ToolResult:
        product = self._product(produto_id)
        self.seen_ids.add(product.id)
        data = views.product_details(product, self.store)
        if not product.is_available:
            data['alternativas'] = self._alternatives_view(product)
            return ToolResult(data, (OpenProduct(product.id),))
        return ToolResult(data, (OpenProduct(product.id),), (self.t('suggestion.add_to_cart'), self.t('suggestion.pix')))

    def _alternatives_view(self, product: Product) -> list[dict]:
        alternatives = self._alternatives(product)
        self.seen_ids.update(p.id for p in alternatives)
        return [views.product_summary(p, self.store) for p in alternatives]

    def _alternatives(self, product: Product, limit: int = 3) -> list[Product]:
        """Available products of the same category: same type of instrument first (7 strings, dreadnought...), then
        the same string material, then the closest price."""
        candidates = [p for p in self.store.products.values()
                      if p.category_id == product.category_id and p.is_available and p.id != product.id]
        candidates.sort(key=lambda p: (p.image != product.image, p.specs.get('strings') != product.specs.get('strings'),
                                       abs(p.price - product.price)))
        return candidates[:limit]

    # --- cart ----------------------------------------------------------------------------------------------------

    def view_cart(self, mostrar: bool = False) -> ToolResult:
        events = (OpenCart(),) if _boolean(mostrar, 'mostrar') else ()
        return ToolResult(views.cart_view(self.cart), events)

    def add_to_cart(self, produto_id: int, quantidade: int = 1) -> ToolResult:
        product = self._product(produto_id)
        quantity = _integer(quantidade, 'quantidade')
        if product.id not in self.seen_ids:
            self.seen_ids.add(product.id)  # the model now knows which product this id is
            raise ToolError(f'Nada foi adicionado. O id {product.id} é {product.name} ({product.availability_label}) e não '
                            'apareceu em nenhuma consulta desta resposta. Se for o produto que o cliente pediu e estiver '
                            'disponível, chame de novo; se não for, use buscar_produtos com o nome que ele disse.')
        if error := self.cart.add(product.id, quantity):
            in_cart = next((line.quantity for line in self.cart.lines if line.product.id == product.id), 0)
            if in_cart and quantity >= 1 and product.is_available:  # refused for stock: say what still fits
                room = product.stock - in_cart
                error += (f' O cliente já tem {in_cart} no carrinho; cabem mais {room}.' if room > 0
                          else f' O cliente já tem todas as {in_cart} no carrinho.')
            raise ToolError(f'Nada foi adicionado. {error}')
        return ToolResult({'adicionado': f'{quantity}x {product.name}', 'carrinho': views.cart_view(self.cart)},
                          (AddToCart(product.id, quantity),), (self.t('suggestion.view_cart'), self.t('suggestion.installments')))

    def remove_from_cart(self, produto_id: int) -> ToolResult:
        product = self._product(produto_id)
        if all(line.product.id != product.id for line in self.cart.lines):
            raise ToolError(f'{product.name} não está no carrinho.')
        self.cart.remove(product.id)
        return ToolResult({'removido': product.name, 'carrinho': views.cart_view(self.cart)},
                          (RemoveFromCart(product.id),))

    # --- orders --------------------------------------------------------------------------------------------------

    def lookup_order(self, numero_pedido: int, email: str = '') -> ToolResult:
        order_id = _integer(numero_pedido, 'numero_pedido')
        if order_id not in self.context.verified_orders:
            if self.guard.failures[self.context.session_id] >= MAX_FAILED_LOOKUPS:
                raise ToolError(ORDER_BLOCKED)
            if not email:
                raise ToolError('Peça ao cliente o e-mail usado na compra: sem ele o pedido não pode ser consultado '
                                '(privacidade, seção 9).')
            if self.store.find_order(order_id, _text(email, 'email')) is None:
                self.guard.failures[self.context.session_id] += 1
                raise ToolError(ORDER_NOT_FOUND)
            self.context.verified_orders.add(order_id)
        order = self.store.orders[order_id]
        return ToolResult(views.order_view(order, self.store, self.today), (ShowOrder(order.id),),
                          (self.t('suggestion.exchange'), self.t('suggestion.team')))

    def deadlines(self, numero_pedido: int | None = None, data_compra: str = '', data_recebimento: str = '',
                  compra_online: bool = True) -> ToolResult:
        if numero_pedido is not None:
            order_id = _integer(numero_pedido, 'numero_pedido')
            if order_id not in self.context.verified_orders:
                raise ToolError('Antes, consulte o pedido com consultar_pedido (número e e-mail do cliente).')
            order = self.store.orders[order_id]
            if order.status == 'cancelled':
                raise ToolError(f'O pedido {order.id} foi cancelado: não há troca nem devolução a fazer.')
            purchased, online = order.order_date, True  # the store's orders are online purchases
            received = order.estimated_delivery if order.status == 'delivered' else None
            data: dict = {'pedido': order.id, 'compra': br_date(purchased)}
            if data_recebimento:  # the base only has a forecast: the customer's date is what they actually lived
                received = self._date(data_recebimento, 'data_recebimento')
                if received > self.today or received < purchased:
                    raise ToolError('Datas impossíveis: o recebimento vem depois da compra e não pode ser no futuro.')
                data['recebimento'] = f'{br_date(received)} (data que você informou; a equipe confirma o recebimento)'
            elif received:
                data['recebimento'] = f'{br_date(received)} (previsão de entrega; se você recebeu em outro dia, me diga a data)'
            else:
                data['observacao'] = ('O pedido ainda não foi entregue. Trate antes da entrega ou do atraso; os prazos '
                                      'contados do recebimento só começam quando o produto chegar.')
        elif data_compra or data_recebimento:
            purchased = self._date(data_compra, 'data_compra') if data_compra else None
            online = _boolean(compra_online, 'compra_online')
            received = self._date(data_recebimento, 'data_recebimento') if data_recebimento else (
                None if online else purchased)  # in the store the customer takes the product home
            if max(day for day in (purchased, received) if day) > self.today or (
                    purchased and received and received < purchased):
                raise ToolError('Datas impossíveis: a compra vem antes do recebimento, e os dois não podem ser no '
                                'futuro.')
            data = {'compra': br_date(purchased)} if purchased else {}
            if received:
                data['recebimento'] = br_date(received)
        else:
            raise ToolError('Informe numero_pedido (já consultado), data_compra ou data_recebimento.')
        windows = policies.return_windows(purchased or received, received, online=online)
        if purchased is None:  # the defect exchange counts from the purchase
            windows = [window for window in windows if window.section != '4.2']
            data['observacao'] = ('Sem a data da compra, a troca por defeito (4.2) não foi calculada: se o problema for '
                                  'defeito, peça essa data ao cliente.')
        data['hoje'] = br_date(self.today)
        data['prazos'] = views.deadlines_view(windows, self.today)
        if not any(w.last_day is None or w.is_open(self.today) for w in windows):
            data['depois_dos_prazos'] = AFTER_DEADLINES
        return ToolResult(data)

    # --- policies and store --------------------------------------------------------------------------------------

    def read_policies(self, secoes: list[str]) -> ToolResult:
        numbers = [str(n).strip() for n in (secoes if isinstance(secoes, list) else [secoes])]
        found, missing = [], []
        for number in numbers:
            group = manual.find(number)
            found += [s for s in group if s not in found]
            if not group:
                missing.append(number)
        if not found:
            raise ToolError(f'Seção inexistente: {", ".join(missing)}. Use os números do índice.')
        data: dict = {'secoes': [{'secao': s.number, 'titulo': s.title, 'texto': s.text} for s in found]}
        if corrections := manual.corrections(found):
            data['correcoes'] = corrections
        if missing:
            data['inexistentes'] = missing
        return ToolResult(data)

    def store_info(self) -> ToolResult:
        is_open, status = policies.opening_status(self.now)
        weekdays, saturday = policies.OPENING_HOURS[0], policies.OPENING_HOURS[5]
        info = policies.STORE
        holidays = ', '.join(f'{day:02d}/{month:02d}' for month, day in sorted(policies.HOLIDAYS))
        data = {
            'agora': f'{policies.WEEKDAYS[self.now.weekday()]}, {br_date(self.today)}, {self.now:%H:%M}',
            'aberta_agora': is_open,
            'situacao': status,
            'proxima_abertura': views.next_opening(self.now),
            'feriado_hoje': policies.is_holiday(self.today),
            'horario': (f'segunda a sexta das {weekdays[0]}h às {weekdays[1]}h, sábado das {saturday[0]}h às '
                        f'{saturday[1]}h; fechada aos domingos e feriados'),
            'feriados': f'{holidays} e Sexta-feira Santa; Carnaval e Corpus Christi não fecham a loja',
            'datas_especiais': SPECIAL_DATES,
            'endereco': f'{info.street}, {info.city}',
            'telefone_e_whatsapp': info.phone,
            'email': info.email,
        }
        return ToolResult(data)

    # --- argument checks -----------------------------------------------------------------------------------------

    def _date(self, value: object, name: str) -> date:
        """A date as the customer said it. Without a year it is the most recent one that isn't in the future: the code knows
        today better than the model does."""
        text = _text(value, name)
        if match := re.fullmatch(r'(\d{1,2})/(\d{1,2})', text):
            for year in (self.today.year, self.today.year - 1):
                try:
                    if (day := date(year, int(match[2]), int(match[1]))) <= self.today:
                        return day
                except ValueError:
                    pass
        for pattern in ('%Y-%m-%d', '%d/%m/%Y'):
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                pass
        raise ToolError(f'{name} precisa estar no formato DD/MM ou DD/MM/AAAA, recebi {value!r}.')

    def _product(self, value: object) -> Product:
        product_id = _integer(value, 'produto_id')
        if product_id not in self.store.products:
            raise ToolError(f'Não existe produto com id {product_id}. Use buscar_produtos para achar o id.')
        return self.store.products[product_id]

    def _category(self, value: object) -> Category:
        wanted = normalize(_text(value, 'categoria'))
        for category in self.store.categories_in_use:  # whatever language the model answers with
            names = {category.name} | {views.category_choice(category, Translator(language)) for language in LANGUAGES}
            if wanted in {normalize(name) for name in names} or wanted == normalize(category.name).split(' (')[0]:
                return category
        names = ', '.join(c.name for c in self.store.categories_in_use)
        raise ToolError(f'Categoria desconhecida: {value}. Use uma destas: {names}.')

    def _tools(self) -> list[Tool]:
        categories = [views.category_choice(c, self.t) for c in self.store.categories_in_use]
        return [
            Tool('buscar_produtos',
                 'Busca instrumentos no catálogo. Use sempre que o cliente procurar, comparar ou perguntar preço, '
                 'estoque ou promoção de produtos: preço e estoque só valem consultados aqui. Os resultados aparecem '
                 'destacados na vitrine. Para o tipo de instrumento use `categoria`, para orçamento `preco_maximo` e '
                 'para "o que está em promoção" `em_promocao`; em `consulta` vão só palavras como marca, modelo ou '
                 'característica ("Yamaha", "GD20", "aço", "eletroacústico", "7 cordas").',
                 _object({
                     'consulta': {'type': 'string', 'description': 'Marca, modelo ou característica, sem preço.'},
                     'categoria': {'type': 'string', 'enum': categories},
                     'preco_maximo': {'type': 'number', 'description': 'Preço máximo em reais, ex.: 1000.'},
                     'em_promocao': {'type': 'boolean', 'description': 'true para trazer só produtos em promoção.'},
                     'so_disponiveis': {'type': 'boolean', 'description': 'Padrão true. Use false para incluir '
                                        'esgotados, fora de linha e em breve.'},
                     'ordem': {'type': 'string', 'enum': ['menor_preco', 'maior_preco']},
                 }), self.search_products),
            Tool('ver_produto',
                 'Detalhes de um produto (preço, PIX, parcelas, estoque, frete em Campo Grande, especificações, '
                 'promoção) e abre a página dele na vitrine. Use quando o cliente se interessar por um item ou '
                 'perguntar sobre ele. Se o produto estiver indisponível, a resposta traz alternativas disponíveis.',
                 _object({'produto_id': {'type': 'integer'}}, required=['produto_id']), self.view_product),
            Tool('ver_carrinho',
                 'Itens, total, total no PIX, parcelas, frete e pagamento combinado do carrinho do cliente. Use para '
                 'perguntas como "quanto fica tudo?" ou "tem frete grátis?". `mostrar` abre o carrinho na tela.',
                 _object({'mostrar': {'type': 'boolean'}}), self.view_cart),
            Tool('adicionar_ao_carrinho',
                 'Coloca um produto no carrinho do cliente, conferindo disponibilidade e estoque. Use só quando o '
                 'cliente pedir. Se der erro, explique o motivo ao cliente; não diga que adicionou.',
                 _object({'produto_id': {'type': 'integer'},
                          'quantidade': {'type': 'integer', 'description': 'Padrão 1.'}}, required=['produto_id']),
                 self.add_to_cart),
            Tool('remover_do_carrinho',
                 'Tira um produto (todas as unidades) do carrinho do cliente. Use só quando o cliente pedir.',
                 _object({'produto_id': {'type': 'integer'}}, required=['produto_id']), self.remove_from_cart),
            Tool('consultar_pedido',
                 'Situação, itens, total, pagamento, rastreio e atraso de um pedido, que também abre na tela. Exige o '
                 'número do pedido e o e-mail usado na compra (privacidade, seção 9): peça os dois ao cliente e nunca '
                 'tente adivinhar. Um pedido já consultado nesta conversa dispensa o e-mail.',
                 _object({'numero_pedido': {'type': 'integer'}, 'email': {'type': 'string'}},
                         required=['numero_pedido']), self.lookup_order),
            Tool('prazos_troca_devolucao',
                 'Calcula, com a data de hoje, até quando valem o arrependimento (4.1), a troca por defeito (4.2), '
                 'a troca por preferência (4.3) e a garantia legal (8.1). Use antes de dizer se o cliente ainda pode '
                 'trocar ou devolver. Para um pedido, consulte-o antes com consultar_pedido; para uma compra fora '
                 'da base (por exemplo, na loja física), informe as datas que o cliente souber.',
                 _object({
                     'numero_pedido': {'type': 'integer'},
                     'data_compra': {'type': 'string', 'description': 'DD/MM, se não houver número de pedido. Só '
                                     'ponha o ano (DD/MM/AAAA) se o cliente disse o ano.'},
                     'data_recebimento': {'type': 'string', 'description': 'DD/MM, com ou sem número de pedido, sempre que '
                                          'o cliente disser a data. Só ponha o ano (DD/MM/AAAA) se o cliente disse o ano. '
                                          'Vazio se ainda não recebeu.'},
                     'compra_online': {'type': 'boolean', 'description': 'Padrão true. false para compra na loja.'},
                 }), self.deadlines),
            Tool('consultar_politicas',
                 'Lê seções do manual de políticas da loja, junto com as correções que prevalecem sobre ele. '
                 'Use para regras gerais de pagamento, trocas, devoluções, entregas, promoções, garantia, privacidade '
                 'e atendimento, e cite a seção na resposta. Preço, PIX, parcelas e frete de um produto vêm de '
                 'buscar_produtos, ver_produto e ver_carrinho. Um número sem subseção ("4") traz a seção '
                 f'inteira. Índice:\n{manual.index()}',
                 _object({'secoes': {'type': 'array', 'items': {'type': 'string'},
                                     'description': 'Números das seções, ex.: ["4.1", "8"].'}}, required=['secoes']),
                 self.read_policies),
            Tool('info_loja',
                 'Endereço, telefone/WhatsApp, e-mail, horário de funcionamento e se a loja está aberta agora (com '
                 'feriados), além de quando abre de novo. Use para perguntas sobre a loja física, contato ou horário '
                 'e quando o cliente quiser falar com a equipe.',
                 _object({}), self.store_info),
        ]


def _object(properties: dict, required: list[str] | None = None) -> dict:
    schema = {'type': 'object', 'properties': properties}
    if required:
        schema['required'] = required
    return schema


def _integer(value: object, name: str) -> int:
    """8, 8.0, '8' and '#8' are all 8; anything else is a mistake to report."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and (match := re.fullmatch(r'\s*(?:n[ºo°.]?\s*|#)?\s*(\d+)\s*', value.lower())):
        return int(match[1])
    raise ToolError(f'{name} precisa ser um número inteiro, recebi {value!r}.')


def _number(value: object, name: str) -> float:
    if isinstance(value, str) and re.fullmatch(r'\s*\d+(\.\d{1,2})?\s*', value):  # '1000' or '999.90', not '1.000'
        value = float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < float('inf'):
        return float(value)
    raise ToolError(f'{name} precisa ser um valor em reais maior que zero, recebi {value!r}.')


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise ToolError(f'{name} precisa ser true ou false, recebi {value!r}.')


def _text(value: object, name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ToolError(f'{name} precisa ser um texto, recebi {value!r}.')
