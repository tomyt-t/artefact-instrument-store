"""The policy manual (data/políticas_da_loja.pdf) split into its numbered sections, for the agent to read and quote.

`python -m emporio.manual` lists the sections; `python -m emporio.manual 4.1 8` prints them.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pypdfium2

from . import policies
from .data import DATA_DIR, load_store
from .format import brl

MANUAL_FILE = 'políticas_da_loja.pdf'
PAGE_MARKS = re.compile(r'Empório da Música Manual de Políticas e Procedimentos|Página \d+')  # header and footer
HEADING = re.compile(r'(\d+)\.(\d+)? (\S.*)')  # '4. Política de Trocas' or '4.1 Direito de Arrependimento'
# (first, last, minimum installment) of section 3.1, the tiers policies.max_installments computes
INSTALLMENT_TIERS = ((2, 3, 50.0), (4, 6, 80.0), (7, 12, 100.0))
LOCAL_HOLIDAYS = {(10, 11): 'Mato Grosso do Sul', (6, 13): 'Campo Grande', (8, 26): 'Campo Grande'}


@dataclass(frozen=True)
class Section:
    number: str  # '4' or '4.1'
    title: str
    text: str

    @property
    def heading(self) -> str:
        """As printed in the manual: '4. Política de Trocas e Devoluções', '4.1 Direito de Arrependimento'."""
        return f'{self.number}{"" if "." in self.number else "."} {self.title}'


@lru_cache(maxsize=1)
def sections(path: Path = DATA_DIR / MANUAL_FILE) -> dict[str, Section]:
    """Every section in manual order. Text before section 1 (the cover) is dropped."""
    pdf = pypdfium2.PdfDocument(path)
    try:
        lines = [line.strip() for page in pdf for line in page.get_textpage().get_text_range().splitlines()]
    finally:
        pdf.close()
    found: dict[str, tuple[str, list[str]]] = {}
    body: list[str] = []
    top = sub = 0
    for line in lines:
        if not line or PAGE_MARKS.fullmatch(line):
            continue
        match = HEADING.fullmatch(line)
        # a heading must be the next number, so the numbered steps inside 7.2 ('3. Consulta: ...') stay as text
        if match and ((not match[2] and int(match[1]) == top + 1)
                      or (match[2] and int(match[1]) == top and int(match[2]) == sub + 1)):
            top, sub, body = int(match[1]), int(match[2] or 0), []
            found[f'{top}.{sub}' if sub else str(top)] = (match[3], body)
        elif found:
            body.append(line)
    return {number: Section(number, title, '\n'.join(body)) for number, (title, body) in found.items()}


def find(number: str) -> list[Section]:
    """The section and its subsections ('4' also gives 4.1 to 4.4), or [] when the manual has no such section."""
    number = number.strip().strip('.')
    if number not in sections():
        return []
    return [s for key, s in sections().items() if key == number or key.startswith(number + '.')]


def index() -> str:
    """One heading per line, subsections indented."""
    return '\n'.join(('  ' if '.' in s.number else '') + s.heading for s in sections().values())


def official_facts() -> str:
    """What the store data and the code establish where the manual is wrong or silent. Prevails over the manual."""
    info, store = policies.STORE, load_store()
    weekdays, saturday = policies.OPENING_HOURS[0], policies.OPENING_HOURS[5]
    *others, last = [c.name for c in store.categories_in_use]
    tiers = '; '.join(f'de {first}x a {most}x, parcela mínima de {brl(minimum)}'
                      for first, most, minimum in INSTALLMENT_TIERS)
    thresholds = ', '.join(f'{n}x a partir de {brl(n * minimum)}' for first, most, minimum in INSTALLMENT_TIERS
                           for n in range(first, most + 1))
    *national, last_national = [f'{day:02d}/{month:02d}' for month, day in sorted(policies.HOLIDAYS)
                                if (month, day) not in LOCAL_HOLIDAYS]
    local = ', '.join(f'{day:02d}/{month:02d} em {place}' for (month, day), place in LOCAL_HOLIDAYS.items())
    return '\n'.join([
        'Dados oficiais (prevalecem sobre o manual):',
        f'- Telefone e WhatsApp: {info.phone}. E-mail: {info.email}, sem acento. Os outros contatos que aparecem '
        'no manual (outro número na seção 7, e-mail com acento na 1.2) não valem.',
        f'- Endereço: {info.street}, {info.city}.',
        f'- Horário: segunda a sexta das {weekdays[0]}h às {weekdays[1]}h e sábado das {saturday[0]}h às '
        f'{saturday[1]}h. Fecha aos domingos e nos feriados: nacionais ({", ".join(national)} e {last_national}), '
        f'locais ({local}) e Sexta-feira Santa (dois dias antes da Páscoa). Carnaval e Corpus Christi não fecham a '
        'loja.',
        f'- Parcelamento sem juros no cartão de crédito, conforme a seção 3.1: {tiers}. A tabela da seção 3 só cita '
        'a parcela mínima de R$ 100,00. O máximo de um produto ou do carrinho já vem calculado no campo "parcelas" de '
        'ver_produto e ver_carrinho: não calcule, e o frete não entra na conta. Para um valor que o cliente disser, '
        f'sem produto: {thresholds}.',
        '- Arrependimento (4.1): vale só para compras online; compra na loja física segue a troca por preferência '
        '(4.3). Os itens não elegíveis da seção 4.4 tratam de troca: para devolver um item ajustado sob encomenda, a '
        'equipe confirma.',
        f'- Catálogo: {len(store.products)} produtos cadastrados, de {", ".join(others)} e {last}. O manual fala em '
        'mais de 300 instrumentos e cita sopros e cordas orquestrais, que a loja não tem hoje. Os teclados são todos '
        'sintetizadores: não há piano digital nem acústico. Preço, estoque, promoção e prazo de entrega só valem '
        'consultados no sistema.',
        '- Troca por preferência (4.3): os 7 dias corridos contam a partir do recebimento, como no arrependimento '
        '(4.1), e a troca depende de o produto desejado estar disponível. O manual não diz quem paga o frete dessa '
        'troca: a equipe confirma.',
        '- Entrega fora do Brasil: as políticas só preveem entregas no Brasil (seção 5). Diga isso e que a equipe '
        'confirma se há alternativa; não afirme que a loja não entrega.',
        '- Pedido atrasado ou que não chegou: o manual não trata de reembolso por atraso, e a seção 4.1 só vale depois '
        'do recebimento. A equipe avalia o caso: não diga que ela vai cancelar nem reembolsar.',
        '- Avaria na entrega (5.2): o cliente deve recusar o recebimento e falar com a loja na hora. Não há outra opção: '
        'não sugira aceitar e conferir depois.',
        '- PIX: os 5% valem só para produto sem promoção. Produto em promoção custa o mesmo no PIX: os descontos não '
        'se somam (6.2).',
        '- Região metropolitana de Campo Grande (5.1): o manual não lista as cidades. Numa cidade vizinha, não afirme '
        'se ela entra ou não: diga que a equipe confirma.',
        '- Frete para outras cidades (5.2): os prazos valem como estão no manual e devem ser informados: PAC de 5 a 12 '
        'dias úteis, SEDEX de 2 a 5 e Jadlog de 3 a 8. O valor depende do CEP, e a vitrine não tem campo de CEP nem '
        'fechamento de compra: quem informa o valor é a equipe.',
    ])


def main(argv: list[str] | None = None) -> int:
    numbers = sys.argv[1:] if argv is None else argv
    if not numbers:
        print(index())
        return 0
    found = [find(number) for number in numbers]
    if missing := [number for number, group in zip(numbers, found, strict=True) if not group]:
        print(f'Section not found: {", ".join(missing)}. With no arguments, the command lists the sections.',
              file=sys.stderr)
        return 1
    print(official_facts())
    for section in (s for group in found for s in group):
        print(f'\n{section.heading}\n{section.text}'.rstrip())
    return 0


if __name__ == '__main__':
    sys.exit(main())
