"""Visual identity: warm wood tones, a serif display face and the few styles Tailwind can't express."""
from nicegui import app, ui

COLORS = {
    'primary': '#a8461f',
    'secondary': '#2b211c',
    'accent': '#e2a03f',
    'positive': '#2f7d4f',
    'negative': '#c0392b',
    'ink': '#2b211c',
    'cream': '#faf6f0',
    'sand': '#f1e8dc',
    'muted': '#7a6a5f',
}

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,700'
         '&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">')

CSS = '''
body { font-family: 'Inter', system-ui, sans-serif; color: #2b211c; }
.font-display { font-family: 'Fraunces', Georgia, serif; letter-spacing: -0.01em; }
.product-card { transition: transform .18s ease, box-shadow .18s ease; }
.product-card:hover { transform: translateY(-3px); box-shadow: 0 14px 30px -12px rgba(43, 33, 28, .28); }
.product-photo img { filter: drop-shadow(0 10px 12px rgba(43, 33, 28, .22)); transition: transform .25s ease; }
.product-card:hover .product-photo img { transform: scale(1.04); }
.bump { animation: bump .5s ease; }
@keyframes bump { 30% { transform: scale(1.35); } 100% { transform: scale(1); } }
.hero-photo { filter: drop-shadow(0 22px 22px rgba(43, 33, 28, .3)); }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
'''


def setup() -> None:
    """App-wide theme; call once before ui.run()."""
    app.colors(**COLORS)
    ui.add_head_html(FONTS + f'<style>{CSS}</style>', shared=True)


def product_image(slug: str) -> str:
    return f'/static/products/{slug}.webp'
