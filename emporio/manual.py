"""The policy manual (data/políticas_da_loja.pdf) split into its numbered sections, for the agent to read and quote.

`python -m emporio.manual` lists the sections; `python -m emporio.manual 4.1 8` prints them with their corrections.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pypdfium2

from .data import DATA_DIR
from .format import brl
from .policies import STORE

MANUAL_FILE = 'políticas_da_loja.pdf'
PAGE_MARKS = re.compile(r'Empório da Música Manual de Políticas e Procedimentos|Página \d+')  # header and footer
HEADING = re.compile(r'(\d+)\.(\d+)? (\S.*)')  # '4. Política de Trocas' or '4.1 Direito de Arrependimento'
_THRESHOLDS = ', '.join(f'{n}x a partir de {brl(n * minimum)}' for first, most, minimum in ((2, 3, 50.0), (4, 6, 80.0), (7, 12, 100.0))
                        for n in range(first, most + 1))
# Where the store data and the code settle what the manual gets wrong or leaves out, with the sections each one corrects: reading
# a section brings only its own, and each text cites its section so a whole chapter can be read without losing track.
# What already reaches the model another way (hours, categories, PIX on sale, address) is not here.
CORRECTIONS = (
    (('1',), 'Seção 1: o catálogo tem 65 produtos, não "mais de 300 instrumentos".'),
    (('1.2',), f'Seção 1.2: o e-mail certo é {STORE.email}, sem acento.'),
    (('3', '3.1'), 'Seção 3: a parcela mínima de R$ 100,00 da tabela só vale de 7x a 12x; as faixas são as da 3.1. O máximo de um produto ou '
                   'do carrinho já vem calculado no campo "parcelas" de ver_produto e ver_carrinho: não calcule, e o frete não entra na '
                   f'conta. Para um valor que o cliente disser, sem produto: {_THRESHOLDS}.'),
    (('4.1',), 'Seção 4.1: o arrependimento vale só para compras online, depois do recebimento. Compra na loja física segue a troca por '
               'preferência (4.3).'),
    (('4.1',), 'Seção 4.1: pedido atrasado ou que não chegou: o manual não trata de reembolso por atraso. A equipe avalia o caso: não diga que '
               'ela vai cancelar nem reembolsar.'),
    (('4.3',), 'Seção 4.3: os 7 dias corridos contam a partir do recebimento, como no arrependimento (4.1). O manual não diz quem paga o frete '
               'dessa troca: a equipe confirma.'),
    (('4.4',), 'Seção 4.4: estes itens não têm troca, mesmo dentro do prazo. A lista não trata de devolução: para devolver um item ajustado sob '
               'encomenda, a equipe confirma.'),
    (('5.1',), 'Seção 5.1: o manual não lista as cidades da região metropolitana: numa cidade vizinha, não afirme se ela entra ou não; a equipe '
               'confirma.'),
    (('5.2',), 'Seção 5.2: informe os prazos da tabela. O valor do frete depende do CEP, mas não é calculado aqui: quem informa é a equipe.'),
    (('5.2',), 'Seção 5.2: avaria na entrega: o cliente deve recusar o recebimento e falar com a loja na hora. Não há outra opção: não sugira '
               'aceitar e conferir depois.'),
    (('5', '5.2'), 'Seção 5.2: entrega fora do Brasil: as políticas só preveem entregas no Brasil. Diga isso e que a equipe confirma se há '
                   'alternativa; não afirme que a loja não entrega.'),
    (('7',), f'Seção 7: o WhatsApp certo é {STORE.phone}, não o número citado nesta seção.'),
    (('7.1',), 'Seção 7.1: o manual não lista lojas parceiras: não indique outras lojas.'),
    (('7.3',), 'Seção 7.3: você não registra nem encaminha reclamações: oriente o cliente a falar com a equipe, que retorna em até 24 '
               'horas úteis. Nunca diga que registrou ou encaminhou.'),
)


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


def corrections(read: Iterable[Section]) -> list[str]:
    """The corrections of the sections read, in manual order, with no repeats."""
    numbers = {section.number for section in read}
    return [text for targets, text in CORRECTIONS if numbers.intersection(targets)]


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
    read = [section for group in found for section in group]
    print('\n\n'.join(f'{section.heading}\n{section.text}'.rstrip() for section in read))
    if fixes := corrections(read):
        print('\nCorreções (prevalecem sobre o texto acima):')
        print('\n'.join(f'- {fix}' for fix in fixes))
    return 0


if __name__ == '__main__':
    sys.exit(main())
