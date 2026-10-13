# Writes docs/images/banner-light.svg and banner-dark.svg (1280x320). With --social, writes social-preview.svg
# instead (the picture behind docs/images/social-preview.png; render it to 1280x640 PNG).
# Run: python3 docs/src/banner.py [output directory] [--social]
import os
import sys
args = [a for a in sys.argv[1:] if a != '--social']
OUT = args[0] if args else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'images')
SOCIAL = '--social' in sys.argv
T = {
 'light': dict(bg1='#ffffff', bg2='#eef3fb', dot='#0d1117', dota='0.07', panel='#ffffff', stroke='#c3ccd6', rule='#e1e6ec',
               title='#1f2328', sub='#31363d', small='#59636e', text='#1f2328', dim='#59636e',
               accent='#0b7a75', failed='#cf222e'),
 'dark':  dict(bg1='#0d1117', bg2='#161b22', dot='#ffffff', dota='0.06', panel='#161b22', stroke='#30363d', rule='#30363d',
               title='#e6edf3', sub='#c9d1d9', small='#8b949e', text='#e6edf3', dim='#8b949e',
               accent='#2ec4b6', failed='#f85149'),
}
def page(c, px, top, pitch, f, right):
    """A Logseq page as an outline: f scales offsets and font sizes, right is the x the status tags end at."""
    bx, tx = px + 34 * f, px + 54 * f
    y = [top + i * pitch for i in range(4)]
    caret_x = tx + 33 * 0.6 * 19 * f  # after "Call with Alex about the contract"
    return f'''
  <text x="{px+30*f:.0f}" y="{y[0]+7*f:.0f}" class="mono" font-size="{19*f:.0f}" font-weight="700" fill="{c['dim']}">## Log</text>
  <line x1="{px+30*f:.0f}" y1="{y[0]+pitch/2:.0f}" x2="{right + 8*f:.0f}" y2="{y[0]+pitch/2:.0f}" stroke="{c['rule']}" stroke-width="2"/>
  <circle cx="{bx:.0f}" cy="{y[1]+2*f:.0f}" r="{4*f:.1f}" fill="{c['dim']}"/>
  <text x="{tx:.0f}" y="{y[1]+7*f:.0f}" class="mono" font-size="{19*f:.0f}" fill="{c['text']}">Call with Alex about the contract</text>
  <rect x="{caret_x+3*f:.0f}" y="{y[1]-13*f:.0f}" width="{2.5*f:.1f}" height="{26*f:.0f}" fill="{c['text']}"/>
  <text x="{right}" y="{y[1]+6*f:.0f}" class="mono" font-size="{17*f:.0f}" font-weight="700" text-anchor="end" fill="{c['failed']}">✕ refused</text>
  <circle cx="{bx:.0f}" cy="{y[2]+2*f:.0f}" r="{4*f:.1f}" fill="{c['accent']}"/>
  <text x="{tx:.0f}" y="{y[2]+7*f:.0f}" class="mono" font-size="{19*f:.0f}" fill="{c['text']}"><tspan font-weight="700">14:31</tspan> Decided to ship on Friday</text>
  <text x="{right}" y="{y[2]+6*f:.0f}" class="mono" font-size="{17*f:.0f}" font-weight="700" text-anchor="end" fill="{c['accent']}">✓ written</text>
  <text x="{tx:.0f}" y="{y[3]+6*f:.0f}" class="mono" font-size="{15*f:.0f}" fill="{c['dim']}">confirmed with Logseq · the block you type in stays yours</text>'''
def svg(theme, social):
    c = T[theme]
    w, h = (1280, 640) if social else (1280, 320)
    r = 0 if social else 24
    head = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="logseq-cli: lets an AI agent write into a Logseq graph without breaking it">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c['bg1']}"/><stop offset="1" stop-color="{c['bg2']}"/></linearGradient>
    <pattern id="dots" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1.2" fill="{c['dot']}" opacity="{c['dota']}"/></pattern>
    <style>
      .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }}
      .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    </style>
  </defs>
  <rect width="{w}" height="{h}" rx="{r}" fill="url(#bg)"/>
  <rect width="{w}" height="{h}" rx="{r}" fill="url(#dots)"/>'''
    if social:
        px, py, pw, ph = 160, 56, 960, 244
        body = f'''
  <rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="18" fill="{c['panel']}" stroke="{c['stroke']}" stroke-width="2"/>{page(c, px, py+46, 50, 1.15, px+pw-36)}
  <text x="640" y="418" text-anchor="middle" class="mono" font-size="96" font-weight="700" fill="{c['title']}" letter-spacing="-2">logseq<tspan fill="{c['accent']}">-</tspan>cli</text>
  <text x="640" y="484" text-anchor="middle" class="sans" font-size="36" fill="{c['sub']}">Your agent writes into Logseq</text>
  <text x="640" y="536" text-anchor="middle" class="sans" font-size="26" fill="{c['small']}">without breaking your graph · every write verified</text>'''
    else:
        px, py, pw, ph = 48, 44, 700, 232
        body = f'''
  <rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="16" fill="{c['panel']}" stroke="{c['stroke']}" stroke-width="2"/>{page(c, px, py+41, 50, 1.0, px+pw-28)}
  <text x="800" y="160" class="mono" font-size="68" font-weight="700" fill="{c['title']}" letter-spacing="-2">logseq<tspan fill="{c['accent']}">-</tspan>cli</text>
  <text x="804" y="204" class="sans" font-size="26" fill="{c['sub']}">Your agent writes into Logseq</text>
  <text x="804" y="240" class="sans" font-size="19" fill="{c['small']}">without breaking your graph</text>'''
    return head + body + '\n</svg>\n'
if SOCIAL:
    open(os.path.join(OUT, 'social-preview.svg'), 'w', encoding='utf-8').write(svg('dark', True))
else:
    for th in T:
        open(os.path.join(OUT, f'banner-{th}.svg'), 'w', encoding='utf-8').write(svg(th, False))
