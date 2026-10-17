"""PNG card renderer. Uses Windows fonts if available; no fonts bundled."""
from __future__ import annotations
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps

YELLOW = (255, 210, 30)
WHITE = (245, 245, 245)
GRAY = (164, 164, 164)

def font(size, bold=False):
    for filename in ([r'C:\Windows\Fonts\arialbd.ttf',r'C:\Windows\Fonts\segoeuib.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'] if bold else
                     [r'C:\Windows\Fonts\arial.ttf',r'C:\Windows\Fonts\segoeui.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']):
        try: return ImageFont.truetype(filename,size)
        except OSError: pass
    return ImageFont.load_default()

def card_png(name, character, rank, joined, hours, points, badges, avatar_bytes=None):
    im = Image.new('RGB',(1024,565),(16,16,18))
    draw = ImageDraw.Draw(im)
    draw.rectangle((0,0,1024,16),fill=YELLOW)
    draw.rectangle((0,548,1024,565),fill=YELLOW)
    draw.rounded_rectangle((35,48,989,515),radius=25,fill=(28,28,31),outline=(74,74,74),width=2)
    draw.text((310,76),'WEST STAINS MASSIVE',fill=YELLOW,font=font(38,True))
    draw.text((313,128),'OFFICIAL MEMBER ID  /  FIVE M ROLEPLAY',fill=GRAY,font=font(17,True))
    draw.line((313,162,957,162),fill=YELLOW,width=3)
    draw.rounded_rectangle((67,105,276,329),radius=14,fill=(58,58,61))
    if avatar_bytes:
        try:
            portrait=Image.open(BytesIO(avatar_bytes)).convert('RGB')
            portrait=ImageOps.fit(portrait,(190,190))
            im.paste(portrait,(77,116))
        except Exception: pass
    draw.text((69,350),'WSM  /  LOYALTY',fill=YELLOW,font=font(19,True))
    rank=rank.split(' ',1)[-1]  # Avoid missing emoji glyphs in standard Windows fonts.
    pairs=[('DISCORD',name),('IN CITY',character or 'Not set'),('RANK',rank),
           ('MEMBER SINCE',joined),('RP ACTIVITY',f'{hours:.1f} hours'),
           ('MISSION POINTS',str(points)),('BADGES EARNED',str(badges))]
    y=179
    for label,value in pairs:
        x=315 if y<420 else 315
        draw.text((x,y),label,fill=GRAY,font=font(15,True))
        # Pillow length test; truncate safely
        value=str(value)
        while draw.textbbox((0,0),value,font=font(22,True))[2]>615 and len(value)>4:
            value=value[:-2]+'…'
        draw.text((x,y+20),value,fill=WHITE,font=font(22,True))
        y+=42
    draw.text((70,489),'ONE BLOCK  •  ONE FAMILY  •  ONE MASSIVE',fill=YELLOW,font=font(20,True))
    out=BytesIO(); im.save(out,format='PNG'); out.seek(0); return out
