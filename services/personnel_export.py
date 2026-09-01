from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES_BOLD=("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf","/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf","/usr/share/fonts/truetype/freefont/FreeSansBold.ttf")
FONT_CANDIDATES_REGULAR=("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf","/usr/share/fonts/dejavu/DejaVuSans.ttf","/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf","/usr/share/fonts/truetype/freefont/FreeSans.ttf")
BG=(15,17,22); PANEL=(25,28,35); PANEL_SOFT=(31,34,42); TEXT=(248,249,252); MUTED=(178,184,197); BLUE=(91,110,245); GREEN=(71,201,145); GOLD=(245,184,72); TRACK=(43,47,57)

@lru_cache(maxsize=64)
def _font(size:int,*,bold:bool=False):
    for path in (FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REGULAR):
        if Path(path).is_file():
            try:return ImageFont.truetype(path,size=size)
            except OSError:pass
    for name in (("DejaVuSans-Bold.ttf","LiberationSans-Bold.ttf") if bold else ("DejaVuSans.ttf","LiberationSans-Regular.ttf")):
        try:return ImageFont.truetype(name,size=size)
        except OSError:pass
    try:return ImageFont.load_default(size=size)
    except TypeError as exc:raise RuntimeError("No scalable TrueType font found. Install fonts-dejavu-core.") from exc

def _measure(draw,value,font):
    b=draw.textbbox((0,0),str(value),font=font);return b[2]-b[0],b[3]-b[1]
def _fit_text(draw,value,max_width,*,preferred,minimum,bold=True):
    for size in range(preferred,minimum-1,-2):
        f=_font(size,bold=bold)
        if _measure(draw,value,f)[0]<=max_width:return f
    return _font(minimum,bold=bold)
def _fit_box(draw,value,max_width,max_height,*,maximum,minimum,bold=True):
    for size in range(maximum,minimum-1,-2):
        f=_font(size,bold=bold);w,h=_measure(draw,value,f)
        if w<=max_width and h<=max_height:return f
    return _font(minimum,bold=bold)
def _rounded(draw,box,*,fill,radius=24):draw.rounded_rectangle(box,radius=radius,fill=fill)
def _row_value(row,key,default=0):
    try:v=row[key]
    except (KeyError,TypeError,IndexError):v=default
    return v if v is not None else default
def _png_bytes(image):
    out=BytesIO();image.save(out,format="PNG",optimize=True);return out.getvalue()

def render_personnel_png(title:str,rows)->bytes:
    all_rows=list(rows); width=1920; margin=54; cols=2
    # Keep every employee in one image. Grow vertically instead of silently cutting entries.
    row_count=max(1,(len(all_rows)+cols-1)//cols); card_h=190; row_gap=22; grid_top=385
    height=max(1280,grid_top+row_count*(card_h+row_gap)+70)
    image=Image.new("RGB",(width,height),BG);draw=ImageDraw.Draw(image)
    draw.text((margin,48),title,font=_fit_text(draw,title,width-margin*2,preferred=86,minimum=66),fill=TEXT)
    draw.text((margin,145),f"{len(all_rows)} Mitarbeiter • Aktivität auf einen Blick",font=_font(34),fill=MUTED)
    total_e=sum(int(_row_value(r,"inductions")) for r in all_rows);total_b=sum(int(_row_value(r,"bwg")) for r in all_rows)
    gap=24;kpi_y=205;kpi_h=145;kpi_w=(width-margin*2-gap*2)//3
    for i,(label,value,accent) in enumerate((("EINWEISUNGEN",total_e,BLUE),("BWG",total_b,GREEN),("GESAMT",total_e+total_b,GOLD))):
        x=margin+i*(kpi_w+gap);_rounded(draw,(x,kpi_y,x+kpi_w,kpi_y+kpi_h),fill=PANEL);draw.rounded_rectangle((x,kpi_y,x+9,kpi_y+kpi_h),radius=4,fill=accent);draw.text((x+34,kpi_y+24),label,font=_font(28,bold=True),fill=MUTED);draw.text((x+34,kpi_y+61),str(value),font=_font(58,bold=True),fill=TEXT)
    col_gap=28;card_w=(width-margin*2-col_gap)//2
    for index,row in enumerate(all_rows):
        col=index%2;rindex=index//2;x=margin+col*(card_w+col_gap);y=grid_top+rindex*(card_h+row_gap);_rounded(draw,(x,y,x+card_w,y+card_h),fill=PANEL)
        rank=index+1;badge_fill=GOLD if rank==1 else PANEL_SOFT;draw.ellipse((x+24,y+22,x+68,y+66),fill=badge_fill);bt=str(rank);bf=_font(21,bold=True);bw,bh=_measure(draw,bt,bf);draw.text((x+46-bw/2,y+44-bh/2-2),bt,font=bf,fill=BG if rank==1 else TEXT)
        name=str(_row_value(row,"display_name","Unbekannt"));nf=_fit_box(draw,name,card_w-116,78,maximum=96,minimum=44,bold=True);draw.text((x+88,y+18),name,font=nf,fill=TEXT)
        e=int(_row_value(row,"inductions"));b=int(_row_value(row,"bwg"));a=int(_row_value(row,"activity",e+b));stat_y=y+119;stat_gap=22;stat_w=(card_w-48-stat_gap*2)//3
        for si,(label,value,accent) in enumerate((("E",e,BLUE),("BWG",b,GREEN),("GESAMT",a,GOLD))):
            sx=x+24+si*(stat_w+stat_gap);_rounded(draw,(sx,stat_y,sx+stat_w,stat_y+53),fill=PANEL_SOFT,radius=14);draw.text((sx+14,stat_y+15),label,font=_font(21,bold=True),fill=accent);vf=_font(36,bold=True);vw,_=_measure(draw,str(value),vf);draw.text((sx+stat_w-vw-14,stat_y+7),str(value),font=vf,fill=TEXT)
    draw.text((margin,height-46),"Raspberry-Bot • Perso 2.0",font=_font(24),fill=MUTED);return _png_bytes(image)

def render_personnel_chart(title:str,rows)->bytes:
    width,height=1920,1080;image=Image.new("RGB",(width,height),BG);draw=ImageDraw.Draw(image);margin=70
    draw.text((margin,45),title,font=_fit_text(draw,title,width-margin*2,preferred=76,minimum=56),fill=TEXT);draw.text((margin,135),"Einweisungen vs. BWG • Top 10 nach Gesamtaktivität",font=_font(30),fill=MUTED)
    all_rows=list(rows);values=sorted(all_rows,key=lambda r:int(_row_value(r,"activity",int(_row_value(r,"inductions"))+int(_row_value(r,"bwg")))),reverse=True)[:10];max_total=max((int(_row_value(r,"activity",int(_row_value(r,"inductions"))+int(_row_value(r,"bwg")))) for r in values),default=1) or 1
    legend_y=194;draw.rounded_rectangle((margin,legend_y,margin+26,legend_y+26),radius=6,fill=BLUE);draw.text((margin+38,legend_y-3),"Einweisungen",font=_font(25,bold=True),fill=TEXT);draw.rounded_rectangle((margin+230,legend_y,margin+256,legend_y+26),radius=6,fill=GREEN);draw.text((margin+268,legend_y-3),"BWG",font=_font(25,bold=True),fill=TEXT)
    top=260;row_h=72;name_w=430;chart_x=margin+name_w;chart_w=width-chart_x-margin
    for index,row in enumerate(values):
        y=top+index*row_h;name=str(_row_value(row,"display_name","Unbekannt"));draw.text((margin,y+14),f"{index+1}. {name}",font=_fit_text(draw,name,name_w-55,preferred=30,minimum=20),fill=TEXT);e=int(_row_value(row,"inductions"));b=int(_row_value(row,"bwg"));total=e+b;ty=y+12;th=42;draw.rounded_rectangle((chart_x,ty,chart_x+chart_w,ty+th),radius=14,fill=TRACK);ew=int(chart_w*e/max_total);bw=int(chart_w*b/max_total)
        if ew:draw.rounded_rectangle((chart_x,ty,chart_x+ew,ty+th),radius=14,fill=BLUE)
        if bw:draw.rounded_rectangle((chart_x+ew,ty,min(chart_x+ew+bw,chart_x+chart_w),ty+th),radius=14,fill=GREEN)
        label=f"E {e}   BWG {b}   = {total}";lf=_font(23,bold=True);lw,_=_measure(draw,label,lf);draw.text((min(chart_x+max(ew+bw,12)+12,width-margin-lw),y+18),label,font=lf,fill=TEXT)
    total_e=sum(int(_row_value(r,"inductions")) for r in all_rows);total_b=sum(int(_row_value(r,"bwg")) for r in all_rows);draw.text((margin,height-65),f"Gesamt • Einweisungen {total_e}  |  BWG {total_b}  |  Aktivität {total_e+total_b}",font=_font(28,bold=True),fill=MUTED);return _png_bytes(image)
