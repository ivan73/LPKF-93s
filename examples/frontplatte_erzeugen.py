# Erzeugt examples/frontplatte.dxf (Testzeichnung 100x50 mm): python3 frontplatte_erzeugen.py frontplatte.dxf
import ezdxf, sys
doc = ezdxf.new('R2010'); doc.header['$INSUNITS'] = 4
msp = doc.modelspace()
for n in ['Kontur','Bohrungen','Ausbrueche','Gravur']: doc.layers.add(n)
# Außenkontur 100x50 mit abgerundeten Ecken (Bulge)
import math
r=3; b=math.tan(math.pi/8)
pts=[(r,0,0),(100-r,0,b),(100,r,0),(100,50-r,b),(100-r,50,0),(r,50,b),(0,50-r,0),(0,r,b)]
msp.add_lwpolyline(pts, format='xyb', close=True, dxfattribs={'layer':'Kontur'})
# Befestigungslöcher 3.2 (größer als Bohrer -> fräsen), 2.0 bohren
for x,y in [(5,5),(95,5),(5,45),(95,45)]: msp.add_circle((x,y),1.6,dxfattribs={'layer':'Bohrungen'})
for x in range(60,90,6): msp.add_circle((x,10),1.0,dxfattribs={'layer':'Bohrungen'})
# Display-Ausschnitt aus 4 LINEs
for a,c in [((10,20),(45,20)),((45,20),(45,40)),((45,40),(10,40)),((10,40),(10,20))]:
    msp.add_line(a,c,dxfattribs={'layer':'Ausbrueche'})
# Poti-Loch 7 mm
msp.add_circle((70,30),3.5,dxfattribs={'layer':'Ausbrueche'})
# Gravur: Skala als Block
blk=doc.blocks.new('TICK'); blk.add_line((0,0),(0,2))
for i in range(11):
    a=math.radians(225-27*i); msp.add_blockref('TICK',(70+6*math.cos(a),30+6*math.sin(a)),dxfattribs={'layer':'Gravur','rotation':math.degrees(a)-90})
msp.add_text('LBS Meran', height=4, dxfattribs={'layer':'Gravur'}).set_placement((10,8))
msp.add_arc((70,30),8,-45,225,dxfattribs={'layer':'Gravur'})
doc.saveas(sys.argv[1])
