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
.hero-photo { filter: drop-shadow(0 22px 22px rgba(43, 33, 28, .3)); }
'''


def setup() -> None:
    """App-wide theme; call once before ui.run()."""
    app.colors(**COLORS)
    ui.add_head_html(FONTS + f'<style>{CSS}</style>', shared=True)


def product_image(slug: str) -> str:
    return f'/static/products/{slug}.webp'
